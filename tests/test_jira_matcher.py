import json
from unittest.mock import MagicMock
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
        _jira("TEC-2", summary="Login SSO office365"),
        _jira("TEC-3", summary="Stripe factura"),
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
    ids = [r["jira_id"] for r in result]
    assert "TEC-1" in ids
    assert "TEC-2" not in ids


def test_prefilter_orders_by_score_desc(matcher):
    cluster = _cluster(resumen="stripe cobro duplicado cliente", sistema="stripe")
    pool = [
        _jira("TEC-A", summary="stripe"),
        _jira("TEC-B", summary="stripe cobro duplicado cliente"),
        _jira("TEC-C", summary="stripe cobro"),
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
    ids = [r["jira_id"] for r in result]
    assert ids[0] == "TEC-B"


def test_prefilter_labels_weighted_double(matcher):
    cluster = _cluster(resumen="crm migracion", sistema="crm")
    pool = [
        _jira("TEC-A", summary="crm migracion", labels=[]),
        _jira("TEC-B", summary="crm migracion", labels=["CRM"]),
    ]
    result = matcher._prefilter_keywords(matcher._cluster_signals(cluster), pool, limit=15)
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
