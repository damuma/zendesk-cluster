import os
from datetime import date, datetime, timezone
import streamlit as st
from storage import Storage


def _parse_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        return None

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
TENDENCIA_ICON = {"creciente": "↑", "estable": "→", "decreciente": "↓", "nuevo": "✨"}
PRIORITY_ICON = {"urgent": "🚨", "high": "🔴", "normal": "🟡", "low": "⚪"}

_TOOLTIP_CSS = """
<style>
/* Let Streamlit's markdown container overflow so absolute tooltips are visible */
div[data-testid="stMarkdownContainer"],
div[data-testid="stMarkdownContainer"] > div,
.stMarkdown {
    overflow: visible !important;
}

/* ── Chip + tooltip ─────────────────────────────────────── */
.zt-ticket,
.zt-ticket * {
    font-size: 12px !important;
    line-height: 1.5 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
    box-sizing: border-box;
}
.zt-ticket {
    position: relative;
    display: inline-block;
    vertical-align: middle;
    margin: 2px 6px;
}
/* The chip link */
.zt-chip {
    color: #4fa3e0 !important;
    text-decoration: none !important;
    font-family: monospace !important;
    font-size: 13px !important;
    font-weight: 600 !important;
}
.zt-chip:hover { text-decoration: underline !important; }

/* ── Tooltip bubble (absolute, above chip) ──────────────── */
.zt-ticket .zt-tip {
    visibility: hidden;
    opacity: 0;
    width: 400px;
    max-width: min(400px, 90vw);
    background: #1a1a2e;
    color: #e0e0e0 !important;
    border-radius: 10px;
    padding: 14px 16px 10px;
    position: absolute;
    bottom: calc(100% + 10px);  /* always above the chip */
    left: 0;                     /* anchor to chip left — never overflows left */
    z-index: 9999;
    box-shadow: 0 6px 24px rgba(0,0,0,0.5);
    pointer-events: auto;        /* allow clicks inside (for the Zendesk link) */
    white-space: normal;
    word-break: break-word;
    /* Hide with delay so mouse can travel from chip to tooltip */
    transition: opacity 0.12s ease 0s, visibility 0s ease 0s;
}
/* Keep visible while hovering chip OR the tooltip itself */
.zt-ticket:hover .zt-tip,
.zt-ticket .zt-tip:hover {
    visibility: visible;
    opacity: 1;
}
/* Arrow pointing down */
.zt-tip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 18px;
    border: 7px solid transparent;
    border-top-color: #1a1a2e;
}

/* ── Tooltip internals ──────────────────────────────────── */
.zt-tip .zt-subject {
    font-weight: 700 !important;
    font-size: 13px !important;
    color: #ffffff !important;
    margin-bottom: 6px !important;
    padding-bottom: 6px !important;
    border-bottom: 1px solid #3a3a5a !important;
}
.zt-tip .zt-body {
    color: #b8b8d0 !important;
    margin-bottom: 8px !important;
    font-size: 11.5px !important;
}
.zt-tip .zt-meta {
    font-size: 11px !important;
    color: #888 !important;
    border-top: 1px solid #3a3a5a !important;
    padding-top: 6px !important;
    margin-top: 4px !important;
}
.zt-tip .zt-tags {
    margin-top: 4px !important;
    font-size: 11px !important;
    color: #aaa !important;
}
.zt-tip .zt-open {
    display: inline-block !important;
    margin-top: 8px !important;
    padding: 3px 8px !important;
    background: #2a2a4a !important;
    border-radius: 4px !important;
    font-size: 11px !important;
    color: #7ab8f5 !important;
    text-decoration: none !important;
    border: 1px solid #4a4a6a !important;
}
.zt-tip .zt-open:hover {
    background: #3a3a5a !important;
    color: #aad4ff !important;
}
.zt-conf-ok   { color: #4caf50 !important; }
.zt-conf-warn { color: #ff9800 !important; }
</style>
"""


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _ticket_html(t: dict, zendesk_subdomain: str) -> str:
    tid = t.get("zendesk_id", "?")
    url = t.get("zendesk_url") or f"https://{zendesk_subdomain}.zendesk.com/agent/tickets/{tid}"

    subject  = _esc(t.get("subject") or "Sin asunto")
    body     = _esc((t.get("body_preview") or "")[:450])
    created  = (t.get("created_at") or "")[:19].replace("T", " ")
    channel  = _esc(t.get("channel") or "—")
    priority = t.get("priority") or "—"
    p_icon   = PRIORITY_ICON.get(priority, "⚪")
    ttype    = _esc(t.get("ticket_type") or "—")
    tags_raw = t.get("tags", [])
    tags_str = _esc(", ".join(tags_raw[:8]) or "—")

    conf     = t.get("fase1_confianza", 0)
    conf_pct = f"{conf:.0%}"
    metodo   = _esc(t.get("fase1_modelo") or "—")
    conf_cls = "zt-conf-ok" if conf >= 0.8 else "zt-conf-warn"
    conf_icon = "✓" if conf >= 0.8 else "⚠"

    return (
        f'<span class="zt-ticket">'
        f'  <a class="zt-chip" href="{url}" target="_blank">#{tid}</a>'
        f'  <span class="zt-tip">'
        f'    <div class="zt-subject">{subject}</div>'
        f'    <div class="zt-body">{body}</div>'
        f'    <div class="zt-meta">'
        f'      📅 {created}&nbsp;·&nbsp;📡 {channel}&nbsp;·&nbsp;{p_icon} {priority}&nbsp;·&nbsp;📋 {ttype}<br>'
        f'      <span class="{conf_cls}">{conf_icon} Fase 1: {conf_pct} ({metodo})</span>'
        f'    </div>'
        f'    <div class="zt-tags">🏷️ {tags_str}</div>'
        f'    <a class="zt-open" href="{url}" target="_blank">🔗 Abrir en Zendesk →</a>'
        f'  </span>'
        f'</span>'
    )


def render():
    st.markdown(_TOOLTIP_CSS, unsafe_allow_html=True)
    st.title("📊 Clusters de incidencias técnicas")

    subdomain = os.environ.get("ZENDESK_SUBDOMAIN", "")
    storage   = Storage()
    all_tickets = storage.get_tickets()
    clusters    = storage.get_clusters(estado="abierto")

    if not clusters:
        st.info("No hay clusters activos. Ejecuta el pipeline: `python pipeline.py --horas 24`")
        return

    # ── Rango de fechas disponible ─────────────────────────────
    ticket_dates = [_parse_date(t.get("created_at")) for t in all_tickets]
    ticket_dates = [d for d in ticket_dates if d]
    data_min = min(ticket_dates) if ticket_dates else date.today()
    data_max = max(ticket_dates) if ticket_dates else date.today()

    st.caption(
        f"📅 Datos disponibles: **{data_min.strftime('%d %b %Y')}** → **{data_max.strftime('%d %b %Y')}**"
        f"  ·  {(data_max - data_min).days + 1} días"
    )

    # ── Selector de rango ──────────────────────────────────────
    date_range = st.date_input(
        "Rango de fechas",
        value=(data_min, data_max),
        min_value=data_min,
        max_value=data_max,
        format="DD/MM/YYYY",
    )
    # date_input returns a tuple when range mode, or a single date while selecting
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        rango_inicio, rango_fin = date_range
    else:
        rango_inicio, rango_fin = data_min, data_max

    # ── Filtrar tickets por fecha ──────────────────────────────
    tickets_en_rango = {
        t["zendesk_id"]
        for t in all_tickets
        if (d := _parse_date(t.get("created_at"))) and rango_inicio <= d <= rango_fin
    }
    tickets_filtrados = [t for t in all_tickets if t.get("zendesk_id") in tickets_en_rango]

    # ── Métricas (sobre el rango seleccionado) ─────────────────
    tecnicos    = [t for t in tickets_filtrados if t.get("fase1_resultado") == "TECNICO"]
    descartados = len(tickets_filtrados) - len(tecnicos)

    # Filtrar clusters: solo los que tienen al menos 1 ticket en el rango
    clusters_en_rango = [
        c for c in clusters
        if any(tid in tickets_en_rango for tid in c.get("ticket_ids", []))
        or (not c.get("ticket_ids") and _parse_date(c.get("created_at")) and
            rango_inicio <= _parse_date(c.get("created_at")) <= rango_fin)
    ]
    total_en_clusters = sum(c.get("ticket_count", 0) for c in clusters_en_rango)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total tickets", len(tickets_filtrados),
              delta=f"{len(tickets_filtrados) - len(all_tickets):+d}" if len(tickets_filtrados) != len(all_tickets) else None)
    m2.metric("Técnicos", len(tecnicos))
    m3.metric("Descartados", descartados)
    m4.metric("Clusters activos", len(clusters_en_rango))
    m5.metric("Tickets en clusters", total_en_clusters)
    st.markdown("---")

    # ── Filtros de cluster ─────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_sev = st.selectbox("Severidad", ["Todas", "HIGH", "MEDIUM", "LOW"])
    with col2:
        sistemas_disponibles = list({c.get("sistema", "desconocido") for c in clusters_en_rango})
        filtro_sis = st.selectbox("Sistema", ["Todos"] + sorted(sistemas_disponibles))
    with col3:
        if st.button("🔄 Actualizar"):
            st.rerun()

    filtered = clusters_en_rango
    if filtro_sev != "Todas":
        filtered = [c for c in filtered if c.get("severidad") == filtro_sev]
    if filtro_sis != "Todos":
        filtered = [c for c in filtered if c.get("sistema") == filtro_sis]

    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    filtered.sort(key=lambda c: (sev_order.get(c.get("severidad", "LOW"), 2), -c.get("ticket_count", 0)))

    st.markdown(f"**{len(filtered)} clusters** encontrados")
    st.markdown("---")

    # ── Lista de clusters ──────────────────────────────────────
    for cluster in filtered:
        sev  = cluster.get("severidad", "MEDIUM")
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

            tickets = storage.get_cluster_tickets(cluster["cluster_id"])
            if tickets:
                st.markdown("**Últimos tickets:**")
                chips = " ".join(_ticket_html(t, subdomain) for t in tickets[-10:])
                st.markdown(chips, unsafe_allow_html=True)
