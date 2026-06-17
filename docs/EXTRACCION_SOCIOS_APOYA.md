# Extracción de remitentes socios@ / apoya@

Documentación del script **`extraer_socios_apoya.py`** (raíz del repo).

## Para qué sirve

Responde a una petición de negocio concreta:

> Lista de las personas que escribieron a `socios@eldiario.es` y a `apoya@eldiario.es`
> (en dos listas separadas) entre el **4 de marzo y el 8 de abril de 2026, ambos incluidos**,
> con su **email + fecha de contacto + buzón**, quedándonos **solo con quienes NO volvieron a
> contactar** (a socios ni a apoya) **a partir del 9 de abril**.

El script descarga todo el histórico de tickets desde el inicio de la ventana hasta hoy,
los agrupa por buzón, resuelve el email real de cada remitente y aplica el filtro de
"vuelta a contactar".

## Cómo se identifica el buzón

El buzón al que escribió la persona está en el campo **`recipient`** de cada ticket de
Zendesk. Las direcciones reales (support addresses) son `socios@eldiario.es` (la
predeterminada) y `apoya@eldiario.es`.

> ⚠️ **No se usa el Search API** (`recipient:socios@eldiario.es` devuelve 0 resultados por
> ser la dirección predeterminada). Se usa el **export incremental**
> (`/incremental/tickets/cursor.json`) y se agrupa por `recipient` en local, que sí trae
> el valor correcto.

Tickets con `recipient` vacío = canales que no son email (formulario web, API). No son
atribuibles a socios/apoya y se ignoran (se registran en `sin_atribuir.csv` si tienen email).

## Reglas de negocio aplicadas

| Regla | Decisión |
|------|----------|
| **Ventana** | 4-mar-2026 → 8-abr-2026, **ambos días incluidos** |
| **Zona horaria** | Las fechas se interpretan en **Europe/Madrid** (lo que ve un humano), no en UTC |
| **"Volvió a contactar"** | Cualquier ticket a **socios o apoya** (cualquiera de los dos buzones) con fecha **>= 9-abr-2026**. Si la persona vuelve a escribir a un buzón distinto del de la ventana, **también** se descarta |
| **Fecha en el listado** | `contacto_1` = primer contacto en la ventana (principal); `contacto_2`, `contacto_3`… = resto de interacciones en la ventana |
| **Dominio interno** | Se excluyen los remitentes `@eldiario.es` por defecto (`brainhub@`, `crm@`, `aldia@`, `contacto@`…), porque son remitentes internos/automáticos, no personas |
| **Tickets cerrados** | **Incluidos** (imprescindible: casi todo el histórico está cerrado) |

## Uso

Desde la raíz del repo, con el venv:

```bash
venv/bin/python extraer_socios_apoya.py
```

### Flags

| Flag | Default | Descripción |
|------|---------|-------------|
| `--start` | `2026-03-04` | Inicio de ventana (incluido), `YYYY-MM-DD` |
| `--window-end` | `2026-04-08` | Fin de ventana (incluido), `YYYY-MM-DD` |
| `--output-dir` | `data/socios_apoya` | Carpeta de salida de los CSV |
| `--users-cache` | `data/zendesk_users.json` | Cache local id→email de usuarios Zendesk |
| `--exclude-domains` | `eldiario.es` | Dominios de remitente a excluir. Vacío (`--exclude-domains`) = no excluir ninguno |

El día de "vuelta a contactar" se calcula automáticamente como `window-end + 1 día`.

### Ejemplos

```bash
# Ejecución estándar (la de la petición)
venv/bin/python extraer_socios_apoya.py

# Otra ventana
venv/bin/python extraer_socios_apoya.py --start 2026-01-01 --window-end 2026-01-31

# Incluir también remitentes internos @eldiario.es
venv/bin/python extraer_socios_apoya.py --exclude-domains
```

## Salida

En `data/socios_apoya/` (la carpeta `data/` está en `.gitignore`: los CSV con datos
personales **no** se suben al repositorio):

| Archivo | Contenido |
|---------|-----------|
| `socios_mantener.csv` | **Lista final socios**: escribieron a socios@ en la ventana y NO volvieron a contactar |
| `apoya_mantener.csv` | **Lista final apoya**: ídem para apoya@ |
| `socios_descartar.csv` | Escribieron a socios@ en la ventana pero volvieron a contactar después |
| `apoya_descartar.csv` | Ídem para apoya@ |
| `sin_atribuir.csv` | Tickets del periodo a socios/apoya sin email resoluble (usuarios borrados, etc.). No se genera si está vacío |

### Columnas

**`*_mantener.csv`**

```
email, n_contactos, contacto_1, contacto_2, …
```

- `email` — remitente.
- `n_contactos` — nº de veces que escribió a ese buzón dentro de la ventana.
- `contacto_N` — fecha/hora (Madrid, `YYYY-MM-DD HH:MM`) de cada interacción en la ventana.
  El número de columnas se ajusta al máximo de contactos de cualquier fila.

**`*_descartar.csv`**

Igual que arriba (con `n_contactos_ventana`) más:

- `interacciones_posteriores` — lista de contactos a partir del 9-abr que motivaron el
  descarte, formato `buzón @ fecha; buzón @ fecha; …`.

Los CSV se escriben en **UTF-8 con BOM** para que Excel muestre las tildes correctamente.

## Excel formateado (recomendado para compartir)

Para entregar un único `.xlsx` con todo combinado y formateado:

```bash
venv/bin/python scripts/socios_apoya_a_excel.py
```

Genera `data/socios_apoya/socios_apoya.xlsx` con:

- Pestaña **Resumen** (reglas + recuentos).
- Una pestaña por lista: `socios — mantener`, `socios — descartar`, `apoya — mantener`,
  `apoya — descartar`.
- Cabecera fija (freeze) y autofiltro en cada pestaña.

Las columnas `contacto_N` de los CSV se resumen a **primer / segundo / tercer contacto** +
**otras interacciones en ventana** (resto agrupado con `; `), para que la tabla sea legible
en vez de tener decenas de columnas. Las listas de descarte añaden **interacciones
posteriores (motivo de descarte)**.

| Flag | Default | Descripción |
|------|---------|-------------|
| `--input-dir` | `data/socios_apoya` | Carpeta con los CSV de entrada |
| `--output` | `<input-dir>/socios_apoya.xlsx` | Ruta del `.xlsx` de salida |

## Resultados de la ejecución de referencia (2026-06-17)

Sobre 24.872 tickets descargados (4-mar → 17-jun-2026):

| Buzón | A mantener | Descartados |
|-------|-----------:|------------:|
| socios@ | 2.780 | 483 |
| apoya@ | 726 | 116 |

### Avisos sobre los datos

- **Outlier**: `countmarcosvongoihman@gmail.com` aparece con ~112 contactos a socios@ en
  5 semanas (gmail externo, por lo que se mantiene). Tiene pinta de remitente
  automático/spam — conviene revisarlo antes de usar la lista para captación.
- Las direcciones internas `@eldiario.es` (brainhub, crm, aldia, contacto…) se excluyen
  por defecto. Si se necesitaran, usar `--exclude-domains` sin argumentos.

## Notas técnicas

- Reutiliza `ZendeskClient` (`zendesk_client.py`) y `ZendeskUsersCache`
  (`zendesk_users_cache.py`). Se añadió el campo `recipient` al normalizador de tickets y
  el método público `ZendeskClient.get_tickets_created_since(since, exclude_statuses)`.
- La resolución de emails usa el cache `data/zendesk_users.json` y solo descarga vía
  `users/show_many.json` los ids que falten.
- Credenciales en `.env` (`ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`).
