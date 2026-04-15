import os
import time
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv()


class ZendeskClient:
    def __init__(self, subdomain=None, email=None, token=None):
        self.subdomain = subdomain or os.environ["ZENDESK_SUBDOMAIN"]
        self.email = email or os.environ["ZENDESK_EMAIL"]
        self.token = token or os.environ["ZENDESK_API_TOKEN"]
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        self.auth = (f"{self.email}/token", self.token)

    def get_tickets(self, days_back: int = 30) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        tickets = []
        url = f"{self.base_url}/tickets.json?created_after={since_str}&sort_by=created_at&sort_order=asc"
        while url:
            resp = requests.get(url, auth=self.auth)
            resp.raise_for_status()
            data = resp.json()
            tickets.extend(data.get("tickets", []))
            url = data.get("next_page")
            if url:
                time.sleep(0.1)  # respect rate limit
        return [self._normalize(t) for t in tickets]

    def get_tickets_since(self, since_hours: int = 24) -> list[dict]:
        return self.get_tickets(days_back=since_hours / 24)

    def get_ticket(self, ticket_id: int) -> dict:
        resp = requests.get(f"{self.base_url}/tickets/{ticket_id}.json", auth=self.auth)
        resp.raise_for_status()
        return self._normalize(resp.json()["ticket"])

    def _normalize(self, t: dict) -> dict:
        return {
            "zendesk_id": t["id"],
            "id": t["id"],  # keep raw id so tests asserting ticket["id"] still pass
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("description") or "")[:1000],
            "status": t.get("status"),
            "channel": t.get("via", {}).get("channel", "unknown"),
            "tags": t.get("tags", []),
        }
