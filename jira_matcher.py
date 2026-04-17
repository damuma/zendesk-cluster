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

    def _prefilter_keywords(self, signals: dict, pool: Iterable[dict], limit: int = 15) -> list[dict]:
        scored: list[tuple[int, dict]] = []
        for t in pool:
            s = self._score(signals["keywords"], t)
            if s > 0:
                scored.append((s, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:limit]]

    # ── LLM selection ───────────────────────────────────────
    def _llm_select(self, signals: dict, candidatos: list[dict], top_k: int) -> list[dict]:
        brief = [
            {
                "jira_id": c["jira_id"],
                "summary": c.get("summary", ""),
                "labels": c.get("labels", []),
                "status": c.get("status"),
            }
            for c in candidatos
        ]
        prompt = f"""Eres un ingeniero de soporte técnico. Te doy un CLUSTER de incidencias
de usuarios y una lista de TICKETS de Jira candidatos. Elige los Jira que
corresponden al mismo problema técnico del cluster. Descarta los que solo
comparten palabras sueltas pero son de otro dominio.

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
            result.append({
                "jira_id": base["jira_id"],
                "url": base["url"],
                "summary": base.get("summary", ""),
                "description_text": base.get("description_text", ""),
                "status": base.get("status"),
                "confianza": m.get("confianza"),
                "razon": m.get("razon", ""),
            })
        return result

    # ── public entry point ──────────────────────────────────
    def match(self, cluster: dict, jira_pool: list[dict], top_k: int = 5) -> list[dict]:
        if not jira_pool:
            return []
        signals = self._cluster_signals(cluster)
        if not signals["keywords"]:
            return []
        candidatos = self._prefilter_keywords(signals, jira_pool, limit=15)
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
                    "confianza": None,
                    "razon": "sin LLM disponible",
                }
                for c in candidatos[:top_k]
            ]
        return self._llm_select(signals, candidatos, top_k)
