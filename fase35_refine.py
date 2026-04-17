"""Fase 3.5 — refine batch de clusters heterogéneos por subtipo.

Divide clusters gordos o mezclados en sub-clusters usando un modelo de
razonamiento (gpt-5.4 con fallback gpt-4o). Padre queda `estado: refined`;
hijos `CLU-NNN-A`, `CLU-NNN-B`, …
"""
from __future__ import annotations

import json as _json
import logging
import os
import string
from collections import Counter
from datetime import datetime, timezone

from storage import Storage

_log = logging.getLogger(__name__)


# ── heuristics ─────────────────────────────────────────────
def heterogeneity_score(tickets: list[dict]) -> float:
    if not tickets:
        return 0.0
    sistemas: list[str] = []
    for t in tickets:
        anclas = t.get("anclas") or {}
        sist = anclas.get("sistemas") or []
        if sist:
            sistemas.append(sist[0])
    if not sistemas:
        return 0.0
    counts = Counter(sistemas)
    modal = counts.most_common(1)[0][1]
    return round(1.0 - (modal / len(tickets)), 4)


def should_refine(
    cluster: dict,
    tickets: list[dict],
    min_tickets: int = 15,
    het_min: float = 0.5,
) -> bool:
    if cluster.get("estado") not in (None, "abierto"):
        return False
    if cluster.get("ticket_count", 0) >= min_tickets:
        return True
    if heterogeneity_score(tickets) >= het_min:
        return True
    return False


# ── LLM split ──────────────────────────────────────────────
_PROMPT_TEMPLATE = """Eres un ingeniero de soporte técnico. Te doy un CLUSTER de tickets
que ha sido clasificado como "{tipo_problema} en {sistema}" pero es
demasiado amplio.

Divide los tickets en SUBGRUPOS homogéneos por subtipo de problema
técnico concreto. Cada subgrupo debe describir UN fallo específico
reproducible, no una categoría genérica.

Reglas:
- Si todos los tickets son realmente del mismo subtipo, devuelve UN
  único grupo con todos los ticket_ids.
- Los tickets con subject y body vacíos o con sólo metadata
  ("Conversation with Web User…") agrúpalos en un grupo
  "sin_contenido" — no intentes clasificarlos.
- Un ticket va a exactamente un subgrupo.

TICKETS:
{tickets_json}

Responde SOLO JSON:
{{"subgrupos": [{{"subtipo": "snake_case", "nombre": "...", "resumen": "...", "ticket_ids": [...]}}]}}"""


def split_cluster(
    tickets: list[dict],
    openai_client,
    model: str,
    fallback_model: str = "gpt-4o",
    max_tickets_per_batch: int = 40,
    cluster_meta: dict | None = None,
) -> list[dict]:
    meta = cluster_meta or {}
    brief = [
        {
            "zendesk_id": t.get("zendesk_id"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("body_preview") or "")[:500],
        }
        for t in tickets[:max_tickets_per_batch]
    ]
    prompt = _PROMPT_TEMPLATE.format(
        tipo_problema=meta.get("tipo_problema", "?"),
        sistema=meta.get("sistema", "?"),
        tickets_json=_json.dumps(brief, ensure_ascii=False, indent=2),
    )

    def _call(m: str) -> dict:
        resp = openai_client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return _json.loads(resp.choices[0].message.content)

    try:
        data = _call(model)
    except Exception as e:
        _log.warning("refine: modelo %s falló (%s), fallback a %s", model, e, fallback_model)
        data = _call(fallback_model)

    return data.get("subgrupos", []) or []


# ── apply split ────────────────────────────────────────────
def apply_split(parent: dict, subgrupos: list[dict], now: str) -> list[dict]:
    """Mutate parent and return children list.

    - <=1 subgroup: parent stays abierto; only `refined_at` is stamped.
    - >=2: create `CLU-NNN-A/B/…` children, mark parent `refined`.
    """
    parent["refined_at"] = now
    if len(subgrupos) <= 1:
        return []
    parent_id = parent["cluster_id"]
    children: list[dict] = []
    for idx, g in enumerate(subgrupos):
        suffix = string.ascii_uppercase[idx]
        child = {
            "cluster_id": f"{parent_id}-{suffix}",
            "parent_cluster_id": parent_id,
            "nombre": g.get("nombre") or parent.get("nombre", ""),
            "sistema": parent.get("sistema"),
            "tipo_problema": parent.get("tipo_problema"),
            "severidad": parent.get("severidad", "MEDIUM"),
            "subtipo": g.get("subtipo", "sin_etiqueta"),
            "resumen": g.get("resumen", ""),
            "estado": "abierto",
            "ticket_ids": list(g.get("ticket_ids") or []),
            "ticket_count": len(g.get("ticket_ids") or []),
            "jira_candidatos": [],
            "created_at": now,
            "updated_at": now,
            "refined_at": now,
        }
        children.append(child)
    parent["estado"] = "refined"
    parent["ticket_ids"] = []
    parent["jira_candidatos"] = []
    parent["ticket_count"] = 0
    parent["updated_at"] = now
    return children


# ── orchestrator ───────────────────────────────────────────
def run_refine(
    openai_client=None,
    matcher=None,
    storage: Storage | None = None,
    model: str | None = None,
    fallback_model: str = "gpt-4o",
    min_tickets: int = 15,
    het_min: float = 0.5,
) -> dict:
    storage = storage or Storage()
    clusters = storage.get_clusters()
    tickets_by_id = storage.get_tickets_by_id()
    jira_pool = storage.get_jira_tickets()

    stats = {"clusters_refined": 0, "children_created": 0, "noop": 0}
    now = datetime.now(timezone.utc).isoformat()
    model = model or os.environ.get("OPENAI_MODEL_REFINE", "gpt-5.4")

    new_clusters: list[dict] = []
    for cluster in clusters:
        tickets_en_cluster = [tickets_by_id[t] for t in cluster.get("ticket_ids", []) if t in tickets_by_id]
        if not should_refine(cluster, tickets_en_cluster, min_tickets, het_min):
            new_clusters.append(cluster)
            continue
        subgrupos = split_cluster(
            tickets_en_cluster,
            openai_client=openai_client,
            model=model,
            fallback_model=fallback_model,
            cluster_meta={
                "sistema": cluster.get("sistema"),
                "tipo_problema": cluster.get("tipo_problema"),
            },
        )
        children = apply_split(cluster, subgrupos, now=now)
        if children:
            stats["clusters_refined"] += 1
            stats["children_created"] += len(children)
            for ch in children:
                if matcher is not None and jira_pool:
                    ch["jira_candidatos"] = matcher.match(
                        ch, jira_pool, top_k=5, tickets_by_id=tickets_by_id
                    )
            new_clusters.append(cluster)
            new_clusters.extend(children)
        else:
            stats["noop"] += 1
            new_clusters.append(cluster)

    storage.save_clusters(new_clusters)
    return stats


def main() -> None:
    import argparse
    from openai import OpenAI
    from dotenv import load_dotenv
    from jira_matcher import JiraMatcher

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-tickets", type=int, default=int(os.environ.get("REFINE_MIN_TICKETS", 15)))
    parser.add_argument("--het-min", type=float, default=float(os.environ.get("REFINE_HETEROGENEITY_MIN", 0.5)))
    args = parser.parse_args()

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    matcher = JiraMatcher(openai_client=client, model=os.environ.get("OPENAI_MODEL", "gpt-4o"))
    stats = run_refine(
        openai_client=client,
        matcher=matcher,
        min_tickets=args.min_tickets,
        het_min=args.het_min,
    )
    print(f"✅ Refine: {stats}")


if __name__ == "__main__":
    main()
