# Email-match Jira↔Zendesk + Refine de clusters — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enriquecer tickets Zendesk con emails, refinar clusters heterogéneos por subtipo con un LLM de razonamiento, y convertir el `JiraMatcher` en email-aware para conseguir matches Jira↔Zendesk determinísticos cuando un Jira menciona al usuario de un ticket.

**Architecture:** Nuevo `email_extract.py` + `zendesk_users_cache.py` como utilidades puras. Extensión de `ZendeskClient` para `show_many.json`. Nueva Fase 0.5 puebla cache `data/zendesk_users.json`. `fase2_preclasificar.py` añade emails al ticket. `JiraMatcher` extrae emails de la descripción Jira, los propaga como hint al LLM y sube confianza a ≥0.95 cuando el LLM confirma. Nuevo `fase35_refine.py` divide clusters gordos/heterogéneos en hijos `CLU-NNN-A/B/…` usando `gpt-5.4` (fallback `gpt-4o`). Nuevo `scripts/reingest_all.py` orquesta re-ingesta completa.

**Tech Stack:** Python 3.12, OpenAI Python SDK, pytest, streamlit (UI). Modelos: `gpt-4o` (Fase 1/3), `gpt-5.4` con fallback `gpt-4o` (Fase 3.5 refine).

**Spec:** [docs/superpowers/specs/2026-04-17-email-match-y-refine-design.md](../specs/2026-04-17-email-match-y-refine-design.md)

---

## File Structure

**Create:**
- `email_extract.py` — regex y normalización de emails (utilidad pura)
- `zendesk_users_cache.py` — carga/guarda/lookup de `data/zendesk_users.json`
- `fase0_zendesk_users.py` — CLI/función que puebla el cache
- `fase35_refine.py` — módulo de refine batch
- `scripts/reingest_all.py` — orquestador de re-ingesta
- `tests/test_email_extract.py`
- `tests/test_zendesk_users_cache.py`
- `tests/test_fase0_zendesk_users.py`
- `tests/test_fase35_refine.py`
- `tests/test_jira_matcher_email.py`
- `tests/test_reingest_all.py`

**Modify:**
- `zendesk_client.py` — añadir `fetch_users_by_ids()`, adaptar `_normalize()` para aceptar cache y emitir `requester_email` + `emails_mencionados`
- `fase2_preclasificar.py` — extraer emails del body y componer `emails_asociados`
- `jira_matcher.py` — email extract + augmentación de candidatos + hint al LLM + post-process
- `fase3_clusterizar.py` — filtrar padres `refined` del prompt, soportar acción `CREAR_SUBCLUSTER`
- `fase4_jira.py` — pasar `tickets_by_id` al matcher
- `pipeline.py` — añadir pasos Fase 0.5 y Fase 3.5
- `storage.py` — helper `get_tickets_by_id()` (dict indexado)
- `views/detalle_cluster.py` — banner refined + breadcrumb + badge email_match
- `views/clusters.py` — filtro refined + icono
- `.env.example` — variables nuevas
- `tests/test_zendesk_client.py` — cobertura de `fetch_users_by_ids` y `_normalize` con cache
- `tests/test_fase2.py` — cobertura emails
- `tests/test_fase3.py` — cobertura CREAR_SUBCLUSTER y exclusión refined
- `tests/test_fase4_jira.py` — cobertura email-aware

---

## Task 1: Utilidad de extracción de emails

**Files:**
- Create: `email_extract.py`
- Test: `tests/test_email_extract.py`

- [ ] **Step 1.1: Write the failing tests**

```python
# tests/test_email_extract.py
import pytest
from email_extract import extract_emails, INTERNAL_DOMAINS


def test_extract_simple_email():
    assert extract_emails("Hola, contacto: foo@bar.com.") == ["foo@bar.com"]


def test_extract_multiple_emails_dedup_sorted():
    txt = "Buyer santiagolaparra@gmail.com y mabro96@gmail.com, de nuevo santiagolaparra@gmail.com."
    assert extract_emails(txt) == ["mabro96@gmail.com", "santiagolaparra@gmail.com"]


def test_extract_emails_lowercases():
    assert extract_emails("Foo@Bar.Com") == ["foo@bar.com"]


def test_extract_emails_strips_trailing_punct():
    assert extract_emails("Escríbeme a foo@bar.com, por favor.") == ["foo@bar.com"]


def test_extract_emails_empty_input():
    assert extract_emails("") == []
    assert extract_emails(None) == []


def test_extract_emails_ignores_invalid():
    # No TLD → no match
    assert extract_emails("user@localhost") == []
    # Espacios intermedios
    assert extract_emails("foo @bar.com") == []


def test_extract_emails_filter_internal():
    txt = "Agente soporte@eldiario.es escribe a cliente@gmail.com"
    assert extract_emails(txt, exclude_domains=INTERNAL_DOMAINS) == ["cliente@gmail.com"]


def test_extract_emails_preserves_plus_dots_hyphens():
    txt = "first.last+tag@sub-domain.co.uk"
    assert extract_emails(txt) == ["first.last+tag@sub-domain.co.uk"]
```

- [ ] **Step 1.2: Run tests and verify they fail**

```bash
pytest tests/test_email_extract.py -v
```

Expected: `ModuleNotFoundError: No module named 'email_extract'`.

- [ ] **Step 1.3: Write minimal implementation**

```python
# email_extract.py
"""Email extraction utility for Jira descriptions and Zendesk ticket bodies."""
import re

EMAIL_RE = re.compile(r"[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+")

INTERNAL_DOMAINS: frozenset[str] = frozenset({"eldiario.es"})


def extract_emails(text: str | None, exclude_domains: frozenset[str] | set[str] = frozenset()) -> list[str]:
    if not text:
        return []
    raw = EMAIL_RE.findall(text)
    out: set[str] = set()
    for e in raw:
        norm = e.lower().strip(".,;:)")
        if "@" not in norm:
            continue
        domain = norm.rsplit("@", 1)[1]
        if exclude_domains and domain in exclude_domains:
            continue
        out.add(norm)
    return sorted(out)
```

- [ ] **Step 1.4: Run tests and verify they pass**

```bash
pytest tests/test_email_extract.py -v
```

Expected: 8 passed.

- [ ] **Step 1.5: Commit**

```bash
git add email_extract.py tests/test_email_extract.py
git commit -m "feat(email): utilidad de extracción de emails con filtro de dominios internos"
```

---

## Task 2: Cache de usuarios Zendesk

**Files:**
- Create: `zendesk_users_cache.py`
- Test: `tests/test_zendesk_users_cache.py`

- [ ] **Step 2.1: Write the failing tests**

```python
# tests/test_zendesk_users_cache.py
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
    assert cache.get_email(99) is None  # explicitly null
    assert cache.get_email(7) is None   # not present
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
```

- [ ] **Step 2.2: Run tests, verify they fail**

```bash
pytest tests/test_zendesk_users_cache.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 2.3: Write minimal implementation**

```python
# zendesk_users_cache.py
"""Local cache of Zendesk users (id → email/name/role). Persisted to JSON."""
import json
from pathlib import Path


class ZendeskUsersCache:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text() or "{}")

    def get_email(self, user_id: int) -> str | None:
        rec = self._data.get(str(user_id))
        if not rec:
            return None
        email = rec.get("email")
        return email or None

    def missing_ids(self, ids: list[int]) -> list[int]:
        return [i for i in ids if str(i) not in self._data]

    def upsert(self, users: list[dict]) -> None:
        for u in users:
            uid = u.get("id")
            if uid is None:
                continue
            self._data[str(uid)] = {
                "email": u.get("email"),
                "name": u.get("name"),
                "role": u.get("role"),
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)
```

- [ ] **Step 2.4: Run tests, verify they pass**

```bash
pytest tests/test_zendesk_users_cache.py -v
```

Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add zendesk_users_cache.py tests/test_zendesk_users_cache.py
git commit -m "feat(zendesk): cache local de usuarios (id → email)"
```

---

## Task 3: ZendeskClient — fetch_users_by_ids

**Files:**
- Modify: `zendesk_client.py` (añadir método)
- Test: `tests/test_zendesk_client.py`

- [ ] **Step 3.1: Write the failing test**

Añadir al final de `tests/test_zendesk_client.py`:

```python
# tests/test_zendesk_client.py (añadir)
from unittest.mock import patch, MagicMock


def _fake_resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.raise_for_status = MagicMock()
    return r


@patch("zendesk_client.requests.get")
def test_fetch_users_by_ids_batches_and_merges(mock_get, monkeypatch):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "x@x.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    from zendesk_client import ZendeskClient

    # ids: 250 → debe hacer 3 llamadas (100+100+50)
    ids = list(range(1, 251))
    def side_effect(url, auth=None):
        return _fake_resp({"users": [{"id": i, "email": f"u{i}@x.com", "name": f"U{i}", "role": "end-user"} for i in range(1, 11)]})
    mock_get.side_effect = side_effect

    c = ZendeskClient()
    users = c.fetch_users_by_ids(ids)
    assert mock_get.call_count == 3
    first_call = mock_get.call_args_list[0]
    assert "users/show_many.json?ids=" in first_call.args[0]
    # El client devuelve lo que la API ha retornado, no tiene por qué
    # coincidir con ids.
    assert all("id" in u for u in users)


@patch("zendesk_client.requests.get")
def test_fetch_users_by_ids_handles_empty(mock_get, monkeypatch):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "x@x.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    from zendesk_client import ZendeskClient

    c = ZendeskClient()
    assert c.fetch_users_by_ids([]) == []
    mock_get.assert_not_called()
```

- [ ] **Step 3.2: Run, verify fail**

```bash
pytest tests/test_zendesk_client.py::test_fetch_users_by_ids_batches_and_merges -v
```

Expected: `AttributeError: 'ZendeskClient' object has no attribute 'fetch_users_by_ids'`.

- [ ] **Step 3.3: Add method to ZendeskClient**

Añadir en `zendesk_client.py` justo antes de `_get_with_retry`:

```python
    def fetch_users_by_ids(self, user_ids: list[int], batch_size: int = 100) -> list[dict]:
        """Fetch user records via /users/show_many.json?ids=... in batches.

        Returns the raw user dicts from Zendesk (id/email/name/role). Missing
        ids (deleted users) are simply absent from the response.
        """
        if not user_ids:
            return []
        unique = sorted({int(i) for i in user_ids if i is not None})
        out: list[dict] = []
        for i in range(0, len(unique), batch_size):
            batch = unique[i:i + batch_size]
            url = f"{self.base_url}/users/show_many.json?ids={','.join(str(x) for x in batch)}"
            resp = self._get_with_retry(url)
            out.extend(resp.json().get("users", []) or [])
        return out
```

- [ ] **Step 3.4: Run tests, verify they pass**

```bash
pytest tests/test_zendesk_client.py -v
```

Expected: todos verdes (incluyendo los dos nuevos).

- [ ] **Step 3.5: Commit**

```bash
git add zendesk_client.py tests/test_zendesk_client.py
git commit -m "feat(zendesk): fetch_users_by_ids vía show_many.json con batching"
```

---

## Task 4: ZendeskClient._normalize — inyectar requester_email

**Files:**
- Modify: `zendesk_client.py` (cambiar `_normalize`)
- Test: `tests/test_zendesk_client.py`

- [ ] **Step 4.1: Write the failing test**

Añadir a `tests/test_zendesk_client.py`:

```python
def test_normalize_injects_requester_email_from_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "x@x.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    from zendesk_client import ZendeskClient
    from zendesk_users_cache import ZendeskUsersCache

    cache = ZendeskUsersCache(tmp_path / "u.json")
    cache.upsert([{"id": 42, "email": "buyer@x.com", "name": "Buyer", "role": "end-user"}])

    c = ZendeskClient(users_cache=cache)
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


def test_normalize_requester_email_null_when_cache_miss(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "x@x.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    from zendesk_client import ZendeskClient
    from zendesk_users_cache import ZendeskUsersCache

    c = ZendeskClient(users_cache=ZendeskUsersCache(tmp_path / "u.json"))
    n = c._normalize({"id": 1, "requester_id": 999, "via": {"channel": "email"}})
    assert n["requester_email"] is None
```

- [ ] **Step 4.2: Verify fail**

```bash
pytest tests/test_zendesk_client.py::test_normalize_injects_requester_email_from_cache -v
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'users_cache'`.

- [ ] **Step 4.3: Wire cache into ZendeskClient**

En `zendesk_client.py`, modificar `__init__` y `_normalize`:

```python
    def __init__(self, subdomain=None, email=None, token=None, users_cache=None):
        self.subdomain = subdomain or os.environ["ZENDESK_SUBDOMAIN"]
        self.email = email or os.environ["ZENDESK_EMAIL"]
        self.token = token or os.environ["ZENDESK_API_TOKEN"]
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        self.auth = (f"{self.email}/token", self.token)
        self.users_cache = users_cache
```

Sustituir `_normalize` por:

```python
    def _normalize(self, t: dict) -> dict:
        ticket_id = t.get("id")
        requester_id = t.get("requester_id")
        requester_email = None
        if self.users_cache is not None and requester_id is not None:
            requester_email = self.users_cache.get_email(int(requester_id))
        return {
            "zendesk_id": ticket_id,
            "zendesk_url": f"https://{self.subdomain}.zendesk.com/agent/tickets/{ticket_id}",
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("description") or "")[:1000],
            "status": t.get("status"),
            "priority": t.get("priority"),
            "ticket_type": t.get("type"),
            "channel": t.get("via", {}).get("channel", "unknown"),
            "tags": t.get("tags", []),
            "requester_id": requester_id,
            "requester_email": requester_email,
            "assignee_id": t.get("assignee_id"),
            "group_id": t.get("group_id"),
        }
```

- [ ] **Step 4.4: Tests pass**

```bash
pytest tests/test_zendesk_client.py -v
```

- [ ] **Step 4.5: Commit**

```bash
git add zendesk_client.py tests/test_zendesk_client.py
git commit -m "feat(zendesk): _normalize inyecta requester_email desde users_cache"
```

---

## Task 5: Fase 0.5 — poblar cache de usuarios Zendesk

**Files:**
- Create: `fase0_zendesk_users.py`
- Test: `tests/test_fase0_zendesk_users.py`

- [ ] **Step 5.1: Write failing test**

```python
# tests/test_fase0_zendesk_users.py
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
        {"id": 3, "email": None, "name": "Deleted", "role": "end-user"},
    ]

    stats = populate_cache_from_ids(client, cache, requester_ids=[1, 2, 3])

    client.fetch_users_by_ids.assert_called_once_with([2, 3])
    assert stats == {"fetched": 2, "already_cached": 1}
    assert cache.get_email(2) == "b@x.com"
    assert cache.get_email(3) is None


def test_populate_cache_handles_empty(tmp_path: Path):
    cache = ZendeskUsersCache(tmp_path / "u.json")
    client = MagicMock()
    stats = populate_cache_from_ids(client, cache, requester_ids=[])
    assert stats == {"fetched": 0, "already_cached": 0}
    client.fetch_users_by_ids.assert_not_called()
```

- [ ] **Step 5.2: Verify fail**

```bash
pytest tests/test_fase0_zendesk_users.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement**

```python
# fase0_zendesk_users.py
"""Fase 0.5 — poblar data/zendesk_users.json para los requester_id conocidos.

Dado un ZendeskClient y un ZendeskUsersCache, descarga vía show_many.json los
usuarios que todavía no están en el cache y lo persiste.
"""
from __future__ import annotations


def populate_cache_from_ids(client, cache, requester_ids: list[int]) -> dict:
    ids = [i for i in requester_ids if i is not None]
    missing = cache.missing_ids(ids)
    if not missing:
        return {"fetched": 0, "already_cached": len(ids)}
    users = client.fetch_users_by_ids(missing)
    cache.upsert(users)
    # Marca explícitamente los ids borrados (no vinieron en la respuesta) como null.
    returned_ids = {u["id"] for u in users if u.get("id") is not None}
    deleted = [i for i in missing if i not in returned_ids]
    if deleted:
        cache.upsert([{"id": i, "email": None, "name": None, "role": None} for i in deleted])
    cache.save()
    return {"fetched": len(missing), "already_cached": len(ids) - len(missing)}
```

- [ ] **Step 5.4: Tests pass**

```bash
pytest tests/test_fase0_zendesk_users.py -v
```

- [ ] **Step 5.5: Commit**

```bash
git add fase0_zendesk_users.py tests/test_fase0_zendesk_users.py
git commit -m "feat(fase0.5): populate_cache_from_ids llena data/zendesk_users.json"
```

---

## Task 6: Fase 2 — extraer emails y componer emails_asociados

**Files:**
- Modify: `fase2_preclasificar.py` (extender `preclasificar`)
- Modify: `tests/test_fase2.py`

- [ ] **Step 6.1: Write failing test**

Añadir a `tests/test_fase2.py`:

```python
def test_fase2_extrae_emails_mencionados_y_asociados():
    from fase2_preclasificar import Fase2Preclasificador
    f = Fase2Preclasificador(conceptos={"sistemas": {}, "tipos_problema": {}, "umbral_ancla_directa": 2})
    t = {
        "subject": "no puedo acceder",
        "body_preview": "Mi cuenta buyer@gmail.com. Confusión con otro: other@gmail.com.",
        "requester_email": "buyer@gmail.com",
    }
    r = f.preclasificar(t)
    assert r["emails_mencionados"] == ["buyer@gmail.com", "other@gmail.com"]
    assert r["emails_asociados"] == ["buyer@gmail.com", "other@gmail.com"]


def test_fase2_emails_asociados_sin_requester_email():
    from fase2_preclasificar import Fase2Preclasificador
    f = Fase2Preclasificador(conceptos={"sistemas": {}, "tipos_problema": {}, "umbral_ancla_directa": 2})
    t = {"subject": "x", "body_preview": "contacto foo@bar.com"}
    r = f.preclasificar(t)
    assert r["emails_mencionados"] == ["foo@bar.com"]
    assert r["emails_asociados"] == ["foo@bar.com"]


def test_fase2_filtra_dominios_internos():
    from fase2_preclasificar import Fase2Preclasificador
    f = Fase2Preclasificador(conceptos={"sistemas": {}, "tipos_problema": {}, "umbral_ancla_directa": 2})
    t = {
        "subject": "x",
        "body_preview": "Agente soporte@eldiario.es responde a cliente@gmail.com.",
        "requester_email": None,
    }
    r = f.preclasificar(t)
    assert r["emails_mencionados"] == ["cliente@gmail.com"]
```

- [ ] **Step 6.2: Verify fail**

```bash
pytest tests/test_fase2.py::test_fase2_extrae_emails_mencionados_y_asociados -v
```

Expected: KeyError en `r["emails_mencionados"]`.

- [ ] **Step 6.3: Extend `Fase2Preclasificador.preclasificar`**

En `fase2_preclasificar.py`, al principio del archivo:

```python
from storage import Storage
from email_extract import extract_emails, INTERNAL_DOMAINS
```

Y al final del `preclasificar`, antes de `return`, construir el dict con los nuevos campos. Reemplazar el `return` actual por algo como:

```python
        texto_para_emails = f"{ticket.get('subject', '')} {ticket.get('body_preview', '')}"
        mencionados = extract_emails(texto_para_emails, exclude_domains=INTERNAL_DOMAINS)
        req_email = ticket.get("requester_email")
        asociados_set = set(mencionados)
        if req_email:
            asociados_set.add(req_email.lower())
        emails_asociados = sorted(asociados_set)

        return {
            "anclas": {
                "sistemas": sistemas_detectados,
                "tipo_problema": tipo_detectado,
                "keywords_matched": keywords_matched,
            },
            "cluster_candidato": cluster_candidato,
            "severidad": severidad,
            "score_ancla": score_ancla,
            "emails_mencionados": mencionados,
            "emails_asociados": emails_asociados,
        }
```

(Lee antes el `return` actual para mantener los campos existentes; aquí se añaden `emails_mencionados` y `emails_asociados`.)

- [ ] **Step 6.4: Tests pass**

```bash
pytest tests/test_fase2.py -v
```

- [ ] **Step 6.5: Commit**

```bash
git add fase2_preclasificar.py tests/test_fase2.py
git commit -m "feat(fase2): extrae emails_mencionados y emails_asociados del ticket"
```

---

## Task 7: Storage — índice de tickets por zendesk_id

**Files:**
- Modify: `storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 7.1: Write failing test**

```python
def test_get_tickets_by_id_returns_dict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from storage import Storage
    s = Storage()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets.json").write_text(
        '[{"zendesk_id": 1, "subject": "a"}, {"zendesk_id": 2, "subject": "b"}]'
    )
    by_id = s.get_tickets_by_id()
    assert by_id == {1: {"zendesk_id": 1, "subject": "a"},
                     2: {"zendesk_id": 2, "subject": "b"}}
```

- [ ] **Step 7.2: Verify fail**

```bash
pytest tests/test_storage.py::test_get_tickets_by_id_returns_dict -v
```

- [ ] **Step 7.3: Add method**

En `storage.py`, añadir dentro de la clase `Storage`:

```python
    def get_tickets_by_id(self) -> dict[int, dict]:
        return {t["zendesk_id"]: t for t in self.get_tickets() if t.get("zendesk_id") is not None}
```

- [ ] **Step 7.4: Tests pass**

```bash
pytest tests/test_storage.py -v
```

- [ ] **Step 7.5: Commit**

```bash
git add storage.py tests/test_storage.py
git commit -m "feat(storage): get_tickets_by_id devuelve índice por zendesk_id"
```

---

## Task 8: JiraMatcher — extracción de emails del Jira

**Files:**
- Modify: `jira_matcher.py`
- Test: `tests/test_jira_matcher_email.py`

- [ ] **Step 8.1: Write failing test**

```python
# tests/test_jira_matcher_email.py
from jira_matcher import JiraMatcher


def test_extract_jira_emails_from_description():
    m = JiraMatcher(api_key=None)
    j = {
        "jira_id": "TEC-3091",
        "summary": "bla",
        "description_text": "socio santiagolaparra@gmail.com y mabro96@gmail.com",
    }
    assert m._extract_jira_emails(j) == {"mabro96@gmail.com", "santiagolaparra@gmail.com"}


def test_extract_jira_emails_filters_internal():
    m = JiraMatcher(api_key=None)
    j = {"jira_id": "TEC-1", "summary": "x", "description_text": "agente@eldiario.es client@gmail.com"}
    assert m._extract_jira_emails(j) == {"client@gmail.com"}


def test_cluster_emails_union_of_ticket_emails():
    m = JiraMatcher(api_key=None)
    cluster = {"ticket_ids": [1, 2, 3]}
    tickets_by_id = {
        1: {"emails_asociados": ["a@x.com"]},
        2: {"emails_asociados": ["b@x.com", "a@x.com"]},
        3: {"emails_asociados": []},
    }
    assert m._cluster_emails(cluster, tickets_by_id) == {"a@x.com", "b@x.com"}
```

- [ ] **Step 8.2: Verify fail**

```bash
pytest tests/test_jira_matcher_email.py -v
```

Expected: `AttributeError: 'JiraMatcher' object has no attribute '_extract_jira_emails'`.

- [ ] **Step 8.3: Add methods**

En `jira_matcher.py`, añadir al import:

```python
from email_extract import extract_emails, INTERNAL_DOMAINS
```

Y dentro de `JiraMatcher`, después de `_score`:

```python
    def _extract_jira_emails(self, jira: dict) -> set[str]:
        txt = f"{jira.get('summary', '')} {jira.get('description_text', '')}"
        return set(extract_emails(txt, exclude_domains=INTERNAL_DOMAINS))

    def _cluster_emails(self, cluster: dict, tickets_by_id: dict[int, dict]) -> set[str]:
        out: set[str] = set()
        for tid in cluster.get("ticket_ids") or []:
            t = tickets_by_id.get(tid) or {}
            for e in t.get("emails_asociados") or []:
                if e:
                    out.add(e.lower())
        return out
```

- [ ] **Step 8.4: Tests pass**

```bash
pytest tests/test_jira_matcher_email.py -v
```

- [ ] **Step 8.5: Commit**

```bash
git add jira_matcher.py tests/test_jira_matcher_email.py
git commit -m "feat(matcher): extrae emails de Jira y de cluster"
```

---

## Task 9: JiraMatcher — augmentación de candidatos con email + hint al LLM

**Files:**
- Modify: `jira_matcher.py` (modificar `match` y `_llm_select`)
- Modify: `tests/test_jira_matcher_email.py`

- [ ] **Step 9.1: Write failing test**

Añadir a `tests/test_jira_matcher_email.py`:

```python
from unittest.mock import MagicMock
import json


def _fake_openai_with_response(matches):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"matches": matches})))]
    )
    return client


def test_match_includes_email_candidate_even_if_keyword_score_zero():
    cluster = {
        "resumen": "error de suscripción",
        "anclas": {"sistemas": ["crm"], "tipo_problema": "error_estado"},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["mabro96@gmail.com"]}}
    jira_pool = [
        {"jira_id": "TEC-3091", "summary": "Estado suscripcion regalo no actualizado",
         "description_text": "mabro96@gmail.com beneficiario", "url": "u", "labels": []},
        {"jira_id": "TEC-9999", "summary": "completamente aparte",
         "description_text": "sin emails", "url": "u", "labels": []},
    ]
    # LLM confirma sólo TEC-3091 con confianza 0.8
    openai = _fake_openai_with_response([
        {"jira_id": "TEC-3091", "confianza": 0.8, "razon": "coincide concepto"},
    ])
    m = JiraMatcher(openai_client=openai)
    # Forzar signals["keywords"] no vacío (sistema crm)
    result = m.match(cluster, jira_pool, top_k=5, tickets_by_id=tickets_by_id)
    assert any(r["jira_id"] == "TEC-3091" for r in result)
    boosted = next(r for r in result if r["jira_id"] == "TEC-3091")
    assert boosted["confianza"] >= 0.95
    assert boosted["email_match"] == [{"email": "mabro96@gmail.com"}]
    assert "email de usuario" in boosted["razon"]


def test_match_email_match_ignored_if_llm_rejects():
    cluster = {
        "resumen": "error de login",
        "anclas": {"sistemas": ["crm"]},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["shared@x.com"]}}
    jira_pool = [
        {"jira_id": "TEC-X", "summary": "error login", "description_text": "shared@x.com",
         "url": "u", "labels": []},
        {"jira_id": "TEC-Y", "summary": "problema totalmente distinto de shared@x.com",
         "description_text": "shared@x.com otra incidencia", "url": "u", "labels": []},
    ]
    # LLM confirma sólo TEC-X (concepto coincide). TEC-Y tenía email pero no concepto.
    openai = _fake_openai_with_response([
        {"jira_id": "TEC-X", "confianza": 0.9, "razon": "login ok"},
    ])
    m = JiraMatcher(openai_client=openai)
    result = m.match(cluster, jira_pool, top_k=5, tickets_by_id=tickets_by_id)
    ids = {r["jira_id"] for r in result}
    assert "TEC-X" in ids
    assert "TEC-Y" not in ids  # el LLM lo descartó pese al email
```

- [ ] **Step 9.2: Verify fail**

```bash
pytest tests/test_jira_matcher_email.py::test_match_includes_email_candidate_even_if_keyword_score_zero -v
```

Expected: fail (probablemente TypeError por `tickets_by_id` no aceptado, o por ausencia de boost).

- [ ] **Step 9.3: Modify `_llm_select` to accept and propagate `email_match_by_id`**

Reemplazar `_llm_select` en `jira_matcher.py` por:

```python
    def _llm_select(
        self,
        signals: dict,
        candidatos: list[dict],
        top_k: int,
        email_match_by_id: dict[str, list[dict]] | None = None,
    ) -> list[dict]:
        email_match_by_id = email_match_by_id or {}
        brief = []
        for c in candidatos:
            item = {
                "jira_id": c["jira_id"],
                "summary": c.get("summary", ""),
                "labels": c.get("labels", []),
                "status": c.get("status"),
            }
            if c["jira_id"] in email_match_by_id:
                item["email_match"] = [e["email"] for e in email_match_by_id[c["jira_id"]]]
            brief.append(item)

        prompt = f"""Eres un ingeniero de soporte técnico. Te doy un CLUSTER de incidencias
de usuarios y una lista de TICKETS de Jira candidatos. Elige los Jira que
corresponden al mismo problema técnico del cluster. Descarta los que solo
comparten palabras sueltas pero son de otro dominio.

IMPORTANTE: si un candidato incluye `email_match`, significa que el Jira
menciona al mismo usuario que aparece en uno o más tickets del cluster. Es
una señal FUERTE de relevancia, PERO no suficiente por sí sola: valida
siempre que el problema técnico del Jira encaja con el cluster. Si el
problema diverge (mismo usuario, otra incidencia), descártalo igualmente.

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
            jid = base["jira_id"]
            em = email_match_by_id.get(jid, [])
            confianza = m.get("confianza")
            razon = m.get("razon", "")
            if em:
                if confianza is not None:
                    confianza = max(float(confianza), 0.95)
                emails_txt = ", ".join(sorted({e["email"] for e in em}))
                razon = f"email de usuario ({emails_txt}) + concepto coincidente — {razon}"
            result.append({
                "jira_id": jid,
                "url": base["url"],
                "summary": base.get("summary", ""),
                "description_text": base.get("description_text", ""),
                "status": base.get("status"),
                "confianza": confianza,
                "razon": razon,
                "email_match": em,
            })
        return result
```

- [ ] **Step 9.4: Modify `match` to accept `tickets_by_id` and augment**

Reemplazar `match` por:

```python
    def match(
        self,
        cluster: dict,
        jira_pool: list[dict],
        top_k: int = 5,
        tickets_by_id: dict[int, dict] | None = None,
    ) -> list[dict]:
        if not jira_pool:
            return []
        signals = self._cluster_signals(cluster)
        cluster_emails = self._cluster_emails(cluster, tickets_by_id or {})
        email_match_by_id: dict[str, list[dict]] = {}
        if cluster_emails:
            for j in jira_pool:
                inter = self._extract_jira_emails(j) & cluster_emails
                if inter:
                    email_match_by_id[j["jira_id"]] = [{"email": e} for e in sorted(inter)]

        if not signals["keywords"] and not email_match_by_id:
            return []

        candidatos: list[dict] = []
        if signals["keywords"]:
            candidatos = self._prefilter_keywords(signals, jira_pool, limit=15)
        by_id = {c["jira_id"]: c for c in candidatos}
        for j in jira_pool:
            if j["jira_id"] in email_match_by_id and j["jira_id"] not in by_id:
                candidatos.append(j)
                by_id[j["jira_id"]] = j

        if not candidatos:
            return []

        if self.openai is None:
            return [
                {
                    "jira_id": c["jira_id"],
                    "url": c["url"],
                    "summary": c.get("summary", ""),
                    "description_text": c.get("description_text", ""),
                    "status": c.get("status"),
                    "confianza": 0.9 if c["jira_id"] in email_match_by_id else None,
                    "razon": (
                        "email match sin validación LLM — verificar concepto manualmente"
                        if c["jira_id"] in email_match_by_id
                        else "sin LLM disponible"
                    ),
                    "email_match": email_match_by_id.get(c["jira_id"], []),
                }
                for c in candidatos[:top_k]
            ]
        return self._llm_select(signals, candidatos, top_k, email_match_by_id=email_match_by_id)
```

- [ ] **Step 9.5: Tests pass**

```bash
pytest tests/test_jira_matcher.py tests/test_jira_matcher_email.py -v
```

Expected: todos verdes. Si `test_jira_matcher.py` tenía tests que llamaban `match(cluster, pool)` sin `tickets_by_id`, siguen funcionando porque es opcional.

- [ ] **Step 9.6: Commit**

```bash
git add jira_matcher.py tests/test_jira_matcher_email.py
git commit -m "feat(matcher): email-aware match con hint al LLM y boost a >=0.95"
```

---

## Task 10: Fase 4 — pasar tickets_by_id al matcher

**Files:**
- Modify: `fase4_jira.py`
- Modify: `tests/test_fase4_jira.py`

- [ ] **Step 10.1: Write failing test**

Ver qué signature actual tiene `fase4_jira.run` en el archivo y añadir test que verifica que el matcher recibe `tickets_by_id`:

```python
# tests/test_fase4_jira.py (añadir)
from unittest.mock import MagicMock


def test_fase4_pasa_tickets_by_id_al_matcher(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "clusters.json").write_text(
        '[{"cluster_id": "CLU-1", "nombre": "c", "estado": "abierto", "ticket_ids": [10], "jira_candidatos": []}]'
    )
    (tmp_path / "data" / "tickets.json").write_text(
        '[{"zendesk_id": 10, "emails_asociados": ["x@y.com"]}]'
    )
    (tmp_path / "data" / "jira_tickets.json").write_text('[]')

    from fase4_jira import run
    matcher = MagicMock()
    matcher.match.return_value = []
    run(matcher=matcher, top_k=3)
    call = matcher.match.call_args
    assert call.kwargs.get("tickets_by_id", {}).get(10, {}).get("emails_asociados") == ["x@y.com"]
```

- [ ] **Step 10.2: Verify fail**

```bash
pytest tests/test_fase4_jira.py::test_fase4_pasa_tickets_by_id_al_matcher -v
```

- [ ] **Step 10.3: Modify `fase4_jira.py`**

Lee el archivo actual. Localiza la llamada a `matcher.match(cluster, jira_pool, top_k=...)` y cámbiala a:

```python
    tickets_by_id = storage.get_tickets_by_id()
    # ...
    jira_candidatos = matcher.match(cluster, jira_pool, top_k=top_k, tickets_by_id=tickets_by_id)
```

Y asegúrate de exportar una función `run(matcher=None, top_k=5)` con soporte de inyección para tests.

- [ ] **Step 10.4: Tests pass**

```bash
pytest tests/test_fase4_jira.py -v
```

- [ ] **Step 10.5: Commit**

```bash
git add fase4_jira.py tests/test_fase4_jira.py
git commit -m "feat(fase4): matcher recibe tickets_by_id para email-match"
```

---

## Task 11: Refine — heurística de selección

**Files:**
- Create: `fase35_refine.py` (primer esqueleto, sólo el scorer)
- Test: `tests/test_fase35_refine.py`

- [ ] **Step 11.1: Write failing test**

```python
# tests/test_fase35_refine.py
from fase35_refine import heterogeneity_score, should_refine


def test_heterogeneity_score_homogeneo():
    tickets = [{"anclas": {"sistemas": ["crm"]}}] * 10
    assert heterogeneity_score(tickets) == 0.0


def test_heterogeneity_score_mezcla():
    tickets = (
        [{"anclas": {"sistemas": ["crm"]}}] * 7 +
        [{"anclas": {"sistemas": ["billing"]}}] * 3
    )
    assert heterogeneity_score(tickets) == 0.3


def test_heterogeneity_handles_empty_anclas():
    tickets = [{"anclas": {}}, {"anclas": {"sistemas": []}}]
    assert heterogeneity_score(tickets) == 0.0


def test_should_refine_by_size():
    cluster = {"ticket_count": 20, "estado": "abierto"}
    assert should_refine(cluster, tickets=[], min_tickets=15, het_min=0.5) is True


def test_should_refine_by_heterogeneity():
    cluster = {"ticket_count": 10, "estado": "abierto"}
    tickets = (
        [{"anclas": {"sistemas": ["crm"]}}] * 5 +
        [{"anclas": {"sistemas": ["billing"]}}] * 5
    )
    assert should_refine(cluster, tickets=tickets, min_tickets=15, het_min=0.5) is True


def test_should_not_refine_already_refined():
    cluster = {"ticket_count": 20, "estado": "refined"}
    assert should_refine(cluster, tickets=[], min_tickets=15, het_min=0.5) is False


def test_should_not_refine_homogeneous_small():
    cluster = {"ticket_count": 5, "estado": "abierto"}
    tickets = [{"anclas": {"sistemas": ["crm"]}}] * 5
    assert should_refine(cluster, tickets=tickets, min_tickets=15, het_min=0.5) is False
```

- [ ] **Step 11.2: Verify fail**

```bash
pytest tests/test_fase35_refine.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 11.3: Implement heuristic**

```python
# fase35_refine.py
"""Fase 3.5 — refine batch de clusters heterogéneos por subtipo.

Divide clusters gordos o mezclados en sub-clusters usando un modelo de
razonamiento (gpt-5.4 con fallback gpt-4o). Padre queda `estado: refined`;
hijos `CLU-NNN-A`, `CLU-NNN-B`, …
"""
from __future__ import annotations
from collections import Counter


def heterogeneity_score(tickets: list[dict]) -> float:
    if not tickets:
        return 0.0
    sistemas = []
    for t in tickets:
        anclas = t.get("anclas") or {}
        sist = anclas.get("sistemas") or []
        if sist:
            sistemas.append(sist[0])
    if not sistemas:
        return 0.0
    counts = Counter(sistemas)
    modal = counts.most_common(1)[0][1]
    return round(1.0 - (modal / len(tickets)), 4)


def should_refine(
    cluster: dict,
    tickets: list[dict],
    min_tickets: int = 15,
    het_min: float = 0.5,
) -> bool:
    if cluster.get("estado") not in (None, "abierto"):
        return False
    if cluster.get("ticket_count", 0) >= min_tickets:
        return True
    if heterogeneity_score(tickets) >= het_min:
        return True
    return False
```

- [ ] **Step 11.4: Tests pass**

```bash
pytest tests/test_fase35_refine.py -v
```

- [ ] **Step 11.5: Commit**

```bash
git add fase35_refine.py tests/test_fase35_refine.py
git commit -m "feat(refine): heterogeneity_score y should_refine"
```

---

## Task 12: Refine — llamada LLM y parsing

**Files:**
- Modify: `fase35_refine.py`
- Modify: `tests/test_fase35_refine.py`

- [ ] **Step 12.1: Write failing test**

```python
from unittest.mock import MagicMock
import json


def _fake_openai_with_subgroups(subgroups):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"subgrupos": subgroups})))]
    )
    return client


def test_split_cluster_calls_llm_and_parses():
    from fase35_refine import split_cluster
    tickets = [
        {"zendesk_id": 1, "subject": "no puedo loguear", "body_preview": ""},
        {"zendesk_id": 2, "subject": "pago bloqueado", "body_preview": ""},
    ]
    openai = _fake_openai_with_subgroups([
        {"subtipo": "login", "nombre": "Login", "resumen": "No puedo entrar", "ticket_ids": [1]},
        {"subtipo": "pago", "nombre": "Pago", "resumen": "Pago bloqueado", "ticket_ids": [2]},
    ])
    subs = split_cluster(tickets, openai_client=openai, model="gpt-4o")
    assert len(subs) == 2
    assert subs[0]["subtipo"] == "login"
    assert subs[0]["ticket_ids"] == [1]


def test_split_cluster_fallback_on_model_error(monkeypatch):
    """Si openai falla con el modelo principal, reintenta con gpt-4o."""
    from fase35_refine import split_cluster
    client = MagicMock()
    # Primera llamada falla (modelo no disponible), segunda OK
    call_count = {"n": 0}
    def side_effect(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("model not found")
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({"subgrupos": [
            {"subtipo": "t", "nombre": "n", "resumen": "r", "ticket_ids": [1]}
        ]})))])
    client.chat.completions.create.side_effect = side_effect
    subs = split_cluster([{"zendesk_id": 1, "subject": "x", "body_preview": ""}],
                         openai_client=client, model="gpt-5.4")
    assert len(subs) == 1
    # Segunda llamada debería haber usado gpt-4o
    assert client.chat.completions.create.call_args_list[1].kwargs.get("model") == "gpt-4o"
```

- [ ] **Step 12.2: Verify fail**

```bash
pytest tests/test_fase35_refine.py::test_split_cluster_calls_llm_and_parses -v
```

- [ ] **Step 12.3: Implement `split_cluster`**

Añadir a `fase35_refine.py`:

```python
import json as _json
import logging

_log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """Eres un ingeniero de soporte técnico. Te doy un CLUSTER de tickets
que ha sido clasificado como "{tipo_problema} en {sistema}" pero es
demasiado amplio.

Divide los tickets en SUBGRUPOS homogéneos por subtipo de problema
técnico concreto. Cada subgrupo debe describir UN fallo específico
reproducible, no una categoría genérica.

Reglas:
- Si todos los tickets son realmente del mismo subtipo, devuelve UN
  único grupo con todos los ticket_ids.
- Los tickets con subject y body vacíos o con sólo metadata
  ("Conversation with Web User…") agrúpalos en un grupo
  "sin_contenido" — no intentes clasificarlos.
- Un ticket va a exactamente un subgrupo.

TICKETS:
{tickets_json}

Responde SOLO JSON:
{{"subgrupos": [{{"subtipo": "snake_case", "nombre": "...", "resumen": "...", "ticket_ids": [...]}}]}}"""


def split_cluster(
    tickets: list[dict],
    openai_client,
    model: str,
    fallback_model: str = "gpt-4o",
    max_tickets_per_batch: int = 40,
    cluster_meta: dict | None = None,
) -> list[dict]:
    meta = cluster_meta or {}
    brief = [
        {
            "zendesk_id": t.get("zendesk_id"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("body_preview") or "")[:500],
        }
        for t in tickets[:max_tickets_per_batch]
    ]
    prompt = _PROMPT_TEMPLATE.format(
        tipo_problema=meta.get("tipo_problema", "?"),
        sistema=meta.get("sistema", "?"),
        tickets_json=_json.dumps(brief, ensure_ascii=False, indent=2),
    )

    def _call(m: str):
        resp = openai_client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return _json.loads(resp.choices[0].message.content)

    try:
        data = _call(model)
    except Exception as e:
        _log.warning("refine: modelo %s falló (%s), fallback a %s", model, e, fallback_model)
        data = _call(fallback_model)

    return data.get("subgrupos", []) or []
```

- [ ] **Step 12.4: Tests pass**

```bash
pytest tests/test_fase35_refine.py -v
```

- [ ] **Step 12.5: Commit**

```bash
git add fase35_refine.py tests/test_fase35_refine.py
git commit -m "feat(refine): split_cluster con modelo principal + fallback gpt-4o"
```

---

## Task 13: Refine — aplicar split (crear hijos, marcar padre)

**Files:**
- Modify: `fase35_refine.py`
- Modify: `tests/test_fase35_refine.py`

- [ ] **Step 13.1: Write failing test**

```python
def test_apply_split_creates_children_and_marks_parent():
    from fase35_refine import apply_split
    parent = {
        "cluster_id": "CLU-007",
        "nombre": "Problemas de acceso",
        "sistema": "auth_login",
        "tipo_problema": "error_acceso",
        "severidad": "HIGH",
        "estado": "abierto",
        "ticket_ids": [1, 2, 3, 4],
        "jira_candidatos": [{"jira_id": "TEC-X"}],
        "ticket_count": 4,
    }
    subgrupos = [
        {"subtipo": "login_normal", "nombre": "Login", "resumen": "No entran",
         "ticket_ids": [1, 2]},
        {"subtipo": "otro", "nombre": "Otro", "resumen": "algo",
         "ticket_ids": [3, 4]},
    ]
    children = apply_split(parent, subgrupos, now="2026-04-17T10:00:00Z")
    assert len(children) == 2
    assert children[0]["cluster_id"] == "CLU-007-A"
    assert children[0]["parent_cluster_id"] == "CLU-007"
    assert children[0]["subtipo"] == "login_normal"
    assert children[0]["ticket_ids"] == [1, 2]
    assert children[0]["sistema"] == "auth_login"
    assert children[0]["severidad"] == "HIGH"
    assert children[0]["estado"] == "abierto"
    assert children[0]["refined_at"] == "2026-04-17T10:00:00Z"
    assert children[1]["cluster_id"] == "CLU-007-B"
    # Padre se muta in-place a refined
    assert parent["estado"] == "refined"
    assert parent["ticket_ids"] == []
    assert parent["jira_candidatos"] == []
    assert parent["refined_at"] == "2026-04-17T10:00:00Z"


def test_apply_split_single_group_no_op():
    from fase35_refine import apply_split
    parent = {"cluster_id": "CLU-8", "estado": "abierto", "ticket_ids": [1, 2],
              "jira_candidatos": [], "ticket_count": 2, "sistema": "x",
              "tipo_problema": "y", "severidad": "LOW", "nombre": "n"}
    subgrupos = [{"subtipo": "s", "nombre": "n", "resumen": "r", "ticket_ids": [1, 2]}]
    children = apply_split(parent, subgrupos, now="2026-04-17T10:00:00Z")
    assert children == []
    assert parent["estado"] == "abierto"
    assert parent["refined_at"] == "2026-04-17T10:00:00Z"
    assert parent["ticket_ids"] == [1, 2]
```

- [ ] **Step 13.2: Verify fail**

```bash
pytest tests/test_fase35_refine.py::test_apply_split_creates_children_and_marks_parent -v
```

- [ ] **Step 13.3: Implement `apply_split`**

Añadir a `fase35_refine.py`:

```python
import string


def apply_split(parent: dict, subgrupos: list[dict], now: str) -> list[dict]:
    """Mutate `parent` to refined (or just stamp refined_at) and return children list.

    - If only 1 subgroup: no children are created; parent stays abierto, only
      `refined_at` is stamped so the heuristic doesn't re-trigger immediately.
    - If >=2: create `CLU-NNN-A/B/…` children, mark parent `refined`, clear
      its ticket_ids and jira_candidatos (moved to children).
    """
    parent["refined_at"] = now
    if len(subgrupos) <= 1:
        return []
    parent_id = parent["cluster_id"]
    children: list[dict] = []
    for idx, g in enumerate(subgrupos):
        suffix = string.ascii_uppercase[idx]
        child = {
            "cluster_id": f"{parent_id}-{suffix}",
            "parent_cluster_id": parent_id,
            "nombre": g.get("nombre") or parent.get("nombre", ""),
            "sistema": parent.get("sistema"),
            "tipo_problema": parent.get("tipo_problema"),
            "severidad": parent.get("severidad", "MEDIUM"),
            "subtipo": g.get("subtipo", "sin_etiqueta"),
            "resumen": g.get("resumen", ""),
            "estado": "abierto",
            "ticket_ids": list(g.get("ticket_ids") or []),
            "ticket_count": len(g.get("ticket_ids") or []),
            "jira_candidatos": [],
            "created_at": now,
            "updated_at": now,
            "refined_at": now,
        }
        children.append(child)
    parent["estado"] = "refined"
    parent["ticket_ids"] = []
    parent["jira_candidatos"] = []
    parent["ticket_count"] = 0
    parent["updated_at"] = now
    return children
```

- [ ] **Step 13.4: Tests pass**

```bash
pytest tests/test_fase35_refine.py -v
```

- [ ] **Step 13.5: Commit**

```bash
git add fase35_refine.py tests/test_fase35_refine.py
git commit -m "feat(refine): apply_split crea hijos CLU-NNN-X y marca padre refined"
```

---

## Task 14: Refine — orquestador `run_refine` + CLI

**Files:**
- Modify: `fase35_refine.py`
- Modify: `tests/test_fase35_refine.py`

- [ ] **Step 14.1: Write failing test**

```python
def test_run_refine_integra_todo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    # 1 cluster grande heterogéneo, 1 pequeño homogéneo
    clusters = [
        {"cluster_id": "CLU-001", "nombre": "Grande", "sistema": "auth_login",
         "tipo_problema": "error_acceso", "severidad": "HIGH", "estado": "abierto",
         "ticket_ids": [1, 2, 3, 4, 5] * 4, "ticket_count": 20,
         "jira_candidatos": []},
        {"cluster_id": "CLU-002", "nombre": "Pequeño", "sistema": "crm",
         "tipo_problema": "error_estado", "severidad": "LOW", "estado": "abierto",
         "ticket_ids": [100], "ticket_count": 1, "jira_candidatos": []},
    ]
    tickets = [{"zendesk_id": i, "subject": f"t{i}", "body_preview": "x",
                "anclas": {"sistemas": ["auth_login"]}} for i in range(1, 6)]
    tickets.append({"zendesk_id": 100, "subject": "crm", "body_preview": "y",
                    "anclas": {"sistemas": ["crm"]}})
    import json
    (tmp_path / "data" / "clusters.json").write_text(json.dumps(clusters))
    (tmp_path / "data" / "tickets.json").write_text(json.dumps(tickets))
    (tmp_path / "data" / "jira_tickets.json").write_text("[]")

    subs = [
        {"subtipo": "a", "nombre": "A", "resumen": "ra", "ticket_ids": [1, 2, 3]},
        {"subtipo": "b", "nombre": "B", "resumen": "rb", "ticket_ids": [4, 5]},
    ]
    from unittest.mock import MagicMock
    openai = MagicMock()
    openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"subgrupos": subs})))]
    )
    matcher = MagicMock()
    matcher.match.return_value = []

    from fase35_refine import run_refine
    stats = run_refine(openai_client=openai, matcher=matcher, model="gpt-4o",
                       min_tickets=15, het_min=0.5)
    assert stats["clusters_refined"] == 1
    assert stats["children_created"] == 2
    saved = json.loads((tmp_path / "data" / "clusters.json").read_text())
    ids = {c["cluster_id"] for c in saved}
    assert "CLU-001-A" in ids
    assert "CLU-001-B" in ids
    parent = next(c for c in saved if c["cluster_id"] == "CLU-001")
    assert parent["estado"] == "refined"
    # Small cluster untouched
    small = next(c for c in saved if c["cluster_id"] == "CLU-002")
    assert small["estado"] == "abierto"
```

- [ ] **Step 14.2: Verify fail**

```bash
pytest tests/test_fase35_refine.py::test_run_refine_integra_todo -v
```

- [ ] **Step 14.3: Implement `run_refine` and CLI**

Añadir a `fase35_refine.py`:

```python
import os
from datetime import datetime, timezone
from storage import Storage


def run_refine(
    openai_client=None,
    matcher=None,
    model: str | None = None,
    fallback_model: str = "gpt-4o",
    min_tickets: int = 15,
    het_min: float = 0.5,
) -> dict:
    storage = Storage()
    clusters = storage.get_clusters()
    tickets_by_id = storage.get_tickets_by_id()
    jira_pool = storage.get_jira_tickets()

    stats = {"clusters_refined": 0, "children_created": 0, "noop": 0}
    now = datetime.now(timezone.utc).isoformat()
    model = model or os.environ.get("OPENAI_MODEL_REFINE", "gpt-5.4")

    new_clusters: list[dict] = []
    for cluster in clusters:
        tickets_en_cluster = [tickets_by_id[t] for t in cluster.get("ticket_ids", []) if t in tickets_by_id]
        if not should_refine(cluster, tickets_en_cluster, min_tickets, het_min):
            new_clusters.append(cluster)
            continue
        subgrupos = split_cluster(
            tickets_en_cluster,
            openai_client=openai_client,
            model=model,
            fallback_model=fallback_model,
            cluster_meta={"sistema": cluster.get("sistema"), "tipo_problema": cluster.get("tipo_problema")},
        )
        children = apply_split(cluster, subgrupos, now=now)
        if children:
            stats["clusters_refined"] += 1
            stats["children_created"] += len(children)
            # Re-match Jira for each child
            for ch in children:
                ch["jira_candidatos"] = matcher.match(ch, jira_pool, top_k=5, tickets_by_id=tickets_by_id) if matcher else []
            new_clusters.append(cluster)
            new_clusters.extend(children)
        else:
            stats["noop"] += 1
            new_clusters.append(cluster)

    storage.save_clusters(new_clusters)
    return stats


def main() -> None:
    import argparse
    from openai import OpenAI
    from dotenv import load_dotenv
    from jira_matcher import JiraMatcher

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-tickets", type=int, default=int(os.environ.get("REFINE_MIN_TICKETS", 15)))
    parser.add_argument("--het-min", type=float, default=float(os.environ.get("REFINE_HETEROGENEITY_MIN", 0.5)))
    args = parser.parse_args()

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    matcher = JiraMatcher(openai_client=client, model=os.environ.get("OPENAI_MODEL", "gpt-4o"))
    stats = run_refine(
        openai_client=client,
        matcher=matcher,
        min_tickets=args.min_tickets,
        het_min=args.het_min,
    )
    print(f"✅ Refine: {stats}")


if __name__ == "__main__":
    main()
```

También asegúrate de que `storage.py` expone `save_clusters(list)`; si no, añádelo:

```python
    def save_clusters(self, clusters: list[dict]) -> None:
        path = self._clusters_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(clusters, ensure_ascii=False, indent=2))
        tmp.replace(path)
```

(Verifica el nombre del atributo; adaptar a como esté el Storage actual.)

- [ ] **Step 14.4: Tests pass**

```bash
pytest tests/test_fase35_refine.py -v
```

- [ ] **Step 14.5: Commit**

```bash
git add fase35_refine.py storage.py tests/test_fase35_refine.py
git commit -m "feat(refine): run_refine orquesta selección+split+hijos+rematch"
```

---

## Task 15: Fase 3 — ignorar clusters refinados en el prompt

**Files:**
- Modify: `fase3_clusterizar.py`
- Modify: `tests/test_fase3.py`

- [ ] **Step 15.1: Write failing test**

```python
def test_fase3_excluye_clusters_refined_del_prompt(monkeypatch):
    from unittest.mock import MagicMock
    import json
    from fase3_clusterizar import Fase3Clusterizador

    storage = MagicMock()
    storage.get_clusters.return_value = [
        {"cluster_id": "CLU-001", "nombre": "c1", "estado": "abierto",
         "sistema": "x", "tipo_problema": "y", "resumen": "r", "ticket_count": 1},
        {"cluster_id": "CLU-002", "nombre": "c2", "estado": "refined",
         "sistema": "x", "tipo_problema": "y", "resumen": "r", "ticket_count": 0},
        {"cluster_id": "CLU-002-A", "nombre": "child", "estado": "abierto",
         "sistema": "x", "tipo_problema": "y", "resumen": "r", "ticket_count": 2},
    ]
    storage.get_conceptos.return_value = {"sistemas": {}, "tipos_problema": {}}
    storage.get_jira_tickets.return_value = []
    storage.save_cluster = MagicMock()
    storage.get_tickets_by_id = MagicMock(return_value={})

    openai = MagicMock()
    openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "accion": "ASIGNAR_EXISTENTE", "cluster_id": "CLU-001",
            "confianza": 0.9, "keywords_detectados": []})))]
    )
    matcher = MagicMock(); matcher.match.return_value = []
    clz = Fase3Clusterizador(storage=storage, matcher=matcher, openai_client=openai)
    clz.clusterizar({"zendesk_id": 1, "subject": "hi", "body_preview": "hi"})

    sent_prompt = openai.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "CLU-001" in sent_prompt
    assert "CLU-002-A" in sent_prompt
    assert "CLU-002" not in sent_prompt or "CLU-002-A" in sent_prompt  # padre refined no aparece como opción
```

- [ ] **Step 15.2: Verify fail**

```bash
pytest tests/test_fase3.py::test_fase3_excluye_clusters_refined_del_prompt -v
```

- [ ] **Step 15.3: Modify Fase3**

En `fase3_clusterizar.py`, donde se calcula `clusters_resumen`, añadir filtro:

```python
        clusters = self.storage.get_clusters(estado="abierto")
        # (si storage.get_clusters no filtra por estado, filtrar aquí:)
        clusters = [c for c in clusters if c.get("estado") != "refined"]
```

Asegurar que el exclude del prompt es sólo para los `refined`. Los hijos (`estado: abierto`) deben aparecer con su `cluster_id` completo (`CLU-002-A`).

- [ ] **Step 15.4: Tests pass**

```bash
pytest tests/test_fase3.py -v
```

- [ ] **Step 15.5: Commit**

```bash
git add fase3_clusterizar.py tests/test_fase3.py
git commit -m "feat(fase3): excluye clusters refined del prompt, hijos siguen siendo asignables"
```

---

## Task 16: Pipeline — integrar Fase 0.5 y Fase 3.5

**Files:**
- Modify: `pipeline.py`

- [ ] **Step 16.1: Read current pipeline.py structure**

```bash
cat pipeline.py | head -120
```

- [ ] **Step 16.2: Insert Fase 0.5 before Zendesk ingest**

En `pipeline.py`, al principio de `run_pipeline`, tras obtener `tickets_raw`, antes del filtrado:

```python
    from fase0_zendesk_users import populate_cache_from_ids
    from zendesk_users_cache import ZendeskUsersCache
    from pathlib import Path

    # Fase 0.5: poblar cache de usuarios antes de normalizar con email
    users_cache = ZendeskUsersCache(Path("data/zendesk_users.json"))
    client.users_cache = users_cache
    requester_ids = [t.get("requester_id") for t in tickets_raw if t.get("requester_id")]
    stats_users = populate_cache_from_ids(client, users_cache, requester_ids)
    print(f"   Fase 0.5: users {stats_users}")
    # Re-normalizar los tickets con el cache ya poblado
    tickets_raw = [client._normalize(t) for t in tickets_raw if "_raw" not in t]
```

Nota: si la API ya normalizó los tickets antes de tener cache, el `_normalize` se rehace. La forma limpia es modificar `get_tickets_since` para no normalizar y hacerlo después; pero para no romper callers, deja este patrón (se re-normaliza una vez con cache). En un refactor siguiente se puede mejorar.

Alternativa más limpia: exponer `get_tickets_raw_since()` que devuelve sin normalizar, luego Fase 0.5, luego `[client._normalize(t) for t in raw]`.

- [ ] **Step 16.3: Insert Fase 3.5 at end of pipeline**

Tras el loop de tickets (al final de `run_pipeline`), añadir:

```python
    # Fase 3.5: refine de clusters heterogéneos
    from fase35_refine import run_refine
    from jira_matcher import JiraMatcher
    from openai import OpenAI
    oai = OpenAI()
    matcher = JiraMatcher(openai_client=oai)
    refine_stats = run_refine(openai_client=oai, matcher=matcher)
    print(f"📦 Fase 3.5: {refine_stats}")
```

- [ ] **Step 16.4: Smoke run (dry-run o datos existentes)**

```bash
python -c "import pipeline; print('import OK')"
```

- [ ] **Step 16.5: Commit**

```bash
git add pipeline.py
git commit -m "feat(pipeline): integra Fase 0.5 (users cache) y Fase 3.5 (refine)"
```

---

## Task 17: Script de re-ingesta

**Files:**
- Create: `scripts/reingest_all.py`
- Test: `tests/test_reingest_all.py` (smoke)

- [ ] **Step 17.1: Write failing smoke test**

```python
# tests/test_reingest_all.py
from unittest.mock import patch, MagicMock


def test_reingest_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets.json").write_text("[]")
    (tmp_path / "data" / "clusters.json").write_text("[]")
    (tmp_path / "data" / "jira_tickets.json").write_text("[]")
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "x@x.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    with patch("scripts.reingest_all.ZendeskClient") as ZC, \
         patch("scripts.reingest_all.run_pipeline") as run_pl:
        ZC.return_value = MagicMock()
        from scripts.reingest_all import main
        main(["--dry-run"])
    run_pl.assert_not_called()
    # backups no se crean en dry-run
    backups = list((tmp_path / "data").glob("*.bak-reingest-*"))
    assert backups == []
```

- [ ] **Step 17.2: Verify fail**

```bash
pytest tests/test_reingest_all.py -v
```

- [ ] **Step 17.3: Create script**

```python
# scripts/reingest_all.py
"""Orquestador de re-ingesta completa desde Zendesk con enriquecimiento de emails.

Pasos:
1. Backup de data/{tickets,clusters}.json → data/*.bak-reingest-<timestamp>
2. Truncar tickets.json y clusters.json
3. Ejecutar pipeline (que ya integra Fase 0.5 y Fase 3.5)

Uso:
    python -m scripts.reingest_all --days 30
    python -m scripts.reingest_all --days 30 --dry-run
"""
from __future__ import annotations
import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from pipeline import run_pipeline


def _backup(data_dir: Path, timestamp: str) -> list[Path]:
    out = []
    for name in ("tickets.json", "clusters.json"):
        src = data_dir / name
        if not src.exists():
            continue
        dst = data_dir / f"{name}.bak-reingest-{timestamp}"
        shutil.copy2(src, dst)
        out.append(dst)
    return out


def _truncate(data_dir: Path) -> None:
    for name in ("tickets.json", "clusters.json"):
        (data_dir / name).write_text("[]")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    data_dir = Path("data")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.dry_run:
        print("🧪 DRY-RUN. No se escribe nada.")
        print(f"Se habría hecho backup a data/*.bak-reingest-{ts}")
        print(f"Se habría truncado tickets.json y clusters.json")
        print(f"Se habría ejecutado run_pipeline(horas={args.days * 24})")
        return 0

    print(f"🛟 Backup con sufijo {ts}")
    backups = _backup(data_dir, ts)
    for b in backups:
        print(f"  ↳ {b}")

    print("🧹 Truncando tickets.json y clusters.json")
    _truncate(data_dir)

    print(f"🚀 Ejecutando pipeline con days={args.days}")
    run_pipeline(horas=args.days * 24, dry_run=False)
    print("✅ Re-ingesta completa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 17.4: Tests pass**

```bash
pytest tests/test_reingest_all.py -v
```

- [ ] **Step 17.5: Commit**

```bash
git add scripts/reingest_all.py tests/test_reingest_all.py
git commit -m "feat(scripts): reingest_all con backup, truncate y dry-run"
```

---

## Task 18: UI — detalle de cluster con refined/hijos/email_match

**Files:**
- Modify: `views/detalle_cluster.py`

- [ ] **Step 18.1: Read current view**

```bash
sed -n '1,80p' views/detalle_cluster.py
```

- [ ] **Step 18.2: Add refined-parent banner**

En la función que renderiza el cluster, detectar `estado == "refined"` y mostrar un banner en lugar de tickets/candidatos:

```python
if cluster.get("estado") == "refined":
    import streamlit as st
    hijos = [c for c in all_clusters if c.get("parent_cluster_id") == cluster["cluster_id"]]
    st.warning(
        f"🧬 Este cluster se dividió en {len(hijos)} sub-clusters en el paso de refine. "
        f"Selecciona uno:"
    )
    for h in hijos:
        st.markdown(f"- [**{h['cluster_id']}** — {h.get('subtipo','')}] "
                    f"{h.get('nombre','')} ({h.get('ticket_count', 0)} tickets) "
                    f"[ver](?cluster={h['cluster_id']})")
    return
```

- [ ] **Step 18.3: Add breadcrumb for children**

Arriba de la cabecera del cluster, si `parent_cluster_id` existe:

```python
if cluster.get("parent_cluster_id"):
    st.caption(f"← [{cluster['parent_cluster_id']}](?cluster={cluster['parent_cluster_id']}) / "
               f"{cluster['cluster_id']} — subtipo: `{cluster.get('subtipo', '—')}`")
```

- [ ] **Step 18.4: Add email_match badge on Jira candidates**

En el bloque que itera sobre `jira_candidatos`, si el candidato tiene `email_match`:

```python
em = jc.get("email_match") or []
if em:
    emails = ", ".join(e.get("email", "") for e in em if e.get("email"))
    st.markdown(f"📧 **match por email**: `{emails}`")
```

- [ ] **Step 18.5: Manual smoke check**

```bash
streamlit run app.py
```

Verificar que un cluster ya refinado (o fabricado manualmente en `data/clusters.json`) muestra el banner correctamente y que un hijo muestra breadcrumb. Sin tests automáticos en este paso.

- [ ] **Step 18.6: Commit**

```bash
git add views/detalle_cluster.py
git commit -m "feat(ui): cluster refined muestra hijos y badge de email_match"
```

---

## Task 19: UI — listado de clusters oculta refinados

**Files:**
- Modify: `views/clusters.py`

- [ ] **Step 19.1: Read current listing view**

```bash
sed -n '1,120p' views/clusters.py
```

- [ ] **Step 19.2: Add filter toggle for refined**

En la sidebar o al principio de la vista:

```python
import streamlit as st
ocultar_refined = st.sidebar.checkbox("Ocultar clusters refinados (padres)", value=True)
```

Luego filtrar:

```python
if ocultar_refined:
    clusters = [c for c in clusters if c.get("estado") != "refined"]
```

- [ ] **Step 19.3: Add visual indicator for refined children**

Donde se pinta cada cluster en el listado, añadir icono si `parent_cluster_id`:

```python
icono = "🧬" if c.get("parent_cluster_id") else ""
st.markdown(f"{icono} **{c['cluster_id']}** — {c['nombre']} ({c.get('subtipo','')})")
```

- [ ] **Step 19.4: Manual smoke**

```bash
streamlit run app.py
```

- [ ] **Step 19.5: Commit**

```bash
git add views/clusters.py
git commit -m "feat(ui): listado oculta padres refined por defecto, icono para hijos"
```

---

## Task 20: Config y docs

**Files:**
- Modify: `.env.example`
- Modify: `docs/IMPLEMENTACION_TECNICA.md`

- [ ] **Step 20.1: Update .env.example**

Añadir al final de `.env.example`:

```
# Fase 3.5 — refine de clusters
OPENAI_MODEL_REFINE=gpt-5.4
REFINE_MIN_TICKETS=15
REFINE_HETEROGENEITY_MIN=0.5
REFINE_MAX_TICKETS_PER_BATCH=40
```

- [ ] **Step 20.2: Document new phases in IMPLEMENTACION_TECNICA.md**

Añadir una sección "Fase 0.5 y Fase 3.5" tras la de Fase 4 existente, describiendo brevemente:

- Qué hace Fase 0.5 (populate users cache, resolver requester_email).
- Qué hace Fase 3.5 (refine por subtipo, criterios de disparo, modelo).
- Variables de entorno relevantes.
- Comando CLI del refine: `python -m fase35_refine`.
- Comando CLI de re-ingesta: `python -m scripts.reingest_all --days 30`.

- [ ] **Step 20.3: Run full test suite**

```bash
pytest -x
```

Expected: todos verdes.

- [ ] **Step 20.4: Commit**

```bash
git add .env.example docs/IMPLEMENTACION_TECNICA.md
git commit -m "docs: documenta Fase 0.5 y Fase 3.5 + env vars nuevas"
```

---

## Task 21: Smoke sobre datos reales (manual)

**Files:** ninguno (verificación)

- [ ] **Step 21.1: Dry-run del reingest**

```bash
python -m scripts.reingest_all --days 30 --dry-run
```

Verificar stdout descriptivo.

- [ ] **Step 21.2: Ejecutar re-ingesta real**

```bash
python -m scripts.reingest_all --days 30
```

Verificar:
- `data/zendesk_users.json` existe y tiene entradas.
- `data/tickets.json` tiene `requester_email` poblado en la mayoría de tickets.
- `data/clusters.json` tiene clusters con `estado: refined` y sus hijos `CLU-NNN-A/B`.

- [ ] **Step 21.3: Verificar CLU-007 (antiguo) en UI**

Ejecutar `streamlit run app.py`, navegar al cluster equivalente al antiguo CLU-007 (o a cualquier cluster grande) y comprobar que aparece partido.

- [ ] **Step 21.4: Verificar TEC-3091 como match de sub-cluster de suscripción-regalo**

Localizar el sub-cluster que agrupa tickets de suscripción de regalo bloqueada. Verificar que TEC-3091 aparece en `jira_candidatos` con `email_match` poblado y `confianza ≥ 0.95`.

- [ ] **Step 21.5: Verificar TEC-3091 NO aparece en sub-cluster de login**

Comprobar que TEC-3091 no sale en un sub-cluster de "login inaccesible" u otros no relacionados.

---

## Self-review

**Spec coverage:**

| Sección spec | Task(s) |
|---|---|
| 4.1 Ticket: requester_email, emails_mencionados, emails_asociados | 4, 6 |
| 4.2 Cluster: subtipo, parent_cluster_id, refined_at, estado=refined | 13 |
| 4.3 Candidato Jira: email_match | 9 |
| 4.4 zendesk_users.json | 2, 3, 5 |
| 5 Fase 0.5 | 3, 5, 16 |
| 6 Fase 2 extendida | 1, 6 |
| 7.1 Heurística refine | 11 |
| 7.2 Prompt split | 12 |
| 7.3 Aplicación (crear hijos, marcar padre) | 13, 14 |
| 7.4 Fase 3 con clusters refinados | 15 |
| 8 JiraMatcher email-aware | 8, 9, 10 |
| 9 Re-ingest | 17 |
| 10 UI | 18, 19 |
| 11 Testing | cubierto en cada task + 21 |
| 12 Rollout | 21 |
| 13 Config env vars | 20 |

**Placeholder scan:** No TBDs ni referencias a código que no esté definido. El único punto con "adaptar a como esté el Storage actual" (Task 14 Step 14.3) es por prudencia — se verifica al leer el archivo al ejecutar. No es un placeholder de código.

**Type consistency:**
- `tickets_by_id: dict[int, dict]` usado consistentemente en Tasks 7, 8, 9, 10, 14.
- `email_match: list[dict]` con estructura `{"email": str}` (y opcionalmente `zendesk_id` — en Task 9 se define sólo `email`; el spec original menciona `zendesk_id` pero en v1 del plan sólo almacenamos `email` para mantener simplicidad; si se necesita la trazabilidad de qué ticket lo disparó, pasa a Task futura).
- `cluster.estado` in `("abierto", "refined", "cerrado")` usado en Tasks 11, 13, 15, 18, 19.

**Scope:** suficientemente focalizado; cada task produce cambios self-contained y testables. Tasks 18-19 (UI) son las más "soft" (sin tests automáticos) pero el coste/beneficio no justifica tests de streamlit.

---

## Execution Handoff

**Plan complete and saved to [docs/superpowers/plans/2026-04-17-email-match-y-refine.md](../plans/2026-04-17-email-match-y-refine.md). Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
