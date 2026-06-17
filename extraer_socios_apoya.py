#!/usr/bin/env python3
"""Extrae remitentes que escribieron a socios@/apoya@ en una ventana temporal
y los separa según si volvieron a contactar después.

Por cada buzón (socios, apoya) genera dos CSV:

  *_mantener.csv   → personas que escribieron a ese buzón DENTRO de la ventana
                     [start .. window-end] y que NO volvieron a escribir a
                     socios NI a apoya a partir del día siguiente al fin de
                     ventana.
  *_descartar.csv  → personas que escribieron a ese buzón en la ventana PERO
                     volvieron a escribir a socios o apoya después (se incluyen
                     sus fechas de interacción posteriores como justificación).

Además escribe `sin_atribuir.csv` con los tickets del periodo cuyo remitente no
se pudo resolver a un email (formularios web, usuarios borrados, etc.).

Las fechas se interpretan en horario Europe/Madrid (lo que ve un humano), no UTC.

Uso:
    python extraer_socios_apoya.py
    python extraer_socios_apoya.py --start 2026-03-04 --window-end 2026-04-08
    python extraer_socios_apoya.py --output-dir data/socios_apoya
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from zendesk_users_cache import ZendeskUsersCache
from fase0_zendesk_users import populate_cache_from_ids

load_dotenv()

MADRID = ZoneInfo("Europe/Madrid")
TRACKED = {"socios@eldiario.es", "apoya@eldiario.es"}


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def local_dt(created_at: str) -> dt.datetime:
    """Zendesk created_at (UTC ISO con Z) → datetime en Europe/Madrid."""
    d = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return d.astimezone(MADRID)


def fmt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


def short_label(addr: str) -> str:
    return addr.split("@", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=parse_date, default=parse_date("2026-03-04"),
                        help="Inicio de ventana (incluido). YYYY-MM-DD. Default 2026-03-04")
    parser.add_argument("--window-end", type=parse_date, default=parse_date("2026-04-08"),
                        help="Fin de ventana (incluido). YYYY-MM-DD. Default 2026-04-08")
    parser.add_argument("--output-dir", default="data/socios_apoya",
                        help="Carpeta de salida para los CSV")
    parser.add_argument("--users-cache", default="data/zendesk_users.json",
                        help="Ruta del cache de usuarios Zendesk")
    parser.add_argument("--exclude-domains", nargs="*", default=["eldiario.es"],
                        help="Dominios de remitente a excluir (internos). Vacío para no excluir ninguno. Default: eldiario.es")
    args = parser.parse_args()

    exclude_domains = {d.lower().lstrip("@") for d in args.exclude_domains}

    later_start = args.window_end + dt.timedelta(days=1)
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    # since = inicio de ventana a las 00:00 hora de Madrid, convertido a UTC
    since = dt.datetime.combine(args.start, dt.time(0, 0), tzinfo=MADRID).astimezone(dt.timezone.utc)

    print(f"📥 Descargando tickets creados desde {args.start} (Madrid) hasta ahora…")
    print("   (incluye tickets cerrados — imprescindible para histórico)")
    cache = ZendeskUsersCache(args.users_cache)
    client = ZendeskClient(users_cache=cache)
    tickets = client.get_tickets_created_since(since, exclude_statuses=())
    print(f"   → {len(tickets)} tickets descargados")

    # Resolver emails de remitentes que falten en el cache
    req_ids = [t["requester_id"] for t in tickets if t.get("requester_id") is not None]
    stats = populate_cache_from_ids(client, cache, req_ids)
    print(f"   → usuarios: {stats['fetched']} descargados, {stats['already_cached']} ya en cache")
    client.users_cache = cache
    client.apply_users_cache(tickets)

    # --- Agregación ---
    # window[addr][email] = lista de datetimes (Madrid) dentro de la ventana
    window: dict[str, dict[str, list[dt.datetime]]] = {a: defaultdict(list) for a in TRACKED}
    # later[email] = lista de (addr, datetime) a un buzón trackeado a partir de later_start
    later: dict[str, list[tuple[str, dt.datetime]]] = defaultdict(list)
    sin_atribuir: list[dict] = []

    for t in tickets:
        recipient = (t.get("recipient") or "").lower()
        if recipient not in TRACKED:
            continue
        if not t.get("created_at"):
            continue
        ldt = local_dt(t["created_at"])
        ldate = ldt.date()
        email = (t.get("requester_email") or "").lower().strip()
        if email and email.rsplit("@", 1)[-1] in exclude_domains:
            continue

        if args.start <= ldate <= args.window_end:
            if not email:
                sin_atribuir.append({
                    "zendesk_id": t.get("zendesk_id"),
                    "recipient": recipient,
                    "fecha": fmt(ldt),
                    "requester_id": t.get("requester_id"),
                })
                continue
            window[recipient][email].append(ldt)
        elif ldate >= later_start and email:
            later[email].append((recipient, ldt))

    # --- Escritura de CSV por buzón ---
    summary = []
    for addr in sorted(TRACKED):
        label = short_label(addr)
        contacts = window[addr]

        kept, discarded = {}, {}
        for email, fechas in contacts.items():
            (discarded if email in later else kept)[email] = sorted(fechas)

        max_kept = max((len(f) for f in kept.values()), default=0)
        max_disc = max((len(f) for f in discarded.values()), default=0)

        # MANTENER
        keep_path = outdir / f"{label}_mantener.csv"
        with open(keep_path, "w", newline="", encoding="utf-8-sig") as f:
            cols = ["email", "n_contactos"] + [f"contacto_{i}" for i in range(1, max(max_kept, 1) + 1)]
            w = csv.writer(f)
            w.writerow(cols)
            for email in sorted(kept):
                fechas = kept[email]
                row = [email, len(fechas)] + [fmt(d) for d in fechas]
                row += [""] * (len(cols) - len(row))
                w.writerow(row)

        # DESCARTAR
        disc_path = outdir / f"{label}_descartar.csv"
        with open(disc_path, "w", newline="", encoding="utf-8-sig") as f:
            cols = (["email", "n_contactos_ventana"]
                    + [f"contacto_{i}" for i in range(1, max(max_disc, 1) + 1)]
                    + ["interacciones_posteriores"])
            w = csv.writer(f)
            w.writerow(cols)
            for email in sorted(discarded):
                fechas = discarded[email]
                posteriores = "; ".join(f"{short_label(a)} @ {fmt(d)}"
                                        for a, d in sorted(later[email], key=lambda x: x[1]))
                row = [email, len(fechas)] + [fmt(d) for d in fechas]
                row += [""] * (2 + max(max_disc, 1) - len(row))
                row.append(posteriores)
                w.writerow(row)

        summary.append((label, len(kept), len(discarded)))
        print(f"\n📋 {addr}")
        print(f"   mantener:  {len(kept):>5}  → {keep_path}")
        print(f"   descartar: {len(discarded):>5}  → {disc_path}")

    # sin_atribuir
    if sin_atribuir:
        sa_path = outdir / "sin_atribuir.csv"
        with open(sa_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["zendesk_id", "recipient", "fecha", "requester_id"])
            w.writeheader()
            w.writerows(sorted(sin_atribuir, key=lambda r: r["fecha"]))
        print(f"\n⚠️  {len(sin_atribuir)} tickets en ventana sin email resoluble → {sa_path}")

    print("\n✅ Listo. Resumen (ventana {} … {}, vuelta a contactar desde {}):".format(
        args.start, args.window_end, later_start))
    for label, k, d in summary:
        print(f"   {label:>7}: {k} a mantener, {d} descartados")


if __name__ == "__main__":
    main()
