import json, datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
TRACKED = {"socios@eldiario.es", "apoya@eldiario.es"}
START = dt.date(2026, 3, 4); WIN_END = dt.date(2026, 4, 8); LATER = WIN_END + dt.timedelta(days=1)


def ldt(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MADRID)


t = json.loads(open("data/socios_apoya/_raw_tickets.json").read())

win_dates = Counter(); later_min = None; win_min = None; win_max = None
for x in t:
    r = (x.get("recipient") or "").lower()
    if r not in TRACKED or not x.get("created_at"):
        continue
    e = (x.get("requester_email") or "").lower().strip()
    if e and e.endswith("@eldiario.es"):
        continue
    d = ldt(x["created_at"]).date()
    if START <= d <= WIN_END:
        win_dates[d] += 1
        win_min = d if win_min is None or d < win_min else win_min
        win_max = d if win_max is None or d > win_max else win_max
    elif d >= LATER and e:
        later_min = d if later_min is None or d < later_min else later_min

print("Ventana: fecha mínima =", win_min, "| fecha máxima =", win_max)
print("  (deben ser exactamente 2026-03-04 y 2026-04-08)")
print("Tickets en los bordes: 2026-03-04 =", win_dates.get(dt.date(2026,3,4)),
      "| 2026-04-08 =", win_dates.get(dt.date(2026,4,8)))
print("Later: fecha mínima =", later_min, "(debe ser >= 2026-04-09)")

# ¿hay algún ticket clasificado en ventana fuera de rango? (no debería)
fuera = [d for d in win_dates if not (START <= d <= WIN_END)]
print("Fechas de ventana fuera de rango:", fuera)

# Spot-check caso de descarte conocido
target = "a170310@telefonica.net"
print(f"\nSpot-check {target}:")
for x in t:
    r = (x.get("recipient") or "").lower()
    if r not in TRACKED:
        continue
    e = (x.get("requester_email") or "").lower().strip()
    if e != target:
        continue
    d = ldt(x["created_at"])
    tag = "VENTANA" if START <= d.date() <= WIN_END else ("LATER" if d.date() >= LATER else "previo")
    print(f"  {x['zendesk_id']} {r:18} {d:%Y-%m-%d %H:%M} [{tag}]")
