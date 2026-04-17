import os
import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from typing import Iterator
from dotenv import load_dotenv

load_dotenv()


class JiraClient:
    def __init__(self, host=None, email=None, token=None, project=None):
        self.host = host or os.environ.get("JIRA_HOST", "eldiario.atlassian.net")
        self.email = email or os.environ["JIRA_EMAIL"]
        self.token = token or os.environ["JIRA_TOKEN"]
        self.project = project or os.environ.get("JIRA_PROJECT", "TEC")
        self.base_url = f"https://{self.host}/rest/api/3"
        _tok = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {_tok}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── HTTP helpers ────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict | None = None, max_retries: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(max_retries):
            req = urllib.request.Request(url, headers=self.headers, data=data, method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    retry_after = int(e.headers.get("Retry-After", "60"))
                    time.sleep(retry_after)
                    continue
                raise

    # ── ADF extractor ───────────────────────────────────────
    def adf_to_text(self, adf: dict | None) -> str:
        if not adf or not isinstance(adf, dict):
            return ""
        block_types = {"paragraph", "heading", "listItem", "tableRow", "blockquote", "codeBlock"}
        parts: list[str] = []

        def walk(node: dict, into: list[str]) -> None:
            if not isinstance(node, dict):
                return
            ntype = node.get("type")
            if ntype == "text":
                into.append(node.get("text", ""))
                return
            buf: list[str] = [] if ntype in block_types else into
            for child in node.get("content", []) or []:
                walk(child, buf)
            if ntype in block_types and buf is not into:
                piece = "".join(buf).strip()
                if piece:
                    into.append(piece)

        for child in adf.get("content", []) or []:
            walk(child, parts)
        return "\n".join(p for p in parts if p)

    # ── Issue normalizer ────────────────────────────────────
    def normalize_issue(self, issue: dict) -> dict:
        key = issue["key"]
        f = issue.get("fields", {}) or {}
        status = f.get("status") or {}
        priority = f.get("priority") or {}
        issuetype = f.get("issuetype") or {}
        assignee = f.get("assignee") or {}
        return {
            "jira_id": key,
            "url": f"https://{self.host}/browse/{key}",
            "summary": f.get("summary", ""),
            "description_text": self.adf_to_text(f.get("description")),
            "status": status.get("name"),
            "status_category": (status.get("statusCategory") or {}).get("key"),
            "priority": priority.get("name") if priority else None,
            "issuetype": issuetype.get("name"),
            "labels": list(f.get("labels") or []),
            "components": [c.get("name") for c in (f.get("components") or [])],
            "assignee": assignee.get("displayName") if assignee else None,
            "created": f.get("created"),
            "updated": f.get("updated"),
        }

    # ── Search endpoints ────────────────────────────────────
    DEFAULT_FIELDS = "summary,description,status,priority,labels,issuetype,components,assignee,created,updated"

    def fetch_tickets_jql(self, jql: str, fields: str = DEFAULT_FIELDS, max_per_page: int = 100) -> Iterator[dict]:
        """Yields normalized tickets matching JQL, paginated via nextPageToken."""
        next_token: str | None = None
        while True:
            params = {"jql": jql, "maxResults": str(max_per_page), "fields": fields}
            if next_token:
                params["nextPageToken"] = next_token
            qs = urllib.parse.urlencode(params)
            data = self._request("GET", f"/search/jql?{qs}")
            for issue in data.get("issues", []):
                yield self.normalize_issue(issue)
            if data.get("isLast", True):
                break
            next_token = data.get("nextPageToken")
            if not next_token:
                break

    def approximate_count(self, jql: str) -> int:
        data = self._request("POST", "/search/approximate-count", body={"jql": jql})
        return int(data.get("count", 0))
