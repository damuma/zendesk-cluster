#!/usr/bin/env python3
"""Etiqueta un ticket de Zendesk manualmente.

Uso:
    python scripts/tag_ticket.py 538248 error_acceso
    python scripts/tag_ticket.py 538248 error_acceso cluster_xyz

Este script es una utilidad puntual para probar/operar la capacidad de
escritura (`ZendeskClient.add_tags`). El pipeline NO etiqueta en automático.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zendesk_client import ZendeskClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket_id", type=int)
    parser.add_argument("tags", nargs="+")
    args = parser.parse_args()

    client = ZendeskClient()
    before = client.get_ticket(args.ticket_id)
    print(f"Ticket {args.ticket_id} — status={before.get('status')}")
    print(f"  Tags antes:   {before.get('tags')}")

    after = client.add_tags(args.ticket_id, args.tags)
    print(f"  Tags después: {after}")


if __name__ == "__main__":
    main()
