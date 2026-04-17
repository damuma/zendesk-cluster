import json
from unittest.mock import MagicMock

import pytest

from fase35_refine import (
    heterogeneity_score,
    should_refine,
    split_cluster,
    apply_split,
    run_refine,
)
from storage import Storage


# ── heuristics ─────────────────────────────────────────────
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


def test_heterogeneity_empty_tickets():
    assert heterogeneity_score([]) == 0.0


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


# ── split_cluster ──────────────────────────────────────────
def _fake_openai_with_subgroups(subgroups):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"subgrupos": subgroups})))]
    )
    return client


def test_split_cluster_calls_llm_and_parses():
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


def test_split_cluster_fallback_on_model_error():
    client = MagicMock()
    call_count = {"n": 0}

    def side_effect(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("model not found")
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({"subgrupos": [
            {"subtipo": "t", "nombre": "n", "resumen": "r", "ticket_ids": [1]}
        ]})))])

    client.chat.completions.create.side_effect = side_effect
    subs = split_cluster(
        [{"zendesk_id": 1, "subject": "x", "body_preview": ""}],
        openai_client=client,
        model="gpt-5.4",
    )
    assert len(subs) == 1
    assert client.chat.completions.create.call_args_list[1].kwargs.get("model") == "gpt-4o"


# ── apply_split ────────────────────────────────────────────
def test_apply_split_creates_children_and_marks_parent():
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
    assert parent["estado"] == "refined"
    assert parent["ticket_ids"] == []
    assert parent["jira_candidatos"] == []
    assert parent["refined_at"] == "2026-04-17T10:00:00Z"


def test_apply_split_single_group_no_op():
    parent = {
        "cluster_id": "CLU-8", "estado": "abierto", "ticket_ids": [1, 2],
        "jira_candidatos": [], "ticket_count": 2, "sistema": "x",
        "tipo_problema": "y", "severidad": "LOW", "nombre": "n",
    }
    subgrupos = [{"subtipo": "s", "nombre": "n", "resumen": "r", "ticket_ids": [1, 2]}]
    children = apply_split(parent, subgrupos, now="2026-04-17T10:00:00Z")
    assert children == []
    assert parent["estado"] == "abierto"
    assert parent["refined_at"] == "2026-04-17T10:00:00Z"
    assert parent["ticket_ids"] == [1, 2]


# ── run_refine ─────────────────────────────────────────────
@pytest.fixture
def storage(tmp_path):
    return Storage(backend="json", data_dir=str(tmp_path))


def test_run_refine_integra_todo(storage):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    # 1 cluster grande, 1 pequeño homogéneo
    storage.save_cluster({
        "cluster_id": "CLU-001", "nombre": "Grande", "sistema": "auth_login",
        "tipo_problema": "error_acceso", "severidad": "HIGH", "estado": "abierto",
        "ticket_ids": [1, 2, 3, 4, 5], "ticket_count": 20,
        "jira_candidatos": [], "created_at": now, "updated_at": now,
    })
    storage.save_cluster({
        "cluster_id": "CLU-002", "nombre": "Pequeño", "sistema": "crm",
        "tipo_problema": "error_estado", "severidad": "LOW", "estado": "abierto",
        "ticket_ids": [100], "ticket_count": 1, "jira_candidatos": [],
        "created_at": now, "updated_at": now,
    })
    for i in range(1, 6):
        storage.save_ticket({"zendesk_id": i, "subject": f"t{i}", "body_preview": "x",
                             "anclas": {"sistemas": ["auth_login"]}})
    storage.save_ticket({"zendesk_id": 100, "subject": "crm", "body_preview": "y",
                         "anclas": {"sistemas": ["crm"]}})
    storage.save_jira_tickets([], {"project": "TEC", "fecha_inicio": "", "fecha_fin": "",
                                   "last_sync": "", "total_tickets": 0, "filtro": ""})

    subs = [
        {"subtipo": "a", "nombre": "A", "resumen": "ra", "ticket_ids": [1, 2, 3]},
        {"subtipo": "b", "nombre": "B", "resumen": "rb", "ticket_ids": [4, 5]},
    ]
    openai = _fake_openai_with_subgroups(subs)
    matcher = MagicMock()
    matcher.match.return_value = []

    stats = run_refine(
        openai_client=openai, matcher=matcher, storage=storage,
        model="gpt-4o", min_tickets=15, het_min=0.5,
    )
    assert stats["clusters_refined"] == 1
    assert stats["children_created"] == 2
    saved = storage.get_clusters()
    ids = {c["cluster_id"] for c in saved}
    assert "CLU-001-A" in ids
    assert "CLU-001-B" in ids
    parent = next(c for c in saved if c["cluster_id"] == "CLU-001")
    assert parent["estado"] == "refined"
    small = next(c for c in saved if c["cluster_id"] == "CLU-002")
    assert small["estado"] == "abierto"
