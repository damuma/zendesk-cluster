import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
from storage import Storage
from jira_client import JiraClient

load_dotenv()


class Fase3Clusterizador:
    def __init__(self, storage=None, jira=None, openai_client=None):
        self.openai = openai_client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.storage = storage or Storage()
        self.jira = jira or JiraClient()

    def _next_cluster_id(self, clusters: list[dict]) -> str:
        if not clusters:
            return "CLU-001"
        nums = []
        for c in clusters:
            try:
                nums.append(int(c["cluster_id"].split("-")[1]))
            except (IndexError, ValueError):
                pass
        return f"CLU-{(max(nums) + 1 if nums else 1):03d}"

    def clusterizar(self, ticket: dict) -> dict:
        clusters = self.storage.get_clusters(estado="abierto")
        conceptos = self.storage.get_conceptos()
        if not isinstance(conceptos, dict):
            conceptos = {}

        clusters_resumen = [
            {
                "cluster_id": c["cluster_id"],
                "nombre": c["nombre"],
                "sistema": c.get("sistema"),
                "tipo_problema": c.get("tipo_problema"),
                "resumen": c.get("resumen", ""),
                "ticket_count": c.get("ticket_count", 0),
            }
            for c in clusters
        ]

        sistemas = list(conceptos.get("sistemas", {}).keys())
        tipos = list(conceptos.get("tipos_problema", {}).keys())

        prompt = f"""Eres un sistema de clustering de incidencias técnicas de soporte.

CLUSTERS EXISTENTES ({len(clusters_resumen)} activos):
{json.dumps(clusters_resumen, ensure_ascii=False, indent=2)}

TAXONOMÍA DISPONIBLE:
Sistemas: {sistemas} (o NUEVO si no encaja)
Tipos: {tipos} (o NUEVO si no encaja)

TICKET A CLASIFICAR:
Asunto: {ticket.get('subject', '')}
Cuerpo: {ticket.get('body_preview', '')[:800]}

Responde SOLO con JSON válido:
{{
  "accion": "ASIGNAR_EXISTENTE" o "CREAR_NUEVO",
  "cluster_id": "CLU-XXX",
  "cluster_nuevo": {{
    "nombre": "...",
    "sistema": "...",
    "tipo_problema": "...",
    "severidad": "HIGH|MEDIUM|LOW",
    "resumen": "..."
  }},
  "confianza": 0.0-1.0,
  "keywords_detectados": [...],
  "jira_query": "texto para buscar en Jira"
}}
Si accion es ASIGNAR_EXISTENTE, cluster_nuevo puede ser null.
Si accion es CREAR_NUEVO, cluster_id puede ser null."""

        try:
            resp = self.openai.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            data = json.loads(resp.choices[0].message.content)
        except Exception:
            data = {"accion": "CREAR_NUEVO", "cluster_nuevo": None, "confianza": 0.0, "keywords_detectados": [], "jira_query": ""}

        jira_query = data.get("jira_query", " ".join(data.get("keywords_detectados", [])[:3]))
        jira_candidatos = []
        if jira_query:
            try:
                jira_results = self.jira.buscar_tickets_crm(jira_query)
                jira_candidatos = [r["jira_id"] for r in jira_results]
            except Exception:
                pass

        now = datetime.now(timezone.utc).isoformat()

        accion = data.get("accion", "CREAR_NUEVO")

        if accion == "ASIGNAR_EXISTENTE":
            cluster_id = data.get("cluster_id")
            cluster = next((c for c in clusters if c["cluster_id"] == cluster_id), None)
            if cluster:
                cluster["ticket_count"] = cluster.get("ticket_count", 0) + 1
                cluster["updated_at"] = now
                if ticket["zendesk_id"] not in cluster.get("ticket_ids", []):
                    cluster.setdefault("ticket_ids", []).append(ticket["zendesk_id"])
                existing_jira = set(cluster.get("jira_candidatos", []))
                cluster["jira_candidatos"] = list(existing_jira | set(jira_candidatos))
                self.storage.save_cluster(cluster)
            else:
                # cluster_id from LLM not found — create a new cluster instead
                accion = "CREAR_NUEVO"

        if accion == "CREAR_NUEVO":
            cluster_id = self._next_cluster_id(clusters)
            nuevo = data.get("cluster_nuevo") or {}
            cluster = {
                "cluster_id": cluster_id,
                "nombre": nuevo.get("nombre", f"Cluster {cluster_id}"),
                "sistema": nuevo.get("sistema"),
                "tipo_problema": nuevo.get("tipo_problema"),
                "severidad": nuevo.get("severidad", "MEDIUM"),
                "created_at": now,
                "updated_at": now,
                "ticket_count": 1,
                "ticket_ids": [ticket["zendesk_id"]],
                "jira_candidatos": jira_candidatos,
                "jira_vinculado": None,
                "estado": "abierto",
                "resumen": nuevo.get("resumen", ""),
                "tendencia": "nuevo",
            }
            self.storage.save_cluster(cluster)

        return {
            "cluster_id": cluster_id,
            "resumen_llm": cluster.get("resumen", ""),
            "severidad": cluster.get("severidad", "MEDIUM"),
            "jira_candidatos": jira_candidatos,
            "confianza": data.get("confianza", 0.0),
            "keywords_detectados": data.get("keywords_detectados", []),
        }
