from unittest.mock import MagicMock
import pytest

from fase0_jira import run, build_jql
from storage import Storage


def _ticket(key, status_cat="new", updated="2026-04-17T06:00:00+0200"):
    return {
        "jira_id": key, "url": f"https://x/browse/{key}", "summary": key,
        "description_text": "", "status": "Backlog", "status_category": status_cat,
        "priority": None, "issuetype": "Task", "labels": [], "components": [],
        "assignee": None, "created": "", "updated": updated,
    }


@pytest.fixture
def env(tmp_path):
    storage = Storage(backend="json", data_dir=str(tmp_path / "data"), config_dir=str(tmp_path / "config"))
    client = MagicMock()
    client.project = "TEC"
    client.approximate_count.return_value = 0
    return storage, client


def test_build_jql_full_mode():
    jql = build_jql(mode="full", project="TEC", days=60, since=None)
    assert "project = TEC" in jql
    assert "statusCategory != Done" in jql
    assert "updated >= -60d" in jql
    assert "ORDER BY updated DESC" in jql


def test_build_jql_incremental_mode():
    jql = build_jql(mode="incremental", project="TEC", days=60, since="2026-04-10T00:00:00Z")
    assert "project = TEC" in jql
    assert "statusCategory" not in jql
    assert "updated >= \"2026-04-10" in jql


def test_run_full_mode_empty_storage(env):
    storage, client = env
    client.fetch_tickets_jql.return_value = iter([_ticket("TEC-1"), _ticket("TEC-2")])
    client.approximate_count.return_value = 2
    run(storage=storage, client=client, mode="full", days=60)
    tickets = storage.get_jira_tickets()
    ids = {t["jira_id"] for t in tickets}
    assert ids == {"TEC-1", "TEC-2"}
    meta = storage.get_jira_metadata()
    assert meta["project"] == "TEC"
    assert meta["total_tickets"] == 2


def test_run_incremental_detects_done_and_removes(env):
    storage, client = env
    meta = {
        "_meta": True, "project": "TEC",
        "fecha_inicio": "2026-02-16T00:00:00Z",
        "fecha_fin": "2026-04-17T00:00:00Z",
        "last_sync": "2026-04-17T00:00:00Z",
        "total_tickets": 2,
        "filtro": "project = TEC AND statusCategory != Done",
    }
    storage.save_jira_tickets([_ticket("TEC-1"), _ticket("TEC-2")], meta)

    client.fetch_tickets_jql.return_value = iter([
        _ticket("TEC-1", status_cat="done"),
        _ticket("TEC-3"),
    ])
    client.approximate_count.return_value = 2

    run(storage=storage, client=client, mode="incremental", days=60)

    ids = {t["jira_id"] for t in storage.get_jira_tickets()}
    assert ids == {"TEC-2", "TEC-3"}


def test_run_full_mode_skips_done_in_input(env):
    """Full mode JQL excludes done, but safety guard still skips any done leaked in."""
    storage, client = env
    client.fetch_tickets_jql.return_value = iter([_ticket("TEC-1"), _ticket("TEC-99", status_cat="done")])
    client.approximate_count.return_value = 1
    run(storage=storage, client=client, mode="full", days=60)
    ids = {t["jira_id"] for t in storage.get_jira_tickets()}
    assert ids == {"TEC-1"}
