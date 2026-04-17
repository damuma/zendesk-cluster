from pathlib import Path
from unittest.mock import MagicMock

from zendesk_users_cache import ZendeskUsersCache
from fase0_zendesk_users import populate_cache_from_ids


def test_populate_cache_fetches_only_missing(tmp_path: Path):
    cache = ZendeskUsersCache(tmp_path / "u.json")
    cache.upsert([{"id": 1, "email": "a@x.com", "name": "A", "role": "end-user"}])

    client = MagicMock()
    client.fetch_users_by_ids.return_value = [
        {"id": 2, "email": "b@x.com", "name": "B", "role": "end-user"},
    ]

    stats = populate_cache_from_ids(client, cache, requester_ids=[1, 2, 3])

    client.fetch_users_by_ids.assert_called_once_with([2, 3])
    assert stats == {"fetched": 2, "already_cached": 1}
    assert cache.get_email(2) == "b@x.com"
    # 3 no vino en la respuesta → marcado como null
    assert cache.get_email(3) is None
    assert 3 not in cache.missing_ids([3])


def test_populate_cache_handles_empty(tmp_path: Path):
    cache = ZendeskUsersCache(tmp_path / "u.json")
    client = MagicMock()
    stats = populate_cache_from_ids(client, cache, requester_ids=[])
    assert stats == {"fetched": 0, "already_cached": 0}
    client.fetch_users_by_ids.assert_not_called()


def test_populate_cache_all_already_cached(tmp_path: Path):
    cache = ZendeskUsersCache(tmp_path / "u.json")
    cache.upsert([{"id": 1, "email": "a@x.com", "name": "A", "role": "end-user"}])
    client = MagicMock()
    stats = populate_cache_from_ids(client, cache, requester_ids=[1])
    assert stats == {"fetched": 0, "already_cached": 1}
    client.fetch_users_by_ids.assert_not_called()
