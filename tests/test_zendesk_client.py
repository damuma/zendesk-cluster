import os
from unittest.mock import patch, MagicMock
from zendesk_client import ZendeskClient


def test_get_tickets_returns_list():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "tickets": [{"id": 1, "subject": "Test", "description": "body"}],
            "next_page": None
        }
        mock_get.return_value.status_code = 200
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1)
    assert isinstance(tickets, list)
    assert tickets[0]["id"] == 1


def test_get_ticket_single():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "ticket": {"id": 42, "subject": "Single", "description": "body"}
        }
        mock_get.return_value.status_code = 200
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        ticket = client.get_ticket(42)
    assert ticket["id"] == 42
