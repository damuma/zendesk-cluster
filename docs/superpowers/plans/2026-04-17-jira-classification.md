# Jira Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Jira tickets from project TEC locally in JSON and match them against existing Zendesk clusters via hybrid keyword+LLM matching.

**Architecture:** New `fase0_jira.py` downloads TEC tickets (excluding `statusCategory=done`) into `data/jira_tickets.json` with a `_meta` first record. New `jira_matcher.py` runs keyword prefilter + GPT-4o selection. Fase 3 uses the matcher against the local pool. `fase4_jira.py` re-matches existing clusters.

**Tech Stack:** Python 3.12, urllib (Jira), OpenAI GPT-4o, pytest. Jira Cloud REST API v3 `/search/jql` endpoint.

**Spec:** `docs/superpowers/specs/2026-04-17-jira-classification-design.md`

---

## File Structure

**Create:**
- `fase0_jira.py` — CLI to download Jira tickets with full/incremental modes
- `jira_matcher.py` — Hybrid keywords+LLM matcher
- `fase4_jira.py` — CLI to re-match existing clusters
- `tests/test_jira_matcher.py`
- `tests/test_fase0_jira.py`
- `tests/test_storage_jira.py`

**Modify:**
- `jira_client.py` — replace `buscar_tickets_crm` with `fetch_tickets_jql`, `approximate_count`, `adf_to_text`, `normalize_issue`
- `storage.py` — add `get_jira_tickets`, `get_jira_metadata`, `save_jira_tickets`, `upsert_jira_tickets`
- `fase3_clusterizar.py` — use `JiraMatcher` instead of `jira.buscar_tickets_crm`
- `views/clusters.py:281` — handle `jira_candidatos` as list of dicts (with str fallback)
- `views/detalle_cluster.py:39-44` — enriched render
- `tests/test_jira_client.py` — replace tests for old API
- `tests/test_fase3.py` — replace `buscar_tickets_crm` mock
- `tests/test_storage.py` — (if needed; new tests in test_storage_jira.py)
- `docs/DESIGN.md` — updated stack + flow
- `docs/IMPLEMENTACION_TECNICA.md` — new scripts section
- `docs/arquitectura-general.svg` — Jira JSON box
- `docs/flujo-embudo.svg` — matching step

**Delete:**
- `poc_jira.py` — its job is done

---

## Preamble: environment

Tests run with the venv at `/Users/dmurciano/code/eldario-lab/zendesk-cluster/venv`. All pytest commands in this plan assume:

```bash
PY=/Users/dmurciano/code/eldario-lab/zendesk-cluster/venv/bin/python
```

Use `$PY -m pytest ...` instead of plain `pytest`.

---

## Task 1: Rewrite `jira_client.py` (API migration + helpers)

**Files:**
- Modify: `jira_client.py`
- Modify: `tests/test_jira_client.py` (full replacement)

### - [ ] Step 1.1: Replace `tests/test_jira_client.py` with failing tests

Overwrite with:

```python
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
```

- [ ] Step 1.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_jira_client.py -v
```
Expected: FAIL — `AttributeError: ... has no attribute 'adf_to_text'` (etc).

- [ ] Step 1.3: Rewrite `jira_client.py` with the new API

Replace the entire file with:

```python
import os
import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from typing import Iterator
from dotenv import load_dotenv

load_dotenv()


class JiraClient:
    def __init__(self, host=None, email=None, token=None, project=None):
        self.host = host or os.environ.get("JIRA_HOST", "eldiario.atlassian.net")
        self.email = email or os.environ["JIRA_EMAIL"]
        self.token = token or os.environ["JIRA_TOKEN"]
        self.project = project or os.environ.get("JIRA_PROJECT", "TEC")
        self.base_url = f"https://{self.host}/rest/api/3"
        _tok = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {_tok}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── HTTP helpers ────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict | None = None, max_retries: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(max_retries):
            req = urllib.request.Request(url, headers=self.headers, data=data, method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    retry_after = int(e.headers.get("Retry-After", "60"))
                    time.sleep(retry_after)
                    continue
                raise

    # ── ADF extractor ───────────────────────────────────────
    def adf_to_text(self, adf: dict | None) -> str:
        if not adf or not isinstance(adf, dict):
            return ""
        block_types = {"paragraph", "heading", "listItem", "tableRow", "blockquote", "codeBlock"}
        parts: list[str] = []

        def walk(node: dict, into: list[str]) -> None:
            if not isinstance(node, dict):
                return
            ntype = node.get("type")
            if ntype == "text":
                into.append(node.get("text", ""))
                return
            buf: list[str] = [] if ntype in block_types else into
            for child in node.get("content", []) or []:
                walk(child, buf)
            if ntype in block_types and buf is not into:
                piece = "".join(buf).strip()
                if piece:
                    into.append(piece)

        for child in adf.get("content", []) or []:
            walk(child, parts)
        return "\n".join(p for p in parts if p)

    # ── Issue normalizer ────────────────────────────────────
    def normalize_issue(self, issue: dict) -> dict:
        key = issue["key"]
        f = issue.get("fields", {}) or {}
        status = f.get("status") or {}
        priority = f.get("priority") or {}
        issuetype = f.get("issuetype") or {}
        assignee = f.get("assignee") or {}
        return {
            "jira_id": key,
            "url": f"https://{self.host}/browse/{key}",
            "summary": f.get("summary", ""),
            "description_text": self.adf_to_text(f.get("description")),
            "status": status.get("name"),
            "status_category": (status.get("statusCategory") or {}).get("key"),
            "priority": priority.get("name") if priority else None,
            "issuetype": issuetype.get("name"),
            "labels": list(f.get("labels") or []),
            "components": [c.get("name") for c in (f.get("components") or [])],
            "assignee": assignee.get("displayName") if assignee else None,
            "created": f.get("created"),
            "updated": f.get("updated"),
        }

    # ── Search endpoints ────────────────────────────────────
    DEFAULT_FIELDS = "summary,description,status,priority,labels,issuetype,components,assignee,created,updated"

    def fetch_tickets_jql(self, jql: str, fields: str = DEFAULT_FIELDS, max_per_page: int = 100) -> Iterator[dict]:
        """Yields normalized tickets matching JQL, paginated via nextPageToken."""
        next_token: str | None = None
        while True:
            params = {"jql": jql, "maxResults": str(max_per_page), "fields": fields}
            if next_token:
                params["nextPageToken"] = next_token
            qs = urllib.parse.urlencode(params)
            data = self._request("GET", f"/search/jql?{qs}")
            for issue in data.get("issues", []):
                yield self.normalize_issue(issue)
            if data.get("isLast", True):
                break
            next_token = data.get("nextPageToken")
            if not next_token:
                break

    def approximate_count(self, jql: str) -> int:
        data = self._request("POST", "/search/approximate-count", body={"jql": jql})
        return int(data.get("count", 0))
```

- [ ] Step 1.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_jira_client.py -v
```
Expected: all PASS.

- [ ] Step 1.5: Commit

```bash
git add jira_client.py tests/test_jira_client.py
git commit -m "refactor(jira): migrate to /search/jql endpoint with ADF extractor

Replaces deprecated /search endpoint (HTTP 410) with the new /search/jql
with token-based pagination. Adds adf_to_text walker, normalize_issue,
and approximate_count. Removes buscar_tickets_crm (replaced by local
matching in jira_matcher.py)."
```

---

## Task 2: Extend `storage.py` with Jira methods

**Files:**
- Modify: `storage.py`
- Create: `tests/test_storage_jira.py`

### - [ ] Step 2.1: Write failing tests in `tests/test_storage_jira.py`

```python
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
    assert [t["jira_id"] for t in tickets] == ["TEC-1", "TEC-2"]


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
```

- [ ] Step 2.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_storage_jira.py -v
```
Expected: FAIL (methods don't exist).

- [ ] Step 2.3: Add methods to `storage.py`

Append at the end of the `Storage` class (before the file ends, inside the class):

```python
    # ── Jira tickets ─────────────────────────────────────────
    def _raw_jira(self) -> list:
        data = self._read("jira_tickets.json")
        return data if isinstance(data, list) else []

    def get_jira_tickets(self) -> list[dict]:
        return [t for t in self._raw_jira() if not t.get("_meta")]

    def get_jira_metadata(self) -> dict:
        for entry in self._raw_jira():
            if entry.get("_meta"):
                return entry
        return {}

    def save_jira_tickets(self, tickets: list[dict], meta: dict) -> None:
        meta = {**meta, "_meta": True}
        self._write("jira_tickets.json", [meta, *tickets])

    def upsert_jira_tickets(self, nuevos: list[dict], done_ids: set[str], meta: dict) -> None:
        existentes = {t["jira_id"]: t for t in self.get_jira_tickets()}
        for t in nuevos:
            existentes[t["jira_id"]] = t
        for jid in done_ids:
            existentes.pop(jid, None)
        ordered = sorted(existentes.values(), key=lambda t: t.get("updated") or "", reverse=True)
        self.save_jira_tickets(ordered, meta)
```

- [ ] Step 2.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_storage_jira.py -v
```
Expected: all PASS.

- [ ] Step 2.5: Commit

```bash
git add storage.py tests/test_storage_jira.py
git commit -m "feat(storage): add Jira ticket persistence with _meta record

New methods: get_jira_tickets, get_jira_metadata, save_jira_tickets,
upsert_jira_tickets. Stores a _meta record as first entry containing
date range and sync metadata."
```

---

## Task 3: Create `fase0_jira.py` (downloader)

**Files:**
- Create: `fase0_jira.py`
- Create: `tests/test_fase0_jira.py`

### - [ ] Step 3.1: Write failing tests in `tests/test_fase0_jira.py`

```python
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
    storage = Storage(backend="json", data_dir=str(tmp_path))
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
    assert "statusCategory" not in jql  # incremental must include done to detect closures
    assert "updated >= \"2026-04-10" in jql


def test_run_full_mode_empty_storage(env):
    storage, client = env
    client.fetch_tickets_jql.return_value = iter([_ticket("TEC-1"), _ticket("TEC-2")])
    client.approximate_count.return_value = 2
    run(storage=storage, client=client, mode="full", days=60)
    tickets = storage.get_jira_tickets()
    assert [t["jira_id"] for t in tickets] == ["TEC-2", "TEC-1"] or [t["jira_id"] for t in tickets] == ["TEC-1", "TEC-2"]
    meta = storage.get_jira_metadata()
    assert meta["project"] == "TEC"
    assert meta["total_tickets"] == 2


def test_run_incremental_detects_done_and_removes(env):
    storage, client = env
    # Seed: TEC-1 and TEC-2 open.
    from datetime import datetime, timezone
    meta = {
        "_meta": True, "project": "TEC",
        "fecha_inicio": "2026-02-16T00:00:00Z",
        "fecha_fin": "2026-04-17T00:00:00Z",
        "last_sync": "2026-04-17T00:00:00Z",
        "total_tickets": 2,
        "filtro": "project = TEC AND statusCategory != Done",
    }
    storage.save_jira_tickets([_ticket("TEC-1"), _ticket("TEC-2")], meta)

    # Incremental returns TEC-1 as done (it closed) and TEC-3 as new open.
    client.fetch_tickets_jql.return_value = iter([
        _ticket("TEC-1", status_cat="done"),
        _ticket("TEC-3"),
    ])
    client.approximate_count.return_value = 2

    run(storage=storage, client=client, mode="incremental", days=60)

    ids = {t["jira_id"] for t in storage.get_jira_tickets()}
    assert ids == {"TEC-2", "TEC-3"}  # TEC-1 removed (closed), TEC-3 added


def test_run_full_mode_skips_done_in_input(env):
    """Full mode uses a JQL that excludes done, but the safety guard should still skip any done leaked in."""
    storage, client = env
    client.fetch_tickets_jql.return_value = iter([_ticket("TEC-1"), _ticket("TEC-99", status_cat="done")])
    client.approximate_count.return_value = 1
    run(storage=storage, client=client, mode="full", days=60)
    ids = {t["jira_id"] for t in storage.get_jira_tickets()}
    assert ids == {"TEC-1"}
```

- [ ] Step 3.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_fase0_jira.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'fase0_jira'`.

- [ ] Step 3.3: Create `fase0_jira.py`

```python
#!/usr/bin/env python3
"""
Descarga de tickets de Jira (proyecto TEC) a JSON local.

Uso:
    python fase0_jira.py               # incremental (default)
    python fase0_jira.py --full        # re-descarga completa
    python fase0_jira.py --days 60     # ventana (default 60)
"""
import argparse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from jira_client import JiraClient
from storage import Storage

load_dotenv()


def build_jql(mode: str, project: str, days: int, since: str | None) -> str:
    if mode == "full":
        return (
            f"project = {project} AND statusCategory != Done "
            f"AND updated >= -{days}d ORDER BY updated DESC"
        )
    return (
        f'project = {project} AND updated >= "{since}" ORDER BY updated DESC'
    )


def run(storage: Storage, client: JiraClient, mode: str, days: int) -> dict:
    now = datetime.now(timezone.utc)

    prev_meta = storage.get_jira_metadata()
    if mode == "incremental" and not prev_meta:
        print("  (sin _meta previa, cambiando a modo FULL)")
        mode = "full"

    if mode == "full":
        since = None
        fecha_inicio = (now - timedelta(days=days)).isoformat()
    else:
        fecha_fin_prev = prev_meta.get("fecha_fin") or now.isoformat()
        since_dt = datetime.fromisoformat(fecha_fin_prev.replace("Z", "+00:00")) - timedelta(minutes=10)
        since = since_dt.strftime("%Y-%m-%d %H:%M")
        fecha_inicio = prev_meta.get("fecha_inicio") or (now - timedelta(days=days)).isoformat()

    jql = build_jql(mode, client.project, days, since)
    print(f"  JQL: {jql}")

    nuevos: list[dict] = []
    done_ids: set[str] = set()
    descargados = 0
    for ticket in client.fetch_tickets_jql(jql):
        descargados += 1
        if ticket.get("status_category") == "done":
            done_ids.add(ticket["jira_id"])
        else:
            nuevos.append(ticket)

    # Count canonical (no done) for meta.
    base_jql = (
        f"project = {client.project} AND statusCategory != Done "
        f"AND updated >= -{days}d"
    )
    try:
        total = client.approximate_count(base_jql)
    except Exception:
        total = None

    meta = {
        "project": client.project,
        "fecha_inicio": fecha_inicio,
        "fecha_fin": now.isoformat(),
        "last_sync": now.isoformat(),
        "total_tickets": total if total is not None else len(storage.get_jira_tickets()) + len(nuevos) - len(done_ids),
        "filtro": f"project = {client.project} AND statusCategory != Done",
    }

    if mode == "full":
        storage.save_jira_tickets(nuevos, meta)
    else:
        storage.upsert_jira_tickets(nuevos, done_ids, meta)

    stats = {
        "mode": mode,
        "descargados": descargados,
        "upsertados": len(nuevos),
        "borrados_por_done": len(done_ids),
        "total_en_json": len(storage.get_jira_tickets()),
    }
    print(
        f"  ✅ mode={mode} descargados={stats['descargados']} "
        f"upsertados={stats['upsertados']} borrados={stats['borrados_por_done']} "
        f"total={stats['total_en_json']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    mode = "full" if args.full else "incremental"
    print(f"📥 Fase 0 Jira — modo={mode}, días={args.days}")
    run(Storage(), JiraClient(), mode=mode, days=args.days)


if __name__ == "__main__":
    main()
```

- [ ] Step 3.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_fase0_jira.py -v
```
Expected: all PASS.

- [ ] Step 3.5: Commit

```bash
git add fase0_jira.py tests/test_fase0_jira.py
git commit -m "feat(fase0): add Jira ticket downloader with incremental mode

New CLI fase0_jira.py persists project TEC tickets to data/jira_tickets.json
(60-day window, excludes statusCategory=done). Incremental mode detects
closures and removes them from the pool."
```

---

## Task 4: Create `jira_matcher.py` (hybrid matcher)

**Files:**
- Create: `jira_matcher.py`
- Create: `tests/test_jira_matcher.py`

### - [ ] Step 4.1: Write failing tests in `tests/test_jira_matcher.py`

```python
import json
from unittest.mock import MagicMock, patch
import pytest

from jira_matcher import JiraMatcher


def _jira(jid, summary="", description_text="", labels=None, status="Backlog"):
    return {
        "jira_id": jid, "url": f"https://x/browse/{jid}",
        "summary": summary, "description_text": description_text,
        "status": status, "status_category": "new",
        "priority": None, "issuetype": "Task",
        "labels": labels or [], "components": [],
        "assignee": None, "created": "", "updated": "",
    }


def _cluster(anclas=None, resumen="", tipo_problema="", sistema=""):
    return {
        "cluster_id": "CLU-1", "nombre": "x",
        "anclas": anclas or {}, "resumen": resumen,
        "tipo_problema": tipo_problema, "sistema": sistema,
    }


@pytest.fixture
def matcher():
    return JiraMatcher(openai_client=MagicMock(), model="gpt-4o")


# ── _cluster_signals ────────────────────────────────────────
def test_cluster_signals_extracts_keywords(matcher):
    cluster = _cluster(
        anclas={"sistema": "stripe", "tipo_problema": "cobro_duplicado"},
        resumen="Usuarios reportan cobros duplicados en Stripe",
        tipo_problema="cobro_duplicado",
        sistema="stripe",
    )
    s = matcher._cluster_signals(cluster)
    assert "stripe" in s["keywords"]
    assert "cobros" in s["keywords"] or "cobro" in s["keywords"]
    assert "duplicados" in s["keywords"] or "duplicado" in s["keywords"]
    assert s["resumen"] == "Usuarios reportan cobros duplicados en Stripe"


# ── _prefilter_keywords ─────────────────────────────────────
def test_prefilter_filters_zero_score(matcher):
    cluster = _cluster(resumen="cobro duplicado stripe", sistema="stripe")
    pool = [
        _jira("TEC-1", summary="Error stripe cobro duplicado"),
        _jira("TEC-2", summary="Login SSO office365"),  # 0 score
        _jira("TEC-3", summary="Stripe factura"),
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
    ids = [r["jira_id"] for r in result]
    assert "TEC-1" in ids
    assert "TEC-2" not in ids


def test_prefilter_orders_by_score_desc(matcher):
    cluster = _cluster(resumen="stripe cobro duplicado cliente", sistema="stripe")
    pool = [
        _jira("TEC-A", summary="stripe"),  # 1 keyword
        _jira("TEC-B", summary="stripe cobro duplicado cliente"),  # 4 keywords
        _jira("TEC-C", summary="stripe cobro"),  # 2 keywords
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
    ids = [r["jira_id"] for r in result]
    assert ids[0] == "TEC-B"


def test_prefilter_labels_weighted_double(matcher):
    cluster = _cluster(resumen="crm migracion", sistema="crm")
    pool = [
        _jira("TEC-A", summary="crm migracion", labels=[]),  # score ~ 2
        _jira("TEC-B", summary="crm migracion", labels=["CRM"]),  # +2 for label match
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
    # TEC-B should come first due to label bonus
    assert result[0]["jira_id"] == "TEC-B"


def test_prefilter_respects_limit(matcher):
    cluster = _cluster(resumen="stripe")
    pool = [_jira(f"TEC-{i}", summary="stripe") for i in range(20)]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=5)
    assert len(result) == 5


# ── _llm_select ─────────────────────────────────────────────
def test_llm_select_calls_openai_and_enriches(matcher):
    cluster_signals = {
        "keywords": ["stripe", "cobro"],
        "resumen": "cobros duplicados en stripe",
        "anclas": {"sistema": "stripe"},
    }
    candidatos = [
        _jira("TEC-1", summary="stripe cobro error"),
        _jira("TEC-2", summary="stripe factura"),
    ]
    matcher.openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "matches": [
                {"jira_id": "TEC-1", "confianza": 0.9, "razon": "mismo error stripe"}
            ]
        })))]
    )
    result = matcher._llm_select(cluster_signals, candidatos, top_k=5)
    assert len(result) == 1
    assert result[0]["jira_id"] == "TEC-1"
    assert result[0]["confianza"] == 0.9
    assert result[0]["razon"] == "mismo error stripe"
    assert result[0]["summary"] == "stripe cobro error"
    assert result[0]["url"].endswith("TEC-1")


def test_llm_select_orders_by_confianza_and_truncates(matcher):
    cs = {"keywords": ["x"], "resumen": "r", "anclas": {}}
    candidatos = [_jira(f"TEC-{i}", summary="x") for i in range(1, 6)]
    matcher.openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "matches": [
                {"jira_id": "TEC-1", "confianza": 0.5, "razon": ""},
                {"jira_id": "TEC-2", "confianza": 0.9, "razon": ""},
                {"jira_id": "TEC-3", "confianza": 0.7, "razon": ""},
            ]
        })))]
    )
    result = matcher._llm_select(cs, candidatos, top_k=2)
    assert [r["jira_id"] for r in result] == ["TEC-2", "TEC-3"]


# ── match (end to end) ───────────────────────────────────────
def test_match_empty_pool_returns_empty(matcher):
    assert matcher.match(_cluster(resumen="stripe"), [], top_k=5) == []


def test_match_no_keyword_matches_returns_empty(matcher):
    cluster = _cluster(resumen="stripe cobro duplicado")
    pool = [_jira("TEC-1", summary="office365 login sso")]
    assert matcher.match(cluster, pool, top_k=5) == []


def test_match_skips_llm_when_no_key():
    """Without OPENAI_API_KEY AND without injected client, matcher falls back to prefilter top_k."""
    m = JiraMatcher(openai_client=None, api_key=None, model="gpt-4o")
    cluster = _cluster(resumen="stripe cobro duplicado")
    pool = [
        _jira("TEC-1", summary="stripe cobro duplicado"),
        _jira("TEC-2", summary="stripe"),
    ]
    result = m.match(cluster, pool, top_k=2)
    assert len(result) == 2
    assert result[0]["confianza"] is None
    assert result[0]["razon"] == "sin LLM disponible"
```

- [ ] Step 4.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_jira_matcher.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] Step 4.3: Create `jira_matcher.py`

```python
"""
Hybrid matcher: prefiltra tickets Jira por keywords y selecciona los
matches finales con GPT-4o.
"""
import os
import json
import re
import unicodedata
from typing import Iterable
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


STOPWORDS_ES = {
    "de", "la", "el", "en", "y", "a", "los", "las", "del", "un", "una", "por",
    "con", "no", "se", "su", "al", "lo", "es", "que", "o", "como", "para",
    "me", "mi", "te", "ti", "le", "les", "ha", "he", "has", "este", "esta",
    "esto", "estos", "estas", "eso", "ese", "esa", "esos", "esas", "muy",
    "más", "pero", "sin", "son", "ser", "hay", "tiene", "tener",
}


def _normalize(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    no_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_acc.lower()


def _tokens(text: str) -> set[str]:
    n = _normalize(text)
    words = re.findall(r"[a-z0-9_]{3,}", n)
    return {w for w in words if w not in STOPWORDS_ES}


class JiraMatcher:
    def __init__(self, openai_client=None, api_key: str | None = "__env__", model: str = "gpt-4o"):
        """
        api_key:
          - "__env__" (default) → use os.environ.get("OPENAI_API_KEY").
          - None              → explicit: disable LLM (fallback to prefilter only).
          - other str         → use as key.
        openai_client: pre-built client (used in tests). If given, LLM is enabled.
        """
        if openai_client is not None:
            self.openai = openai_client
        else:
            key = os.environ.get("OPENAI_API_KEY") if api_key == "__env__" else api_key
            self.openai = OpenAI(api_key=key) if key else None
        self.model = model

    # ── signal extraction ───────────────────────────────────
    def _cluster_signals(self, cluster: dict) -> dict:
        anclas = cluster.get("anclas") or {}
        textos: list[str] = [
            cluster.get("resumen") or "",
            cluster.get("tipo_problema") or "",
            cluster.get("sistema") or "",
            cluster.get("nombre") or "",
        ]
        for v in anclas.values() if isinstance(anclas, dict) else []:
            if isinstance(v, str):
                textos.append(v)
            elif isinstance(v, list):
                textos.extend(x for x in v if isinstance(x, str))
        keywords = set()
        for t in textos:
            keywords |= _tokens(t)
        return {
            "keywords": keywords,
            "resumen": cluster.get("resumen") or "",
            "anclas": anclas,
        }

    # ── prefilter ───────────────────────────────────────────
    def _score(self, keywords: set[str], ticket: dict) -> int:
        text = " ".join([
            ticket.get("summary") or "",
            ticket.get("description_text") or "",
        ])
        tokens = _tokens(text)
        base = len(keywords & tokens)
        label_tokens: set[str] = set()
        for lab in ticket.get("labels") or []:
            label_tokens |= _tokens(lab)
        bonus = 2 * len(keywords & label_tokens)
        return base + bonus

    def _prefilter_keywords(self, signals: dict, pool: Iterable[dict], limit: int = 15) -> list[dict]:
        scored: list[tuple[int, dict]] = []
        for t in pool:
            s = self._score(signals["keywords"], t)
            if s > 0:
                scored.append((s, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:limit]]

    # ── LLM selection ───────────────────────────────────────
    def _llm_select(self, signals: dict, candidatos: list[dict], top_k: int) -> list[dict]:
        brief = [
            {
                "jira_id": c["jira_id"],
                "summary": c.get("summary", ""),
                "labels": c.get("labels", []),
                "status": c.get("status"),
            }
            for c in candidatos
        ]
        prompt = f"""Eres un ingeniero de soporte técnico. Te doy un CLUSTER de incidencias
de usuarios y una lista de TICKETS de Jira candidatos. Elige los Jira que
corresponden al mismo problema técnico del cluster. Descarta los que solo
comparten palabras sueltas pero son de otro dominio.

CLUSTER:
- Resumen: {signals['resumen']}
- Anclas: {json.dumps(signals['anclas'], ensure_ascii=False)}

CANDIDATOS:
{json.dumps(brief, ensure_ascii=False, indent=2)}

Responde SOLO con JSON:
{{"matches": [{{"jira_id": "TEC-...", "confianza": 0.0-1.0, "razon": "..."}}]}}"""
        resp = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(resp.choices[0].message.content)
        matches = data.get("matches", [])
        matches.sort(key=lambda m: m.get("confianza", 0.0), reverse=True)
        by_id = {c["jira_id"]: c for c in candidatos}
        result: list[dict] = []
        for m in matches[:top_k]:
            base = by_id.get(m.get("jira_id"))
            if not base:
                continue
            result.append({
                "jira_id": base["jira_id"],
                "url": base["url"],
                "summary": base.get("summary", ""),
                "status": base.get("status"),
                "confianza": m.get("confianza"),
                "razon": m.get("razon", ""),
            })
        return result

    # ── public entry point ──────────────────────────────────
    def match(self, cluster: dict, jira_pool: list[dict], top_k: int = 5) -> list[dict]:
        if not jira_pool:
            return []
        signals = self._cluster_signals(cluster)
        if not signals["keywords"]:
            return []
        candidatos = self._prefilter_keywords(signals, jira_pool, limit=15)
        if not candidatos:
            return []
        if self.openai is None:
            return [
                {
                    "jira_id": c["jira_id"],
                    "url": c["url"],
                    "summary": c.get("summary", ""),
                    "status": c.get("status"),
                    "confianza": None,
                    "razon": "sin LLM disponible",
                }
                for c in candidatos[:top_k]
            ]
        return self._llm_select(signals, candidatos, top_k)
```

- [ ] Step 4.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_jira_matcher.py -v
```
Expected: all PASS.

- [ ] Step 4.5: Commit

```bash
git add jira_matcher.py tests/test_jira_matcher.py
git commit -m "feat: add JiraMatcher hybrid keyword+LLM cluster-to-Jira matcher

Keyword prefilter (cluster anclas + resumen tokens, labels weighted 2x)
reduces pool to top-15. GPT-4o selects final matches with confidence
and reason. Falls back to prefilter-only when no OPENAI_API_KEY."
```

---

## Task 5: Wire matcher into `fase3_clusterizar.py`

**Files:**
- Modify: `fase3_clusterizar.py`
- Modify: `tests/test_fase3.py`

### - [ ] Step 5.1: Update `tests/test_fase3.py`

Replace the fixture and the jira-error test. Change the file at these exact locations:

**Replace lines 14-21** (the `tmp_clusterizador` fixture):

```python
@pytest.fixture
def tmp_clusterizador(tmp_path):
    storage = Storage(backend="json", data_dir=str(tmp_path))
    mock_matcher = MagicMock()
    mock_matcher.match.return_value = []
    mock_openai = MagicMock()
    c = Fase3Clusterizador(storage=storage, matcher=mock_matcher, openai_client=mock_openai)
    return c, storage, mock_openai, mock_matcher
```

**Update all test functions** to unpack 4 values from the fixture. For example, `test_crear_nuevo_cluster`:

```python
def test_crear_nuevo_cluster(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    # ... (body unchanged)
```

Apply the same 4-tuple unpacking to `test_asignar_existente_incrementa_contador`, `test_next_cluster_id_increments`, `test_jira_error_no_bloquea`, `test_phantom_cluster_falls_through_to_crear_nuevo`, `test_missing_accion_key_falls_through_to_crear_nuevo`.

**Replace `test_jira_error_no_bloquea`** body (starting at line 96):

```python
def test_jira_error_no_bloquea(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    mock_matcher.match.side_effect = Exception("matcher down")
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "accion": "CREAR_NUEVO",
        "cluster_id": None,
        "cluster_nuevo": {"nombre": "Test", "sistema": "auth", "tipo_problema": "login", "severidad": "LOW", "resumen": ""},
        "confianza": 0.7,
        "keywords_detectados": ["login"],
        "jira_query": "login",
    })
    ticket = {"zendesk_id": 2001, "subject": "Login error", "body_preview": "No puedo entrar"}
    result = clusterizador.clusterizar(ticket)
    assert result["cluster_id"] == "CLU-001"
    assert result["jira_candidatos"] == []
```

Also add a new test at the end of the file:

```python
def test_matcher_candidates_saved_on_cluster(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    mock_matcher.match.return_value = [
        {"jira_id": "TEC-1", "url": "https://x/TEC-1", "summary": "s",
         "status": "Backlog", "confianza": 0.9, "razon": "match"}
    ]
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "accion": "CREAR_NUEVO",
        "cluster_id": None,
        "cluster_nuevo": {"nombre": "x", "sistema": "stripe", "tipo_problema": "cobro", "severidad": "HIGH", "resumen": "r"},
        "confianza": 0.9,
        "keywords_detectados": ["stripe"],
        "jira_query": "stripe",
    })
    ticket = {"zendesk_id": 7001, "subject": "s", "body_preview": "stripe"}
    result = clusterizador.clusterizar(ticket)
    clusters = storage.get_clusters()
    assert len(result["jira_candidatos"]) == 1
    assert result["jira_candidatos"][0]["jira_id"] == "TEC-1"
    assert clusters[0]["jira_candidatos"][0]["jira_id"] == "TEC-1"
```

- [ ] Step 5.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_fase3.py -v
```
Expected: FAIL — `Fase3Clusterizador` doesn't accept `matcher` kwarg.

- [ ] Step 5.3: Update `fase3_clusterizar.py`

Make these exact edits:

**Replace the import block (lines 1-9)** with:

```python
import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
from storage import Storage
from jira_matcher import JiraMatcher

load_dotenv()
```

**Replace `__init__` (lines 12-17)** with:

```python
    def __init__(self, storage=None, matcher=None, openai_client=None):
        self.openai = openai_client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.storage = storage or Storage()
        self.matcher = matcher or JiraMatcher(openai_client=self.openai, model=self.model)
```

**Replace the Jira block (lines 93-100)** — the section starting with `jira_query = data.get(...)` — with:

```python
        jira_candidatos: list[dict] = []
        try:
            jira_pool = self.storage.get_jira_tickets()
            if jira_pool:
                preview = {
                    "cluster_id": None,
                    "nombre": (data.get("cluster_nuevo") or {}).get("nombre") or "",
                    "sistema": (data.get("cluster_nuevo") or {}).get("sistema") or "",
                    "tipo_problema": (data.get("cluster_nuevo") or {}).get("tipo_problema") or "",
                    "resumen": (data.get("cluster_nuevo") or {}).get("resumen") or ticket.get("subject", ""),
                    "anclas": ticket.get("fase2_anclas") or {},
                }
                jira_candidatos = self.matcher.match(preview, jira_pool, top_k=5)
        except Exception:
            jira_candidatos = []
```

**Update the two places where `jira_candidatos` is merged** (in `ASIGNAR_EXISTENTE` branch, line 114-115):

Replace:
```python
                existing_jira = set(cluster.get("jira_candidatos", []))
                cluster["jira_candidatos"] = list(existing_jira | set(jira_candidatos))
```
With:
```python
                # Merge: dedup by jira_id, keep richer entries (with confianza) over legacy strings.
                existing = cluster.get("jira_candidatos", [])
                by_id: dict[str, dict | str] = {}
                for e in existing:
                    jid = e if isinstance(e, str) else e.get("jira_id")
                    if jid:
                        by_id[jid] = e
                for n in jira_candidatos:
                    by_id[n["jira_id"]] = n
                cluster["jira_candidatos"] = list(by_id.values())
```

- [ ] Step 5.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_fase3.py -v
```
Expected: all PASS.

- [ ] Step 5.5: Run full Fase 3 tests plus Fase 1 and 2 to confirm no regressions

```bash
$PY -m pytest tests/ -v -x
```
Expected: all PASS.

- [ ] Step 5.6: Commit

```bash
git add fase3_clusterizar.py tests/test_fase3.py
git commit -m "feat(fase3): use JiraMatcher against local pool instead of live Jira API

Replaces broken buscar_tickets_crm call with JiraMatcher.match against
storage.get_jira_tickets(). Clusters now store rich candidate objects
(jira_id, url, summary, status, confianza, razon) instead of plain IDs.
Backwards-compatible merge with legacy string IDs in existing clusters."
```

---

## Task 6: Create `fase4_jira.py` (re-matcher)

**Files:**
- Create: `fase4_jira.py`
- Create: `tests/test_fase4_jira.py`

### - [ ] Step 6.1: Write failing tests in `tests/test_fase4_jira.py`

```python
from unittest.mock import MagicMock
import pytest

from fase4_jira import run
from storage import Storage


@pytest.fixture
def env(tmp_path):
    storage = Storage(backend="json", data_dir=str(tmp_path))
    matcher = MagicMock()
    return storage, matcher


def _seed_cluster(storage, cluster_id, jira_candidatos):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({
        "cluster_id": cluster_id, "nombre": cluster_id,
        "estado": "abierto", "created_at": now, "updated_at": now,
        "resumen": "stripe cobro", "sistema": "stripe", "tipo_problema": "cobro",
        "jira_candidatos": jira_candidatos,
    })


def test_run_updates_all_clusters(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    _seed_cluster(storage, "CLU-002", ["TEC-1"])  # legacy string form
    # Meta+tickets: jira pool non-empty so matcher is actually called
    storage.save_jira_tickets(
        [{"jira_id": "TEC-9", "url": "", "summary": "", "description_text": "",
          "status": "Backlog", "status_category": "new", "priority": None,
          "issuetype": "Task", "labels": [], "components": [],
          "assignee": None, "created": "", "updated": ""}],
        {"project": "TEC", "fecha_inicio": "", "fecha_fin": "", "last_sync": "", "total_tickets": 1, "filtro": ""},
    )
    matcher.match.return_value = [
        {"jira_id": "TEC-9", "url": "u", "summary": "s", "status": "Backlog",
         "confianza": 0.8, "razon": "match"}
    ]
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    assert stats["procesados"] == 2
    clusters = storage.get_clusters()
    for c in clusters:
        assert c["jira_candidatos"][0]["jira_id"] == "TEC-9"


def test_run_only_empty_skips_populated(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    _seed_cluster(storage, "CLU-002", [{"jira_id": "TEC-3", "url": "", "summary": "",
                                         "status": "Backlog", "confianza": 0.7, "razon": ""}])
    storage.save_jira_tickets(
        [{"jira_id": "TEC-9", "url": "", "summary": "", "description_text": "",
          "status": "Backlog", "status_category": "new", "priority": None,
          "issuetype": "Task", "labels": [], "components": [],
          "assignee": None, "created": "", "updated": ""}],
        {"project": "TEC", "fecha_inicio": "", "fecha_fin": "", "last_sync": "", "total_tickets": 1, "filtro": ""},
    )
    matcher.match.return_value = [
        {"jira_id": "TEC-9", "url": "u", "summary": "s", "status": "Backlog",
         "confianza": 0.9, "razon": "m"}
    ]
    stats = run(storage=storage, matcher=matcher, only_empty=True, cluster_id=None)
    assert stats["procesados"] == 1
    clusters = {c["cluster_id"]: c for c in storage.get_clusters()}
    assert clusters["CLU-001"]["jira_candidatos"][0]["jira_id"] == "TEC-9"
    assert clusters["CLU-002"]["jira_candidatos"][0]["jira_id"] == "TEC-3"  # unchanged


def test_run_single_cluster_id(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    _seed_cluster(storage, "CLU-002", [])
    storage.save_jira_tickets(
        [{"jira_id": "TEC-9", "url": "", "summary": "", "description_text": "",
          "status": "Backlog", "status_category": "new", "priority": None,
          "issuetype": "Task", "labels": [], "components": [],
          "assignee": None, "created": "", "updated": ""}],
        {"project": "TEC", "fecha_inicio": "", "fecha_fin": "", "last_sync": "", "total_tickets": 1, "filtro": ""},
    )
    matcher.match.return_value = []
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id="CLU-002")
    assert stats["procesados"] == 1


def test_run_empty_pool_is_noop(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    assert stats["procesados"] == 0
    matcher.match.assert_not_called()
```

- [ ] Step 6.2: Run tests to verify they fail

```bash
$PY -m pytest tests/test_fase4_jira.py -v
```
Expected: FAIL — module not found.

- [ ] Step 6.3: Create `fase4_jira.py`

```python
#!/usr/bin/env python3
"""
Re-matchea clusters existentes contra el JSON actual de tickets Jira.

Uso:
    python fase4_jira.py                    # todos los clusters
    python fase4_jira.py --cluster CLU-001  # uno solo
    python fase4_jira.py --solo-vacios      # solo los sin jira_candidatos
"""
import argparse
from dotenv import load_dotenv

from storage import Storage
from jira_matcher import JiraMatcher

load_dotenv()


def _is_empty(jc) -> bool:
    return not jc or len(jc) == 0


def run(storage: Storage, matcher: JiraMatcher, only_empty: bool, cluster_id: str | None) -> dict:
    jira_pool = storage.get_jira_tickets()
    clusters = storage.get_clusters()

    if cluster_id:
        clusters = [c for c in clusters if c.get("cluster_id") == cluster_id]
    if only_empty:
        clusters = [c for c in clusters if _is_empty(c.get("jira_candidatos"))]

    if not jira_pool:
        print("  (pool Jira vacío — ejecuta primero `python fase0_jira.py`)")
        return {"procesados": 0, "actualizados": 0}

    actualizados = 0
    for c in clusters:
        preview = {
            "cluster_id": c.get("cluster_id"),
            "nombre": c.get("nombre", ""),
            "sistema": c.get("sistema", ""),
            "tipo_problema": c.get("tipo_problema", ""),
            "resumen": c.get("resumen", ""),
            "anclas": {},
        }
        try:
            candidatos = matcher.match(preview, jira_pool, top_k=5)
        except Exception as e:
            print(f"  ⚠️  {c['cluster_id']}: {e}")
            continue
        before_ids = [j if isinstance(j, str) else j.get("jira_id") for j in c.get("jira_candidatos", [])]
        after_ids = [j["jira_id"] for j in candidatos]
        if before_ids != after_ids:
            actualizados += 1
        c["jira_candidatos"] = candidatos
        storage.save_cluster(c)

    stats = {"procesados": len(clusters), "actualizados": actualizados}
    print(f"  ✅ procesados={stats['procesados']} actualizados={stats['actualizados']}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", type=str, default=None)
    parser.add_argument("--solo-vacios", action="store_true")
    args = parser.parse_args()
    print("🔗 Fase 4 Jira — re-matching clusters")
    run(Storage(), JiraMatcher(), only_empty=args.solo_vacios, cluster_id=args.cluster)


if __name__ == "__main__":
    main()
```

- [ ] Step 6.4: Run tests to verify they pass

```bash
$PY -m pytest tests/test_fase4_jira.py -v
```
Expected: all PASS.

- [ ] Step 6.5: Commit

```bash
git add fase4_jira.py tests/test_fase4_jira.py
git commit -m "feat: fase4_jira re-matches existing clusters against Jira pool

CLI to refresh jira_candidatos on existing clusters after running
fase0_jira. Supports --cluster for a single cluster and --solo-vacios
to skip clusters already populated."
```

---

## Task 7: Update UI views

**Files:**
- Modify: `views/clusters.py:281`
- Modify: `views/detalle_cluster.py:39-44`

### - [ ] Step 7.1: Update `views/clusters.py`

Replace line 281:

```python
        jira_list = ", ".join(cluster.get("jira_candidatos", [])) or "—"
```

With:

```python
        _jira_items = cluster.get("jira_candidatos", []) or []
        _jira_ids = [j if isinstance(j, str) else j.get("jira_id", "") for j in _jira_items]
        jira_list = ", ".join(i for i in _jira_ids if i) or "—"
```

### - [ ] Step 7.2: Update `views/detalle_cluster.py`

Replace lines 39-44:

```python
    jira_ids = cluster.get("jira_candidatos", [])
    if jira_ids:
        st.subheader("🔗 Jira candidatos")
        jira_host = __import__("os").environ.get("JIRA_HOST", "eldiario.atlassian.net")
        for jid in jira_ids:
            st.markdown(f"- [{jid}](https://{jira_host}/browse/{jid})")
```

With:

```python
    jira_items = cluster.get("jira_candidatos", []) or []
    if jira_items:
        st.subheader("🔗 Jira candidatos")
        jira_host = __import__("os").environ.get("JIRA_HOST", "eldiario.atlassian.net")
        for item in jira_items:
            if isinstance(item, str):
                # Legacy format
                st.markdown(f"- [{item}](https://{jira_host}/browse/{item})")
                continue
            jid = item.get("jira_id", "")
            url = item.get("url") or f"https://{jira_host}/browse/{jid}"
            status = item.get("status") or "—"
            conf = item.get("confianza")
            conf_str = f" · **{int(conf * 100)}%**" if isinstance(conf, (int, float)) else ""
            razon = item.get("razon") or ""
            summary = item.get("summary") or ""
            st.markdown(f"- [{jid}]({url}) · `{status}`{conf_str} — {summary}")
            if razon:
                st.caption(f"  ↳ {razon}")
```

### - [ ] Step 7.3: Smoke-check the Streamlit app loads without errors

Run:
```bash
$PY -c "from views.clusters import render; from views.detalle_cluster import render as r2; print('OK')"
```
Expected: `OK`.

### - [ ] Step 7.4: Commit

```bash
git add views/clusters.py views/detalle_cluster.py
git commit -m "feat(ui): render enriched Jira candidates with status and confianza

Supports both legacy string IDs (from older clusters) and new dict
format with jira_id/url/summary/status/confianza/razon."
```

---

## Task 8: Update `docs/DESIGN.md` and `docs/IMPLEMENTACION_TECNICA.md`

### - [ ] Step 8.1: Update `docs/DESIGN.md`

**In section 3 (El embudo de clasificación), after the Fase 3 description**, add:

```markdown
### Matching Cluster ↔ Jira

Cuando se crea o actualiza un cluster, el sistema busca tickets en Jira
(proyecto TEC, ya descargados en `data/jira_tickets.json`) que
representen el mismo problema técnico. Se usa un matcher híbrido:

1. **Prefiltrado por keywords** — tokens del resumen y anclas del cluster
   comparados contra `summary + description + labels` de cada Jira.
2. **Selección con GPT-4o** — de los 15 candidatos mejor puntuados, el
   LLM elige los que realmente corresponden al problema y devuelve
   confianza + razón.

Los tickets de Jira no se clusterizan; son un índice contra el que los
clusters de Zendesk se emparejan. Esto permite consolidar varios casos
de usuario en un Jira existente.

El script `fase0_jira.py` descarga los tickets de Jira (últimos 60 días,
excluyendo `statusCategory=done`). `fase4_jira.py` re-matchea clusters
existentes cuando hay nuevas Jiras.
```

**In section 7 (Stack tecnológico), update the Jira row**:

Replace:
```
| API Jira | REST API v3 (eldiario.atlassian.net) |
```
With:
```
| API Jira | REST API v3 `/search/jql` (paginación por nextPageToken) |
```

**In section 9 (Lo que NO cubre esta PoC), add**:

```markdown
- Botón UI para adjuntar tickets Zendesk del cluster al ticket Jira candidato
- Re-ejecución automática del matcher cuando llegan Jiras nuevas
```

### - [ ] Step 8.2: Update `docs/IMPLEMENTACION_TECNICA.md`

**In section 1 (Estructura del proyecto)**, update the tree to include:

```
├── data/
│   ├── conceptos.json
│   ├── tickets.json
│   ├── clusters.json
│   └── jira_tickets.json            # Pool Jira local (primer registro es _meta)
│
├── fase0_explorar.py
├── fase0_jira.py                    # NUEVO — descarga Jira TEC 60d
├── fase1_filtrar.py
├── fase2_preclasificar.py
├── fase3_clusterizar.py             # Usa JiraMatcher contra pool local
├── fase4_jira.py                    # NUEVO — re-match de clusters
├── jira_matcher.py                  # NUEVO — keywords + GPT-4o
├── pipeline.py
```

**Add a new section at the end** (before any appendix):

```markdown
## 12. Scripts de Jira

### `fase0_jira.py` — descarga

```bash
python fase0_jira.py            # modo incremental (default)
python fase0_jira.py --full     # re-descarga completa 60d
python fase0_jira.py --days 90  # cambia ventana
```

En modo FULL: `project = TEC AND statusCategory != Done AND updated >= -60d`.
En modo INCREMENTAL: pide `updated >= fecha_fin - 10min` (incluye done para
detectar cierres y borrarlos del pool).

El primer registro de `data/jira_tickets.json` es `_meta` con el rango de
fechas, último sync y total aproximado. `storage.get_jira_tickets()` lo
filtra automáticamente.

### `fase4_jira.py` — re-matching

```bash
python fase4_jira.py                    # todos los clusters
python fase4_jira.py --cluster CLU-001  # uno solo
python fase4_jira.py --solo-vacios      # solo clusters sin candidatos
```

Útil tras ejecutar `fase0_jira.py` para refrescar candidatos en clusters
ya existentes sin re-procesar tickets de Zendesk.

### Nota sobre la API de Jira

El endpoint clásico `GET /rest/api/3/search` está deprecado (HTTP 410).
Usamos `GET /rest/api/3/search/jql` con paginación por `nextPageToken`
(no hay `total`, usar `POST /search/approximate-count`).
```

### - [ ] Step 8.3: Commit docs

```bash
git add docs/DESIGN.md docs/IMPLEMENTACION_TECNICA.md
git commit -m "docs: describe Jira classification flow and new scripts

Adds matching section to DESIGN.md, updates stack table, notes the
/search deprecation, and adds fase0_jira / fase4_jira CLI reference
to IMPLEMENTACION_TECNICA.md."
```

---

## Task 9: Update SVG diagrams

**Files:**
- Modify: `docs/arquitectura-general.svg`
- Modify: `docs/flujo-embudo.svg`

### - [ ] Step 9.1: Read current `docs/arquitectura-general.svg`

```bash
$PY -c "print(open('docs/arquitectura-general.svg').read())" | head -200
```

Find the Jira section (search for `JIRA`) and identify the viewport.

### - [ ] Step 9.2: Update `docs/arquitectura-general.svg`

Add a new rectangle labeled `jira_tickets.json (local)` next to or below the existing JIRA block, with an arrow from JIRA → `jira_tickets.json` labeled "fase0_jira.py (60d)", and an arrow from `jira_tickets.json` → the EMBUDO/Fase 3 block labeled "matcher híbrido".

Because the current SVG is hand-authored, make minimal, surgical edits:
- Add one `<rect>` + two `<text>` (box for jira_tickets.json).
- Add two `<line>` with `marker-end="url(#arrow)"` (arrows).
- Keep viewport and font-family consistent with the existing file.

If needed, expand `viewBox` height by 40-60 units to fit below the existing JIRA box.

### - [ ] Step 9.3: Update `docs/flujo-embudo.svg`

After the Fase 3 box, add a small side branch: an arrow from Fase 3 → new rectangle "Matching Jira (keywords + LLM)" → rectangle "jira_candidatos en cluster".

Same minimal-edit approach.

### - [ ] Step 9.4: Visual check

Open both SVGs in a browser or image viewer and confirm readability.

```bash
open docs/arquitectura-general.svg
open docs/flujo-embudo.svg
```

If anything looks misaligned, adjust coordinates.

### - [ ] Step 9.5: Commit

```bash
git add docs/arquitectura-general.svg docs/flujo-embudo.svg
git commit -m "docs(svg): add Jira pool and matching step to diagrams

arquitectura-general.svg: new jira_tickets.json box + arrows.
flujo-embudo.svg: matching step after Fase 3."
```

---

## Task 10: End-to-end validation with live Jira

This step validates the real download (not just unit tests with mocks).

### - [ ] Step 10.1: Run `fase0_jira.py --full` against real Jira

```bash
$PY fase0_jira.py --full --days 60
```

Expected output (approximate):
```
📥 Fase 0 Jira — modo=full, días=60
  JQL: project = TEC AND statusCategory != Done AND updated >= -60d ORDER BY updated DESC
  ✅ mode=full descargados=120 upsertados=120 borrados=0 total=120
```

- [ ] Step 10.2: Verify JSON structure

```bash
$PY -c "
import json
d = json.load(open('data/jira_tickets.json'))
print('entries:', len(d))
print('meta:', d[0])
print('first ticket:', list(d[1].keys()))
print('first summary:', d[1]['summary'][:80])
"
```

Expected: 121 entries (1 meta + 120 tickets), meta with `project=TEC`, first ticket having all normalized fields.

### - [ ] Step 10.3: Run `fase0_jira.py` (incremental) — should be fast

```bash
time $PY fase0_jira.py
```

Expected: <3 seconds. Since no tickets changed since last sync, descargados=0 or very few.

### - [ ] Step 10.4: Run `fase4_jira.py` against any existing clusters

```bash
$PY fase4_jira.py
```

Expected: processes N clusters, some may get populated with Jira candidates.

### - [ ] Step 10.5: Smoke-test Streamlit app

```bash
$PY -m streamlit run app.py &
APP_PID=$!
sleep 4
curl -sf http://localhost:8501/ > /dev/null && echo "Streamlit OK"
kill $APP_PID
```

Expected: "Streamlit OK".

### - [ ] Step 10.6: Commit validation log (if anything changed e.g. data/)

```bash
# data/ is in .gitignore, so typically nothing to commit here.
git status
```

If everything clean, skip commit.

---

## Task 11: Cleanup

### - [ ] Step 11.1: Remove `poc_jira.py`

```bash
rm poc_jira.py
```

### - [ ] Step 11.2: Run entire test suite one more time

```bash
$PY -m pytest tests/ -v
```

Expected: all tests pass.

### - [ ] Step 11.3: Commit cleanup

```bash
git add poc_jira.py
git commit -m "chore: remove PoC script (migrated to fase0_jira and tests)"
```

If `git add poc_jira.py` doesn't stage the deletion (because the file no longer exists), use:

```bash
git rm poc_jira.py
git commit -m "chore: remove PoC script (migrated to fase0_jira and tests)"
```

---

## Final check

- [ ] All 11 tasks' test suites pass (`$PY -m pytest tests/ -v`)
- [ ] `fase0_jira.py` (full) downloaded ~120 real tickets
- [ ] `fase0_jira.py` (incremental) runs in <3s
- [ ] Streamlit app loads
- [ ] `jira_tickets.json` exists with `_meta` first entry
- [ ] `docs/DESIGN.md` and `IMPLEMENTACION_TECNICA.md` reflect Jira flow
- [ ] Both SVGs updated
- [ ] `poc_jira.py` removed
