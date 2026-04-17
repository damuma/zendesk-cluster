import pytest
from unittest.mock import MagicMock
from fase3_clusterizar import Fase3Clusterizador, _merge_jira_candidates
from storage import Storage


def test_merge_jira_candidates_caps_at_five_and_prefers_email_match():
    existing = [
        {"jira_id": "A", "confianza": 0.9},
        {"jira_id": "B", "confianza": 0.85},
        {"jira_id": "C", "confianza": 0.8},
        {"jira_id": "D", "confianza": 0.75},
        "LEGACY-1",
    ]
    nuevos = [
        {"jira_id": "E", "confianza": 0.7},
        {"jira_id": "F", "confianza": 0.6, "email_match": [{"email": "x@y.com", "zendesk_id": 1}]},
        {"jira_id": "A", "confianza": 0.95},  # override del existente
    ]
    merged = _merge_jira_candidates(existing, nuevos, cap=5)
    ids = [m["jira_id"] for m in merged]
    assert ids[0] == "F"  # único con email_match
    assert ids[1:5] == ["A", "B", "C", "D"]
    assert len(merged) == 5


def test_merge_jira_candidates_drops_legacy_strings():
    merged = _merge_jira_candidates(["TEC-1", "TEC-2"], [{"jira_id": "TEC-3", "confianza": 0.9}])
    assert [m["jira_id"] for m in merged] == ["TEC-3"]


def _make_openai_response(data: dict):
    import json
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(data)
    return mock_resp


@pytest.fixture
def tmp_clusterizador(tmp_path):
    storage = Storage(backend="json", data_dir=str(tmp_path))
    mock_matcher = MagicMock()
    mock_matcher.match.return_value = []
    mock_openai = MagicMock()
    c = Fase3Clusterizador(storage=storage, matcher=mock_matcher, openai_client=mock_openai)
    return c, storage, mock_openai, mock_matcher


def test_crear_nuevo_cluster(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "accion": "CREAR_NUEVO",
        "cluster_id": None,
        "cluster_nuevo": {
            "nombre": "Cobro doble Stripe",
            "sistema": "stripe",
            "tipo_problema": "cobro_indebido",
            "severidad": "HIGH",
            "resumen": "Clientes cobrados dos veces via Stripe",
        },
        "confianza": 0.92,
        "keywords_detectados": ["stripe", "cobro", "doble"],
        "jira_query": "stripe cobro doble",
    })
    ticket = {"zendesk_id": 1001, "subject": "Cobro doble", "body_preview": "Me han cobrado dos veces via stripe"}
    result = clusterizador.clusterizar(ticket)
    assert result["cluster_id"] == "CLU-001"
    assert result["severidad"] == "HIGH"
    assert result["confianza"] == 0.92
    clusters = storage.get_clusters()
    assert len(clusters) == 1
    assert clusters[0]["nombre"] == "Cobro doble Stripe"
    assert clusters[0]["ticket_count"] == 1


def test_asignar_existente_incrementa_contador(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({
        "cluster_id": "CLU-001",
        "nombre": "Cobro doble Stripe",
        "sistema": "stripe",
        "ticket_count": 3,
        "ticket_ids": [101, 102, 103],
        "jira_candidatos": [],
        "estado": "abierto",
        "created_at": now,
        "updated_at": now,
        "severidad": "HIGH",
        "resumen": "Cobros duplicados",
        "tendencia": "creciente",
    })
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "accion": "ASIGNAR_EXISTENTE",
        "cluster_id": "CLU-001",
        "cluster_nuevo": None,
        "confianza": 0.88,
        "keywords_detectados": ["stripe", "cobro"],
        "jira_query": "stripe cobro",
    })
    ticket = {"zendesk_id": 104, "subject": "Otro cobro doble", "body_preview": "Stripe me cobró otra vez"}
    result = clusterizador.clusterizar(ticket)
    assert result["cluster_id"] == "CLU-001"
    clusters = storage.get_clusters()
    assert clusters[0]["ticket_count"] == 4
    assert 104 in clusters[0]["ticket_ids"]


def test_next_cluster_id_increments(tmp_clusterizador):
    clusterizador, storage, _, _ = tmp_clusterizador
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({"cluster_id": "CLU-003", "nombre": "x", "estado": "abierto", "created_at": now, "updated_at": now})
    storage.save_cluster({"cluster_id": "CLU-007", "nombre": "y", "estado": "abierto", "created_at": now, "updated_at": now})
    clusters = storage.get_clusters()
    assert clusterizador._next_cluster_id(clusters) == "CLU-008"


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


def test_phantom_cluster_falls_through_to_crear_nuevo(tmp_clusterizador):
    """If LLM returns ASIGNAR_EXISTENTE with a non-existent cluster_id, create a new cluster."""
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "accion": "ASIGNAR_EXISTENTE",
        "cluster_id": "CLU-999",
        "cluster_nuevo": None,
        "confianza": 0.75,
        "keywords_detectados": ["stripe"],
        "jira_query": "stripe",
    })
    ticket = {"zendesk_id": 5001, "subject": "Stripe error", "body_preview": "Stripe no funciona"}
    result = clusterizador.clusterizar(ticket)
    clusters = storage.get_clusters()
    assert len(clusters) == 1
    assert result["cluster_id"] != "CLU-999"
    assert clusters[0]["ticket_ids"] == [5001]


def test_missing_accion_key_falls_through_to_crear_nuevo(tmp_clusterizador):
    """If GPT-4o returns JSON without 'accion', treat as CREAR_NUEVO."""
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    mock_openai.chat.completions.create.return_value = _make_openai_response({
        "cluster_nuevo": {"nombre": "Error login", "sistema": "auth", "tipo_problema": "login", "severidad": "MEDIUM", "resumen": ""},
        "confianza": 0.6,
        "keywords_detectados": ["login"],
        "jira_query": "login",
    })
    ticket = {"zendesk_id": 6001, "subject": "No puedo entrar", "body_preview": "Login falla"}
    result = clusterizador.clusterizar(ticket)
    clusters = storage.get_clusters()
    assert len(clusters) == 1
    assert result["cluster_id"] == "CLU-001"


def test_matcher_candidates_saved_on_cluster(tmp_clusterizador):
    clusterizador, storage, mock_openai, mock_matcher = tmp_clusterizador
    # Seed a jira ticket so pool is non-empty (matcher gate)
    storage.save_jira_tickets(
        [{"jira_id": "TEC-1", "url": "u", "summary": "s", "description_text": "",
          "status": "Backlog", "status_category": "new", "priority": None,
          "issuetype": "Task", "labels": [], "components": [], "assignee": None,
          "created": "", "updated": ""}],
        {"project": "TEC", "fecha_inicio": "", "fecha_fin": "", "last_sync": "",
         "total_tickets": 1, "filtro": ""},
    )
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
