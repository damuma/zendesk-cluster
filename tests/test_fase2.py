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

def test_fase2_extrae_emails_mencionados_y_asociados(clasificador):
    t = {
        "subject": "no puedo acceder",
        "body_preview": "Mi cuenta buyer@gmail.com. Confusión con otro: other@gmail.com.",
        "requester_email": "buyer@gmail.com",
    }
    r = clasificador.preclasificar(t)
    assert r["emails_mencionados"] == ["buyer@gmail.com", "other@gmail.com"]
    assert r["emails_asociados"] == ["buyer@gmail.com", "other@gmail.com"]


def test_fase2_emails_asociados_sin_requester_email(clasificador):
    t = {"subject": "x", "body_preview": "contacto foo@bar.com"}
    r = clasificador.preclasificar(t)
    assert r["emails_mencionados"] == ["foo@bar.com"]
    assert r["emails_asociados"] == ["foo@bar.com"]


def test_fase2_filtra_dominios_internos(clasificador):
    t = {
        "subject": "x",
        "body_preview": "Agente soporte@eldiario.es responde a cliente@gmail.com.",
        "requester_email": None,
    }
    r = clasificador.preclasificar(t)
    assert r["emails_mencionados"] == ["cliente@gmail.com"]
    assert r["emails_asociados"] == ["cliente@gmail.com"]


def test_fase2_excluye_requester_email_interno(clasificador):
    """Si el ticket lo abre un agente (email interno), no debe aparecer en
    emails_asociados aunque sea el requester — el email del cliente está
    en el body y se captura por `mencionados`."""
    t = {
        "subject": "cliente reporta fallo",
        "body_preview": "El socio cliente@gmail.com no puede acceder.",
        "requester_email": "contacto@eldiario.es",
    }
    r = clasificador.preclasificar(t)
    assert r["emails_mencionados"] == ["cliente@gmail.com"]
    assert r["emails_asociados"] == ["cliente@gmail.com"]
    # El requester_email interno NO se mezcla con los asociados.
    assert "contacto@eldiario.es" not in r["emails_asociados"]


def test_fase2_requester_email_externo_si_se_incluye(clasificador):
    t = {
        "subject": "x",
        "body_preview": "",
        "requester_email": "cliente@gmail.com",
    }
    r = clasificador.preclasificar(t)
    assert r["emails_asociados"] == ["cliente@gmail.com"]


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
