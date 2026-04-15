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

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from storage import Storage
from fase1_filtrar import Fase1Filtrador
from fase2_preclasificar import Fase2Preclasificador
from fase3_clusterizar import Fase3Clusterizador

load_dotenv()


def run_pipeline(horas: int = 24, dry_run: bool = False):
    storage = Storage()
    conceptos = storage.get_conceptos()
    if not conceptos:
        print("❌ No hay conceptos.json. Ejecuta primero: python pipeline.py --fase0 --days 30")
        return

    print(f"📥 Descargando tickets de las últimas {horas}h...")
    client = ZendeskClient()
    tickets_raw = client.get_tickets_since(since_hours=horas)

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
        f1 = filtrador.clasificar(ticket)
        ticket["fase1_resultado"] = f1["resultado"]
        ticket["fase1_confianza"] = f1["confianza"]
        ticket["fase1_modelo"] = f1["metodo"]

        if f1["resultado"] == "DESCARTADO":
            stats["descartados"] += 1
            if not dry_run:
                storage.save_ticket(ticket)
            continue

        stats["tecnicos"] += 1

        f2 = preclasificador.preclasificar(ticket)
        ticket["fase2_anclas"] = f2["anclas"]

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

    clusters_despues = len(storage.get_clusters())
    stats["clusters_nuevos"] = clusters_despues - clusters_antes

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
