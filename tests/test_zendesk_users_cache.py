import json
from pathlib import Path

from zendesk_users_cache import ZendeskUsersCache


def test_empty_cache_when_file_missing(tmp_path: Path):
    cache = ZendeskUsersCache(tmp_path / "nope.json")
    assert cache.get_email(42) is None
    assert cache.missing_ids([1, 2, 3]) == [1, 2, 3]


def test_load_and_lookup(tmp_path: Path):
    p = tmp_path / "users.json"
    p.write_text(json.dumps({
        "42": {"email": "a@x.com", "name": "A", "role": "end-user"},
        "99": {"email": None, "name": "Borrado", "role": "end-user"},
    }))
    cache = ZendeskUsersCache(p)
    assert cache.get_email(42) == "a@x.com"
    assert cache.get_email(99) is None
    assert cache.get_email(7) is None
    assert cache.missing_ids([42, 99, 7]) == [7]


def test_upsert_and_save(tmp_path: Path):
    p = tmp_path / "users.json"
    cache = ZendeskUsersCache(p)
    cache.upsert([
        {"id": 10, "email": "x@y.com", "name": "X", "role": "end-user"},
        {"id": 20, "email": None, "name": "Deleted", "role": "end-user"},
    ])
    cache.save()
    data = json.loads(p.read_text())
    assert data["10"]["email"] == "x@y.com"
    assert data["20"]["email"] is None


def test_upsert_overwrites_existing(tmp_path: Path):
    p = tmp_path / "users.json"
    p.write_text(json.dumps({"10": {"email": "old@x.com", "name": "Old", "role": "end-user"}}))
    cache = ZendeskUsersCache(p)
    cache.upsert([{"id": 10, "email": "new@x.com", "name": "New", "role": "agent"}])
    assert cache.get_email(10) == "new@x.com"
