# Clasificación de tickets Jira y matching con clusters Zendesk

**Fecha:** 2026-04-17
**Estado:** Aprobado, pendiente de plan de implementación
**Autor:** David Murciano
**Alcance:** Feature completa, branch `claude/epic-mestorf-1c7d4a`

---

## 1. Problema y motivación

La UI actual muestra "Jira candidatos" en cada cluster de Zendesk, pero aparece vacío. Dos causas:

1. **Bug en producción**: `jira_client.buscar_tickets_crm()` usa `/rest/api/3/search`, que Atlassian **deprecó** (responde HTTP 410 Gone). Nunca ha funcionado en esta versión.
2. **Modelo insuficiente**: aunque funcionara, una búsqueda por texto libre contra la API en caliente no da buenos matches ni permite razonamiento sobre el dominio.

El valor de negocio del proyecto es **consolidar múltiples tickets de Zendesk de usuarios afectados por el mismo problema en un único ticket de Jira existente**, evitando abrir un Jira por caso individual.

## 2. Solución

1. **Fase 0 Jira**: descarga periódica de tickets del proyecto TEC de los últimos 60 días (excluyendo los ya resueltos) y persistencia en JSON local.
2. **Matcher híbrido**: para cada cluster de Zendesk, pre-filtrar por keywords contra el JSON y decidir matches finales con GPT-4o.
3. **Integración en fase 3**: reemplaza la llamada rota a la API de Jira por el matcher local.
4. **Fase 4 Jira**: script de re-matching para clusters existentes cuando entran nuevas Jiras.

Los tickets de Jira **no se clusterizan** — son un pool de búsqueda contra el que los clusters de Zendesk se emparejan.

## 3. Hallazgos de la prueba de concepto (ejecutada)

PoC ejecutado contra Jira Cloud de elDiario (`eldiario.atlassian.net`, proyecto TEC, cuenta `dmurciano@eldiario.es`):

| Punto | Resultado |
|---|---|
| Auth básica (email:token) | OK |
| Proyecto TEC accesible | OK |
| Endpoint `/rest/api/3/search` | HTTP 410 (deprecado) — obliga a migrar |
| Endpoint `/rest/api/3/search/jql` | Funciona; paginación por `nextPageToken`; NO devuelve `total` |
| Conteo: `/search/approximate-count` (POST) | Devuelve `{count}` para un JQL dado |
| Filtro `statusCategory != Done` | Cubre `Finalizada` y `Rechazado` (únicos con category `done` en TEC) |
| Volumen real últimos 60d, no-Done | **120 tickets** (2 páginas de 100) |
| Campo `description` | Viene en ADF (Atlassian Document Format), dict anidado — necesita extractor a texto plano |
| Estados observados en TEC | `Backlog`, `Revisar`, `Blocked`, `Developing`, `Ready to Deploy`, `Tareas por hacer`, `Dev Review`, `REVISAR`, `PRE - Pendiente de validar`, `Revisar (PRE)`, `Finalizada` (done), `Rechazado` (done) |

## 4. Arquitectura

```
  ┌──────────────────────────────┐
  │ fase0_jira.py (NUEVO)        │  periódico, incremental
  │   lee /search/jql paginado   │
  │   → data/jira_tickets.json   │
  └─────────┬────────────────────┘
            │
  ┌─────────▼────────────────────┐
  │ jira_client.py (MODIFICADO)  │  endpoint nuevo, paginación,
  │   fetch_tickets_jql()        │  extractor ADF→texto
  │   (se elimina buscar_tickets │
  │    _crm — ya no hace falta)  │
  └─────────┬────────────────────┘
            │
  ┌─────────▼────────────────────┐     ┌───────────────────────────┐
  │ jira_matcher.py (NUEVO)      │     │ fase4_jira.py (NUEVO)     │
  │   prefilter_keywords()       │     │  itera clusters y llama   │
  │   llm_select() (GPT-4o)      │◀────│  al matcher de nuevo      │
  │   match(cluster, pool)       │     └───────────────────────────┘
  └─────────┬────────────────────┘
            │ usado en
  ┌─────────▼────────────────────┐
  │ fase3_clusterizar.py (MOD)   │  reemplaza buscar_tickets_crm
  │   jira_pool = storage.get_.. │  por matcher.match()
  │   matcher.match(cluster,…)   │
  └──────────────────────────────┘
```

## 5. Esquema de datos

### `data/jira_tickets.json`

Lista con un registro `_meta` al principio:

```json
[
  {
    "_meta": true,
    "project": "TEC",
    "fecha_inicio": "2026-02-16T00:00:00Z",
    "fecha_fin":    "2026-04-17T08:30:00Z",
    "last_sync":    "2026-04-17T08:30:00Z",
    "total_tickets": 120,
    "filtro": "project = TEC AND statusCategory != Done"
  },
  {
    "jira_id": "TEC-3082",
    "url": "https://eldiario.atlassian.net/browse/TEC-3082",
    "summary": "[CRM] [BACK] [TARIFA] [COMBO] Dudas nuevo combo elDiario.es + Sapiens",
    "description_text": "Texto plano extraído del ADF...",
    "status": "Backlog",
    "status_category": "new",
    "priority": "High",
    "issuetype": "Tarea",
    "labels": ["BITBAN-CRM", "CRM"],
    "components": [],
    "assignee": "Bitban CRM",
    "created": "2026-04-13T12:37:19+02:00",
    "updated": "2026-04-17T06:37:27+02:00"
  }
]
```

Decisiones:
- `_meta.true` marca el registro de metadatos y `storage.get_jira_tickets()` lo filtra.
- `description_text` — texto plano extraído del ADF; necesario para el matcher de keywords. Se descarta el ADF en bruto.
- `status_category` (`new`, `indeterminate`, `done`) facilita el filtro programático.
- Upsert por `jira_id`. Si un ticket pasa a `statusCategory=done`, se **elimina** del JSON (no nos interesan resueltos).

### Cambio de esquema en `clusters.json` — `jira_candidatos`

Pasa de `list[str]` a `list[dict]`:

```json
"jira_candidatos": [
  {
    "jira_id": "TEC-3082",
    "url": "https://eldiario.atlassian.net/browse/TEC-3082",
    "summary": "[CRM] [BACK] [TARIFA]...",
    "status": "Backlog",
    "confianza": 0.87,
    "razon": "Mismo error de Stripe con cobros duplicados"
  }
]
```

Compatibilidad hacia atrás: las vistas detectan `isinstance(item, str)` y renderizan clusters antiguos hasta que `fase4_jira.py` los actualice.

## 6. Componentes

### 6.1 `jira_client.py` (modificado)

- **Eliminar** `buscar_tickets_crm()` (endpoint deprecado, búsqueda sustituida por matcher local).
- **Añadir** `fetch_tickets_jql(jql, fields) -> Iterator[dict]` que pagina con `/search/jql?jql=...&nextPageToken=...` hasta `isLast=True`. Max 100 por página. Reintento en 429 con `Retry-After`.
- **Añadir** `approximate_count(jql) -> int` que hace POST a `/search/approximate-count`.
- **Añadir** `adf_to_text(adf: dict) -> str` — walker recursivo que recorre `content[]` y extrae todo nodo tipo `text`, con saltos entre `paragraph`/`heading`/`listItem`/`tableRow`. Si recibe `None` devuelve `""`.
- **Añadir** `normalize(issue: dict) -> dict` que produce el esquema de 5.1 (sin `_meta`).

### 6.2 `storage.py` (modificado)

Nuevos métodos:
- `get_jira_tickets() -> list[dict]` — devuelve todos excepto `_meta`.
- `get_jira_metadata() -> dict` — devuelve el registro `_meta` o `{}`.
- `save_jira_tickets(tickets: list[dict], meta: dict) -> None` — reescribe `jira_tickets.json` con `meta` al principio. Para modo FULL.
- `upsert_jira_tickets(nuevos: list[dict], done_ids: set[str], meta: dict) -> None` — upsert por `jira_id`, borra los `done_ids`, reescribe con `meta`.

### 6.3 `fase0_jira.py` (nuevo)

CLI:
```
python fase0_jira.py            # incremental (default)
python fase0_jira.py --full     # re-descarga completa ventana
python fase0_jira.py --days 60  # ventana, default 60
```

Lógica:
1. Leer `_meta`. Si no existe o `--full` → modo FULL.
2. Construir JQL:
   - FULL: `project=TEC AND statusCategory != Done AND updated >= -{days}d ORDER BY updated DESC`
   - INCREMENTAL: `project=TEC AND updated >= '{fecha_fin - 10min}' ORDER BY updated DESC`  (incluye done para detectar cierres)
3. Paginar con `fetch_tickets_jql()`.
4. Clasificar resultados:
   - `statusCategory == "done"` → añadir a `done_ids` (se borrarán).
   - Resto → normalizar y meter en `nuevos`.
5. Llamar a `storage.save_jira_tickets()` (FULL) o `upsert_jira_tickets()` (INCREMENTAL).
6. Actualizar `_meta` con `last_sync`, `fecha_fin`, `total_tickets` (via `approximate_count` sobre JQL base).

Stats por stdout: descargados, upsertados, borrados por done, total en JSON.

### 6.4 `jira_matcher.py` (nuevo)

```python
class JiraMatcher:
    def __init__(self, openai_client=None, model="gpt-4o"):
        ...

    def match(self, cluster: dict, jira_pool: list[dict], top_k: int = 5) -> list[dict]:
        """Devuelve lista de jira_candidatos enriquecidos para un cluster."""
        signals = self._cluster_signals(cluster)
        candidatos = self._prefilter_keywords(signals, jira_pool, limit=15)
        if not candidatos:
            return []
        return self._llm_select(signals, candidatos, top_k)
```

**`_cluster_signals`** extrae del cluster:
- `anclas`: valores de `cluster.anclas` (sistema, tipo_problema, etc.)
- `resumen`: `cluster.resumen`
- `keywords`: tokens normalizados (lowercase, sin tildes, sin stopwords ES) del resumen + tipo_problema + sistema.

**`_prefilter_keywords`**:
- Para cada Jira, concatena `summary + description_text + labels`.
- Normaliza igual que las keywords del cluster.
- `score = |keywords_cluster ∩ tokens_jira| + 2 * |labels_matching|`.
- Filtra `score > 0`, ordena desc, devuelve `limit` primeros.

**`_llm_select`**:
- Prompt a GPT-4o con el resumen del cluster + los 15 candidatos (jira_id, summary, labels, status).
- Formato JSON estricto con `{"matches": [{"jira_id", "confianza", "razon"}]}`.
- Enriquece cada match con los campos del Jira original del pool (url, summary, status).
- Ordena por `confianza` desc, trunca a `top_k`.

Guard: si `OPENAI_API_KEY` no está, `_llm_select` devuelve los 5 mejores del prefilter con `confianza=None` y `razon="sin LLM disponible"`.

### 6.5 `fase3_clusterizar.py` (modificado)

Reemplazar:
```python
jira_candidatos = self.jira_client.buscar_tickets_crm(resumen)
```

Por:
```python
jira_pool = self.storage.get_jira_tickets()  # cachear en constructor
jira_candidatos = self.matcher.match(cluster_info, jira_pool, top_k=5)
```

Si `jira_pool` está vacío (JSON no descargado), `match()` devuelve `[]` y el pipeline sigue. Log de warning.

### 6.6 `fase4_jira.py` (nuevo)

CLI:
```
python fase4_jira.py                   # todos los clusters
python fase4_jira.py --cluster <id>    # uno solo
python fase4_jira.py --solo-vacios     # solo los sin candidatos
```

Lógica:
1. `jira_pool = storage.get_jira_tickets()`.
2. Iterar `storage.get_clusters()`. Por cada uno:
   - Construir `cluster_info` (mismo shape que espera el matcher).
   - `matcher.match(cluster_info, jira_pool)`.
   - Sobrescribir `cluster["jira_candidatos"]` y `storage.save_cluster(cluster)`.
3. Stats: clusters procesados, con candidatos nuevos, sin cambios, antes con candidatos legacy string convertidos.

### 6.7 Ajustes UI

- **`views/clusters.py:281`**: `jira_list = ", ".join(_jira_id(c) for c in cluster.get("jira_candidatos", [])) or "—"` donde `_jira_id` acepta str o dict.
- **`views/detalle_cluster.py:39-44`**: render enriquecido:
  ```
  🔗 Jira candidatos
  - [TEC-3082](link) · `Backlog` · 87% — Mismo error de Stripe...
  ```
  Con defensive para legacy (si `item` es str, solo el link).

## 7. Documentación a actualizar

1. **`docs/DESIGN.md`**: actualizar sección 3 (embudo) para incluir el matching Jira como fase complementaria, y sección 7 (stack) para reflejar endpoint `/search/jql`.
2. **`docs/IMPLEMENTACION_TECNICA.md`**:
   - Sección 1 (estructura): añadir `fase0_jira.py`, `jira_matcher.py`, `fase4_jira.py`, `data/jira_tickets.json`.
   - Nueva sección sobre el endpoint `/search/jql` y migración desde `/search`.
   - Ejemplos CLI de los nuevos scripts.
3. **`docs/arquitectura-general.svg`**: añadir caja "Jira JSON local" y flecha al matcher de fase 3. Retitular el bloque Jira existente si procede.
4. **`docs/flujo-embudo.svg`**: añadir el paso de matching con Jira tras la asignación a cluster en fase 3. No es una fase aparte del embudo (los tickets de Jira no pasan el embudo), pero el matcher sí forma parte del flujo.
5. **`README.md`** (si existe y menciona comandos): añadir los nuevos scripts a la sección de uso.

## 8. Tests

`tests/test_jira_client.py` (reemplaza los tests existentes):
- Los 5 tests actuales (`test_buscar_tickets_crm_*`) se eliminan junto con el método.
- Nuevos tests: mock `urllib.request.urlopen`. Verifica: paginación con `nextPageToken`, parada en `isLast=True`, extractor ADF con estructura anidada (tabla con celdas, listas, párrafos), normalización de campos, reintento en 429.

`tests/test_fase3.py` (modificado):
- Reemplazar los mocks de `jira.buscar_tickets_crm` (líneas 18, 98) por mocks de `matcher.match()` y `storage.get_jira_tickets()`.

`tests/test_jira_matcher.py`:
- Fixtures: 1 cluster, 10 Jiras (unos matching y otros no).
- Sin mock de OpenAI: testear `_prefilter_keywords` directamente (determinista).
- Con mock de OpenAI: verificar que el prompt incluye el resumen del cluster y los 15 candidatos, y que la respuesta se parsea a objetos enriquecidos.
- Test del guard cuando no hay `OPENAI_API_KEY`.

`tests/test_fase0_jira.py`:
- Modo FULL: JSON vacío → llama a `save_jira_tickets` con todo.
- Modo INCREMENTAL: detecta dos Jiras con `statusCategory=done` y los marca para borrar.
- Verifica que `_meta` se actualiza.

`tests/test_storage_jira.py`:
- `_meta` al principio, `get_jira_tickets()` no lo devuelve.
- `upsert_jira_tickets` hace upsert correcto y borra los de `done_ids`.

## 9. Criterios de éxito

- [ ] `python fase0_jira.py` baja los 120 tickets reales y los persiste con `_meta` correcto.
- [ ] `python fase0_jira.py` (segunda ejecución) es incremental — solo pide Jiras actualizadas desde `fecha_fin`, y tarda notablemente menos que la primera.
- [ ] Al correr el pipeline Zendesk normal con al menos un cluster nuevo, el cluster queda con `jira_candidatos` poblado con objetos ricos (no strings).
- [ ] `python fase4_jira.py` sin argumentos refresca todos los clusters existentes.
- [ ] La UI (`views/detalle_cluster.py`) muestra los candidatos con link, status y confianza sin romperse en clusters legacy.
- [ ] Tests pasan (`pytest`).
- [ ] Docs actualizados: `DESIGN.md`, `IMPLEMENTACION_TECNICA.md`, ambos SVG, README si aplica.
- [ ] Se elimina `poc_jira.py` (cumplió su propósito).

## 10. Fuera de alcance

- **Botón UI** para "vincular tickets Zendesk al ticket Jira" (se mencionó para el futuro).
- **Webhook a n8n** para enriquecer Jira desde el panel.
- **Alertas** cuando un cluster queda sin candidatos Jira (podría indicar un problema nuevo que no tiene Jira abierto todavía).
- **Embeddings** como tercer camino del matcher — por ahora solo keywords + LLM.
- **Modificar el pipeline Zendesk** para re-procesar tickets ya procesados cuando el matcher se afina.

## 11. Decisiones abiertas (resueltas durante brainstorming)

| Pregunta | Decisión |
|---|---|
| Qué tickets Jira descargar | Todos los de TEC (sin filtro de label) |
| Qué es "finalizado" | `statusCategory.key == "done"` |
| Estrategia de matching | Híbrido keywords + LLM |
| Cuándo se matchea | Ambos: en fase 3 al crear cluster + script re-matcher (`fase4_jira.py`) |
| Incremental vs full | Híbrido: incremental por defecto, flag `--full` disponible |
| PoC antes o después del diseño | Antes — reveló que el endpoint actual está roto |

## 12. Riesgos

1. **Coste LLM**: ~$0.01/cluster nuevo. Para 50 clusters/día, <$1/día. Asumible. Mitigación: batch sizes configurables (ahora fijos en 15 candidatos al LLM).
2. **Cambios en la API de Jira**: el nuevo endpoint `/search/jql` también está en evolución (reciente). Mitigación: PoC hecho, tests de paginación con mocks.
3. **ADF parsing incompleto**: nodos de tipo no cubierto (mention, emoji, mediaSingle) se ignoran silenciosamente. Mitigación: walker retorna string incluso si pierde algún nodo; el summary ya tiene casi toda la señal.
4. **Clusters legacy** con `jira_candidatos: [str, ...]`: la UI tiene fallback. `fase4_jira.py` los actualiza al primer run.
