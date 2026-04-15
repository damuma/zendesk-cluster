import pytest
from unittest.mock import MagicMock, patch
from fase3_clusterizar import Fase3Clusterizador
from storage import Storage


def _make_openai_response(data: dict):
    import json
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(data)
    return mock_resp


@pytest.fixture
def tmp_clusterizador(tmp_path):
    storage = Storage(backend="json", data_dir=str(tmp_path))
    mock_jira = MagicMock()
    mock_jira.buscar_tickets_crm.return_value = []
    mock_openai = MagicMock()
    c = Fase3Clusterizador(storage=storage, jira=mock_jira, openai_client=mock_openai)
    return c, storage, mock_openai


def test_crear_nuevo_cluster(tmp_clusterizador):
    clusterizador, storage, mock_openai = tmp_clusterizador
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
    clusterizador, storage, mock_openai = tmp_clusterizador
    # Seed an existing cluster
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
    clusterizador, storage, _ = tmp_clusterizador
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    storage.save_cluster({"cluster_id": "CLU-003", "nombre": "x", "estado": "abierto", "created_at": now, "updated_at": now})
    storage.save_cluster({"cluster_id": "CLU-007", "nombre": "y", "estado": "abierto", "created_at": now, "updated_at": now})
    clusters = storage.get_clusters()
    assert clusterizador._next_cluster_id(clusters) == "CLU-008"


def test_jira_error_no_bloquea(tmp_clusterizador):
    clusterizador, storage, mock_openai = tmp_clusterizador
    clusterizador.jira.buscar_tickets_crm.side_effect = Exception("jira down")
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
