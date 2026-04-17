"""
Hybrid matcher: prefiltra tickets Jira por keywords y selecciona los
matches finales con GPT-4o.
"""
import os
import json
import re
import unicodedata
from typing import Iterable
from openai import OpenAI
from dotenv import load_dotenv

from email_extract import extract_emails, INTERNAL_DOMAINS

load_dotenv()


STOPWORDS_ES = {
    "de", "la", "el", "en", "y", "a", "los", "las", "del", "un", "una", "por",
    "con", "no", "se", "su", "al", "lo", "es", "que", "o", "como", "para",
    "me", "mi", "te", "ti", "le", "les", "ha", "he", "has", "este", "esta",
    "esto", "estos", "estas", "eso", "ese", "esa", "esos", "esas", "muy",
    "mas", "pero", "sin", "son", "ser", "hay", "tiene", "tener",
}


def _normalize(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    no_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_acc.lower()


def _tokens(text: str) -> set[str]:
    n = _normalize(text)
    words = re.findall(r"[a-z0-9_]{3,}", n)
    return {w for w in words if w not in STOPWORDS_ES}


class JiraMatcher:
    def __init__(self, openai_client=None, api_key: str | None = "__env__", model: str = "gpt-4o"):
        """
        api_key:
          - "__env__" (default) → use os.environ.get("OPENAI_API_KEY").
          - None              → explicit: disable LLM (fallback to prefilter only).
          - other str         → use as key.
        openai_client: pre-built client (used in tests). If given, LLM is enabled.
        """
        if openai_client is not None:
            self.openai = openai_client
        else:
            key = os.environ.get("OPENAI_API_KEY") if api_key == "__env__" else api_key
            self.openai = OpenAI(api_key=key) if key else None
        self.model = model

    # ── signal extraction ───────────────────────────────────
    def _cluster_signals(self, cluster: dict) -> dict:
        anclas = cluster.get("anclas") or {}
        textos: list[str] = [
            cluster.get("resumen") or "",
            cluster.get("tipo_problema") or "",
            cluster.get("sistema") or "",
            cluster.get("nombre") or "",
        ]
        if isinstance(anclas, dict):
            for v in anclas.values():
                if isinstance(v, str):
                    textos.append(v)
                elif isinstance(v, list):
                    textos.extend(x for x in v if isinstance(x, str))
        keywords: set[str] = set()
        for t in textos:
            keywords |= _tokens(t)
        return {
            "keywords": keywords,
            "resumen": cluster.get("resumen") or "",
            "anclas": anclas,
        }

    # ── prefilter ───────────────────────────────────────────
    def _score(self, keywords: set[str], ticket: dict) -> int:
        text = " ".join([
            ticket.get("summary") or "",
            ticket.get("description_text") or "",
        ])
        tokens = _tokens(text)
        base = len(keywords & tokens)
        label_tokens: set[str] = set()
        for lab in ticket.get("labels") or []:
            label_tokens |= _tokens(lab)
        bonus = 2 * len(keywords & label_tokens)
        return base + bonus

    # ── email-aware helpers ─────────────────────────────────
    def _extract_jira_emails(self, jira: dict) -> set[str]:
        txt = f"{jira.get('summary', '')} {jira.get('description_text', '')}"
        return set(extract_emails(txt, exclude_domains=INTERNAL_DOMAINS))

    def _cluster_email_sources(
        self, cluster: dict, tickets_by_id: dict[int, dict]
    ) -> dict[str, list[int]]:
        """Map each cluster-associated email to the list of zendesk_ids that
        contributed it. Used to preserve trazabilidad in email_match."""
        out: dict[str, list[int]] = {}
        for tid in cluster.get("ticket_ids") or []:
            t = tickets_by_id.get(tid) or {}
            for e in t.get("emails_asociados") or []:
                if not e:
                    continue
                key = e.lower()
                if key not in out:
                    out[key] = []
                if tid not in out[key]:
                    out[key].append(tid)
        return out

    def _prefilter_keywords(self, signals: dict, pool: Iterable[dict], limit: int = 15) -> list[dict]:
        scored: list[tuple[int, dict]] = []
        for t in pool:
            s = self._score(signals["keywords"], t)
            if s > 0:
                scored.append((s, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:limit]]

    # ── LLM selection ───────────────────────────────────────
    # Umbral mínimo de confianza tras validación LLM. Matches por debajo se
    # descartan (el LLM a veces emite 0.4-0.6 "mismo dominio, otro problema").
    MIN_CONFIANZA = 0.7
    # Trunca la descripción Jira enviada al LLM para limitar tokens.
    _DESC_TRUNCATE = 600

    def _llm_select(
        self,
        signals: dict,
        candidatos: list[dict],
        top_k: int,
        email_match_by_id: dict[str, list[dict]] | None = None,
    ) -> list[dict]:
        email_match_by_id = email_match_by_id or {}
        brief = []
        for c in candidatos:
            desc = (c.get("description_text") or "").strip()
            if len(desc) > self._DESC_TRUNCATE:
                desc = desc[: self._DESC_TRUNCATE] + "…"
            item = {
                "jira_id": c["jira_id"],
                "summary": c.get("summary", ""),
                "description": desc,
                "labels": c.get("labels", []),
                "status": c.get("status"),
            }
            if c["jira_id"] in email_match_by_id:
                item["email_match"] = [e["email"] for e in email_match_by_id[c["jira_id"]]]
            brief.append(item)

        prompt = f"""Eres un ingeniero de soporte técnico. Te doy un CLUSTER de
incidencias de usuarios y una lista de TICKETS de Jira candidatos. Elige
SÓLO los Jira que describen EL MISMO FALLO TÉCNICO REPRODUCIBLE del
cluster — NO basta con que compartan dominio (suscripción, acceso, pagos)
o vocabulario.

Reglas estrictas:
1. Si el Jira describe un escenario distinto del cluster (p. ej. el cluster
   habla de "no puedo acceder tras pagar" y el Jira de "no se puede
   reactivar tras baja"), DESCÁRTALO aunque haya palabras en común.
2. Lee la `description` completa del Jira antes de decidir. No decidas
   sólo por `summary`.
3. Usa confianza 0.9+ sólo si estás muy seguro de que es el MISMO bug.
   0.7-0.8 si es una sub-variante plausible. <0.7 NO lo devuelvas.
4. Si NINGÚN Jira encaja, devuelve `{{"matches": []}}`. Es la respuesta
   correcta para muchos clusters.
5. Si un candidato incluye `email_match`, significa que el Jira menciona
   al mismo usuario que uno o más tickets del cluster. Es una señal
   FUERTE de relevancia PERO sólo cuando el problema también coincide;
   mismo usuario con incidencia distinta → descartar igualmente.

CLUSTER:
- Resumen: {signals['resumen']}
- Anclas: {json.dumps(signals['anclas'], ensure_ascii=False)}

CANDIDATOS:
{json.dumps(brief, ensure_ascii=False, indent=2)}

Responde SOLO con JSON:
{{"matches": [{{"jira_id": "TEC-...", "confianza": 0.0-1.0, "razon": "..."}}]}}"""
        resp = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(resp.choices[0].message.content)
        matches = data.get("matches", [])
        matches.sort(key=lambda m: m.get("confianza", 0.0), reverse=True)
        by_id = {c["jira_id"]: c for c in candidatos}
        result: list[dict] = []
        for m in matches[:top_k]:
            base = by_id.get(m.get("jira_id"))
            if not base:
                continue
            jid = base["jira_id"]
            em = email_match_by_id.get(jid, [])
            confianza = m.get("confianza")
            # Filtro mínimo: descarta matches flojos salvo que haya email_match
            # (ese caso ya se boostea a 0.95 más abajo).
            if not em:
                if confianza is None or float(confianza) < self.MIN_CONFIANZA:
                    continue
            razon = m.get("razon", "")
            if em:
                # Email confirmado por el LLM (devolvió el candidato): boost
                # determinístico a >=0.95, aunque el LLM no haya emitido valor.
                if confianza is None:
                    confianza = 0.95
                else:
                    confianza = max(float(confianza), 0.95)
                emails_txt = ", ".join(sorted({e["email"] for e in em}))
                razon = f"email de usuario ({emails_txt}) + concepto coincidente — {razon}"
            result.append({
                "jira_id": jid,
                "url": base["url"],
                "summary": base.get("summary", ""),
                "description_text": base.get("description_text", ""),
                "status": base.get("status"),
                "confianza": confianza,
                "razon": razon,
                "email_match": em,
            })
        return result

    # ── public entry point ──────────────────────────────────
    def match(
        self,
        cluster: dict,
        jira_pool: list[dict],
        top_k: int = 5,
        tickets_by_id: dict[int, dict] | None = None,
    ) -> list[dict]:
        if not jira_pool:
            return []
        signals = self._cluster_signals(cluster)
        email_sources = self._cluster_email_sources(cluster, tickets_by_id or {})
        cluster_emails = set(email_sources.keys())
        email_match_by_id: dict[str, list[dict]] = {}
        if cluster_emails:
            for j in jira_pool:
                inter = self._extract_jira_emails(j) & cluster_emails
                if inter:
                    entries: list[dict] = []
                    for e in sorted(inter):
                        for zid in email_sources.get(e, []):
                            entries.append({"email": e, "zendesk_id": zid})
                    email_match_by_id[j["jira_id"]] = entries

        if not signals["keywords"] and not email_match_by_id:
            return []

        candidatos: list[dict] = []
        if signals["keywords"]:
            candidatos = self._prefilter_keywords(signals, jira_pool, limit=15)
        by_id = {c["jira_id"]: c for c in candidatos}
        for j in jira_pool:
            if j["jira_id"] in email_match_by_id and j["jira_id"] not in by_id:
                candidatos.append(j)
                by_id[j["jira_id"]] = j

        if not candidatos:
            return []

        if self.openai is None:
            return [
                {
                    "jira_id": c["jira_id"],
                    "url": c["url"],
                    "summary": c.get("summary", ""),
                    "description_text": c.get("description_text", ""),
                    "status": c.get("status"),
                    "confianza": 0.9 if c["jira_id"] in email_match_by_id else None,
                    "razon": (
                        "email match sin validación LLM — verificar concepto manualmente"
                        if c["jira_id"] in email_match_by_id
                        else "sin LLM disponible"
                    ),
                    "email_match": email_match_by_id.get(c["jira_id"], []),
                }
                for c in candidatos[:top_k]
            ]
        return self._llm_select(signals, candidatos, top_k, email_match_by_id=email_match_by_id)
