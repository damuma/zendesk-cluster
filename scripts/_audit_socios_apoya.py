#!/usr/bin/env python3
"""Auditoría independiente de la lógica de extraer_socios_apoya.py.

Re-descarga los tickets, los guarda crudos para no volver a bajarlos y
recalcula TODO desde cero, comprobando:
  A. Duplicados del export incremental (mismo zendesk_id repetido).
  B. Distribución de recipient.
  C. Recuento mantener/descartar (con y sin dedup) vs los CSV existentes.
  D. Exposición a la continuación en el mismo hilo: cuántos tickets de
     'mantener' tienen updated_at posterior al fin de ventana.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from zendesk_users_cache import ZendeskUsersCache
from fase0_zendesk_users import populate_cache_from_ids

load_dotenv()
MADRID = ZoneInfo("Europe/Madrid")
TRACKED = {"socios@eldiario.es", "apoya@eldiario.es"}
START = dt.date(2026, 3, 4)
WIN_END = dt.date(2026, 4, 8)
LATER = WIN_END + dt.timedelta(days=1)
RAW = Path("data/socios_apoya/_raw_tickets.json")


def ldate(iso: str) -> dt.date:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MADRID).date()


def main() -> None:
    cache = ZendeskUsersCache("data/zendesk_users.json")
    client = ZendeskClient(users_cache=cache)

    if RAW.exists():
        print(f"Usando crudo en cache: {RAW}")
        tickets = json.loads(RAW.read_text())
    else:
        since = dt.datetime.combine(START, dt.time(0, 0), tzinfo=MADRID).astimezone(dt.timezone.utc)
        print("Descargando…")
        tickets = client.get_tickets_created_since(since, exclude_statuses=())
        req_ids = [t["requester_id"] for t in tickets if t.get("requester_id") is not None]
        populate_cache_from_ids(client, cache, req_ids)
        client.users_cache = cache
        client.apply_users_cache(tickets)
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(json.dumps(tickets, ensure_ascii=False))
    print(f"Tickets descargados (con posibles duplicados): {len(tickets)}")

    # ---------- A. Duplicados ----------
    ids = [t["zendesk_id"] for t in tickets]
    id_counts = Counter(ids)
    dups = {i: c for i, c in id_counts.items() if c > 1}
    print(f"\n[A] IDs únicos: {len(id_counts)} | IDs duplicados en la descarga: {len(dups)}")
    if dups:
        ej = list(dups.items())[:5]
        print(f"    Ejemplos (id: veces): {ej}")

    # dedup por id (quedándonos con la primera aparición)
    seen = {}
    for t in tickets:
        seen.setdefault(t["zendesk_id"], t)
    uniq = list(seen.values())
    print(f"    Tras dedup: {len(uniq)} tickets únicos")

    # ---------- B. Distribución recipient (sobre únicos) ----------
    rec = Counter((t.get("recipient") or "∅NULL").lower() for t in uniq)
    print("\n[B] recipient (top):")
    for k, v in rec.most_common(8):
        print(f"    {k:40} {v}")

    # ---------- C. Recuento independiente ----------
    def compute(ticket_list):
        window = {a: defaultdict(list) for a in TRACKED}
        later = defaultdict(list)
        sin_email_window = Counter()
        for t in ticket_list:
            r = (t.get("recipient") or "").lower()
            if r not in TRACKED or not t.get("created_at"):
                continue
            d = ldate(t["created_at"])
            email = (t.get("requester_email") or "").lower().strip()
            if email and email.rsplit("@", 1)[-1] == "eldiario.es":
                continue
            if START <= d <= WIN_END:
                if not email:
                    sin_email_window[r] += 1
                    continue
                window[r][email].append(d)
            elif d >= LATER and email:
                later[email].append((r, d))
        out = {}
        for a in TRACKED:
            kept = [e for e in window[a] if e not in later]
            disc = [e for e in window[a] if e in later]
            out[a] = (len(kept), len(disc))
        return out, dict(sin_email_window)

    raw_counts, _ = compute(tickets)       # SIN dedup (como el script actual)
    uniq_counts, sin_email = compute(uniq)  # CON dedup
    print("\n[C] mantener/descartar  (SIN dedup → CON dedup):")
    for a in sorted(TRACKED):
        rk, rd = raw_counts[a]
        uk, ud = uniq_counts[a]
        flag = "  ⚠️ DIFERENCIA" if (rk, rd) != (uk, ud) else ""
        print(f"    {a:22} mantener {rk}→{uk} | descartar {rd}→{ud}{flag}")
    print(f"    sin email en ventana (no @eldiario): {sin_email}")

    # comparación con CSV existentes
    import csv as _csv
    print("\n    CSV en disco (filas):")
    for fn in ["socios_mantener", "socios_descartar", "apoya_mantener", "apoya_descartar"]:
        p = Path(f"data/socios_apoya/{fn}.csv")
        if p.exists():
            with open(p, encoding="utf-8-sig") as f:
                n = sum(1 for _ in f) - 1
            print(f"      {fn}: {n}")

    # ---------- D. Exposición continuación de hilo ----------
    # tickets de 'mantener' (creados en ventana, socios/apoya, email no interno,
    # remitente NO en later) cuyo updated_at es posterior a WIN_END
    later_emails = set()
    for t in uniq:
        r = (t.get("recipient") or "").lower()
        if r not in TRACKED or not t.get("created_at"):
            continue
        d = ldate(t["created_at"])
        email = (t.get("requester_email") or "").lower().strip()
        if email and email.rsplit("@", 1)[-1] == "eldiario.es":
            continue
        if d >= LATER and email:
            later_emails.add(email)

    cont = 0
    cont_emails = set()
    total_mantener_tickets = 0
    for t in uniq:
        r = (t.get("recipient") or "").lower()
        if r not in TRACKED or not t.get("created_at"):
            continue
        d = ldate(t["created_at"])
        email = (t.get("requester_email") or "").lower().strip()
        if not email or email.rsplit("@", 1)[-1] == "eldiario.es":
            continue
        if not (START <= d <= WIN_END):
            continue
        if email in later_emails:
            continue  # ya descartado
        total_mantener_tickets += 1
        up = t.get("updated_at")
        if up and ldate(up) >= LATER:
            cont += 1
            cont_emails.add(email)
    print(f"\n[D] Tickets de 'mantener' creados en ventana: {total_mantener_tickets}")
    print(f"    …con updated_at >= {LATER} (posible actividad posterior en el hilo): "
          f"{cont} tickets, {len(cont_emails)} remitentes distintos")
    print("    (updated_at incluye acciones de agente/sistema, así que es una COTA SUPERIOR)")


if __name__ == "__main__":
    main()
