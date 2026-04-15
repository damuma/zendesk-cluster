import os
import re
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
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

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", headers=self.headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError:
            return {}

    def buscar_tickets_crm(self, query_text: str, max_results: int = 5) -> list[dict]:
        """Busca tickets en Jira proyecto TEC con label CRM que coincidan con el texto."""
        safe_query = re.sub(r"[^\w\s]", " ", query_text)[:100]
        jql = f'project = {self.project} AND labels = "CRM" AND text ~ "{safe_query}" ORDER BY created DESC'
        encoded_jql = urllib.parse.quote(jql)
        data = self._get(f"/search?jql={encoded_jql}&maxResults={max_results}&fields=summary,status,priority,labels")
        issues = data.get("issues", [])
        return [
            {
                "jira_id": i["key"],
                "summary": i["fields"].get("summary", ""),
                "status": i["fields"].get("status", {}).get("name", ""),
                "priority": i["fields"].get("priority", {}).get("name", ""),
                "url": f"https://{self.host}/browse/{i['key']}",
            }
            for i in issues
        ]
