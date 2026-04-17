import os
import time
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv()


class ZendeskClient:
    def __init__(self, subdomain=None, email=None, token=None, users_cache=None):
        self.subdomain = subdomain or os.environ["ZENDESK_SUBDOMAIN"]
        self.email = email or os.environ["ZENDESK_EMAIL"]
        self.token = token or os.environ["ZENDESK_API_TOKEN"]
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        self.auth = (f"{self.email}/token", self.token)
        self.users_cache = users_cache

    DEFAULT_EXCLUDED_STATUSES = ("closed",)

    def get_tickets(self, days_back: int = 30, exclude_statuses: tuple[str, ...] = DEFAULT_EXCLUDED_STATUSES) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        return self._fetch_since(since, exclude_statuses=exclude_statuses)

    def get_tickets_since(self, since_hours: int = 24, exclude_statuses: tuple[str, ...] = DEFAULT_EXCLUDED_STATUSES) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        return self._fetch_since(since, exclude_statuses=exclude_statuses)

    def _fetch_since(self, since: datetime, exclude_statuses: tuple[str, ...] = DEFAULT_EXCLUDED_STATUSES) -> list[dict]:
        """Fetch tickets created/updated since a datetime using Zendesk incremental export API.

        `exclude_statuses` is applied client-side after normalization. By default
        archived/closed tickets are dropped so they don't re-enter the pipeline.
        """
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
        normalized = [self._normalize(t) for t in tickets]
        if exclude_statuses:
            excluded = set(exclude_statuses)
            normalized = [t for t in normalized if t.get("status") not in excluded]
        return normalized

    def get_ticket(self, ticket_id: int) -> dict:
        resp = self._get_with_retry(f"{self.base_url}/tickets/{ticket_id}.json")
        return self._normalize(resp.json()["ticket"])

    def get_ticket_comments(self, ticket_id: int) -> list[dict]:
        """Return the full conversation for a ticket with author info resolved.

        Uses `?include=users` side-loading so we don't hit /users/{id}.json
        once per distinct author. Each returned entry is:
            {created_at, public, channel, body, html_body,
             author: {id, name, email, role}}
        """
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json?include=users"
        comments: list[dict] = []
        users_by_id: dict[int, dict] = {}
        while url:
            resp = self._get_with_retry(url)
            data = resp.json()
            comments.extend(data.get("comments", []) or [])
            for u in data.get("users", []) or []:
                if u.get("id") is not None:
                    users_by_id[u["id"]] = u
            url = data.get("next_page")
        return [self._normalize_comment(c, users_by_id) for c in comments]

    @staticmethod
    def _normalize_comment(c: dict, users_by_id: dict[int, dict]) -> dict:
        author = users_by_id.get(c.get("author_id")) or {}
        return {
            "id": c.get("id"),
            "created_at": c.get("created_at"),
            "public": bool(c.get("public", True)),
            "channel": (c.get("via") or {}).get("channel", "unknown"),
            "body": c.get("body", "") or "",
            "html_body": c.get("html_body", "") or "",
            "author": {
                "id": author.get("id"),
                "name": author.get("name") or "—",
                "email": author.get("email") or "",
                "role": author.get("role") or "unknown",
            },
        }

    def add_tags(self, ticket_id: int, tags: list[str]) -> list[str]:
        """Append tags to a Zendesk ticket (does NOT replace existing tags).

        Returns the full tag list after the update. Intentionally kept separate
        from the ingestion pipeline — call this explicitly when a human or a
        downstream process decides to tag. The pipeline does not auto-tag.
        """
        if not tags:
            return []
        url = f"{self.base_url}/tickets/{ticket_id}/tags.json"
        resp = requests.put(url, auth=self.auth, json={"tags": list(tags)})
        resp.raise_for_status()
        return resp.json().get("tags", [])

    def fetch_users_by_ids(self, user_ids: list[int], batch_size: int = 100) -> list[dict]:
        """Fetch user records via /users/show_many.json?ids=... in batches.

        Returns the raw user dicts from Zendesk (id/email/name/role). Missing
        ids (deleted users) are simply absent from the response.
        """
        if not user_ids:
            return []
        unique = sorted({int(i) for i in user_ids if i is not None})
        out: list[dict] = []
        for i in range(0, len(unique), batch_size):
            batch = unique[i:i + batch_size]
            url = f"{self.base_url}/users/show_many.json?ids={','.join(str(x) for x in batch)}"
            resp = self._get_with_retry(url)
            out.extend(resp.json().get("users", []) or [])
        return out

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
        ticket_id = t.get("id")
        requester_id = t.get("requester_id")
        requester_email = None
        if self.users_cache is not None and requester_id is not None:
            requester_email = self.users_cache.get_email(int(requester_id))
        return {
            "zendesk_id": ticket_id,
            "zendesk_url": f"https://{self.subdomain}.zendesk.com/agent/tickets/{ticket_id}",
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("description") or "")[:1000],
            "status": t.get("status"),
            "priority": t.get("priority"),
            "ticket_type": t.get("type"),
            "channel": t.get("via", {}).get("channel", "unknown"),
            "tags": t.get("tags", []),
            "requester_id": requester_id,
            "requester_email": requester_email,
            "assignee_id": t.get("assignee_id"),
            "group_id": t.get("group_id"),
        }
