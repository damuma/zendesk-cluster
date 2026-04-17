import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Storage:
    def __init__(self, backend=None, data_dir=None):
        self.backend = backend or os.environ.get("STORAGE_BACKEND", "json")
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR", "./data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── JSON helpers ──────────────────────────────────────────
    # Files that hold a single dict (not a list)
    _DICT_FILES = {"conceptos.json"}

    def _read(self, filename: str) -> list | dict:
        path = self.data_dir / filename
        if not path.exists():
            return {} if filename in self._DICT_FILES else []
        with open(path) as f:
            return json.load(f)

    def _write(self, filename: str, data: list | dict) -> None:
        with open(self.data_dir / filename, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # ── Tickets ───────────────────────────────────────────────
    def get_tickets(self, filters: dict = None) -> list[dict]:
        tickets = self._read("tickets.json")
        if filters:
            for key, val in filters.items():
                tickets = [t for t in tickets if t.get(key) == val]
        return tickets

    def save_ticket(self, ticket: dict) -> None:
        tickets = self._read("tickets.json")
        existing_ids = {t["zendesk_id"] for t in tickets}
        if ticket["zendesk_id"] in existing_ids:
            tickets = [t if t["zendesk_id"] != ticket["zendesk_id"] else ticket for t in tickets]
        else:
            tickets.append(ticket)
        self._write("tickets.json", tickets)

    # ── Clusters ──────────────────────────────────────────────
    def get_clusters(self, estado: str = None) -> list[dict]:
        clusters = self._read("clusters.json")
        if estado:
            clusters = [c for c in clusters if c.get("estado") == estado]
        return clusters

    def save_cluster(self, cluster: dict) -> None:
        clusters = self._read("clusters.json")
        existing_ids = {c["cluster_id"] for c in clusters}
        if cluster["cluster_id"] in existing_ids:
            clusters = [c if c["cluster_id"] != cluster["cluster_id"] else cluster for c in clusters]
        else:
            clusters.append(cluster)
        self._write("clusters.json", clusters)

    def get_cluster_tickets(self, cluster_id: str) -> list[dict]:
        return self.get_tickets(filters={"fase3_cluster_id": cluster_id})

    # ── Conceptos ─────────────────────────────────────────────
    def get_conceptos(self) -> dict:
        return self._read("conceptos.json")

    def save_conceptos(self, conceptos: dict) -> None:
        self._write("conceptos.json", conceptos)

    # ── Jira tickets ──────────────────────────────────────────
    def _raw_jira(self) -> list:
        data = self._read("jira_tickets.json")
        return data if isinstance(data, list) else []

    def get_jira_tickets(self) -> list[dict]:
        return [t for t in self._raw_jira() if not t.get("_meta")]

    def get_jira_metadata(self) -> dict:
        for entry in self._raw_jira():
            if entry.get("_meta"):
                return entry
        return {}

    def save_jira_tickets(self, tickets: list[dict], meta: dict) -> None:
        meta = {**meta, "_meta": True}
        self._write("jira_tickets.json", [meta, *tickets])

    def upsert_jira_tickets(self, nuevos: list[dict], done_ids: set[str], meta: dict) -> None:
        existentes = {t["jira_id"]: t for t in self.get_jira_tickets()}
        for t in nuevos:
            existentes[t["jira_id"]] = t
        for jid in done_ids:
            existentes.pop(jid, None)
        ordered = sorted(existentes.values(), key=lambda t: t.get("updated") or "", reverse=True)
        self.save_jira_tickets(ordered, meta)
