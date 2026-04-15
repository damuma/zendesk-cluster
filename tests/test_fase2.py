import pytest
from fase2_preclasificar import Fase2Preclasificador

CONCEPTOS = {
    "sistemas": {
        "stripe": {"keywords": ["stripe", "tarjeta", "visa"]},
        "sepa_iban": {"keywords": ["iban", "domiciliación", "domiciliacion"]},
    },
    "tipos_problema": {
        "cobro_indebido": {"keywords": ["cobrado dos veces", "doble cobro"], "severidad_default": "HIGH"},
        "error_acceso": {"keywords": ["no puedo entrar", "contraseña"], "severidad_default": "MEDIUM"},
    },
    "umbral_ancla_directa": 2,
}

@pytest.fixture
def clasificador():
    return Fase2Preclasificador(conceptos=CONCEPTOS)

def test_detecta_sistema_stripe(clasificador):
    ticket = {"subject": "Cobro Stripe", "body_preview": "Me han cobrado dos veces via stripe con mi tarjeta"}
    result = clasificador.preclasificar(ticket)
    assert "stripe" in result["anclas"]["sistemas"]

def test_detecta_tipo_cobro_indebido(clasificador):
    ticket = {"subject": "Doble cobro", "body_preview": "me han cobrado dos veces este mes"}
    result = clasificador.preclasificar(ticket)
    assert result["anclas"]["tipo_problema"] == "cobro_indebido"

def test_ancla_fuerte_asigna_cluster_directo(clasificador):
    ticket = {"subject": "Stripe cobro doble", "body_preview": "stripe cobrado dos veces tarjeta visa"}
    result = clasificador.preclasificar(ticket)
    assert result["score_ancla"] >= 2
    assert result["cluster_candidato"] is not None

def test_ticket_ambiguo_no_tiene_cluster(clasificador):
    ticket = {"subject": "Problema", "body_preview": "tengo un problema con mi cuenta no sé qué pasa"}
    result = clasificador.preclasificar(ticket)
    assert result["cluster_candidato"] is None

def test_severidad_alta_en_cobro_indebido(clasificador):
    ticket = {"subject": "Doble cobro", "body_preview": "cobrado dos veces en domiciliación iban"}
    result = clasificador.preclasificar(ticket)
    assert result["severidad_estimada"] == "HIGH"
