import os
from unittest.mock import patch, MagicMock
from zendesk_client import ZendeskClient


def test_get_tickets_returns_list():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tickets": [{"id": 1, "subject": "Test", "description": "body", "via": {"channel": "email"}, "tags": []}],
            "end_of_stream": True,
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1)
    assert isinstance(tickets, list)
    assert tickets[0]["zendesk_id"] == 1


def test_get_ticket_single():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ticket": {"id": 42, "subject": "Single", "description": "body", "via": {"channel": "email"}, "tags": []}
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        ticket = client.get_ticket(42)
    assert ticket["zendesk_id"] == 42


def test_get_tickets_handles_pagination():
    """Verify the incremental API pagination loop stops at end_of_stream."""
    responses = [
        MagicMock(status_code=200, json=lambda: {
            "tickets": [{"id": 1, "subject": "A", "description": "", "via": {"channel": "email"}, "tags": []}],
            "end_of_stream": False,
            "after_url": "https://test.zendesk.com/api/v2/incremental/tickets/cursor.json?cursor=abc",
        }),
        MagicMock(status_code=200, json=lambda: {
            "tickets": [{"id": 2, "subject": "B", "description": "", "via": {"channel": "email"}, "tags": []}],
            "end_of_stream": True,
        }),
    ]
    with patch("zendesk_client.requests.get", side_effect=responses):
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1)
    assert len(tickets) == 2
    assert tickets[0]["zendesk_id"] == 1
    assert tickets[1]["zendesk_id"] == 2


def test_get_tickets_retries_on_429():
    """Verify 429 responses trigger a retry after sleeping."""
    rate_limit_resp = MagicMock(status_code=429, headers={"Retry-After": "1"})
    success_resp = MagicMock(status_code=200, json=lambda: {
        "tickets": [],
        "end_of_stream": True,
    })
    with patch("zendesk_client.requests.get", side_effect=[rate_limit_resp, success_resp]):
        with patch("zendesk_client.time.sleep") as mock_sleep:
            client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
            tickets = client.get_tickets(days_back=1)
    mock_sleep.assert_called_once_with(1)
    assert tickets == []
