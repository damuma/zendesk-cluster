import json
import urllib.error
from unittest.mock import patch, MagicMock
import pytest

from jira_client import JiraClient


@pytest.fixture
def client():
    return JiraClient(host="test.atlassian.net", email="a@b.com", token="tok", project="TEC")


def _mock_urlopen(response_data: dict, status: int = 200):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ── adf_to_text ─────────────────────────────────────────────
def test_adf_to_text_simple_paragraph(client):
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Hola mundo"}]}
    ]}
    assert client.adf_to_text(adf) == "Hola mundo"


def test_adf_to_text_nested_and_multiple_blocks(client):
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "heading", "content": [{"type": "text", "text": "Título"}]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Un "},
            {"type": "text", "text": "párrafo"},
        ]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "item 1"}]}
            ]}
        ]},
    ]}
    out = client.adf_to_text(adf)
    assert "Título" in out
    assert "Un párrafo" in out
    assert "item 1" in out


def test_adf_to_text_handles_none(client):
    assert client.adf_to_text(None) == ""
    assert client.adf_to_text({}) == ""


def test_adf_to_text_ignores_unknown_nodes(client):
    """Nodes without 'text' or 'content' should be skipped silently."""
    adf = {"type": "doc", "content": [
        {"type": "mediaSingle", "attrs": {"layout": "center"}},
        {"type": "paragraph", "content": [{"type": "text", "text": "Visible"}]},
    ]}
    assert "Visible" in client.adf_to_text(adf)


# ── normalize_issue ─────────────────────────────────────────
def test_normalize_issue_basic_shape(client):
    issue = {
        "key": "TEC-42",
        "fields": {
            "summary": "Test",
            "description": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "body"}]}
            ]},
            "status": {"name": "Backlog", "statusCategory": {"key": "new"}},
            "priority": {"name": "High"},
            "issuetype": {"name": "Tarea"},
            "labels": ["CRM"],
            "components": [{"name": "Frontend"}],
            "assignee": {"displayName": "Alice"},
            "created": "2026-04-13T12:00:00+0200",
            "updated": "2026-04-17T06:00:00+0200",
        },
    }
    out = client.normalize_issue(issue)
    assert out["jira_id"] == "TEC-42"
    assert out["url"] == "https://test.atlassian.net/browse/TEC-42"
    assert out["summary"] == "Test"
    assert out["description_text"] == "body"
    assert out["status"] == "Backlog"
    assert out["status_category"] == "new"
    assert out["priority"] == "High"
    assert out["issuetype"] == "Tarea"
    assert out["labels"] == ["CRM"]
    assert out["components"] == ["Frontend"]
    assert out["assignee"] == "Alice"


def test_normalize_issue_handles_missing_optional_fields(client):
    issue = {
        "key": "TEC-1",
        "fields": {
            "summary": "x",
            "status": {"name": "Open", "statusCategory": {"key": "new"}},
            "priority": None,
            "assignee": None,
            "description": None,
            "issuetype": {"name": "Bug"},
            "labels": [],
            "components": [],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        },
    }
    out = client.normalize_issue(issue)
    assert out["priority"] is None
    assert out["assignee"] is None
    assert out["description_text"] == ""


# ── fetch_tickets_jql ───────────────────────────────────────
def test_fetch_tickets_jql_paginates_until_isLast(client):
    page1 = {
        "issues": [{"key": "TEC-1", "fields": {
            "summary": "a", "status": {"name": "Backlog", "statusCategory": {"key": "new"}},
            "priority": None, "issuetype": {"name": "Task"}, "labels": [], "components": [],
            "assignee": None, "description": None, "created": "", "updated": "",
        }}],
        "isLast": False,
        "nextPageToken": "TOKEN123",
    }
    page2 = {
        "issues": [{"key": "TEC-2", "fields": {
            "summary": "b", "status": {"name": "Backlog", "statusCategory": {"key": "new"}},
            "priority": None, "issuetype": {"name": "Task"}, "labels": [], "components": [],
            "assignee": None, "description": None, "created": "", "updated": "",
        }}],
        "isLast": True,
    }
    call_count = {"n": 0}

    def fake_urlopen(req):
        call_count["n"] += 1
        data = page1 if call_count["n"] == 1 else page2
        return _mock_urlopen(data)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        tickets = list(client.fetch_tickets_jql("project = TEC"))
    assert len(tickets) == 2
    assert tickets[0]["jira_id"] == "TEC-1"
    assert tickets[1]["jira_id"] == "TEC-2"
    assert call_count["n"] == 2


def test_fetch_tickets_jql_single_page(client):
    page = {
        "issues": [{"key": "TEC-9", "fields": {
            "summary": "only", "status": {"name": "Backlog", "statusCategory": {"key": "new"}},
            "priority": None, "issuetype": {"name": "Task"}, "labels": [], "components": [],
            "assignee": None, "description": None, "created": "", "updated": "",
        }}],
        "isLast": True,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(page)):
        tickets = list(client.fetch_tickets_jql("project = TEC"))
    assert len(tickets) == 1


# ── approximate_count ───────────────────────────────────────
def test_approximate_count_posts_and_reads_count(client):
    captured = {}

    def fake_urlopen(req):
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return _mock_urlopen({"count": 120})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        count = client.approximate_count("project = TEC")
    assert count == 120
    assert captured["method"] == "POST"
    assert b"project = TEC" in captured["data"]
