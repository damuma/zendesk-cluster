import json
import pytest
from storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(backend="json", data_dir=str(tmp_path))


META = {
    "_meta": True,
    "project": "TEC",
    "fecha_inicio": "2026-02-16T00:00:00Z",
    "fecha_fin":    "2026-04-17T00:00:00Z",
    "last_sync":    "2026-04-17T00:00:00Z",
    "total_tickets": 2,
    "filtro": "project = TEC AND statusCategory != Done",
}


def _ticket(key, status_cat="new"):
    return {
        "jira_id": key, "url": f"https://x/browse/{key}", "summary": key,
        "description_text": "", "status": "Backlog", "status_category": status_cat,
        "priority": None, "issuetype": "Task", "labels": [], "components": [],
        "assignee": None, "created": "", "updated": "",
    }


def test_get_jira_tickets_empty(storage):
    assert storage.get_jira_tickets() == []


def test_get_jira_metadata_empty(storage):
    assert storage.get_jira_metadata() == {}


def test_save_jira_tickets_writes_meta_first(storage, tmp_path):
    storage.save_jira_tickets([_ticket("TEC-1"), _ticket("TEC-2")], META)
    raw = json.loads((tmp_path / "jira_tickets.json").read_text())
    assert raw[0] == META
    assert raw[0].get("_meta") is True
    assert [t["jira_id"] for t in raw[1:]] == ["TEC-1", "TEC-2"]


def test_get_jira_tickets_filters_meta(storage):
    storage.save_jira_tickets([_ticket("TEC-1"), _ticket("TEC-2")], META)
    tickets = storage.get_jira_tickets()
    assert [t["jira_id"] for t in tickets] == ["TEC-1", "TEC-2"]
    assert all(not t.get("_meta") for t in tickets)


def test_get_jira_metadata_returns_meta(storage):
    storage.save_jira_tickets([_ticket("TEC-1")], META)
    m = storage.get_jira_metadata()
    assert m["project"] == "TEC"
    assert m["total_tickets"] == 2


def test_upsert_jira_tickets_adds_new(storage):
    storage.save_jira_tickets([_ticket("TEC-1")], META)
    new_meta = {**META, "total_tickets": 2}
    storage.upsert_jira_tickets([_ticket("TEC-2")], done_ids=set(), meta=new_meta)
    tickets = storage.get_jira_tickets()
    ids = sorted(t["jira_id"] for t in tickets)
    assert ids == ["TEC-1", "TEC-2"]


def test_upsert_jira_tickets_updates_existing(storage):
    storage.save_jira_tickets([_ticket("TEC-1")], META)
    updated = _ticket("TEC-1"); updated["summary"] = "UPDATED"
    storage.upsert_jira_tickets([updated], done_ids=set(), meta=META)
    tickets = storage.get_jira_tickets()
    assert len(tickets) == 1
    assert tickets[0]["summary"] == "UPDATED"


def test_upsert_jira_tickets_removes_done_ids(storage):
    storage.save_jira_tickets([_ticket("TEC-1"), _ticket("TEC-2")], META)
    storage.upsert_jira_tickets([], done_ids={"TEC-1"}, meta=META)
    tickets = storage.get_jira_tickets()
    assert [t["jira_id"] for t in tickets] == ["TEC-2"]


def test_upsert_jira_tickets_on_empty_storage(storage):
    storage.upsert_jira_tickets([_ticket("TEC-1")], done_ids=set(), meta=META)
    assert [t["jira_id"] for t in storage.get_jira_tickets()] == ["TEC-1"]
    assert storage.get_jira_metadata()["project"] == "TEC"
