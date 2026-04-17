#!/usr/bin/env python3
"""
Pipeline de triage: Fases 1-3 para un batch de tickets.

Uso:
    python pipeline.py --horas 24
    python pipeline.py --fase0 --days 30
    python pipeline.py --horas 24 --dry-run
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from zendesk_users_cache import ZendeskUsersCache
from fase0_zendesk_users import populate_cache_from_ids
from storage import Storage
from fase1_filtrar import Fase1Filtrador
from fase2_preclasificar import Fase2Preclasificador
from fase3_clusterizar import Fase3Clusterizador

load_dotenv()


def run_pipeline(horas: int = 24, dry_run: bool = False):
    storage = Storage()
    conceptos = storage.get_conceptos()
    if not conceptos:
        print("❌ No hay config/conceptos.json. Ejecuta primero: python pipeline.py --fase0 --days 30")
        return

    print(f"📥 Descargando tickets de las últimas {horas}h...")
    users_cache = ZendeskUsersCache(Path(storage.data_dir) / "zendesk_users.json")
    client = ZendeskClient(users_cache=users_cache)
    tickets_raw = client.get_tickets_since(since_hours=horas)

    # Fase 0.5: poblar cache de usuarios para los requester_id de este batch.
    requester_ids = [t.get("requester_id") for t in tickets_raw if t.get("requester_id")]
    stats_users = populate_cache_from_ids(client, users_cache, requester_ids)
    print(f"   Fase 0.5: users {stats_users}")
    # Aplicar el cache a los tickets ya normalizados (inyecta requester_email).
    client.apply_users_cache(tickets_raw)

    ya_procesados = {t["zendesk_id"] for t in storage.get_tickets()}
    tickets = [t for t in tickets_raw if t["zendesk_id"] not in ya_procesados]
    print(f"   → {len(tickets)} tickets nuevos (de {len(tickets_raw)} descargados)")

    filtrador = Fase1Filtrador()
    preclasificador = Fase2Preclasificador()
    clusterizador = Fase3Clusterizador()

    stats = {
        "total": len(tickets),
        "tecnicos": 0,
        "descartados": 0,
        "ancla_directa": 0,
        "llm": 0,
        "clusters_nuevos": 0,
    }
    clusters_antes = len(storage.get_clusters())

    for ticket in tickets:
        try:
            f1 = filtrador.clasificar(ticket)
            ticket["fase1_resultado"] = f1["resultado"]
            ticket["fase1_confianza"] = f1["confianza"]
            ticket["fase1_modelo"] = f1["metodo"]

            if f1["resultado"] == "DESCARTADO":
                stats["descartados"] += 1
                ticket["procesado_at"] = datetime.now(timezone.utc).isoformat()
                if not dry_run:
                    storage.save_ticket(ticket)
                continue

            stats["tecnicos"] += 1

            f2 = preclasificador.preclasificar(ticket)
            ticket["fase2_anclas"] = f2["anclas"]
            ticket["emails_mencionados"] = f2.get("emails_mencionados", [])
            ticket["emails_asociados"] = f2.get("emails_asociados", [])

            if f2["cluster_candidato"]:
                stats["ancla_directa"] += 1
                ticket["fase3_cluster_id"] = f2["cluster_candidato"]
                ticket["fase3_severidad"] = f2["severidad_estimada"]
                ticket["fase3_jira_candidatos"] = []
                ticket["procesado_at"] = datetime.now(timezone.utc).isoformat()
            else:
                stats["llm"] += 1
                f3 = clusterizador.clusterizar(ticket)
                ticket["fase3_cluster_id"] = f3["cluster_id"]
                ticket["fase3_resumen_llm"] = f3["resumen_llm"]
                ticket["fase3_severidad"] = f3["severidad"]
                ticket["fase3_jira_candidatos"] = f3["jira_candidatos"]
                ticket["procesado_at"] = datetime.now(timezone.utc).isoformat()

            if not dry_run:
                storage.save_ticket(ticket)
        except Exception as e:
            print(f"   ⚠️  Error procesando ticket {ticket.get('zendesk_id')}: {e}")
            continue

    clusters_despues = len(storage.get_clusters())
    stats["clusters_nuevos"] = clusters_despues - clusters_antes

    # Fase 3.5: refine batch de clusters heterogéneos
    if not dry_run:
        try:
            from fase35_refine import run_refine
            from jira_matcher import JiraMatcher
            from openai import OpenAI
            import os
            oai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            matcher = JiraMatcher(openai_client=oai, model=os.environ.get("OPENAI_MODEL", "gpt-4o"))
            refine_stats = run_refine(
                openai_client=oai, matcher=matcher, storage=storage,
                min_tickets=int(os.environ.get("REFINE_MIN_TICKETS", 15)),
                het_min=float(os.environ.get("REFINE_HETEROGENEITY_MIN", 0.5)),
            )
            print(f"📦 Fase 3.5 refine: {refine_stats}")
        except Exception as e:
            print(f"   ⚠️  Fase 3.5 skip (error: {e})")

    print(f"\n✅ Pipeline completado:")
    print(f"   Total tickets:     {stats['total']}")
    print(f"   Técnicos:          {stats['tecnicos']}")
    print(f"   Descartados:       {stats['descartados']}")
    print(f"   Ancla directa:     {stats['ancla_directa']} (sin coste API)")
    print(f"   LLM (GPT-4o):      {stats['llm']}")
    print(f"   Clusters nuevos:   {stats['clusters_nuevos']}")
    if dry_run:
        print("   ⚠️  DRY-RUN: nada guardado")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas", type=int, default=24)
    parser.add_argument("--fase0", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.fase0:
        import subprocess
        import sys
        subprocess.run([sys.executable, "fase0_explorar.py", "--days", str(args.days)], check=True)
    else:
        run_pipeline(horas=args.horas, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
