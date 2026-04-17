# Clasificación Jira — elDiario.es

**Versión:** 1.0
**Fecha:** 2026-04-17
**Rama:** `claude/epic-mestorf-1c7d4a`
**Relacionado:** [DESIGN.md](DESIGN.md) · [IMPLEMENTACION_TECNICA.md](IMPLEMENTACION_TECNICA.md)

---

## 1. Por qué existe esto

El sistema de triage descubre **clusters de tickets Zendesk** que representan el mismo problema técnico. El valor de negocio está en **vincular esos clusters al ticket de Jira correcto** para no abrir un Jira nuevo por cada usuario afectado — todos los casos se consolidan en la misma tarea de desarrollo.

La versión anterior llamaba a la API de Jira en caliente desde la fase 3 (`jira_client.buscar_tickets_crm`). Esto estaba **roto** desde que Atlassian deprecó `/rest/api/3/search` (devuelve HTTP 410 Gone), y además producía matches pobres porque era una búsqueda por texto libre sin razonamiento sobre el dominio.

## 2. Solución

Dos piezas:

1. **Pool local de Jiras** (`data/jira_tickets.json`): un script descarga los tickets de Jira del proyecto TEC de los últimos 60 días y los guarda localmente. El primer registro es un `_meta` con el rango y un resumen.
2. **Matcher híbrido** (`jira_matcher.py`): cuando un cluster se crea o actualiza, un prefiltrado por keywords reduce el pool a ~15 candidatos y GPT-4o selecciona los que realmente corresponden al problema, con confianza y razón.

Los tickets de Jira **no se clusterizan** — son un índice de búsqueda contra el que los clusters de Zendesk se emparejan.

## 3. Flujo

```
┌─────────────────┐   fase0_jira.py (periódico)
│   Jira TEC API  │ ─────────────────────────────┐
│  /search/jql    │                              │
└─────────────────┘                              ▼
                                    ┌─────────────────────────┐
                                    │ data/jira_tickets.json  │
                                    │ _meta + pool 60d        │
                                    │ (sin statusCategory=done)│
                                    └───────┬─────────────────┘
                                            │
         ┌──────────────────┐               │ lee
         │ Cluster Zendesk  │               ▼
         │ (fase 3)         │──▶ jira_matcher.match()
         └──────────────────┘    │  ├── prefilter keywords (top 15)
                                 │  └── GPT-4o select (top 5)
                                 ▼
                        cluster.jira_candidatos = [
                          {jira_id, url, summary,
                           status, confianza, razon}, ...
                        ]
```

`fase4_jira.py` permite re-ejecutar el matching sobre todos los clusters existentes cuando entra Jiras nuevas (sin re-procesar tickets de Zendesk).

## 4. Scripts

### `fase0_jira.py` — descarga el pool

```bash
python fase0_jira.py            # incremental (por defecto)
python fase0_jira.py --full     # re-descarga completa 60d
python fase0_jira.py --days 90  # cambia la ventana temporal
```

**Modo full**: pide `project = TEC AND statusCategory != Done AND updated >= -60d`. Reescribe el JSON completo.

**Modo incremental**: pide `project = TEC AND updated >= (fecha_fin - 10min)`. Hace upsert en el JSON. Incluye los tickets en `done` para poder **eliminarlos** del pool (tickets que se cerraron desde el último sync).

Primer registro del JSON (`_meta`):

```json
{
  "_meta": true,
  "project": "TEC",
  "fecha_inicio": "2026-02-16T00:00:00Z",
  "fecha_fin":    "2026-04-17T00:00:00Z",
  "last_sync":    "2026-04-17T00:00:00Z",
  "total_tickets": 120,
  "filtro": "project = TEC AND statusCategory != Done"
}
```

### `fase4_jira.py` — re-matching de clusters

```bash
python fase4_jira.py                    # todos los clusters
python fase4_jira.py --cluster CLU-001  # uno concreto
python fase4_jira.py --solo-vacios      # solo clusters sin candidatos
```

Útil tras ejecutar `fase0_jira.py` para refrescar los clusters con los Jiras nuevos sin tocar el pipeline Zendesk.

## 5. Estructura del match

Cada cluster guarda ahora en `jira_candidatos` una lista de objetos enriquecidos (antes eran strings):

```json
"jira_candidatos": [
  {
    "jira_id": "TEC-3082",
    "url": "https://eldiario.atlassian.net/browse/TEC-3082",
    "summary": "[CRM] [BACK] [TARIFA] Dudas nuevo combo...",
    "status": "Backlog",
    "confianza": 0.87,
    "razon": "Mismo error de tarifa combo CRM"
  }
]
```

La UI (`views/detalle_cluster.py`) los renderiza con link, estado, confianza (%) y razón. Hay fallback para clusters antiguos con formato `list[str]`.

## 6. Detalles técnicos

### Endpoint migrado

La API clásica `GET /rest/api/3/search` está **deprecada** (HTTP 410 Gone). Usamos:

| Acción | Endpoint nuevo |
|---|---|
| Listar issues | `GET /rest/api/3/search/jql` |
| Paginación | por `nextPageToken` (no `startAt`) |
| Contar total | `POST /rest/api/3/search/approximate-count` |

El cliente (`jira_client.py`) abstrae todo esto con:

- `fetch_tickets_jql(jql) → Iterator[dict]` (paginado transparente)
- `approximate_count(jql) → int`
- `normalize_issue(raw) → dict`
- `adf_to_text(adf) → str` (extractor del Atlassian Document Format)

### Filtro "finalizado"

En lugar de hardcodear nombres de estados (`Finalizada`, `Rechazado`), se usa `statusCategory.key = "done"`. En el proyecto TEC los estados con category `done` son `Finalizada` y `Rechazado`.

### Descripción ADF

Jira API v3 devuelve `description` en Atlassian Document Format (dict anidado con tipos `paragraph`, `heading`, `bulletList`, `text`, etc.). Se extrae a texto plano con un walker recursivo en `JiraClient.adf_to_text()`.

### Matcher híbrido

```python
def match(cluster, jira_pool, top_k=5):
    signals = cluster_signals(cluster)          # keywords normalizadas
    candidatos = prefilter_keywords(signals,    # score por keywords
                                    jira_pool,
                                    limit=15)
    if not candidatos:
        return []
    return llm_select(signals, candidatos,      # GPT-4o decide matches finales
                      top_k)
```

- **Prefilter**: tokeniza (sin tildes, lowercase, stopwords ES) `summary + description + labels` del Jira; score = cardinalidad del solape con keywords del cluster. Labels pesan x2.
- **LLM**: pasa resumen del cluster + los 15 Jiras pre-filtrados a GPT-4o, devuelve `[{jira_id, confianza, razon}]` en JSON estricto.
- **Sin `OPENAI_API_KEY`**: fallback a los top-k del prefilter con `confianza=None`.

### Coste

- Descarga Jira: ~2 peticiones por ejecución (120 tickets en 2 páginas de 100).
- Matcher: 1 llamada GPT-4o por cluster nuevo (~$0.01/cluster con 15 candidatos en contexto). <$1/día para volúmenes típicos.

## 7. Operación

### Primer arranque

```bash
python fase0_jira.py --full
# ✅ descarga 120 tickets, guarda jira_tickets.json con _meta
```

### Rutina periódica (ej. cron diario)

```bash
python fase0_jira.py              # incremental, <3s
python pipeline.py --horas 24     # pipeline Zendesk (fase 3 ya usa el pool local)
python fase4_jira.py --solo-vacios  # opcional: refresca clusters viejos sin candidatos
```

### Troubleshooting

- **"pool Jira vacío"** al correr `fase4_jira.py`: ejecuta primero `fase0_jira.py --full`.
- **Tests con OpenAI apagado**: `JiraMatcher(openai_client=None, api_key=None)` usa solo el prefilter.
- **Candidatos desactualizados** tras cambios en el matcher: `python fase4_jira.py` recalcula todos.

## 8. Fuera de alcance (futuro)

- **Botón UI** "Adjuntar tickets Zendesk a Jira candidato" — mencionado como siguiente paso.
- **Webhook a n8n** para enriquecer el ticket Jira con el resumen del cluster.
- **Re-matching automático** al entrar Jiras nuevas (hoy es manual vía `fase4_jira.py`).
- **Embeddings** como tercer camino del matcher — hoy solo keywords + LLM.

## 9. Archivos tocados

Detalle en `docs/superpowers/specs/2026-04-17-jira-classification-design.md` y el plan en `docs/superpowers/plans/2026-04-17-jira-classification.md`.

| Archivo | Rol |
|---|---|
| `jira_client.py` | Cliente REST migrado a `/search/jql` |
| `jira_matcher.py` | Matcher híbrido keywords + GPT-4o |
| `fase0_jira.py` | Descarga 60d (full/incremental) |
| `fase3_clusterizar.py` | Usa el matcher local en vez de API en caliente |
| `fase4_jira.py` | Re-matching de clusters |
| `storage.py` | Métodos `get_jira_tickets`, `save_jira_tickets`, `upsert_jira_tickets`, `get_jira_metadata` |
| `views/clusters.py`, `views/detalle_cluster.py` | Render enriquecido con fallback legacy |

Tests: 64/64 passing.
