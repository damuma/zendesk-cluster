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


def test_get_tickets_excludes_closed_by_default():
    """Closed (archived) tickets must not enter the pipeline."""
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tickets": [
                {"id": 1, "subject": "open", "description": "", "via": {"channel": "email"}, "tags": [], "status": "open"},
                {"id": 2, "subject": "solved", "description": "", "via": {"channel": "email"}, "tags": [], "status": "solved"},
                {"id": 3, "subject": "closed", "description": "", "via": {"channel": "email"}, "tags": [], "status": "closed"},
            ],
            "end_of_stream": True,
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1)
    assert {t["zendesk_id"] for t in tickets} == {1, 2}


def test_get_tickets_respects_custom_exclude():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tickets": [
                {"id": 1, "subject": "a", "description": "", "via": {"channel": "email"}, "tags": [], "status": "open"},
                {"id": 2, "subject": "b", "description": "", "via": {"channel": "email"}, "tags": [], "status": "solved"},
            ],
            "end_of_stream": True,
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1, exclude_statuses=("solved", "closed"))
    assert {t["zendesk_id"] for t in tickets} == {1}


def test_add_tags_puts_to_tags_endpoint():
    with patch("zendesk_client.requests.put") as mock_put:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tags": ["urgent", "billing"]}
        mock_put.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        result = client.add_tags(42, ["urgent", "billing"])
    mock_put.assert_called_once()
    args, kwargs = mock_put.call_args
    assert args[0].endswith("/tickets/42/tags.json")
    assert kwargs["json"] == {"tags": ["urgent", "billing"]}
    assert result == ["urgent", "billing"]


def test_get_ticket_comments_resolves_authors_via_sideload():
    """`?include=users` resolves author_id into name/email/role in one call."""
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "comments": [
                {"id": 1, "author_id": 10, "body": "Hola", "html_body": "<p>Hola</p>",
                 "public": True, "created_at": "2026-04-08T15:22:00Z",
                 "via": {"channel": "email"}},
                {"id": 2, "author_id": 20, "body": "Respuesta", "html_body": "<p>Respuesta</p>",
                 "public": True, "created_at": "2026-04-09T13:52:00Z",
                 "via": {"channel": "web"}},
                {"id": 3, "author_id": 20, "body": "nota interna", "html_body": "",
                 "public": False, "created_at": "2026-04-09T13:55:00Z",
                 "via": {"channel": "web"}},
            ],
            "users": [
                {"id": 10, "name": "Juan", "email": "juan@example.com", "role": "end-user"},
                {"id": 20, "name": "Marta", "email": "marta@eldiario.es", "role": "agent"},
            ],
            "next_page": None,
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        comments = client.get_ticket_comments(1234)

    assert len(comments) == 3
    assert comments[0]["author"] == {
        "id": 10, "name": "Juan", "email": "juan@example.com", "role": "end-user",
    }
    assert comments[1]["author"]["role"] == "agent"
    assert comments[2]["public"] is False
    # The include param must be passed so we don't make N extra /users calls.
    called_url = mock_get.call_args[0][0]
    assert "include=users" in called_url


def test_get_ticket_comments_unknown_author_fallback():
    """If the user side-load is missing, author fields degrade gracefully."""
    with patch("zendesk_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "comments": [
                {"id": 1, "author_id": 999, "body": "x", "public": True,
                 "created_at": "2026-04-08T00:00:00Z", "via": {"channel": "api"}},
            ],
            "users": [],
            "next_page": None,
        }
        mock_get.return_value = mock_resp
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        comments = client.get_ticket_comments(1)
    assert comments[0]["author"] == {"id": None, "name": "—", "email": "", "role": "unknown"}


def test_add_tags_noop_on_empty_list():
    with patch("zendesk_client.requests.put") as mock_put:
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        assert client.add_tags(42, []) == []
    mock_put.assert_not_called()


def test_fetch_users_by_ids_batches_across_100(monkeypatch):
    from zendesk_client import ZendeskClient

    def side_effect(url, auth=None):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"users": [{"id": 1, "email": "a@x.com", "name": "A", "role": "end-user"}]}
        r.raise_for_status = MagicMock()
        return r

    with patch("zendesk_client.requests.get", side_effect=side_effect) as mock_get:
        c = ZendeskClient(subdomain="acme", email="a@x.com", token="t")
        ids = list(range(1, 251))
        users = c.fetch_users_by_ids(ids)
    assert mock_get.call_count == 3
    first_url = mock_get.call_args_list[0].args[0]
    assert "users/show_many.json?ids=" in first_url
    assert all("id" in u for u in users)


def test_fetch_users_by_ids_empty():
    with patch("zendesk_client.requests.get") as mock_get:
        c = ZendeskClient(subdomain="acme", email="a@x.com", token="t")
        assert c.fetch_users_by_ids([]) == []
    mock_get.assert_not_called()


def test_normalize_injects_requester_email_from_cache(tmp_path):
    from zendesk_users_cache import ZendeskUsersCache
    cache = ZendeskUsersCache(tmp_path / "u.json")
    cache.upsert([{"id": 42, "email": "buyer@x.com", "name": "Buyer", "role": "end-user"}])

    c = ZendeskClient(subdomain="acme", email="a@x.com", token="t", users_cache=cache)
    raw = {
        "id": 9001,
        "subject": "s",
        "description": "body",
        "requester_id": 42,
        "via": {"channel": "email"},
    }
    n = c._normalize(raw)
    assert n["requester_id"] == 42
    assert n["requester_email"] == "buyer@x.com"


def test_normalize_requester_email_null_when_cache_miss(tmp_path):
    from zendesk_users_cache import ZendeskUsersCache
    c = ZendeskClient(subdomain="acme", email="a@x.com", token="t",
                     users_cache=ZendeskUsersCache(tmp_path / "u.json"))
    n = c._normalize({"id": 1, "requester_id": 999, "via": {"channel": "email"}})
    assert n["requester_email"] is None


def test_normalize_without_cache_sets_requester_email_none():
    c = ZendeskClient(subdomain="acme", email="a@x.com", token="t")
    n = c._normalize({"id": 1, "requester_id": 42, "via": {"channel": "email"}})
    assert n["requester_email"] is None
    assert n["requester_id"] == 42


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
