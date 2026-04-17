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


def test_run_empty_pool_is_noop(env):
    storage, matcher = env
    _seed_cluster(storage, "CLU-001", [])
    stats = run(storage=storage, matcher=matcher, only_empty=False, cluster_id=None)
    assert stats["procesados"] == 0
    matcher.match.assert_not_called()
