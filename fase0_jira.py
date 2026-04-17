#!/usr/bin/env python3
"""
Descarga de tickets de Jira (proyecto TEC) a JSON local.

Uso:
    python fase0_jira.py               # incremental (default)
    python fase0_jira.py --full        # re-descarga completa
    python fase0_jira.py --days 60     # ventana (default 60)
"""
import argparse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from jira_client import JiraClient
from storage import Storage

load_dotenv()


def build_jql(mode: str, project: str, days: int, since: str | None) -> str:
    if mode == "full":
        return (
            f"project = {project} AND statusCategory != Done "
            f"AND updated >= -{days}d ORDER BY updated DESC"
        )
    return (
        f'project = {project} AND updated >= "{since}" ORDER BY updated DESC'
    )


def run(storage: Storage, client: JiraClient, mode: str, days: int) -> dict:
    now = datetime.now(timezone.utc)

    prev_meta = storage.get_jira_metadata()
    if mode == "incremental" and not prev_meta:
        print("  (sin _meta previa, cambiando a modo FULL)")
        mode = "full"

    if mode == "full":
        since = None
        fecha_inicio = (now - timedelta(days=days)).isoformat()
    else:
        fecha_fin_prev = prev_meta.get("fecha_fin") or now.isoformat()
        since_dt = datetime.fromisoformat(fecha_fin_prev.replace("Z", "+00:00")) - timedelta(minutes=10)
        since = since_dt.strftime("%Y-%m-%d %H:%M")
        fecha_inicio = prev_meta.get("fecha_inicio") or (now - timedelta(days=days)).isoformat()

    jql = build_jql(mode, client.project, days, since)
    print(f"  JQL: {jql}")

    nuevos: list[dict] = []
    done_ids: set[str] = set()
    descargados = 0
    for ticket in client.fetch_tickets_jql(jql):
        descargados += 1
        if ticket.get("status_category") == "done":
            done_ids.add(ticket["jira_id"])
        else:
            nuevos.append(ticket)

    base_jql = (
        f"project = {client.project} AND statusCategory != Done "
        f"AND updated >= -{days}d"
    )
    try:
        total = client.approximate_count(base_jql)
    except Exception:
        total = None

    meta = {
        "project": client.project,
        "fecha_inicio": fecha_inicio,
        "fecha_fin": now.isoformat(),
        "last_sync": now.isoformat(),
        "total_tickets": total if total is not None else len(storage.get_jira_tickets()) + len(nuevos) - len(done_ids),
        "filtro": f"project = {client.project} AND statusCategory != Done",
    }

    if mode == "full":
        storage.save_jira_tickets(nuevos, meta)
    else:
        storage.upsert_jira_tickets(nuevos, done_ids, meta)

    stats = {
        "mode": mode,
        "descargados": descargados,
        "upsertados": len(nuevos),
        "borrados_por_done": len(done_ids),
        "total_en_json": len(storage.get_jira_tickets()),
    }
    print(
        f"  ✅ mode={mode} descargados={stats['descargados']} "
        f"upsertados={stats['upsertados']} borrados={stats['borrados_por_done']} "
        f"total={stats['total_en_json']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    mode = "full" if args.full else "incremental"
    print(f"📥 Fase 0 Jira — modo={mode}, días={args.days}")
    run(Storage(), JiraClient(), mode=mode, days=args.days)


if __name__ == "__main__":
    main()
