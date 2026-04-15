import pytest
from unittest.mock import patch, MagicMock
from fase1_filtrar import Fase1Filtrador

CONCEPTOS = {
    "filtrado_tecnico": {
        "indicadores_tecnico": ["error", "no funciona", "cobrado dos veces", "no puedo entrar"],
        "indicadores_no_tecnico": ["quiero darme de baja", "solicito baja", "información sobre"],
        "umbral_confianza_ollama": 0.65,
    }
}

@pytest.fixture
def filtrador():
    return Fase1Filtrador(conceptos=CONCEPTOS)

def test_clasifica_tecnico_por_reglas(filtrador):
    ticket = {"subject": "Error al iniciar sesión", "body_preview": "No puedo entrar a mi cuenta, me da error"}
    result = filtrador.clasificar(ticket)
    assert result["resultado"] == "TECNICO"
    assert result["metodo"] == "reglas"
    assert result["confianza"] >= 0.9

def test_clasifica_no_tecnico_por_reglas(filtrador):
    ticket = {"subject": "Baja", "body_preview": "Quiero darme de baja de la suscripción"}
    result = filtrador.clasificar(ticket)
    assert result["resultado"] == "DESCARTADO"
    assert result["metodo"] == "reglas"

def test_clasifica_tecnico_doble_cobro(filtrador):
    ticket = {"subject": "Cobro duplicado", "body_preview": "Me han cobrado dos veces este mes"}
    result = filtrador.clasificar(ticket)
    assert result["resultado"] == "TECNICO"

def test_resultado_tiene_campos_requeridos(filtrador):
    ticket = {"subject": "Consulta", "body_preview": "Tengo una pregunta sobre mi cuenta"}
    result = filtrador.clasificar(ticket)
    assert "resultado" in result
    assert "confianza" in result
    assert "metodo" in result

def test_ollama_error_cae_a_descartado(filtrador):
    """Si Ollama falla, el ticket debe caer a DESCARTADO (safe default)."""
    with patch("fase1_filtrar.ollama_client.chat", side_effect=Exception("ollama not available")):
        ticket = {"subject": "Consulta", "body_preview": "Tengo una pregunta sobre mi cuenta"}
        result = filtrador.clasificar(ticket)
    assert result["resultado"] == "DESCARTADO"
    assert result["metodo"] == "ollama_error"
