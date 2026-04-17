# Match Jira↔Zendesk por email + Refine de clusters por subtipo

**Fecha:** 2026-04-17
**Estado:** Pendiente de revisión
**Autor:** David Murciano
**Alcance:** Feature combinada (re-ingesta Zendesk + email-match determinístico + refine LLM de clusters)

---

## 1. Problema y motivación

Dos problemas que se refuerzan mutuamente en el matching actual Jira↔Zendesk:

### 1.1. No hay señal determinística en el matching

`JiraMatcher.match()` usa keyword-prefilter + GPT-4o sobre el `(resumen, anclas)` del cluster vs `(summary, description_text, labels)` de cada Jira. Es 100% textual-semántico, sin anclas indubitables.

**Ejemplo real (Jira TEC-3091)**: la descripción cita literalmente `santiagolaparra@gmail.com` (socio comprador) y `mabro96@gmail.com` (beneficiario). El ticket de Zendesk correspondiente tiene a uno de ellos como requester. Hoy no se aprovecha esa señal.

### 1.2. Clusters demasiado abiertos producen matches ruidosos

El pipeline `fase3_clusterizar.py` clasifica por 2 dimensiones `(sistema, tipo_problema)`. Es insuficiente. **Caso CLU-007**:

- `tipo_problema: error_acceso`, `sistema: auth_login`, `ticket_count: 76`.
- Mezcla tickets heterogéneos: "no puedo loguear", "no puedo leer en móvil estando logado", "soy otra persona distinta" (confusión de identidad), "pago bloqueado con error login", tickets con `subject`/`body` vacíos (residuos de purge).
- `jira_candidatos` incluye TEC-3091 (SUSCRIPCION_REGALO) con `confianza: 0.9` — ruido puro, consecuencia directa de que el cluster es tan amplio que su prompt al LLM permite casi cualquier Jira con vocabulario de acceso/login.

El streaming per-ticket en Fase 3 no corrige drift: una vez el cluster es ancho, atrae más. No existe rebalance global.

### 1.3. Limitación adicional detectada

Los tickets Zendesk persistidos en `data/tickets.json` **no guardan `requester_email` ni `requester_id`** (el `_normalize()` de `ZendeskClient` sí lo produce en memoria, pero el dump en disco lo pierde). Sin email almacenado no se puede cruzar contra emails extraídos de Jira.

## 2. Solución

Tres iniciativas combinadas en un único spec y una única re-ingesta:

1. **Enriquecimiento de emails en Zendesk** (Fase 0.5 nueva + extensión de Fase 2): cada ticket persiste `requester_email` y `emails_mencionados` (extraídos del `body_preview` y, opcionalmente, de comentarios).
2. **Refine de clusters por subtipo** (Fase 3.5 nueva): paso batch post-Fase 3 que detecta clusters heterogéneos y los divide en sub-clusters usando un modelo de razonamiento.
3. **Email-aware matcher** (evolución de `JiraMatcher`): email coincidente es señal fuerte pero no sustituye la validación de concepto por LLM; se inyecta como hint estructurado en el prompt.

Re-ingesta completa desde Zendesk aprovechando el cambio estructural. No se preservan los `cluster_id` actuales.

## 3. Pipeline resultante

```
Fase0 (Jira pool + conceptos)
   ↓
Fase0.5 (cache user_id→email de Zendesk)            ← NUEVO
   ↓
Zendesk ingest (incremental/cursor.json)
   ↓
Fase1 (filtrar técnico vs no-técnico)
   ↓
Fase2 (anclas + extracción emails body)             ← EXTENDIDA
   ↓
Fase3 (cluster streaming)                           ← soporta padres refinados
   ↓
Fase3.5 (refine batch por subtipo)                  ← NUEVO
   ↓
Fase4 (re-match Jira, email-aware)                  ← EVOLUCIONADA
```

## 4. Modelo de datos

### 4.1. Ticket Zendesk (`data/tickets.json`)

Campos añadidos:

```json
{
  "requester_id": 12345,              // NUEVO (ya venía en _normalize, ahora se persiste)
  "requester_email": "user@x.com",    // NUEVO — resuelto vía cache/API
  "emails_mencionados": [             // NUEVO — regex sobre body_preview (y comments si se incluyen)
    "abc@y.com"
  ],
  "emails_asociados": [               // NUEVO (derivado) — union de los dos anteriores, normalizados
    "user@x.com", "abc@y.com"
  ]
}
```

`emails_asociados` es el campo que consumen Fase3.5 y Fase4. Todos los emails en minúsculas, sin espacios, deduplicados.

### 4.2. Cluster (`data/clusters.json`)

Campos añadidos:

```json
{
  "subtipo": "login_desde_email_newsletter",  // NUEVO — etiqueta semántica del sub-cluster
  "parent_cluster_id": "CLU-007",              // NUEVO — null si es cluster raíz
  "refined_at": "2026-04-17T10:00:00Z",        // NUEVO — timestamp del refine que lo creó (o null)
  "estado": "abierto" | "refined" | "cerrado"  // ya existía; nuevo valor "refined" para padres divididos
}
```

Los cluster IDs hijos siguen el esquema `CLU-NNN-A`, `CLU-NNN-B`, …, preservando la traza al padre en el propio ID. El padre queda con `estado: refined`, `ticket_ids` vacío (se mueven a los hijos), `jira_candidatos` vacío, y conserva `nombre`/`resumen` para la UI.

### 4.3. Candidato Jira (elemento de `cluster.jira_candidatos`)

Campos añadidos:

```json
{
  "jira_id": "TEC-3091",
  "confianza": 0.97,
  "razon": "email de usuario (mabro96@gmail.com) + concepto coincidente — ...",
  "email_match": [                            // NUEVO — trazabilidad
    {"email": "mabro96@gmail.com", "zendesk_id": 533284}
  ]
}
```

`email_match` es `[]` o ausente cuando no aplica. Cuando aplica y el LLM confirma, `confianza ≥ 0.95`. Un mismo email puede aparecer varias veces si lo comparten varios tickets del cluster (una entrada por `(email, zendesk_id)`).

### 4.4. Nuevo fichero: `data/zendesk_users.json`

Cache `user_id → {email, name, role}`. Poblado en Fase0.5 y mantenido en sucesivas ingestas. Reduce llamadas a `/users/{id}.json` y permite re-resolver tickets antiguos en local.

```json
{
  "12345": {"email": "user@x.com", "name": "User", "role": "end-user"},
  "67890": {"email": "agent@x.com", "name": "Agent", "role": "agent"}
}
```

## 5. Fase 0.5 — Enriquecimiento de emails Zendesk

Nuevo módulo `fase0_zendesk_users.py` (o método en `ZendeskClient`).

**Estrategia**: usar side-loading `?include=users` donde Zendesk lo permite.

- La API incremental de tickets (`/incremental/tickets/cursor.json`) **no soporta** `include=users` de forma fiable en todos los planes → estrategia híbrida:
  1. Durante ingest: recolectar `requester_id` de cada ticket.
  2. En lote, para los IDs no presentes en el cache, llamar a `/users/show_many.json?ids=...` (hasta 100 por petición) y poblar `data/zendesk_users.json`.
- Aplicar el cache en `ZendeskClient._normalize()` (o en una pasada post-fetch) para inyectar `requester_email`.

**Rate limiting**: ya existe `_get_with_retry` con backoff 429. Reusar.

**Permisos**: la cuenta API debe ser admin/agent (end-users no pueden listar otros users). Verificar en fase de prueba.

**Fallback**: si `/users/show_many` no devuelve un id (usuario borrado), se marca `requester_email: null` y se registra en log. El ticket sigue procesándose.

## 6. Fase 2 extendida — Extracción de emails del cuerpo

En `fase2_preclasificar.py`, añadir:

```python
EMAIL_RE = re.compile(r"[\w\.\-\+]+@[\w\-]+(?:\.[\w\-]+)+")

def _extract_emails(texto: str) -> list[str]:
    if not texto:
        return []
    raw = EMAIL_RE.findall(texto)
    return sorted({e.lower().strip(".,;:") for e in raw})
```

Se ejecuta contra `subject + body_preview`. Resultado → `ticket["emails_mencionados"]`.

Luego se calcula `emails_asociados = sorted(set([requester_email] + emails_mencionados) - {None, ""})`.

**No se escanean comentarios de Zendesk en v1** (requiere llamada extra `/tickets/{id}/comments.json` por ticket). Se deja como feature futura si la cobertura de v1 resulta insuficiente.

## 7. Fase 3.5 — Refine batch por subtipo

Nuevo módulo `fase35_refine.py`. Se ejecuta al final del pipeline batch.

### 7.1. Selección de clusters a refinar

Un cluster entra al refine si cumple **al menos una** de estas condiciones:

- `ticket_count ≥ 15` (umbral configurable, default 15).
- `heterogeneidad_score ≥ 0.5`, definida como `1 - (max(counter(ancla_sistema_por_ticket)) / ticket_count)` — ratio de tickets que NO están en el sistema modal del cluster. Requiere que Fase 2 persista `anclas.sistemas` por ticket (ya lo hace).
- `jira_candidatos` incluye Jiras de `sistema` distinto al del cluster en ≥30% de los candidatos.

Cluster ya refinado (`estado: refined` o `refined_at` reciente < 24h) se salta.

### 7.2. Prompt de split

Se usa un **modelo de razonamiento de OpenAI** (por config `OPENAI_MODEL_REFINE`, default `gpt-5.4`; fallback `gpt-4o` si no disponible).

Input al LLM: por cada ticket del cluster `{zendesk_id, subject, body_preview[:500], anclas}`. Para clusters muy grandes (>40 tickets), se hace en 2 pasadas: primera agrupa 40, segunda integra el resto.

Prompt:

```
Eres un ingeniero de soporte técnico. Te doy un CLUSTER de tickets
que ha sido clasificado como "{tipo_problema} en {sistema}" pero
es demasiado amplio.

Divide los tickets en SUBGRUPOS homogéneos por subtipo de
problema técnico concreto. Cada subgrupo debe describir UN fallo
específico reproducible, no una categoría genérica.

Reglas:
- Si todos los tickets son realmente del mismo subtipo, devuelve
  un único grupo.
- Los tickets con subject y body vacíos o con solo metadata
  ("Conversation with Web User...") agrúpalos en un grupo
  "sin_contenido" — no intentes clasificarlos.
- Un ticket va a exactamente un subgrupo.

TICKETS:
[{"zendesk_id": ..., "subject": ..., "body_preview": ...}, ...]

Responde SOLO JSON:
{
  "subgrupos": [
    {
      "subtipo": "snake_case_id",
      "nombre": "Descripción corta",
      "resumen": "Una frase explicando el problema común",
      "ticket_ids": [12345, ...]
    }
  ]
}
```

### 7.3. Aplicación del resultado

- Si `len(subgrupos) == 1`: no-op, sólo marcar `refined_at`.
- Si `len(subgrupos) ≥ 2`:
  1. Por cada subgrupo, crear hijo `CLU-NNN-A`, `CLU-NNN-B`, … heredando `sistema`, `tipo_problema`, `severidad` del padre, y añadiendo `subtipo`, `parent_cluster_id`, `resumen` del subgrupo, `ticket_ids` del subgrupo.
  2. Por cada hijo, invocar `JiraMatcher.match(hijo, jira_pool)` → poblar `jira_candidatos` del hijo.
  3. Padre: `estado: "refined"`, `ticket_ids: []`, `jira_candidatos: []`, `refined_at: now`.
  4. Guardar padre + hijos en `clusters.json` (transaccional — escribir tempfile + rename).

### 7.4. Integración con Fase 3 streaming

Fase 3 streaming necesita convivir con la existencia de padres refinados:

- `clusters_resumen` en el prompt incluye sólo clusters con `estado: abierto`. Los padres con `estado: refined` se excluyen (no son asignables). Los **hijos** tienen `estado: abierto` y aparecen con normalidad, con sus campos `subtipo` y `parent_cluster_id` visibles para el LLM.
- Si el ticket encaja en un hijo, se le asigna directamente (flujo normal `ASIGNAR_EXISTENTE`).
- Si ningún hijo encaja pero el concepto es cercano al padre, Fase 3 puede proponer acción `CREAR_SUBCLUSTER` con `parent_cluster_id: CLU-NNN` → crea un nuevo hijo `CLU-NNN-C`. El padre se mantiene `refined`; sólo cambia que ahora tiene un hijo más.
- Si nada encaja, flujo normal `CREAR_NUEVO` (cluster raíz nuevo, sin parent).

## 8. Email-aware `JiraMatcher`

Evolución de `jira_matcher.py`.

### 8.1. Nuevos métodos privados

```python
def _extract_jira_emails(self, jira: dict) -> set[str]:
    """Extrae emails del description_text + summary del Jira. Cache local por jira_id."""

def _cluster_emails(self, cluster: dict, tickets_by_id: dict[int, dict]) -> set[str]:
    """Devuelve union de emails_asociados de los tickets del cluster."""
```

`tickets_by_id` se pasa desde el caller (`Fase3Clusterizador` o `Fase4`) para evitar cargar `tickets.json` dentro del matcher.

### 8.2. Modificación de `match()`

Orden de operaciones (reemplaza el actual en [jira_matcher.py:154](jira_matcher.py:154)):

1. `signals = _cluster_signals(cluster)`.
2. `cluster_emails = _cluster_emails(cluster, tickets_by_id)`.
3. Keyword prefilter: top-15 como hoy (`_prefilter_keywords`).
4. **Email augment**: recorrer `jira_pool`, añadir al pool de candidatos aquellos con `_extract_jira_emails(j) ∩ cluster_emails ≠ ∅` (aunque el keyword-score sea 0). Dedup por `jira_id`.
5. **Enriquecer brief al LLM**: a cada candidato se le añade `email_match: [{zendesk_id, email}]` cuando aplica. `brief` pasa a incluir ese campo.
6. Prompt del LLM se extiende con estas instrucciones:

   > *"`email_match` indica que este Jira menciona al mismo usuario que uno o más tickets del cluster. Es una señal fuerte de relevancia **si** el problema técnico del Jira también coincide con el cluster. Si el problema diverge (mismo usuario, incidencia distinta), descártalo igualmente — no aceptes sólo por coincidencia de usuario."*

7. Post-procesado: si el LLM confirma un candidato con `email_match ≠ []`:
   - `confianza_final = max(confianza_llm, 0.95)`.
   - `razon = f"email de usuario ({email}) + concepto coincidente — {razon_llm}"`.
   - Se copia `email_match` al match final.

### 8.3. Cuando el LLM está deshabilitado

Si `openai is None` (modo sin LLM), el comportamiento actual devuelve top-k por score. Con email-match:

- Los candidatos con `email_match ≠ []` van primero con `confianza: 0.9`, `razon: "email match sin validación LLM — verificar concepto manualmente"`.
- El resto como hoy.

Se mantiene la escotilla para desarrollo/tests offline.

## 9. Plan de re-ingesta

Script one-off `scripts/reingest_all.py`:

1. Backup: `data/tickets.json` → `data/tickets.json.bak-reingest-<timestamp>`. Idem `clusters.json`.
2. Truncar `tickets.json`, `clusters.json`.
3. Ejecutar Fase0.5 (poblar `zendesk_users.json`) con todos los `requester_id` extraíbles de un primer pase rápido sobre Zendesk (o recolectados on-the-fly en el loop siguiente).
4. Ingestar tickets con `ZendeskClient.get_tickets(days_back=30)` (o el rango actual), enriquecidos con email.
5. Pasar por Fases 1, 2, 3 del pipeline existente.
6. Ejecutar Fase 3.5 (refine batch).
7. Ejecutar Fase 4 (re-match Jira email-aware) sobre todos los clusters finales.

El script es idempotente (no destructivo hasta el backup explícito). Se añade un flag `--dry-run` para simular sin escribir.

**Días de histórico a re-ingestar**: mismo rango que la ingesta actual (`days_back=30`, configurable). El histórico más allá de 30 días no está en `tickets.json` actual, así que no se pierde nada adicional con la re-ingesta.

## 10. Cambios en UI

`views/detalle_cluster.py`:

- **Cluster padre refinado**: mostrar banner "Este cluster fue dividido en N sub-clusters por el paso de refine. [ver sub-clusters]" con enlaces a los hijos. No mostrar `ticket_ids` ni `jira_candidatos` (están vacíos).
- **Cluster hijo**: mostrar breadcrumb "← CLU-NNN (padre) / CLU-NNN-A". Añadir campo visible `subtipo`.
- **Jira candidato con email_match**: badge visual "📧 match por email: `<email>`" + el `zendesk_id` del ticket que lo disparó, linkeable al ticket.

`views/clusters.py` (listado):

- Indicador visual en clusters refinados (icono o color distinto).
- Filtro "ocultar refinados (padres)" activado por defecto.

## 11. Testing

### 11.1. Unit tests nuevos

- `tests/test_email_extract.py`: regex de emails contra casos reales del dataset (TEC-3091, Aurora ticket, cuerpos con emails en mayúsculas/acentos/espurios).
- `tests/test_jira_matcher_email.py`: 
  - Caso 1: Jira con email que intersecta cluster → candidato entra aunque score=0.
  - Caso 2: mismo cliente, Jira con concepto divergente → LLM lo descarta, confianza baja, no se fuerza a 0.95.
  - Caso 3: mismo cliente, Jira con concepto coincidente → boost a ≥0.95 con `razon` prefijada.
  - Mockear `openai` con fixtures.
- `tests/test_refine.py`:
  - Cluster homogéneo (1 subgrupo) → no-op, sólo `refined_at`.
  - Cluster heterogéneo (3 subgrupos) → 3 hijos, padre marcado, `ticket_ids` migrados.
  - Cluster con tickets sin contenido → subgrupo `sin_contenido` separado.
- `tests/test_zendesk_users_cache.py`: show_many con IDs parcialmente en cache, IDs borrados devueltos como null.

### 11.2. Integration test

`tests/test_pipeline_integration.py`:

- Fixture con ~30 tickets sintéticos cubriendo casos: con email requester, con email en body, sin email, duplicados, tickets sin subject.
- Fixture con 10 Jiras sintéticos con emails en descripción.
- Ejecutar pipeline completo.
- Aserciones: tickets tienen `emails_asociados`; clusters se crean; los gordos se refinan; matches email se propagan con `email_match` poblado y `confianza ≥ 0.95` donde aplica.

### 11.3. Smoke test sobre datos reales

Después del merge, antes de la re-ingesta en prod:

- Ejecutar `scripts/reingest_all.py --dry-run` contra datos reales.
- Verificar que CLU-007 se parte en N sub-clusters coherentes.
- Verificar que TEC-3091 aparece como match con `email_match` y `confianza ≥ 0.95` en el sub-cluster de "suscripciones de regalo bloqueadas".
- Verificar que TEC-3091 **no** aparece en el sub-cluster de "login inaccesible".

## 12. Rollout

1. Merge de la feature en una rama `feat/email-match-and-refine`.
2. Backup manual de `data/*.json` por fuera del script (doble seguridad).
3. Ejecutar `scripts/reingest_all.py --dry-run`, revisar stdout/logs.
4. Ejecutar `scripts/reingest_all.py` en caliente.
5. Inspeccionar UI con ojos humanos en CLU-007 (ahora partido) y en un cluster pequeño no refinado.
6. Si algo sale mal: `git checkout` los backups, ejecutar pipeline clásico.

## 13. Configuración

Variables de entorno nuevas (añadir a `.env.example`):

```
OPENAI_MODEL_REFINE=gpt-5.4       # modelo de razonamiento para Fase 3.5 batch
REFINE_MIN_TICKETS=15             # umbral de tamaño para disparar refine
REFINE_HETEROGENEITY_MIN=0.5      # umbral de heterogeneidad
REFINE_MAX_TICKETS_PER_BATCH=40   # split en 2 pasadas si excede
```

## 14. No-goals (v1)

- No se escanean comentarios de Zendesk en busca de emails (sólo `body_preview` y `requester_email`).
- No se hace re-clustering global (k-means/HDBSCAN sobre embeddings). El refine es LLM-only y opera cluster-por-cluster.
- No se persiste historial de cambios de clusters ("este ticket vino de CLU-007, ahora está en CLU-007-B"). Si se necesita auditoría, sale a otra iteración.
- No se añade un job de refine programado — por ahora es on-demand desde pipeline batch o CLI manual.
- No se toca el filtrado de Fase 1 (sigue siendo clasificador técnico/no-técnico con ollama).

## 15. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| La cuenta API Zendesk no tiene permiso para listar usuarios | Alto — bloquea Fase 0.5 | Probar en fase 0.5 con un sample; si falla, caer a lookup 1-a-1 con `?include=users` en endpoints de tickets individuales |
| `gpt-5.4` no disponible en la cuenta OpenAI | Medio | Fallback automático a `gpt-4o` con warning en logs |
| Refine parte mal un cluster bueno (falso positivo de heterogeneidad) | Medio | Umbrales conservadores en v1; visibilidad en UI para revertir manualmente (editar `clusters.json` a mano es aceptable en v1) |
| Email en descripción Jira que es un email de agente de soporte, no de cliente | Bajo | Filtro: excluir emails que coincidan con dominios internos (`@eldiario.es`) — aplicar en extracción Jira |
| Re-ingesta pierde datos si falla a mitad | Alto | Backup explícito antes de truncar; operación idempotente desde estado inicial |
| Regex de email captura falsos positivos (`foo@bar` sin TLD) | Bajo | Regex requiere `.XXX` al final; validación adicional con `len(parte_local) ≥ 1` |

## 16. Métricas de éxito

Después del rollout, se verifica sobre CLU-007 y el resto del dataset:

- **Precisión del matching Jira↔Cluster**: fracción de `jira_candidatos` con `confianza ≥ 0.8` que un humano considera relevantes. Objetivo: ≥85% (hoy estimado <60% por ruido en clusters amplios).
- **Recall de emails**: % de Jiras con emails en descripción cuyos sub-clusters correspondientes reciben el match. Objetivo: 100% cuando existe ticket Zendesk con ese email.
- **Tamaño medio de cluster post-refine**: objetivo ≤10 tickets por cluster (hoy CLU-007 = 76).
- **Coste OpenAI por re-ingesta completa**: medir. Objetivo: documentarlo, sin target duro en v1.
