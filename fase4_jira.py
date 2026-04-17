#!/usr/bin/env python3
"""
Re-matchea clusters existentes contra el JSON actual de tickets Jira.

Uso:
    python fase4_jira.py                    # todos los clusters
    python fase4_jira.py --cluster CLU-001  # uno solo
    python fase4_jira.py --solo-vacios      # solo los sin jira_candidatos
"""
import argparse
from dotenv import load_dotenv

from storage import Storage
from jira_matcher import JiraMatcher

load_dotenv()


def _is_empty(jc) -> bool:
    return not jc or len(jc) == 0


def run(storage: Storage, matcher: JiraMatcher, only_empty: bool, cluster_id: str | None) -> dict:
    jira_pool = storage.get_jira_tickets()
    clusters = storage.get_clusters()

    if cluster_id:
        clusters = [c for c in clusters if c.get("cluster_id") == cluster_id]
    if only_empty:
        clusters = [c for c in clusters if _is_empty(c.get("jira_candidatos"))]

    if not jira_pool:
        print("  (pool Jira vacío — ejecuta primero `python fase0_jira.py`)")
        return {"procesados": 0, "actualizados": 0}

    actualizados = 0
    for c in clusters:
        preview = {
            "cluster_id": c.get("cluster_id"),
            "nombre": c.get("nombre", ""),
            "sistema": c.get("sistema", ""),
            "tipo_problema": c.get("tipo_problema", ""),
            "resumen": c.get("resumen", ""),
            "anclas": {},
        }
        try:
            candidatos = matcher.match(preview, jira_pool, top_k=5)
        except Exception as e:
            print(f"  ⚠️  {c['cluster_id']}: {e}")
            continue
        before_ids = [j if isinstance(j, str) else j.get("jira_id") for j in c.get("jira_candidatos", [])]
        after_ids = [j["jira_id"] for j in candidatos]
        if before_ids != after_ids:
            actualizados += 1
        c["jira_candidatos"] = candidatos
        storage.save_cluster(c)

    stats = {"procesados": len(clusters), "actualizados": actualizados}
    print(f"  ✅ procesados={stats['procesados']} actualizados={stats['actualizados']}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", type=str, default=None)
    parser.add_argument("--solo-vacios", action="store_true")
    args = parser.parse_args()
    print("🔗 Fase 4 Jira — re-matching clusters")
    run(Storage(), JiraMatcher(), only_empty=args.solo_vacios, cluster_id=args.cluster)


if __name__ == "__main__":
    main()
