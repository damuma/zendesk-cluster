"""One-off: recorta jira_candidatos de cada cluster a top-5 sin tocar LLM.

Aplica el mismo criterio de ordenación que fase3_clusterizar._merge_jira_candidates:
email_match primero, luego confianza descendente. Útil para limpiar clusters
que acumularon >5 candidatos durante streaming de Fase 3 antes del fix.

Uso:
    python -m scripts.dedupe_jira_candidates            # aplica
    python -m scripts.dedupe_jira_candidates --dry-run  # sólo imprime plan
"""
from __future__ import annotations

import argparse
import sys
from dotenv import load_dotenv

from storage import Storage
from fase3_clusterizar import _merge_jira_candidates


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--cap", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    storage = Storage()
    clusters = storage.get_clusters()
    total_before = 0
    total_after = 0
    changes: list[tuple[str, int, int]] = []

    for c in clusters:
        existing = c.get("jira_candidatos", []) or []
        before = len(existing)
        total_before += before
        deduped = _merge_jira_candidates(existing=existing, nuevos=[], cap=args.cap)
        after = len(deduped)
        total_after += after
        if before != after or [
            e.get("jira_id") if isinstance(e, dict) else e for e in existing
        ] != [d.get("jira_id") for d in deduped]:
            changes.append((c["cluster_id"], before, after))
            if not args.dry_run:
                c["jira_candidatos"] = deduped

    if not args.dry_run:
        storage.save_clusters(clusters)

    print(f"{'🧪 DRY-RUN' if args.dry_run else '✅ APLICADO'}")
    print(f"Clusters procesados: {len(clusters)}")
    print(f"Clusters modificados: {len(changes)}")
    print(f"Candidatos totales: {total_before} → {total_after}")
    for cid, b, a in changes[:20]:
        print(f"  {cid}: {b} → {a}")
    if len(changes) > 20:
        print(f"  ... ({len(changes) - 20} más)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
