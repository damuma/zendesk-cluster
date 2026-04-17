import json
from unittest.mock import MagicMock

from jira_matcher import JiraMatcher


def _fake_openai_with_response(matches):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"matches": matches})))]
    )
    return client


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


def test_cluster_email_sources_maps_to_zendesk_ids():
    m = JiraMatcher(api_key=None)
    cluster = {"ticket_ids": [1, 2, 3]}
    tickets_by_id = {
        1: {"emails_asociados": ["a@x.com"]},
        2: {"emails_asociados": ["b@x.com", "a@x.com"]},
        3: {"emails_asociados": []},
    }
    sources = m._cluster_email_sources(cluster, tickets_by_id)
    assert set(sources.keys()) == {"a@x.com", "b@x.com"}
    assert sources["a@x.com"] == [1, 2]
    assert sources["b@x.com"] == [2]


def test_match_includes_email_candidate_even_if_keyword_score_zero():
    cluster = {
        "resumen": "error de suscripción",
        "anclas": {"sistemas": ["crm"], "tipo_problema": "error_estado"},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["mabro96@gmail.com"]}}
    # TEC-3091 comparte keyword "crm" con el cluster (vía summary); TEC-9999 no.
    jira_pool = [
        {"jira_id": "TEC-3091", "summary": "crm suscripcion regalo",
         "description_text": "mabro96@gmail.com beneficiario", "url": "u", "labels": []},
        {"jira_id": "TEC-9999", "summary": "totalmente aparte",
         "description_text": "sin emails", "url": "u", "labels": []},
    ]
    openai = _fake_openai_with_response([
        {"jira_id": "TEC-3091", "confianza": 0.8, "razon": "coincide concepto"},
    ])
    m = JiraMatcher(openai_client=openai)
    result = m.match(cluster, jira_pool, top_k=5, tickets_by_id=tickets_by_id)
    assert any(r["jira_id"] == "TEC-3091" for r in result)
    boosted = next(r for r in result if r["jira_id"] == "TEC-3091")
    assert boosted["confianza"] >= 0.95
    assert boosted["email_match"] == [{"email": "mabro96@gmail.com", "zendesk_id": 1}]
    assert "email de usuario" in boosted["razon"]


def test_match_email_augments_even_if_keywords_dont_match():
    """Aunque el keyword prefilter no capture el Jira, el email lo mete en
    los candidatos."""
    cluster = {
        "resumen": "problema de login",
        "anclas": {"sistemas": ["auth_login"]},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["shared@x.com"]}}
    # El Jira no tiene las keywords del cluster, pero sí el email.
    jira_pool = [
        {"jira_id": "TEC-X", "summary": "problema login totalmente distinto",
         "description_text": "shared@x.com", "url": "u", "labels": []},
    ]
    openai = _fake_openai_with_response([
        {"jira_id": "TEC-X", "confianza": 0.7, "razon": "login + mismo user"},
    ])
    m = JiraMatcher(openai_client=openai)
    result = m.match(cluster, jira_pool, top_k=5, tickets_by_id=tickets_by_id)
    ids = {r["jira_id"] for r in result}
    assert "TEC-X" in ids


def test_match_email_match_ignored_if_llm_rejects():
    """LLM puede descartar aunque haya email (mismo cliente, otro problema)."""
    cluster = {
        "resumen": "error de login",
        "anclas": {"sistemas": ["auth_login"]},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["shared@x.com"]}}
    jira_pool = [
        {"jira_id": "TEC-X", "summary": "error login", "description_text": "shared@x.com",
         "url": "u", "labels": []},
        {"jira_id": "TEC-Y", "summary": "auth_login problema otro",
         "description_text": "shared@x.com cuenta bloqueada", "url": "u", "labels": []},
    ]
    # LLM confirma sólo TEC-X. TEC-Y tenía email pero no concepto.
    openai = _fake_openai_with_response([
        {"jira_id": "TEC-X", "confianza": 0.9, "razon": "login ok"},
    ])
    m = JiraMatcher(openai_client=openai)
    result = m.match(cluster, jira_pool, top_k=5, tickets_by_id=tickets_by_id)
    ids = {r["jira_id"] for r in result}
    assert "TEC-X" in ids
    assert "TEC-Y" not in ids


def test_match_no_email_no_keywords_returns_empty():
    cluster = {"resumen": "", "anclas": {}, "ticket_ids": []}
    m = JiraMatcher(api_key=None)
    assert m.match(cluster, [{"jira_id": "T", "summary": "", "description_text": "",
                             "url": "u", "labels": []}]) == []


def test_match_sin_llm_marca_email_match_con_confianza_0_9():
    cluster = {
        "resumen": "algo",
        "anclas": {"sistemas": ["x"]},
        "ticket_ids": [1],
    }
    tickets_by_id = {1: {"emails_asociados": ["user@x.com"]}}
    jira_pool = [
        {"jira_id": "TEC-EMAIL", "summary": "x", "description_text": "user@x.com",
         "url": "u", "labels": []},
    ]
    m = JiraMatcher(api_key=None)  # sin LLM
    result = m.match(cluster, jira_pool, tickets_by_id=tickets_by_id)
    assert len(result) == 1
    assert result[0]["confianza"] == 0.9
    assert result[0]["email_match"] == [{"email": "user@x.com", "zendesk_id": 1}]
    assert "email match" in result[0]["razon"]
