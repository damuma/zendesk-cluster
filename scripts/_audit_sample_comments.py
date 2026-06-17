import json, datetime as dt, random
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from zendesk_client import ZendeskClient
from zendesk_users_cache import ZendeskUsersCache

load_dotenv()
MADRID = ZoneInfo("Europe/Madrid")
TRACKED = {"socios@eldiario.es", "apoya@eldiario.es"}
START = dt.date(2026, 3, 4); WIN_END = dt.date(2026, 4, 8); LATER = WIN_END + dt.timedelta(days=1)


def ld(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MADRID).date()


t = json.loads(open("data/socios_apoya/_raw_tickets.json").read())

later = set()
for x in t:
    r = (x.get("recipient") or "").lower()
    if r not in TRACKED or not x.get("created_at"):
        continue
    e = (x.get("requester_email") or "").lower().strip()
    if e and e.endswith("@eldiario.es"):
        continue
    if ld(x["created_at"]) >= LATER and e:
        later.add(e)

mantener_tickets = []
for x in t:
    r = (x.get("recipient") or "").lower()
    if r not in TRACKED or not x.get("created_at"):
        continue
    e = (x.get("requester_email") or "").lower().strip()
    if not e or e.endswith("@eldiario.es"):
        continue
    if START <= ld(x["created_at"]) <= WIN_END and e not in later:
        mantener_tickets.append((x["zendesk_id"], e))

print("tickets mantener:", len(mantener_tickets),
      "| remitentes distintos:", len({e for _, e in mantener_tickets}))

random.seed(42)
sample = random.sample(mantener_tickets, 200)
client = ZendeskClient(users_cache=ZendeskUsersCache("data/zendesk_users.json"))
con_reply = 0
for tid, email in sample:
    comments = client.get_ticket_comments(tid)
    for c in comments:
        role = (c.get("author") or {}).get("role")
        if role in ("end-user", "enduser") and c.get("created_at") and ld(c["created_at"]) >= LATER:
            con_reply += 1
            break

pct = 100 * con_reply / len(sample)
print(f"\nMuestra={len(sample)} | tickets con respuesta del remitente (end-user) >= {LATER}: "
      f"{con_reply} ({pct:.1f}%)")
print(f"Proyección sobre {len(mantener_tickets)} tickets mantener: ~{round(con_reply/len(sample)*len(mantener_tickets))}")
