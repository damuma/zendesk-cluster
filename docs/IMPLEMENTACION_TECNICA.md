# Implementación Técnica — Zendesk Triage
## elDiario.es · PoC local → producción GCloud

**Versión:** 1.0  
**Fecha:** 2026-04-15  
**Stack:** Python 3.12 · spaCy · Ollama · OpenAI · Streamlit · JSON → PostgreSQL

---

## 1. Estructura del proyecto

```
zendesk-cluster/
├── .env                          # Credenciales (nunca en git)
├── .env.example                  # Plantilla de variables
├── requirements.txt
├── README.md
│
├── data/                         # BBDD JSON local (PoC) — en .gitignore
│   ├── conceptos.json            # Taxonomía generada en Fase 0
│   ├── tickets.json              # Tickets procesados con metadata
│   ├── clusters.json             # Clusters activos
│   └── jira_tickets.json         # Pool Jira local (primer registro es _meta)
│
├── zendesk_client.py             # Wrapper Zendesk REST API v2
├── jira_client.py                # Wrapper Jira REST API v3 /search/jql
├── jira_matcher.py               # Matcher híbrido keywords + GPT-4o
├── storage.py                    # Abstracción JSON → PostgreSQL
│
├── fase0_explorar.py             # Descarga muestra + genera conceptos.json
├── fase0_jira.py                 # Descarga Jira TEC 60d (full/incremental)
├── fase1_filtrar.py              # Clasifica: TECNICO vs DESCARTADO
├── fase2_preclasificar.py        # Asigna anclas por señales fuertes
├── fase3_clusterizar.py          # Clustering fino + matching Jira local
├── fase4_jira.py                 # Re-matching de clusters existentes
├── pipeline.py                   # Orquesta Fases 1-3 para batch
│
├── app.py                        # Streamlit — punto de entrada
└── views/
    ├── clusters.py               # Vista lista de clusters
    ├── detalle_cluster.py        # Vista drill-down cluster
    └── explorar.py               # Vista taxonomía y estadísticas
```

---

## 2. Variables de entorno (.env)

```env
# Zendesk
ZENDESK_SUBDOMAIN=eldiarioeshelp
ZENDESK_EMAIL=tu@eldiario.es
ZENDESK_API_TOKEN=xxx

# OpenAI (Fase 3)
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o

# Jira
JIRA_HOST=eldiario.atlassian.net
JIRA_EMAIL=tu@eldiario.es
JIRA_TOKEN=xxx
JIRA_PROJECT=TEC

# Ollama (Fase 1)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma2:9b

# Almacenamiento
STORAGE_BACKEND=json           # json | postgres
DATA_DIR=./data

# PostgreSQL (producción)
POSTGRES_HOST=104.155.66.254
POSTGRES_PORT=5432
POSTGRES_DB=zendesk_triage
POSTGRES_USER=n8n_user
POSTGRES_PASSWORD=xxx
```

---

## 3. Configuración Zendesk (Paso 0 manual)

Antes de ejecutar nada, generar el token API:

1. Ir a `https://eldiarioeshelp.zendesk.com/admin/apps-integrations/apis/zendesk-api`
2. Activar "Token access" si no está activo
3. Crear nuevo token → copiar al `.env`

Endpoint base: `https://eldiarioeshelp.zendesk.com/api/v2/`

---

## 4. zendesk_client.py

Responsabilidades:
- Autenticación con token
- Descarga de tickets por rango de fechas (paginación)
- Descarga de tickets individuales
- Rate limiting (Zendesk: 700 req/min en plan Team)

```python
# Interface pública
class ZendeskClient:
    def get_tickets(self, days_back: int = 30) -> list[dict]
    def get_ticket(self, ticket_id: int) -> dict
    def get_tickets_since(self, since_hours: int = 24) -> list[dict]
```

Campos que se extraen de cada ticket:
- `id`, `created_at`, `updated_at`
- `subject`, `description` (body del primer mensaje)
- `status`, `priority`, `tags`
- `requester_id` (anonimizado en storage)
- `channel` (email, web, etc.)

---

## 5. fase0_explorar.py

Responsabilidades:
- Descarga muestra histórica (ej. últimos 30 días → ~912 tickets)
- Limpia texto (elimina saludos, firmas, HTML)
- Aplica spaCy `es_core_news_lg`:
  - Extracción de entidades (ORG, LOC, MISC)
  - Frecuencia de sustantivos y verbos relevantes
  - Co-ocurrencias entre términos (ventana de ±3 palabras)
- Genera embeddings con `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Propone clusters candidatos por similitud (UMAP + HDBSCAN)
- Escribe `conceptos.json` con:
  - Top 50 términos por frecuencia
  - Co-ocurrencias más fuertes
  - Categorías propuestas (para revisión manual)

```bash
# Uso
python fase0_explorar.py --days 30 --output data/conceptos.json
python fase0_explorar.py --days 7   # recalibración rápida
```

Dependencias: `spacy`, `sentence-transformers`, `umap-learn`, `hdbscan`, `pandas`

---

## 6. fase1_filtrar.py

Responsabilidades:
- Para cada ticket: determinar si es problema TÉCNICO o petición normal
- Lógica en cascada:
  1. **Señales negativas fuertes** (conceptos.json → `filtrado_tecnico.indicadores_no_tecnico`): si match → DESCARTADO con confianza 0.95
  2. **Señales positivas fuertes** (conceptos.json → `filtrado_tecnico.indicadores_tecnico`): si match → TÉCNICO con confianza 0.90
  3. **Zona gris** (sin match claro) → llamada a Ollama local

Prompt Ollama (Fase 1):
```
Eres un clasificador de tickets de soporte de un medio de comunicación digital.
Tu tarea es determinar si un ticket es consecuencia de un ERROR TÉCNICO del sistema
(CRM, pagos, acceso, web) o es una petición directa voluntaria del usuario.

Responde SOLO con: {"tipo": "TECNICO"|"NO_TECNICO", "confianza": 0.0-1.0, "razon": "..."}

Ticket:
Asunto: {subject}
Cuerpo: {body_preview}
```

```python
# Interface pública
class Fase1Filtrador:
    def clasificar(self, ticket: dict) -> dict:
        # returns: {"resultado": "TECNICO"|"DESCARTADO", "confianza": float, "metodo": str}
```

---

## 7. fase2_preclasificar.py

Responsabilidades:
- Solo procesa tickets marcados como TÉCNICO en Fase 1
- Matching de keywords del ticket contra `conceptos.json`:
  - `sistemas`: stripe, paypal, sepa_iban, auth_login, crm_frontend...
  - `tipos_problema`: cobro_indebido, baja_no_procesada, error_acceso...
- Asigna "anclas" con score (cuántos keywords matchearon)
- Si score_ancla ≥ umbral (configurable, default: 2 keywords): asigna cluster candidato directo
- Si score_ancla < umbral: ticket pasa a Fase 3

```python
# Interface pública
class Fase2Preclasificador:
    def preclasificar(self, ticket: dict, conceptos: dict) -> dict:
        # returns: {
        #   "anclas": {"sistemas": [...], "tipo_problema": str, "keywords_matched": [...]},
        #   "cluster_candidato": str | None,  # None → pasa a Fase 3
        #   "score_ancla": float,
        #   "severidad_estimada": "HIGH"|"MEDIUM"|"LOW"
        # }
```

---

## 8. fase3_clusterizar.py

Responsabilidades:
- Solo procesa tickets sin cluster candidato claro de Fase 2
- Llama a GPT-4o con:
  - Texto del ticket
  - Lista de clusters existentes (nombre + resumen + keywords)
  - Taxonomía de sistemas y tipos de problema
- GPT-4o decide: asignar a cluster existente O crear cluster nuevo
- Si cluster nuevo: genera nombre, descripción, severidad
- Busca tickets de Jira relacionados via JQL:
  ```
  project = TEC AND labels = "CRM" AND text ~ "{keywords}"
  ORDER BY created DESC
  ```

Prompt GPT-4o (Fase 3):
```
Eres un sistema de clustering de incidencias técnicas de soporte.

CLUSTERS EXISTENTES:
{json de clusters activos con nombre, resumen, keywords}

TAXONOMÍA:
Sistemas: stripe, paypal, sepa_iban, auth_login, crm_frontend, [NUEVO]
Tipos: cobro_indebido, baja_no_procesada, error_acceso, error_interfaz, [NUEVO]

TICKET A CLASIFICAR:
Asunto: {subject}
Cuerpo: {body}

Responde en JSON:
{
  "accion": "ASIGNAR_EXISTENTE" | "CREAR_NUEVO",
  "cluster_id": "CLU-XXX" (si ASIGNAR_EXISTENTE),
  "cluster_nuevo": {  (si CREAR_NUEVO)
    "nombre": "...",
    "sistema": "...",
    "tipo_problema": "...",
    "severidad": "HIGH|MEDIUM|LOW",
    "resumen": "..."
  },
  "confianza": 0.0-1.0,
  "keywords_detectados": [...],
  "jira_query": "texto para buscar en Jira"
}
```

---

## 9. storage.py

Abstrae la diferencia entre JSON (PoC) y PostgreSQL (producción).

```python
class Storage:
    def get_tickets(self, filters: dict = None) -> list[dict]
    def save_ticket(self, ticket: dict) -> None
    def get_clusters(self, estado: str = "abierto") -> list[dict]
    def save_cluster(self, cluster: dict) -> None
    def get_conceptos(self) -> dict
    def save_conceptos(self, conceptos: dict) -> None
    def get_cluster_tickets(self, cluster_id: str) -> list[dict]
```

**JSON backend:** Lee/escribe archivos en `DATA_DIR/`.  
**PostgreSQL backend:** Tablas `tickets`, `clusters`, `conceptos` — mismo esquema que los JSON.

Schema PostgreSQL (para cuando se migre):
```sql
CREATE TABLE tickets (
    zendesk_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ,
    subject TEXT,
    body_preview TEXT,
    channel VARCHAR(50),
    fase1_resultado VARCHAR(20),
    fase1_confianza FLOAT,
    fase2_anclas JSONB,
    fase3_cluster_id VARCHAR(20),
    fase3_resumen_llm TEXT,
    fase3_severidad VARCHAR(10),
    fase3_jira_candidatos TEXT[],
    procesado_at TIMESTAMPTZ
);

CREATE TABLE clusters (
    cluster_id VARCHAR(20) PRIMARY KEY,
    nombre TEXT,
    sistema VARCHAR(50),
    tipo_problema VARCHAR(50),
    severidad VARCHAR(10),
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    ticket_count INT,
    ticket_ids BIGINT[],
    jira_candidatos TEXT[],
    jira_vinculado TEXT,
    estado VARCHAR(20),
    resumen TEXT,
    tendencia VARCHAR(20)
);
```

---

## 10. pipeline.py

Orquesta las fases 1-3 para un batch de tickets nuevos.

```bash
# Procesar últimas 24h
python pipeline.py --horas 24

# Procesar fecha concreta
python pipeline.py --desde 2026-04-14 --hasta 2026-04-15

# Solo Fase 0 (exploración)
python pipeline.py --fase0 --days 30

# Dry-run (no escribe, solo muestra)
python pipeline.py --horas 24 --dry-run
```

Flujo interno:
1. Descarga tickets nuevos de Zendesk
2. Para cada ticket: Fase1 → si TÉCNICO → Fase2 → si sin ancla → Fase3
3. Actualiza clusters (conteos, tendencias, updated_at)
4. Escribe resumen de ejecución (tickets procesados, coste API estimado, clusters nuevos)

---

## 11. app.py — Streamlit

```bash
# Ejecutar panel
streamlit run app.py

# Con puerto específico
streamlit run app.py --server.port 8502
```

Estructura de navegación:
- Sidebar: [📊 Clusters] [🎫 Tickets] [🔍 Explorar]
- Estado en `st.session_state`: cluster seleccionado, filtros activos
- Auto-refresh configurable (default: off, botón manual)

---

## 12. Instalación y setup local

```bash
# 1. Clonar y entrar al directorio
cd zendesk-cluster

# 2. Entorno virtual
python3.12 -m venv venv
source venv/bin/activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Modelo spaCy español
python -m spacy download es_core_news_lg

# 5. Ollama
brew install ollama
ollama pull gemma2:9b

# 6. Variables de entorno
cp .env.example .env
# Editar .env con credenciales reales

# 7. Exploración inicial (genera conceptos.json)
python pipeline.py --fase0 --days 30

# 8. Primer batch
python pipeline.py --horas 24

# 9. Panel
streamlit run app.py
```

---

## 13. Requirements.txt

```
# Core
python-dotenv==1.0.1
requests==2.32.3

# NLP local
spacy==3.7.6
sentence-transformers==3.0.1
umap-learn==0.5.6
hdbscan==0.8.38.post1

# LLM
openai==1.50.0
ollama==0.3.3

# Data
pandas==2.2.3

# Panel
streamlit==1.39.0

# PostgreSQL (producción)
psycopg2-binary==2.9.9
```

---

## 14. Estimación de coste API

Para 50 tickets/día técnicos, con 40% pasando a Fase 3 (20 tickets):

| Concepto | Coste estimado |
|---------|----------------|
| GPT-4o input (~800 tokens/ticket × 20) | ~$0.04/día |
| GPT-4o output (~200 tokens/ticket × 20) | ~$0.02/día |
| Jira API | Gratis (incluido) |
| Zendesk API | Gratis (incluido en plan) |
| Ollama local | $0 |
| **Total estimado** | **< $0.10/día** |

Incluso en el escenario de 100 tickets/día técnicos: < $0.50/día.

---

## 15. Migración a producción GCloud

Cuando la PoC esté validada:

1. **STORAGE_BACKEND=postgres** en `.env`
2. Crear base de datos en el PostgreSQL existente de GCloud:
   ```sql
   CREATE DATABASE zendesk_triage;
   CREATE USER zendesk_user WITH PASSWORD 'xxx';
   GRANT ALL ON DATABASE zendesk_triage TO zendesk_user;
   ```
3. Ejecutar schema SQL (sección 9)
4. Desplegar Streamlit en GCloud Run o como servicio en la VM `eldiario-logs`
5. Programar `pipeline.py` con cron o n8n (webhook/schedule)
6. Ollama puede correr en local o en VM (la VM tiene suficiente RAM para Gemma 9B cuantizado)

---

## 16. Scripts de Jira

### `fase0_jira.py` — descarga

```bash
python fase0_jira.py            # modo incremental (default)
python fase0_jira.py --full     # re-descarga completa 60d
python fase0_jira.py --days 90  # cambia ventana
```

En modo FULL: `project = TEC AND statusCategory != Done AND updated >= -60d`.
En modo INCREMENTAL: pide `updated >= fecha_fin - 10min` (incluye done para
detectar cierres y borrarlos del pool).

El primer registro de `data/jira_tickets.json` es `_meta` con el rango de
fechas, último sync y total aproximado. `storage.get_jira_tickets()` lo
filtra automáticamente.

### `fase4_jira.py` — re-matching

```bash
python fase4_jira.py                    # todos los clusters
python fase4_jira.py --cluster CLU-001  # uno solo
python fase4_jira.py --solo-vacios      # solo clusters sin candidatos
```

Útil tras ejecutar `fase0_jira.py` para refrescar candidatos en clusters
ya existentes sin re-procesar tickets de Zendesk.

### Nota sobre la API de Jira

El endpoint clásico `GET /rest/api/3/search` está deprecado (HTTP 410).
Usamos `GET /rest/api/3/search/jql` con paginación por `nextPageToken`
(no hay `total`, usar `POST /search/approximate-count`).
