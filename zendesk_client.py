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
        return self._fetch_since(since)

    def get_tickets_since(self, since_hours: int = 24) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        return self._fetch_since(since)

    def _fetch_since(self, since: datetime) -> list[dict]:
        """Fetch tickets created/updated since a datetime using Zendesk incremental export API."""
        start_time = int(since.timestamp())
        url = f"{self.base_url}/incremental/tickets/cursor.json?start_time={start_time}"
        tickets = []
        while url:
            resp = self._get_with_retry(url)
            data = resp.json()
            tickets.extend(data.get("tickets", []))
            if data.get("end_of_stream", True):
                break
            url = data.get("after_url")
        return [self._normalize(t) for t in tickets]

    def get_ticket(self, ticket_id: int) -> dict:
        resp = self._get_with_retry(f"{self.base_url}/tickets/{ticket_id}.json")
        return self._normalize(resp.json()["ticket"])

    def _get_with_retry(self, url: str, max_retries: int = 3) -> requests.Response:
        for attempt in range(max_retries):
            resp = requests.get(url, auth=self.auth)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp  # unreachable, satisfies type checker

    def _normalize(self, t: dict) -> dict:
        return {
            "zendesk_id": t.get("id"),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("description") or "")[:1000],
            "status": t.get("status"),
            "channel": t.get("via", {}).get("channel", "unknown"),
            "tags": t.get("tags", []),
        }
