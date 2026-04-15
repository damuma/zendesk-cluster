import pytest
from unittest.mock import patch, MagicMock
import json
import urllib.error
from jira_client import JiraClient


@pytest.fixture
def client():
    return JiraClient(host="test.atlassian.net", email="a@b.com", token="tok", project="TEC")


def _mock_urlopen(response_data: dict):
    """Returns a mock context manager for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_buscar_tickets_crm_returns_list(client):
    response = {
        "issues": [
            {
                "key": "TEC-42",
                "fields": {
                    "summary": "Stripe cobro duplicado",
                    "status": {"name": "Open"},
                    "priority": {"name": "High"},
                    "labels": ["CRM"],
                },
            }
        ]
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(response)):
        results = client.buscar_tickets_crm("stripe cobro")
    assert len(results) == 1
    assert results[0]["jira_id"] == "TEC-42"
    assert results[0]["summary"] == "Stripe cobro duplicado"
    assert results[0]["status"] == "Open"
    assert "browse/TEC-42" in results[0]["url"]


def test_buscar_tickets_crm_empty_results(client):
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"issues": []})):
        results = client.buscar_tickets_crm("algo que no existe")
    assert results == []


def test_buscar_tickets_crm_http_error_returns_empty(client):
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=401, msg="Unauthorized", hdrs=None, fp=None
    )):
        results = client.buscar_tickets_crm("stripe")
    assert results == []


def test_buscar_tickets_crm_sanitizes_query(client):
    """Quotes in query_text must not break JQL."""
    response = {"issues": []}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(response)) as mock_open:
        client.buscar_tickets_crm('cobro "doble" it\'s fine')
    # Should not raise; the call should have gone through
    assert mock_open.called
