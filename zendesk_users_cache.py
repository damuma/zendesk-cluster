"""Local cache of Zendesk users (id → email/name/role). Persisted to JSON."""
import json
from pathlib import Path


class ZendeskUsersCache:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            text = self.path.read_text() or ""
            self._data = json.loads(text) if text.strip() else {}

    def get_email(self, user_id: int) -> str | None:
        rec = self._data.get(str(user_id))
        if not rec:
            return None
        email = rec.get("email")
        return email or None

    def missing_ids(self, ids: list[int]) -> list[int]:
        return [i for i in ids if str(i) not in self._data]

    def upsert(self, users: list[dict]) -> None:
        for u in users:
            uid = u.get("id")
            if uid is None:
                continue
            self._data[str(uid)] = {
                "email": u.get("email"),
                "name": u.get("name"),
                "role": u.get("role"),
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)
