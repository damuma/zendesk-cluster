from storage import Storage
from email_extract import extract_emails, INTERNAL_DOMAINS


class Fase2Preclasificador:
    def __init__(self, conceptos: dict = None):
        self._conceptos = conceptos

    def _get_conceptos(self) -> dict:
        return self._conceptos or Storage().get_conceptos()

    def preclasificar(self, ticket: dict) -> dict:
        conceptos = self._get_conceptos()
        texto = f"{ticket.get('subject', '')} {ticket.get('body_preview', '')}".lower()
        umbral = conceptos.get("umbral_ancla_directa", 2)

        # Detectar sistemas
        sistemas_detectados = []
        keywords_matched = []
        for sistema, config in conceptos.get("sistemas", {}).items():
            for kw in config.get("keywords", []):
                if kw.lower() in texto:
                    if sistema not in sistemas_detectados:
                        sistemas_detectados.append(sistema)
                    keywords_matched.append(kw)

        # Detectar tipo de problema
        tipo_detectado = None
        severidad = "MEDIUM"
        tipo_score = 0
        for tipo, config in conceptos.get("tipos_problema", {}).items():
            score = sum(1 for kw in config.get("keywords", []) if kw.lower() in texto)
            if score > tipo_score:
                tipo_score = score
                tipo_detectado = tipo
                severidad = config.get("severidad_default", "MEDIUM")

        score_ancla = len(keywords_matched) + (tipo_score * 1.5)

        # Cluster candidato si ancla fuerte
        cluster_candidato = None
        if score_ancla >= umbral and (sistemas_detectados or tipo_detectado):
            partes = []
            if sistemas_detectados:
                partes.append(sistemas_detectados[0])
            if tipo_detectado:
                partes.append(tipo_detectado)
            cluster_candidato = "_".join(partes).upper()

        texto_para_emails = f"{ticket.get('subject', '')} {ticket.get('body_preview', '')}"
        mencionados = extract_emails(texto_para_emails, exclude_domains=INTERNAL_DOMAINS)
        req_email = (ticket.get("requester_email") or "").lower().strip()
        asociados_set = set(mencionados)
        if req_email and "@" in req_email:
            # Excluir requester_email si pertenece a un dominio interno: el
            # ticket lo puede abrir un agente en nombre del cliente real, y
            # el email del cliente aparece en el body (ya capturado en
            # `mencionados`). Un email interno NO debe cruzarse con Jira.
            domain = req_email.rsplit("@", 1)[1]
            if domain not in INTERNAL_DOMAINS:
                asociados_set.add(req_email)
        emails_asociados = sorted(asociados_set)

        return {
            "anclas": {
                "sistemas": sistemas_detectados,
                "tipo_problema": tipo_detectado,
                "keywords_matched": keywords_matched,
            },
            "cluster_candidato": cluster_candidato,
            "score_ancla": score_ancla,
            "severidad_estimada": severidad,
            "emails_mencionados": mencionados,
            "emails_asociados": emails_asociados,
        }
