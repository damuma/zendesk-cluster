# Zendesk Triage PoC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python/Streamlit proof-of-concept that pulls Zendesk tickets, classifies them through a 3-phase funnel (rules → local LLM → GPT-4o), clusters them by technical problem type, and presents an interactive panel.

**Architecture:** Modular pipeline (fase0→fase1→fase2→fase3) with a clean storage abstraction (JSON for PoC, PostgreSQL-ready). Each phase is an independent script. Streamlit reads from storage and renders three views: cluster list, cluster drill-down, and taxonomy explorer.

**Tech Stack:** Python 3.12, spaCy es_core_news_lg, sentence-transformers, Ollama (Gemma2 9B), OpenAI GPT-4o, Streamlit, python-dotenv, requests

---

## File Map

| File | Responsibility |
|------|---------------|
| `zendesk_client.py` | Zendesk REST API v2 wrapper — fetch tickets by date range |
| `jira_client.py` | Jira REST API v3 wrapper — JQL search for TEC project |
| `storage.py` | Read/write JSON files in `data/` (later: PostgreSQL) |
| `fase0_explorar.py` | Download sample, run spaCy NLP, generate `data/conceptos.json` |
| `fase1_filtrar.py` | Rule-based + Ollama filter: TECNICO vs DESCARTADO |
| `fase2_preclasificar.py` | Keyword anchor matching → direct cluster assignment |
| `fase3_clusterizar.py` | GPT-4o clustering for ambiguous tickets |
| `pipeline.py` | Orchestrates phases 1-3 for a batch of tickets |
| `app.py` | Streamlit entry point, sidebar navigation |
| `views/clusters.py` | Cluster list view with severity badges |
| `views/detalle_cluster.py` | Drill-down view: tickets + Jira candidates |
| `views/explorar.py` | Taxonomy viewer + pipeline stats |
| `tests/test_fase1.py` | Tests for the filtrado logic |
| `tests/test_fase2.py` | Tests for the anchor matching logic |
| `tests/test_storage.py` | Tests for storage read/write |

---

## Task 1: Project setup and environment

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `data/.gitkeep`
- Create: `.gitignore`

- [ ] **Step 1.1: Create project directory**

```bash
mkdir -p zendesk-cluster/data zendesk-cluster/views zendesk-cluster/tests
cd zendesk-cluster
python3.12 -m venv venv
source venv/bin/activate
```

- [ ] **Step 1.2: Create requirements.txt**

```
python-dotenv==1.0.1
requests==2.32.3
spacy==3.7.6
sentence-transformers==3.0.1
umap-learn==0.5.6
hdbscan==0.8.38.post1
openai==1.50.0
ollama==0.3.3
pandas==2.2.3
streamlit==1.39.0
psycopg2-binary==2.9.9
pytest==8.3.3
```

- [ ] **Step 1.3: Create .env.example**

```env
ZENDESK_SUBDOMAIN=eldiarioeshelp
ZENDESK_EMAIL=tu@eldiario.es
ZENDESK_API_TOKEN=xxx

OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o

JIRA_HOST=eldiario.atlassian.net
JIRA_EMAIL=tu@eldiario.es
JIRA_TOKEN=xxx
JIRA_PROJECT=TEC

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma2:9b

STORAGE_BACKEND=json
DATA_DIR=./data
```

- [ ] **Step 1.4: Create .gitignore**

```
.env
data/*.json
venv/
__pycache__/
.pytest_cache/
*.pyc
```

- [ ] **Step 1.5: Copy .env.example to .env and fill credentials**

```bash
cp .env.example .env
# Edit .env with real Zendesk token, OpenAI key, Jira token
```

- [ ] **Step 1.6: Install dependencies and spaCy model**

```bash
pip install -r requirements.txt
python -m spacy download es_core_news_lg
ollama pull gemma2:9b
```

Expected output: `✓ es_core_news_lg` and `success` from ollama.

- [ ] **Step 1.7: Commit**

```bash
git add requirements.txt .env.example .gitignore data/.gitkeep views/ tests/
git commit -m "feat: zendesk-triage project scaffold"
```

---

## Task 2: Zendesk API client

**Files:**
- Create: `zendesk_client.py`
- Create: `tests/test_zendesk_client.py`

- [ ] **Step 2.1: Write failing test**

```python
# tests/test_zendesk_client.py
import os
from unittest.mock import patch, MagicMock
from zendesk_client import ZendeskClient

def test_get_tickets_returns_list():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "tickets": [{"id": 1, "subject": "Test", "description": "body"}],
            "next_page": None
        }
        mock_get.return_value.status_code = 200
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        tickets = client.get_tickets(days_back=1)
    assert isinstance(tickets, list)
    assert tickets[0]["id"] == 1

def test_get_ticket_single():
    with patch("zendesk_client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "ticket": {"id": 42, "subject": "Single", "description": "body"}
        }
        mock_get.return_value.status_code = 200
        client = ZendeskClient(subdomain="test", email="a@b.com", token="tok")
        ticket = client.get_ticket(42)
    assert ticket["id"] == 42
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
pytest tests/test_zendesk_client.py -v
```
Expected: `ModuleNotFoundError: No module named 'zendesk_client'`

- [ ] **Step 2.3: Implement zendesk_client.py**

```python
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

class ZendeskClient:
    def __init__(self, subdomain=None, email=None, token=None):
        self.subdomain = subdomain or os.environ["ZENDESK_SUBDOMAIN"]
        self.email = email or os.environ["ZENDESK_EMAIL"]
        self.token = token or os.environ["ZENDESK_API_TOKEN"]
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        self.auth = (f"{self.email}/token", self.token)

    def get_tickets(self, days_back: int = 30) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        tickets = []
        url = f"{self.base_url}/tickets.json?created_after={since_str}&sort_by=created_at&sort_order=asc"
        while url:
            resp = requests.get(url, auth=self.auth)
            resp.raise_for_status()
            data = resp.json()
            tickets.extend(data.get("tickets", []))
            url = data.get("next_page")
            if url:
                time.sleep(0.1)  # respect rate limit
        return [self._normalize(t) for t in tickets]

    def get_tickets_since(self, since_hours: int = 24) -> list[dict]:
        return self.get_tickets(days_back=since_hours / 24)

    def get_ticket(self, ticket_id: int) -> dict:
        resp = requests.get(f"{self.base_url}/tickets/{ticket_id}.json", auth=self.auth)
        resp.raise_for_status()
        return self._normalize(resp.json()["ticket"])

    def _normalize(self, t: dict) -> dict:
        return {
            "zendesk_id": t["id"],
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "subject": t.get("subject", ""),
            "body_preview": (t.get("description") or "")[:1000],
            "status": t.get("status"),
            "channel": t.get("via", {}).get("channel", "unknown"),
            "tags": t.get("tags", []),
        }
```

- [ ] **Step 2.4: Run tests**

```bash
pytest tests/test_zendesk_client.py -v
```
Expected: `2 passed`

- [ ] **Step 2.5: Smoke test against real Zendesk**

```bash
python -c "
from zendesk_client import ZendeskClient
c = ZendeskClient()
tickets = c.get_tickets(days_back=1)
print(f'Got {len(tickets)} tickets')
print(tickets[0] if tickets else 'No tickets today')
"
```

- [ ] **Step 2.6: Commit**

```bash
git add zendesk_client.py tests/test_zendesk_client.py
git commit -m "feat: zendesk API client with pagination and normalization"
```

---

## Task 3: Storage layer (JSON)

**Files:**
- Create: `storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_storage.py
import json
import os
import pytest
from pathlib import Path
from storage import Storage

@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(backend="json", data_dir=str(tmp_path))

def test_save_and_get_ticket(tmp_storage):
    ticket = {"zendesk_id": 1, "subject": "Test", "fase1_resultado": "TECNICO"}
    tmp_storage.save_ticket(ticket)
    tickets = tmp_storage.get_tickets()
    assert len(tickets) == 1
    assert tickets[0]["zendesk_id"] == 1

def test_save_and_get_cluster(tmp_storage):
    cluster = {"cluster_id": "CLU-001", "nombre": "Test cluster", "estado": "abierto"}
    tmp_storage.save_cluster(cluster)
    clusters = tmp_storage.get_clusters()
    assert len(clusters) == 1
    assert clusters[0]["cluster_id"] == "CLU-001"

def test_get_clusters_filters_by_estado(tmp_storage):
    tmp_storage.save_cluster({"cluster_id": "CLU-001", "estado": "abierto"})
    tmp_storage.save_cluster({"cluster_id": "CLU-002", "estado": "cerrado"})
    abiertos = tmp_storage.get_clusters(estado="abierto")
    assert len(abiertos) == 1
    assert abiertos[0]["cluster_id"] == "CLU-001"

def test_save_and_get_conceptos(tmp_storage):
    conceptos = {"version": "1.0", "sistemas": {"stripe": {"keywords": ["stripe"]}}}
    tmp_storage.save_conceptos(conceptos)
    loaded = tmp_storage.get_conceptos()
    assert loaded["version"] == "1.0"
    assert "stripe" in loaded["sistemas"]
```

- [ ] **Step 3.2: Run to verify failure**

```bash
pytest tests/test_storage.py -v
```
Expected: `ModuleNotFoundError: No module named 'storage'`

- [ ] **Step 3.3: Implement storage.py**

```python
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Storage:
    def __init__(self, backend=None, data_dir=None):
        self.backend = backend or os.environ.get("STORAGE_BACKEND", "json")
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR", "./data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── JSON helpers ──────────────────────────────────────────
    def _read(self, filename: str) -> list | dict:
        path = self.data_dir / filename
        if not path.exists():
            return [] if filename.endswith("s.json") else {}
        with open(path) as f:
            return json.load(f)

    def _write(self, filename: str, data: list | dict) -> None:
        with open(self.data_dir / filename, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # ── Tickets ───────────────────────────────────────────────
    def get_tickets(self, filters: dict = None) -> list[dict]:
        tickets = self._read("tickets.json")
        if filters:
            for key, val in filters.items():
                tickets = [t for t in tickets if t.get(key) == val]
        return tickets

    def save_ticket(self, ticket: dict) -> None:
        tickets = self._read("tickets.json")
        existing_ids = {t["zendesk_id"] for t in tickets}
        if ticket["zendesk_id"] in existing_ids:
            tickets = [t if t["zendesk_id"] != ticket["zendesk_id"] else ticket for t in tickets]
        else:
            tickets.append(ticket)
        self._write("tickets.json", tickets)

    # ── Clusters ──────────────────────────────────────────────
    def get_clusters(self, estado: str = None) -> list[dict]:
        clusters = self._read("clusters.json")
        if estado:
            clusters = [c for c in clusters if c.get("estado") == estado]
        return clusters

    def save_cluster(self, cluster: dict) -> None:
        clusters = self._read("clusters.json")
        existing_ids = {c["cluster_id"] for c in clusters}
        if cluster["cluster_id"] in existing_ids:
            clusters = [c if c["cluster_id"] != cluster["cluster_id"] else cluster for c in clusters]
        else:
            clusters.append(cluster)
        self._write("clusters.json", clusters)

    def get_cluster_tickets(self, cluster_id: str) -> list[dict]:
        return self.get_tickets(filters={"fase3_cluster_id": cluster_id})

    # ── Conceptos ─────────────────────────────────────────────
    def get_conceptos(self) -> dict:
        return self._read("conceptos.json")

    def save_conceptos(self, conceptos: dict) -> None:
        self._write("conceptos.json", conceptos)
```

- [ ] **Step 3.4: Run tests**

```bash
pytest tests/test_storage.py -v
```
Expected: `4 passed`

- [ ] **Step 3.5: Commit**

```bash
git add storage.py tests/test_storage.py
git commit -m "feat: JSON storage layer with save/get for tickets, clusters, conceptos"
```

---

## Task 4: Fase 0 — Exploración y generación de conceptos.json

**Files:**
- Create: `fase0_explorar.py`

- [ ] **Step 4.1: Create fase0_explorar.py**

```python
#!/usr/bin/env python3
"""
Fase 0: Exploración NLP de tickets históricos.
Genera data/conceptos.json con taxonomía de señales.

Uso:
    python fase0_explorar.py --days 30
    python fase0_explorar.py --days 7 --output data/conceptos_test.json
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import spacy
from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from storage import Storage

load_dotenv()

# Señales de partida (semillas conocidas — se enriquecen con NLP)
SEMILLAS_TECNICO = [
    "error", "no funciona", "no puedo", "fallo", "bug", "problema técnico",
    "cobrado dos veces", "doble cobro", "no carga", "página en blanco",
    "error 500", "no me deja", "no responde", "sigue cobrando"
]
SEMILLAS_NO_TECNICO = [
    "quiero darme de baja", "solicito baja", "cambiar dirección",
    "actualizar datos", "información sobre", "cuánto cuesta",
    "factura del mes", "cambio de cuenta bancaria"
]
SISTEMAS_SEMILLA = {
    "stripe": ["stripe", "tarjeta", "visa", "mastercard", "cobro tarjeta"],
    "paypal": ["paypal", "pay pal"],
    "sepa_iban": ["iban", "sepa", "domiciliación", "domiciliacion", "cuenta bancaria", "recibo bancario"],
    "auth_login": ["login", "contraseña", "no puedo entrar", "acceso", "sesión", "sesion"],
    "crm_frontend": ["página", "pagina", "botón", "boton", "formulario", "no carga", "pantalla"],
}
TIPOS_SEMILLA = {
    "cobro_indebido": ["cobrado dos veces", "doble cobro", "cobro duplicado", "cargo no autorizado"],
    "baja_no_procesada": ["di de baja", "sigo siendo cobrado", "cancelé", "baja no efectiva", "no tramitaron"],
    "error_acceso": ["no puedo entrar", "contraseña no funciona", "error al iniciar"],
    "error_interfaz": ["no carga", "error 500", "página en blanco", "no responde"],
}

def limpiar_texto(texto: str) -> str:
    """Elimina saludos comunes, URLs, firmas y HTML."""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"http\S+", "", texto)
    texto = re.sub(r"(hola|buenos días|buenas tardes|estimad[oa]s?|saludos|un saludo|atentamente)[,\s]*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:2000]

def extraer_keywords_nlp(textos: list[str], nlp) -> dict:
    """Extrae keywords relevantes usando spaCy."""
    contador = Counter()
    for texto in textos:
        doc = nlp(texto[:1000])
        for token in doc:
            if (token.pos_ in ("NOUN", "VERB", "ADJ")
                    and not token.is_stop
                    and len(token.lemma_) > 3):
                contador[token.lemma_.lower()] += 1
    return dict(contador.most_common(100))

def calcular_coocurrencias(textos: list[str], top_terms: list[str]) -> dict:
    """Calcula co-ocurrencias entre top_terms dentro de una ventana."""
    cooc = Counter()
    for texto in textos:
        texto_lower = texto.lower()
        presentes = [t for t in top_terms if t in texto_lower]
        for i, t1 in enumerate(presentes):
            for t2 in presentes[i+1:]:
                pair = tuple(sorted([t1, t2]))
                cooc[pair] += 1
    return {f"{a}+{b}": c for (a, b), c in cooc.most_common(30)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", default="data/conceptos.json")
    args = parser.parse_args()

    print(f"📥 Descargando tickets de los últimos {args.days} días...")
    client = ZendeskClient()
    tickets = client.get_tickets(days_back=args.days)
    print(f"   → {len(tickets)} tickets descargados")

    print("🔍 Procesando con spaCy...")
    nlp = spacy.load("es_core_news_lg")
    textos = [limpiar_texto(f"{t['subject']} {t['body_preview']}") for t in tickets]
    keywords_freq = extraer_keywords_nlp(textos, nlp)
    top_terms = list(keywords_freq.keys())[:50]
    coocurrencias = calcular_coocurrencias(textos, top_terms)

    conceptos = {
        "version": "1.0",
        "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
        "muestra_tickets": len(tickets),
        "filtrado_tecnico": {
            "indicadores_tecnico": SEMILLAS_TECNICO,
            "indicadores_no_tecnico": SEMILLAS_NO_TECNICO,
            "umbral_confianza_ollama": 0.65,
        },
        "sistemas": {k: {"keywords": v, "descripcion": k} for k, v in SISTEMAS_SEMILLA.items()},
        "tipos_problema": {
            k: {"keywords": v, "severidad_default": "HIGH" if "cobro" in k or "baja" in k else "MEDIUM"}
            for k, v in TIPOS_SEMILLA.items()
        },
        "keywords_frecuentes": keywords_freq,
        "coocurrencias_top": coocurrencias,
        "umbral_ancla_directa": 2,
        "conceptos_descubiertos": [],
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(conceptos, f, ensure_ascii=False, indent=2)

    print(f"\n✅ conceptos.json generado: {args.output}")
    print(f"   Top 10 keywords: {top_terms[:10]}")
    print(f"   Co-ocurrencias: {list(coocurrencias.items())[:5]}")
    print("\n⚠️  Revisa y ajusta conceptos.json manualmente antes de ejecutar el pipeline.")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4.2: Run Fase 0**

```bash
python fase0_explorar.py --days 30
```
Expected: `✅ conceptos.json generado: data/conceptos.json` con 912 tickets procesados.

- [ ] **Step 4.3: Review conceptos.json and adjust manually**

Abrir `data/conceptos.json` y verificar/ajustar:
- Keywords en `sistemas` según lo que aparezca realmente en los tickets
- `indicadores_no_tecnico` para ajustar al vocabulario real de los usuarios
- `umbral_ancla_directa`: bajar a 1 si hay pocos matches, subir a 3 si hay ruido

- [ ] **Step 4.4: Commit**

```bash
git add fase0_explorar.py
git commit -m "feat: Fase 0 — NLP exploratorio con spaCy, genera conceptos.json"
```

---

## Task 5: Fase 1 — Filtrado técnico/no-técnico

**Files:**
- Create: `fase1_filtrar.py`
- Create: `tests/test_fase1.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/test_fase1.py
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
```

- [ ] **Step 5.2: Run to verify failure**

```bash
pytest tests/test_fase1.py -v
```
Expected: `ModuleNotFoundError: No module named 'fase1_filtrar'`

- [ ] **Step 5.3: Implement fase1_filtrar.py**

```python
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
            # Extract JSON even if model adds text
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
            # Safe default: no clasificar como técnico si hay error
            return {"resultado": "DESCARTADO", "confianza": 0.0, "metodo": "ollama_error", "error": str(e)}
```

- [ ] **Step 5.4: Run tests**

```bash
pytest tests/test_fase1.py -v
```
Expected: `4 passed`

- [ ] **Step 5.5: Commit**

```bash
git add fase1_filtrar.py tests/test_fase1.py
git commit -m "feat: Fase 1 — filtrado técnico/no-técnico con reglas + Ollama fallback"
```

---

## Task 6: Fase 2 — Pre-clasificación por anclas

**Files:**
- Create: `fase2_preclasificar.py`
- Create: `tests/test_fase2.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_fase2.py
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
```

- [ ] **Step 6.2: Run to verify failure**

```bash
pytest tests/test_fase2.py -v
```
Expected: `ModuleNotFoundError: No module named 'fase2_preclasificar'`

- [ ] **Step 6.3: Implement fase2_preclasificar.py**

```python
from storage import Storage

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

        return {
            "anclas": {
                "sistemas": sistemas_detectados,
                "tipo_problema": tipo_detectado,
                "keywords_matched": keywords_matched,
            },
            "cluster_candidato": cluster_candidato,
            "score_ancla": score_ancla,
            "severidad_estimada": severidad,
        }
```

- [ ] **Step 6.4: Run tests**

```bash
pytest tests/test_fase2.py -v
```
Expected: `5 passed`

- [ ] **Step 6.5: Commit**

```bash
git add fase2_preclasificar.py tests/test_fase2.py
git commit -m "feat: Fase 2 — pre-clasificación por anclas de keywords"
```

---

## Task 7: Jira client

**Files:**
- Create: `jira_client.py`

- [ ] **Step 7.1: Implement jira_client.py**

Based on existing `jira_crm_setup.py` patterns from the repo:

```python
import os
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

class JiraClient:
    def __init__(self):
        self.host = os.environ.get("JIRA_HOST", "eldiario.atlassian.net")
        self.email = os.environ["JIRA_EMAIL"]
        self.token = os.environ["JIRA_TOKEN"]
        self.project = os.environ.get("JIRA_PROJECT", "TEC")
        self.base_url = f"https://{self.host}/rest/api/3"
        _tok = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {_tok}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", headers=self.headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {}

    def buscar_tickets_crm(self, query_text: str, max_results: int = 5) -> list[dict]:
        """Busca tickets en Jira proyecto TEC con label CRM que coincidan con el texto."""
        # Sanitize query_text to avoid JQL injection
        safe_query = query_text.replace('"', ' ').replace("'", ' ')[:100]
        jql = f'project = {self.project} AND labels = "CRM" AND text ~ "{safe_query}" ORDER BY created DESC'
        encoded_jql = urllib.parse.quote(jql)
        data = self._get(f"/search?jql={encoded_jql}&maxResults={max_results}&fields=summary,status,priority,labels")
        issues = data.get("issues", [])
        return [
            {
                "jira_id": i["key"],
                "summary": i["fields"].get("summary", ""),
                "status": i["fields"].get("status", {}).get("name", ""),
                "priority": i["fields"].get("priority", {}).get("name", ""),
                "url": f"https://{self.host}/browse/{i['key']}",
            }
            for i in issues
        ]

```

- [ ] **Step 7.2: Smoke test**

```bash
python -c "
from jira_client import JiraClient
c = JiraClient()
results = c.buscar_tickets_crm('stripe cobro')
print(f'Found {len(results)} Jira tickets')
for r in results:
    print(f'  {r[\"jira_id\"]}: {r[\"summary\"]}')
"
```

- [ ] **Step 7.3: Commit**

```bash
git add jira_client.py
git commit -m "feat: Jira client for CRM ticket cross-reference search"
```

---

## Task 8: Fase 3 — Clustering LLM con GPT-4o

**Files:**
- Create: `fase3_clusterizar.py`

- [ ] **Step 8.1: Implement fase3_clusterizar.py**

```python
import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
from storage import Storage
from jira_client import JiraClient

load_dotenv()

class Fase3Clusterizador:
    def __init__(self):
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.storage = Storage()
        self.jira = JiraClient()

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

        clusters_resumen = [
            {"cluster_id": c["cluster_id"], "nombre": c["nombre"],
             "sistema": c.get("sistema"), "tipo_problema": c.get("tipo_problema"),
             "resumen": c.get("resumen", ""), "ticket_count": c.get("ticket_count", 0)}
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

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(resp.choices[0].message.content)

        # Buscar Jira candidatos
        jira_query = data.get("jira_query", " ".join(data.get("keywords_detectados", [])[:3]))
        jira_candidatos = []
        if jira_query:
            try:
                jira_results = self.jira.buscar_tickets_crm(jira_query)
                jira_candidatos = [r["jira_id"] for r in jira_results]
            except Exception:
                pass

        now = datetime.now(timezone.utc).isoformat()

        if data["accion"] == "ASIGNAR_EXISTENTE":
            cluster_id = data["cluster_id"]
            # Update cluster ticket count
            cluster = next((c for c in clusters if c["cluster_id"] == cluster_id), None)
            if cluster:
                cluster["ticket_count"] = cluster.get("ticket_count", 0) + 1
                cluster["updated_at"] = now
                if ticket["zendesk_id"] not in cluster.get("ticket_ids", []):
                    cluster.setdefault("ticket_ids", []).append(ticket["zendesk_id"])
                # Merge jira candidatos
                existing_jira = set(cluster.get("jira_candidatos", []))
                cluster["jira_candidatos"] = list(existing_jira | set(jira_candidatos))
                self.storage.save_cluster(cluster)
        else:
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
```

- [ ] **Step 8.2: Manual integration test with 3 sample tickets**

```bash
python -c "
from storage import Storage
from fase3_clusterizador import Fase3Clusterizador

# Create sample tickets for testing
storage = Storage()
sample_tickets = [
    {'zendesk_id': 9001, 'subject': 'Cobro doble en Stripe', 'body_preview': 'Buenos días, me han cobrado dos veces este mes via stripe tarjeta visa. Por favor solucionen.', 'fase1_resultado': 'TECNICO'},
    {'zendesk_id': 9002, 'subject': 'Error al iniciar sesión', 'body_preview': 'No puedo entrar a mi cuenta, me da error de contraseña aunque la he cambiado.', 'fase1_resultado': 'TECNICO'},
    {'zendesk_id': 9003, 'subject': 'Cobro duplicado stripe', 'body_preview': 'Hola, veo en mi extracto que me han cobrado dos veces en stripe en abril.', 'fase1_resultado': 'TECNICO'},
]

clusterizador = Fase3Clusterizador()
for t in sample_tickets:
    result = clusterizador.clusterizar(t)
    print(f'Ticket {t[\"zendesk_id\"]} → {result[\"cluster_id\"]} (conf: {result[\"confianza\"]:.2f})')
print('Clusters:', [c['nombre'] for c in storage.get_clusters()])
"
```
Expected: tickets 9001 y 9003 van al mismo cluster, 9002 a otro.

- [ ] **Step 8.3: Commit**

```bash
git add fase3_clusterizar.py
git commit -m "feat: Fase 3 — clustering LLM con GPT-4o y cross-referencia Jira"
```

---

## Task 9: Pipeline orchestrator

**Files:**
- Create: `pipeline.py`

- [ ] **Step 9.1: Implement pipeline.py**

```python
#!/usr/bin/env python3
"""
Pipeline de triage: Fases 1-3 para un batch de tickets.

Uso:
    python pipeline.py --horas 24
    python pipeline.py --fase0 --days 30
    python pipeline.py --horas 24 --dry-run
"""
import argparse
import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from storage import Storage
from fase1_filtrar import Fase1Filtrador
from fase2_preclasificar import Fase2Preclasificador
from fase3_clusterizar import Fase3Clusterizador

load_dotenv()

def run_pipeline(horas: int = 24, dry_run: bool = False):
    storage = Storage()
    conceptos = storage.get_conceptos()
    if not conceptos:
        print("❌ No hay conceptos.json. Ejecuta primero: python pipeline.py --fase0 --days 30")
        return

    print(f"📥 Descargando tickets de las últimas {horas}h...")
    client = ZendeskClient()
    tickets_raw = client.get_tickets_since(since_hours=horas)

    # Filtrar ya procesados
    ya_procesados = {t["zendesk_id"] for t in storage.get_tickets()}
    tickets = [t for t in tickets_raw if t["zendesk_id"] not in ya_procesados]
    print(f"   → {len(tickets)} tickets nuevos (de {len(tickets_raw)} descargados)")

    filtrador = Fase1Filtrador()
    preclasificador = Fase2Preclasificador()
    clusterizador = Fase3Clusterizador()

    stats = {"total": len(tickets), "tecnicos": 0, "descartados": 0, "ancla_directa": 0, "llm": 0, "clusters_nuevos": 0}
    clusters_antes = len(storage.get_clusters())

    for ticket in tickets:
        # Fase 1
        f1 = filtrador.clasificar(ticket)
        ticket["fase1_resultado"] = f1["resultado"]
        ticket["fase1_confianza"] = f1["confianza"]
        ticket["fase1_modelo"] = f1["metodo"]

        if f1["resultado"] == "DESCARTADO":
            stats["descartados"] += 1
            if not dry_run:
                storage.save_ticket(ticket)
            continue

        stats["tecnicos"] += 1

        # Fase 2
        f2 = preclasificador.preclasificar(ticket)
        ticket["fase2_anclas"] = f2["anclas"]

        if f2["cluster_candidato"]:
            stats["ancla_directa"] += 1
            ticket["fase3_cluster_id"] = f2["cluster_candidato"]
            ticket["fase3_severidad"] = f2["severidad_estimada"]
            ticket["fase3_jira_candidatos"] = []
            ticket["procesado_at"] = datetime.now(timezone.utc).isoformat()
        else:
            # Fase 3
            stats["llm"] += 1
            f3 = clusterizador.clusterizar(ticket)
            ticket["fase3_cluster_id"] = f3["cluster_id"]
            ticket["fase3_resumen_llm"] = f3["resumen_llm"]
            ticket["fase3_severidad"] = f3["severidad"]
            ticket["fase3_jira_candidatos"] = f3["jira_candidatos"]
            ticket["procesado_at"] = datetime.now(timezone.utc).isoformat()

        if not dry_run:
            storage.save_ticket(ticket)

    clusters_despues = len(storage.get_clusters())
    stats["clusters_nuevos"] = clusters_despues - clusters_antes

    print(f"\n✅ Pipeline completado:")
    print(f"   Total tickets:     {stats['total']}")
    print(f"   Técnicos:          {stats['tecnicos']}")
    print(f"   Descartados:       {stats['descartados']}")
    print(f"   Ancla directa:     {stats['ancla_directa']} (sin coste API)")
    print(f"   LLM (GPT-4o):      {stats['llm']}")
    print(f"   Clusters nuevos:   {stats['clusters_nuevos']}")
    if dry_run:
        print("   ⚠️  DRY-RUN: nada guardado")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas", type=int, default=24)
    parser.add_argument("--fase0", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.fase0:
        import subprocess, sys
        subprocess.run([sys.executable, "fase0_explorar.py", "--days", str(args.days)], check=True)
    else:
        run_pipeline(horas=args.horas, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
```

- [ ] **Step 9.2: Test dry-run**

```bash
python pipeline.py --horas 24 --dry-run
```
Expected: stats printed, no files written.

- [ ] **Step 9.3: Run real pipeline**

```bash
python pipeline.py --horas 24
```
Expected: tickets processed and saved to `data/tickets.json` and `data/clusters.json`.

- [ ] **Step 9.4: Commit**

```bash
git add pipeline.py
git commit -m "feat: pipeline orchestrator for phases 1-3 with dry-run support"
```

---

## Task 10: Streamlit app — clusters view

**Files:**
- Create: `app.py`
- Create: `views/__init__.py`
- Create: `views/clusters.py`

- [ ] **Step 10.1: Create app.py**

```python
import streamlit as st

st.set_page_config(
    page_title="Zendesk Triage — elDiario.es",
    page_icon="🎫",
    layout="wide",
)

# Sidebar navigation
page = st.sidebar.radio(
    "Navegación",
    ["📊 Clusters", "🔍 Explorar"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.caption("Zendesk Triage PoC · elDiario.es")

if page == "📊 Clusters":
    from views.clusters import render
    render()
elif page == "🔍 Explorar":
    from views.explorar import render
    render()
```

- [ ] **Step 10.2: Create views/__init__.py**

```python
```

- [ ] **Step 10.3: Create views/clusters.py**

```python
import streamlit as st
from storage import Storage

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
TENDENCIA_ICON = {"creciente": "↑", "estable": "→", "decreciente": "↓", "nuevo": "✨"}

def render():
    st.title("📊 Clusters de incidencias técnicas")

    storage = Storage()
    clusters = storage.get_clusters(estado="abierto")

    if not clusters:
        st.info("No hay clusters activos. Ejecuta el pipeline: `python pipeline.py --horas 24`")
        return

    # Filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_sev = st.selectbox("Severidad", ["Todas", "HIGH", "MEDIUM", "LOW"])
    with col2:
        sistemas_disponibles = list({c.get("sistema", "desconocido") for c in clusters})
        filtro_sis = st.selectbox("Sistema", ["Todos"] + sorted(sistemas_disponibles))
    with col3:
        if st.button("🔄 Actualizar"):
            st.rerun()

    # Filtrar
    filtered = clusters
    if filtro_sev != "Todas":
        filtered = [c for c in filtered if c.get("severidad") == filtro_sev]
    if filtro_sis != "Todos":
        filtered = [c for c in filtered if c.get("sistema") == filtro_sis]

    # Sort by severity then ticket count
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    filtered.sort(key=lambda c: (sev_order.get(c.get("severidad", "LOW"), 2), -c.get("ticket_count", 0)))

    st.markdown(f"**{len(filtered)} clusters** encontrados")
    st.markdown("---")

    for cluster in filtered:
        sev = cluster.get("severidad", "MEDIUM")
        icon = SEVERIDAD_COLOR.get(sev, "⚪")
        tend = TENDENCIA_ICON.get(cluster.get("tendencia", "estable"), "→")
        jira_list = ", ".join(cluster.get("jira_candidatos", [])) or "—"

        with st.expander(f"{icon} **{cluster['nombre']}** · {cluster.get('ticket_count', 0)} tickets {tend}"):
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f"**Resumen:** {cluster.get('resumen', '_Sin resumen_')}")
                st.markdown(f"**Sistema:** `{cluster.get('sistema', '—')}` · **Tipo:** `{cluster.get('tipo_problema', '—')}`")
                st.markdown(f"**Jira candidatos:** {jira_list}")
            with col2:
                st.metric("Tickets", cluster.get("ticket_count", 0))
                st.caption(f"Creado: {cluster.get('created_at', '')[:10]}")
                st.caption(f"Actualizado: {cluster.get('updated_at', '')[:10]}")

            # Detail tickets
            if st.button(f"Ver tickets detalle", key=f"btn_{cluster['cluster_id']}"):
                st.session_state["selected_cluster"] = cluster["cluster_id"]
                st.session_state["page"] = "detalle"
                st.rerun()

            tickets = storage.get_cluster_tickets(cluster["cluster_id"])
            if tickets:
                st.markdown("**Últimos tickets:**")
                for t in tickets[-5:]:
                    conf = t.get("fase1_confianza", 0)
                    conf_icon = "✓" if conf >= 0.8 else "⚠"
                    st.markdown(f"- `#{t['zendesk_id']}` {conf_icon} _{t.get('subject', '')}_ · confianza: {conf:.0%}")
```

- [ ] **Step 10.4: Launch Streamlit and verify**

```bash
streamlit run app.py
```
Open `http://localhost:8501` and verify clusters display correctly.

- [ ] **Step 10.5: Commit**

```bash
git add app.py views/__init__.py views/clusters.py
git commit -m "feat: Streamlit app with cluster list view, filters, and severity badges"
```

---

## Task 11: Streamlit — explorar view

**Files:**
- Create: `views/explorar.py`

- [ ] **Step 11.1: Create views/explorar.py**

```python
import json
import subprocess
import sys
import streamlit as st
from storage import Storage

def render():
    st.title("🔍 Explorar taxonomía y estadísticas")
    storage = Storage()

    tab1, tab2 = st.tabs(["📋 Taxonomía (conceptos.json)", "📈 Estadísticas pipeline"])

    with tab1:
        conceptos = storage.get_conceptos()
        if not conceptos:
            st.warning("No hay conceptos.json. Ejecuta: `python pipeline.py --fase0 --days 30`")
            return

        st.caption(f"Generado: {conceptos.get('generated_at', '—')} · Muestra: {conceptos.get('muestra_tickets', '—')} tickets")

        st.subheader("Sistemas detectados")
        for sistema, config in conceptos.get("sistemas", {}).items():
            with st.expander(f"`{sistema}`"):
                st.write("Keywords:", config.get("keywords", []))

        st.subheader("Tipos de problema")
        for tipo, config in conceptos.get("tipos_problema", {}).items():
            with st.expander(f"`{tipo}` — severidad default: {config.get('severidad_default')}"):
                st.write("Keywords:", config.get("keywords", []))

        st.subheader("Top keywords frecuentes")
        kw = conceptos.get("keywords_frecuentes", {})
        if kw:
            top = sorted(kw.items(), key=lambda x: -x[1])[:20]
            st.bar_chart({k: v for k, v in top})

        st.subheader("Co-ocurrencias más fuertes")
        cooc = conceptos.get("coocurrencias_top", {})
        if cooc:
            for pair, count in list(cooc.items())[:10]:
                st.markdown(f"- **{pair}**: {count} apariciones juntas")

    with tab2:
        tickets = storage.get_tickets()
        clusters = storage.get_clusters()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total tickets procesados", len(tickets))
        tecnicos = [t for t in tickets if t.get("fase1_resultado") == "TECNICO"]
        col2.metric("Técnicos", len(tecnicos))
        col3.metric("Descartados", len(tickets) - len(tecnicos))
        col4.metric("Clusters activos", len([c for c in clusters if c.get("estado") == "abierto"]))

        if tecnicos:
            via_ancla = [t for t in tecnicos if t.get("fase2_anclas", {}).get("sistemas")]
            via_llm = [t for t in tecnicos if t.get("fase3_resumen_llm")]
            st.markdown(f"**Ancla directa (sin LLM remoto):** {len(via_ancla)} tickets ({len(via_ancla)/len(tecnicos):.0%})")
            st.markdown(f"**Via GPT-4o:** {len(via_llm)} tickets ({len(via_llm)/len(tecnicos):.0%})")

        st.subheader("Re-ejecutar exploración")
        days = st.number_input("Días de histórico", min_value=1, max_value=90, value=30)
        if st.button("🔄 Regenerar conceptos.json"):
            with st.spinner(f"Procesando {days} días de tickets..."):
                result = subprocess.run(
                    [sys.executable, "fase0_explorar.py", "--days", str(days)],
                    capture_output=True, text=True
                )
            if result.returncode == 0:
                st.success("conceptos.json regenerado correctamente")
                st.code(result.stdout)
            else:
                st.error(f"Error: {result.stderr}")
```

- [ ] **Step 11.2: Test in browser**

```bash
streamlit run app.py
```
Navigate to "🔍 Explorar" and verify taxonomy displays and stats are correct.

- [ ] **Step 11.3: Commit**

```bash
git add views/explorar.py
git commit -m "feat: explorar view with taxonomy display, stats, and Fase 0 re-run"
```

---

## Task 12: End-to-end validation

- [ ] **Step 12.1: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 12.2: Run pipeline on real data**

```bash
# Generate taxonomy from last 30 days
python pipeline.py --fase0 --days 30

# Process last 24h
python pipeline.py --horas 24

# Verify output
python -c "
import json
with open('data/clusters.json') as f:
    clusters = json.load(f)
print(f'{len(clusters)} clusters created:')
for c in clusters:
    print(f'  [{c[\"severidad\"]}] {c[\"nombre\"]} — {c[\"ticket_count\"]} tickets')
"
```

- [ ] **Step 12.3: Review clusters manually**

Open Streamlit and verify:
- [ ] Clusters are coherent (similar tickets grouped together)
- [ ] Severity levels are appropriate
- [ ] Jira candidates are relevant where shown
- [ ] Taxonomy in Explorar view reflects real ticket vocabulary

- [ ] **Step 12.4: Adjust conceptos.json if needed**

Edit `data/conceptos.json` directly to fix any keywords, re-run pipeline on same batch.

- [ ] **Step 12.5: Final commit**

```bash
git add .
git commit -m "feat: zendesk-triage PoC complete — pipeline + Streamlit panel"
```

---

## Acceptance Criteria

- [ ] `python pipeline.py --fase0 --days 30` generates a meaningful `conceptos.json`
- [ ] `python pipeline.py --horas 24` processes tickets without errors
- [ ] Streamlit panel shows clusters grouped by severity
- [ ] At least 60% of technical tickets classified via rules/anclas (no GPT-4o cost)
- [ ] GPT-4o cost estimated < $0.50/day for 100 tickets/day scenario
- [ ] Jira cross-reference returns relevant TEC tickets for at least 1 cluster
