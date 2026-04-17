# Sistema de Triaje de Tickets — Vista de negocio
## elDiario.es · Consolidación de incidencias técnicas del CRM

**Audiencia:** equipos de Producto, Soporte, Dirección y partes interesadas no técnicas.
**Para una vista técnica detallada:** [`DESIGN.md`](DESIGN.md), [`IMPLEMENTACION_TECNICA.md`](IMPLEMENTACION_TECNICA.md), [`JIRA.md`](JIRA.md).

---

## 1. El problema

Tras la migración del CRM, una parte significativa de los tickets que el equipo de Soporte recibe en Zendesk **no son peticiones de los suscriptores, sino consecuencias de fallos técnicos del propio CRM**: cobros duplicados, bajas que no se procesan, errores de acceso, fallos de pasarelas de pago, combos mal configurados…

Estos tickets llegan mezclados con el tráfico normal de Soporte. Esto genera tres problemas de negocio:

1. **Fragmentación del diagnóstico.** Veinte suscriptores distintos pueden reportar el mismo fallo de Stripe. El equipo de Soporte lo ve como veinte incidencias individuales; Tecnología no tiene forma de ver el patrón hasta que alguien lo escala manualmente.
2. **Fricción en la comunicación Soporte ↔ Tecnología.** Cada caso se abre como un Jira independiente, o peor, no se abre. Se duplica el trabajo de triaje por el lado de ingeniería.
3. **Tiempo de respuesta.** Un fallo sistémico (una pasarela de pago caída, por ejemplo) puede estar generando tickets durante horas sin que Tecnología lo detecte como incidente.

El sistema que se describe en este documento **automatiza ese triaje** y **vincula los casos de usuario con el ticket de desarrollo correspondiente** en Jira, permitiendo al equipo técnico ver la dimensión real de cada incidencia.

---

## 2. Visión general

![Arquitectura general](arquitectura-general.svg)

El sistema se compone de tres bloques conceptuales:

- **Entrada**: los tickets que llegan a Zendesk de los suscriptores.
- **Embudo de clasificación**: un pipeline de cuatro fases que separa, agrupa y prioriza los tickets técnicos.
- **Panel de decisión**: una interfaz web (Streamlit) donde Soporte y Tecnología ven los grupos de incidencias y deciden a qué ticket de Jira corresponden.

El objetivo final del panel es concreto: **decidir qué tickets de Zendesk pertenecen a qué incidencia de Jira**, para consolidar los casos en una única línea de trabajo de desarrollo en lugar de tener un Jira por usuario afectado.

---

## 3. El embudo de clasificación

![Flujo del embudo](flujo-embudo.svg)

Cada ticket nuevo recorre un embudo de cuatro fases, progresivamente más caras y más inteligentes. La clave de la metodología es que **solo llegan al modelo caro los tickets que las fases anteriores no han podido resolver con confianza**.

### Fase 0 · Descubrir el vocabulario (se ejecuta una vez)

Antes de clasificar nada, el sistema aprende **qué palabras y conceptos son relevantes** en el dominio de elDiario. Analiza una muestra histórica de tickets con procesamiento de lenguaje natural local y genera una taxonomía:

- **Sistemas** afectados: Stripe, PayPal, SEPA/IBAN, login, frontend del CRM, etc.
- **Tipos de problema**: cobro indebido, baja no procesada, error de acceso, error de interfaz…
- **Señales negativas**: palabras que indican que un ticket NO es técnico (bajas voluntarias, peticiones de factura, consultas).

Esta taxonomía se guarda en un fichero editable y es el **mapa contra el que se miden todos los tickets nuevos**. Se puede recalibrar cuando aparezcan nuevos tipos de problemas.

### Fase 1 · ¿Es un problema técnico?

El primer filtro. Para cada ticket se hace lo siguiente:

1. Si tiene señales **muy claras** de ser no-técnico (ej. "quiero darme de baja") → se descarta.
2. Si tiene señales **muy claras** de ser técnico (ej. "me han cobrado dos veces") → pasa al embudo.
3. Si es ambiguo → un modelo de lenguaje **local** (no sale de los servidores de elDiario) lo decide.

El resultado es una confianza numérica. Los tickets descartados se guardan también, pero no entran en los grupos de incidencia.

### Fase 2 · Pre-clasificación por anclas

Los tickets técnicos se comparan contra la taxonomía buscando **coincidencias fuertes** de palabras clave: "stripe + cobro + doble", "iban + error", etc. Cuando hay una coincidencia nítida, el ticket se asigna directamente a un cluster candidato **sin llamar a ningún modelo caro**.

Esto resuelve un gran porcentaje de tickets a coste cero.

### Fase 3 · Agrupación fina con LLM

Los tickets que llegan aquí son los genuinamente ambiguos. Se envían a **GPT-4o** (modelo remoto) con:

- El texto del ticket.
- La lista de clusters (grupos de incidencia) existentes.
- La taxonomía.

GPT-4o decide una de dos cosas:

- **Asignar** el ticket a un cluster existente.
- **Crear** un cluster nuevo (si ninguno encaja), con nombre, sistema afectado, severidad y resumen.

Esta es la fase más cara, pero solo procesa un 30-40 % de los tickets técnicos.

### Fase 4 · Enlace con Jira

Una vez que un cluster está formado, el sistema busca en **un pool local de tickets de Jira** del proyecto TEC (los últimos 60 días, sin incluir los ya cerrados) los que **probablemente sean el ticket de desarrollo correspondiente**.

El emparejamiento se hace en dos pasos:

1. **Pre-filtrado determinista**: se comparan las palabras clave del cluster con el contenido de cada ticket de Jira (título, descripción, etiquetas). Los que no comparten nada se descartan. Se seleccionan los 15 mejores candidatos.
2. **Selección final con GPT-4o**: el modelo revisa el cluster y los 15 candidatos y devuelve los 5 que realmente encajan, con una puntuación de confianza y una explicación en lenguaje natural.

El cluster se guarda enriquecido con estos candidatos. El equipo humano decide finalmente cuál es el correcto.

---

## 4. Metodología y por qué funciona

El diseño sigue tres principios:

### 4.1 Embudo progresivo de coste

Cada fase es más cara que la anterior. No todos los tickets necesitan todas las fases. Esto nos da dos ventajas: **coste bajo** (la mayoría se resuelven antes de llegar al LLM remoto) y **explicabilidad** (cuando un ticket se clasifica, sabemos por qué — reglas claras o decisión del modelo).

### 4.2 Taxonomía revisable por humanos

El fichero de conceptos (`conceptos.json`) es texto plano editable. Si el equipo de Soporte detecta un patrón nuevo ("empieza a aparecer Mailchimp en los tickets"), puede añadirlo directamente sin tocar código.

### 4.3 Matching Jira separado del clustering

Los tickets de Jira **no se agrupan**; son un índice contra el que los clusters de Zendesk se emparejan. Esto permite:

- Re-ejecutar el matching cuando entran Jiras nuevas, sin re-procesar los tickets de Zendesk.
- Afinar los criterios de emparejamiento sin romper el clustering.
- Distinguir claramente **"qué incidencias hay"** (lado Zendesk) de **"qué desarrollo las resuelve"** (lado Jira).

---

## 5. El panel de decisión

La interfaz web tiene dos niveles:

### Vista de lista

Muestra todos los clusters activos ordenados por severidad. Para cada cluster se ve su nombre, número de tickets, tendencia (creciente/estable/decreciente), cuántos candidatos Jira tiene, y un acceso directo al detalle.

Métricas globales en la cabecera:

- Total de tickets procesados en el rango seleccionado.
- Cuántos son técnicos, cuántos descartados.
- Clusters activos y tickets agrupados.
- Estado del pool Jira: rango de fechas y cuántos clusters ya tienen candidatos asignados.

Se puede filtrar por severidad, sistema afectado, y por rango de fechas de proceso.

### Vista de detalle

Cuando el equipo abre un cluster, ve las dos "caras" del problema lado a lado:

- **A la izquierda**: los tickets de Zendesk que el sistema ha agrupado bajo esa incidencia.
- **A la derecha**: los candidatos de Jira que el matcher ha propuesto.

Ambas son tablas ordenables y seleccionables. Al seleccionar una fila en cualquiera de los dos lados, aparece el detalle completo abajo: el cuerpo del ticket, su prioridad, el autor, etc. para Zendesk; el resumen, el estado, la confianza del match y la razón del matcher para Jira.

Esto permite al operador **comparar ambas caras** y decidir si el ticket de Jira propuesto es efectivamente el correcto. La URL de cada vista de detalle es única (`?cluster=CLU-XXX`), así que se puede compartir por Slack, guardar en marcadores o enlazar desde un Jira.

---

## 6. Operación y cadencia

El sistema está pensado para funcionar de forma periódica sin intervención:

- **Diario (automático)**: se descargan los tickets nuevos de Zendesk de las últimas 24 h y se pasan por el embudo; se descargan los cambios recientes del proyecto TEC de Jira.
- **Al detectar un cluster nuevo**: el sistema busca automáticamente candidatos en Jira.
- **Manual, cuando se quiera**: re-emparejar todos los clusters existentes contra el pool actualizado de Jira, por ejemplo si se abre un nuevo ticket técnico que podría ser el correcto para un cluster antiguo.
- **Ocasionalmente**: recalibrar la taxonomía si aparecen nuevos tipos de problemas.

El equipo de Soporte **solo interactúa con el panel**; toda la descarga y el procesamiento ocurre por detrás.

---

## 7. Valor para el negocio

| Ámbito | Antes | Después |
|---|---|---|
| **Visibilidad** | Un ticket = un caso individual | Grupos de incidencia con conteo y tendencia |
| **Coordinación Soporte ↔ Tec** | Cada caso escalado a mano, a veces con semanas de retraso | Panel compartido con candidatos Jira sugeridos |
| **Consolidación en Jira** | Un Jira por usuario afectado, o ninguno | Un Jira para N usuarios afectados, todos visibles |
| **Detección de incidentes** | Reactiva: alguien del equipo nota un patrón | Proactiva: el panel muestra clusters crecientes por severidad |
| **Coste operativo** | Horas de triaje manual | Clasificación automatizada; el humano solo decide los mapeos Zendesk↔Jira |

---

## 8. Fuera de alcance (fases posteriores)

Estos puntos **no forman parte de la versión actual**, pero están dimensionados como próximos pasos:

- **Adjuntar tickets de Zendesk al Jira desde el panel**: hoy la decisión de mapeo se ve en el panel, pero la acción se ejecuta manualmente en Jira. La siguiente iteración incluirá un botón "Adjuntar estos tickets al Jira TEC-XXX".
- **Integración con n8n**: notificaciones automáticas cuando un cluster nuevo supera un umbral de severidad o de número de tickets.
- **Alertas** a canales de Slack cuando aparezca un patrón nuevo de urgencia.
- **Vista multi-usuario** con autenticación corporativa (actualmente el panel es local).

---

## 9. Glosario

| Término | Significado |
|---|---|
| **Ticket Zendesk** | Cada caso individual abierto por un suscriptor en Zendesk. |
| **Ticket Jira** | Una tarea o issue de desarrollo en el proyecto TEC de Jira. |
| **Cluster** | Un grupo de tickets de Zendesk que comparten la misma causa técnica. |
| **Ancla** | Palabra o conjunto de palabras clave que identifican un sistema o un tipo de problema (ej. "stripe", "cobro_duplicado"). |
| **Taxonomía** | El conjunto de sistemas, tipos de problema y señales que el sistema conoce. Se puede editar. |
| **Severidad** | Clasificación HIGH / MEDIUM / LOW asignada al cluster según gravedad del problema. |
| **Matcher** | El componente que propone tickets de Jira candidatos para un cluster. |
| **Embudo** | La cadena de fases 1 → 2 → 3 por las que pasa cada ticket nuevo. |
| **Triaje** | El proceso de decidir qué tickets son técnicos, cómo agruparlos y a qué desarrollo corresponden. |

---

## 10. Contacto y gobierno

- **Responsable técnico**: Tecnología · David Murciano
- **Infraestructura actual**: ejecución local (PoC); plan de producción en GCloud con PostgreSQL.
- **Dependencias externas**: API de Zendesk, API de Jira Cloud (Atlassian), OpenAI (GPT-4o).
- **Coste estimado de la IA remota**: menos de 1 €/día en operación normal.
