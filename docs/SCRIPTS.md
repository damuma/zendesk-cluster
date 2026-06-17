# Scripts — referencia de flags

Todos los scripts se ejecutan desde la raíz del proyecto con el entorno virtual activo.

---

## `pipeline.py` — Pipeline principal

Descarga tickets Zendesk (incrementales), los filtra, pre-clasifica, clusteriza y busca candidatos Jira.

```
python pipeline.py [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--horas` | int | `24` | Ventana de tickets recientes a procesar (horas hacia atrás) |
| `--fase0` | bool | off | Ejecuta también `fase0_explorar.py` para regenerar taxonomía |
| `--days` | int | `30` | Días de historia que pasa a `fase0_explorar` (sólo con `--fase0`) |
| `--dry-run` | bool | off | Muestra qué haría sin escribir nada en disco |

**Ejemplos:**

```bash
python pipeline.py                       # procesa últimas 24 h
python pipeline.py --horas 48            # últimas 48 h
python pipeline.py --fase0 --days 60     # regenera taxonomía + pipeline
python pipeline.py --dry-run             # sin escrituras
```

---

## `fase0_explorar.py` — Exploración NLP histórica

Genera `config/conceptos.json` con la taxonomía de señales (sistemas, tipos de problema) extraída de los tickets locales. El fichero vive en `config/` porque se versiona en git (no contiene datos sensibles).

```
python fase0_explorar.py [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--days` | int | `30` | Ventana de tickets a analizar |
| `--output` | str | `config/conceptos.json` (o `$CONFIG_DIR/conceptos.json`) | Ruta de salida del JSON de taxonomía |

**Ejemplos:**

```bash
python fase0_explorar.py
python fase0_explorar.py --days 90 --output config/conceptos_q1.json
```

---

## `fase0_jira.py` — Descarga de tickets Jira

Descarga tickets del proyecto TEC a `data/jira_tickets.json`.

```
python fase0_jira.py [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--full` | bool | off | Re-descarga completa (ignora `last_sync`, borra caché local) |
| `--days` | int | `60` | Ventana de tickets a descargar en modo incremental |

**Ejemplos:**

```bash
python fase0_jira.py               # incremental, últimos 60 días
python fase0_jira.py --full        # descarga completa desde cero
python fase0_jira.py --days 30     # incremental, últimos 30 días
```

---

## `fase35_refine.py` — Refinado de clusters heterogéneos

Detecta clusters demasiado amplios (alta heterogeneidad) y los divide en sub-clusters precisos mediante LLM.

```
python fase35_refine.py [flags]
```

| Flag | Tipo | Default | Env override | Descripción |
|------|------|---------|--------------|-------------|
| `--min-tickets` | int | `15` | `REFINE_MIN_TICKETS` | Mínimo de tickets para considerar un cluster candidato a refinar |
| `--het-min` | float | `0.5` | `REFINE_HETEROGENEITY_MIN` | Umbral mínimo de puntuación de heterogeneidad (0–1) |

**Ejemplos:**

```bash
python fase35_refine.py                        # valores por defecto / .env
python fase35_refine.py --min-tickets 10 --het-min 0.4
```

---

## `fase4_jira.py` — Matching Jira por cluster

Busca y actualiza candidatos Jira para los clusters activos.

```
python fase4_jira.py [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--cluster` | str | `None` | Procesa sólo el cluster indicado (ej. `CLU-091`) |
| `--solo-vacios` | bool | off | Procesa únicamente los clusters sin `jira_candidatos` |

**Ejemplos:**

```bash
python fase4_jira.py                        # todos los clusters abiertos
python fase4_jira.py --cluster CLU-091      # sólo CLU-091
python fase4_jira.py --solo-vacios          # sólo clusters sin candidatos Jira
```

---

## `scripts/reingest_all.py` — Re-ingesta completa desde cero

Hace backup de los datos actuales, los borra y ejecuta el pipeline de nuevo desde cero. Útil tras cambios estructurales de schema o correcciones masivas.

```
python -m scripts.reingest_all [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--days` | int | `30` | Ventana de días que descargará de Zendesk |
| `--dry-run` | bool | off | Muestra el plan (backup + truncate) sin ejecutar nada |
| `--refresh-users` | bool | off | También purga `data/zendesk_users.json` para re-resolver requester_email (útil si usuarios cambiaron email en Zendesk) |

**Ejemplos:**

```bash
python -m scripts.reingest_all --dry-run          # ver qué haría
python -m scripts.reingest_all --days 60          # re-ingesta con 60 días
python -m scripts.reingest_all --refresh-users    # re-ingesta + refresh caché usuarios
```

> Los backups se guardan en `data/` con sufijo de timestamp antes de borrar.

---

## `scripts/dedupe_jira_candidates.py` — Limpieza de candidatos Jira

Recorta `jira_candidatos` de cada cluster al top-N (sin llamar al LLM). Útil como one-off cuando clusters acumularon más candidatos de los debidos.

```
python -m scripts.dedupe_jira_candidates [flags]
```

| Flag | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `--cap` | int | `5` | Número máximo de candidatos a conservar por cluster |
| `--dry-run` | bool | off | Muestra el plan sin escribir cambios |

**Criterio de ordenación:** email_match primero, luego confianza descendente.

**Ejemplos:**

```bash
python -m scripts.dedupe_jira_candidates --dry-run      # ver cambios
python -m scripts.dedupe_jira_candidates                # aplicar (top-5)
python -m scripts.dedupe_jira_candidates --cap 3        # recortar a top-3
```

---

## `scripts/tag_ticket.py` — Etiquetado manual de tickets Zendesk

Añade tags a un ticket Zendesk directamente vía API. Herramienta puntual de operaciones; el pipeline **no** etiqueta automáticamente.

```
python scripts/tag_ticket.py <ticket_id> <tag> [tag2 ...]
```

| Argumento | Tipo | Descripción |
|-----------|------|-------------|
| `ticket_id` | int (posicional) | ID numérico del ticket Zendesk |
| `tags` | str... (posicional) | Uno o más tags a añadir |

**Ejemplos:**

```bash
python scripts/tag_ticket.py 538248 error_acceso
python scripts/tag_ticket.py 538248 error_acceso cluster_clu091
```

---

## `extraer_socios_apoya.py` — Remitentes a socios@ / apoya@ en una ventana

Lista, por buzón, las personas que escribieron a `socios@eldiario.es` y `apoya@eldiario.es`
dentro de una ventana temporal y que **no volvieron a contactar** después. Documentación
completa y reglas de negocio en [`EXTRACCION_SOCIOS_APOYA.md`](EXTRACCION_SOCIOS_APOYA.md).

```
python extraer_socios_apoya.py [flags]
```

| Flag | Default | Descripción |
|------|---------|-------------|
| `--start` | `2026-03-04` | Inicio de ventana (incluido), `YYYY-MM-DD` |
| `--window-end` | `2026-04-08` | Fin de ventana (incluido), `YYYY-MM-DD` |
| `--output-dir` | `data/socios_apoya` | Carpeta de salida de los CSV |
| `--users-cache` | `data/zendesk_users.json` | Cache id→email de usuarios Zendesk |
| `--exclude-domains` | `eldiario.es` | Dominios de remitente a excluir (internos). Vacío = ninguno |
| `--thread-replies` | off | Descarta también a quien respondió dentro de su mismo hilo a socios/apoya tras el fin de ventana (descarga comentarios, lento) |
| `--raw-cache` | — | Ruta JSON para cachear/reutilizar los tickets descargados entre re-ejecuciones |

**Salida:** `{socios,apoya}_mantener.csv`, `{socios,apoya}_descartar.csv` y, si aplica,
`sin_atribuir.csv` en `--output-dir`. Fechas en horario Europe/Madrid; descarte global
(vale cualquiera de los dos buzones a partir del día siguiente al fin de ventana).

**Ejemplos:**

```bash
python extraer_socios_apoya.py                                  # rápido (solo ticket nuevo)
python extraer_socios_apoya.py --thread-replies \
    --raw-cache data/socios_apoya/_raw_tickets.json             # criterio estricto (entrega)
python extraer_socios_apoya.py --exclude-domains                # incluye internos @eldiario.es
```

---

## `scripts/socios_apoya_a_excel.py` — CSV de socios/apoya → Excel formateado

Combina los CSV generados por `extraer_socios_apoya.py` en un único `.xlsx` con una pestaña
por lista (más una de resumen), cabecera fija y autofiltro. Resume las columnas `contacto_N`
a primer/segundo/tercer contacto + «otras interacciones». Ver [`EXTRACCION_SOCIOS_APOYA.md`](EXTRACCION_SOCIOS_APOYA.md).

```
python scripts/socios_apoya_a_excel.py [flags]
```

| Flag | Default | Descripción |
|------|---------|-------------|
| `--input-dir` | `data/socios_apoya` | Carpeta con los CSV de entrada |
| `--output` | `<input-dir>/socios_apoya.xlsx` | Ruta del `.xlsx` de salida |
