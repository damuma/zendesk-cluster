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
    def _read(self, filename: str) -> list | dict:
        path = self.data_dir / filename
        if not path.exists():
            return [] if filename.endswith("s.json") else {}
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
