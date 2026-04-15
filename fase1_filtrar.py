import os
import json
import ollama as ollama_client
from dotenv import load_dotenv

load_dotenv()

class Fase1Filtrador:
    def __init__(self, conceptos: dict = None):
        self.conceptos = conceptos
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "gemma2:9b")
        self.ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def _get_conceptos(self) -> dict:
        if self.conceptos:
            return self.conceptos
        from storage import Storage
        return Storage().get_conceptos()

    def clasificar(self, ticket: dict) -> dict:
        conceptos = self._get_conceptos()
        config = conceptos.get("filtrado_tecnico", {})
        texto = f"{ticket.get('subject', '')} {ticket.get('body_preview', '')}".lower()

        # 1. Señales negativas fuertes → DESCARTADO
        for ind in config.get("indicadores_no_tecnico", []):
            if ind.lower() in texto:
                return {"resultado": "DESCARTADO", "confianza": 0.95, "metodo": "reglas", "indicador": ind}

        # 2. Señales positivas fuertes → TECNICO
        matches = [ind for ind in config.get("indicadores_tecnico", []) if ind.lower() in texto]
        if len(matches) >= 1:
            return {"resultado": "TECNICO", "confianza": min(0.90 + 0.02 * len(matches), 0.99), "metodo": "reglas", "indicadores": matches}

        # 3. Zona gris → Ollama
        return self._clasificar_ollama(ticket, config.get("umbral_confianza_ollama", 0.65))

    def _clasificar_ollama(self, ticket: dict, umbral: float) -> dict:
        prompt = f"""Eres un clasificador de tickets de soporte de un medio de comunicación.
Determina si este ticket es consecuencia de un ERROR TÉCNICO del sistema (CRM, pagos, acceso web)
o es una petición voluntaria del usuario (baja, consulta, cambio de datos).

Responde SOLO con JSON válido, sin texto adicional:
{{"tipo": "TECNICO" o "NO_TECNICO", "confianza": 0.0-1.0, "razon": "una frase"}}

Asunto: {ticket.get('subject', '')}
Cuerpo: {ticket.get('body_preview', '')[:500]}"""

        try:
            resp = ollama_client.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp["message"]["content"].strip()
            import re
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                raise ValueError(f"No JSON in response: {raw}")
            data = json.loads(match.group())
            tipo = data.get("tipo", "NO_TECNICO")
            confianza = float(data.get("confianza", 0.5))
            resultado = "TECNICO" if tipo == "TECNICO" and confianza >= umbral else "DESCARTADO"
            return {"resultado": resultado, "confianza": confianza, "metodo": "ollama", "razon": data.get("razon", "")}
        except Exception as e:
            return {"resultado": "DESCARTADO", "confianza": 0.0, "metodo": "ollama_error", "error": str(e)}
