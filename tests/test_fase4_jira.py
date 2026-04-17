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


def _seed_pool(storage):
    storage.save_jira_tickets(
        [{"jira_id": "TEC-9", "url": "", "summary": "", "description_text": "",
          "status": "Backlog", "status_category": "new", "priority": None,
          "issuetype": "Task", "labels": [], "components": [],
          "assignee": None, "created": "", "updated": ""}],
        {"project": "TEC", "fecha_inicio": "", "fecha_fin": "", "last_sync": "",
         "total_tickets": 1, "filtro": ""},
    )


def test_run_updates_all_clusters(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    _seed_cluster(storage, "CLU-002", ["TEC-1"])  # legacy string
    _seed_pool(storage)
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
    _seed_pool(storage)
    matcher.match.return_value = [
        {"jira_id": "TEC-9", "url": "u", "summary": "s", "status": "Backlog",
         "confianza": 0.9, "razon": "m"}
    ]
    stats = run(storage=storage, matcher=matcher, only_empty=True, cluster_id=None)
    assert stats["procesados"] == 1
    clusters = {c["cluster_id"]: c for c in storage.get_clusters()}
    assert clusters["CLU-001"]["jira_candidatos"][0]["jira_id"] == "TEC-9"
    assert clusters["CLU-002"]["jira_candidatos"][0]["jira_id"] == "TEC-3"


def test_run_single_cluster_id(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    _seed_cluster(storage, "CLU-002", [])
    _seed_pool(storage)
    matcher.match.return_value = []
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id="CLU-002")
    assert stats["procesados"] == 1


def test_run_passes_tickets_by_id_and_ticket_ids_to_matcher(env):
    storage, matcher = env
    # cluster con ticket_ids
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({
        "cluster_id": "CLU-001", "nombre": "x", "estado": "abierto",
        "created_at": now, "updated_at": now, "resumen": "x",
        "sistema": "s", "tipo_problema": "t", "ticket_ids": [10, 20],
        "jira_candidatos": [],
    })
    storage.save_ticket({"zendesk_id": 10, "emails_asociados": ["a@x.com"]})
    storage.save_ticket({"zendesk_id": 20, "emails_asociados": ["b@x.com"]})
    _seed_pool(storage)
    matcher.match.return_value = []
    run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    call = matcher.match.call_args
    preview = call.args[0]
    assert preview["ticket_ids"] == [10, 20]
    tbi = call.kwargs["tickets_by_id"]
    assert tbi[10]["emails_asociados"] == ["a@x.com"]
    assert tbi[20]["emails_asociados"] == ["b@x.com"]


def test_run_skips_refined_parents(env):
    storage, matcher = env
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({
        "cluster_id": "CLU-001", "nombre": "padre", "estado": "refined",
        "created_at": now, "updated_at": now, "resumen": "", "sistema": "",
        "tipo_problema": "", "ticket_ids": [], "jira_candidatos": [],
    })
    storage.save_cluster({
        "cluster_id": "CLU-001-A", "nombre": "hijo", "estado": "abierto",
        "parent_cluster_id": "CLU-001", "subtipo": "x",
        "created_at": now, "updated_at": now, "resumen": "r", "sistema": "s",
        "tipo_problema": "t", "ticket_ids": [], "jira_candidatos": [],
    })
    _seed_pool(storage)
    matcher.match.return_value = []
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    assert stats["procesados"] == 1  # sólo el hijo


def test_run_empty_pool_is_noop(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    assert stats["procesados"] == 0
    matcher.match.assert_not_called()
