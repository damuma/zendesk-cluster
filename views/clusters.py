import streamlit as st
from storage import Storage

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
TENDENCIA_ICON = {"creciente": "↑", "estable": "→", "decreciente": "↓", "nuevo": "✨"}

_TOOLTIP_CSS = """
<style>
.zt-ticket {
    position: relative;
    display: inline-block;
    cursor: default;
}
.zt-ticket .zt-tip {
    visibility: hidden;
    opacity: 0;
    width: 380px;
    background: #1e1e2e;
    color: #e0e0e0;
    border-radius: 8px;
    padding: 12px 14px;
    position: absolute;
    z-index: 9999;
    bottom: 130%;
    left: 50%;
    transform: translateX(-50%);
    box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    font-size: 12px;
    line-height: 1.5;
    transition: opacity 0.15s ease;
    pointer-events: none;
    white-space: normal;
    word-break: break-word;
}
.zt-ticket .zt-tip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 50%;
    margin-left: -6px;
    border: 6px solid transparent;
    border-top-color: #1e1e2e;
}
.zt-ticket:hover .zt-tip {
    visibility: visible;
    opacity: 1;
}
.zt-ticket a {
    color: #4fa3e0;
    text-decoration: none;
    font-family: monospace;
    font-size: 13px;
}
.zt-tip .zt-subject {
    font-weight: 700;
    font-size: 13px;
    color: #ffffff;
    margin-bottom: 6px;
    border-bottom: 1px solid #444;
    padding-bottom: 5px;
}
.zt-tip .zt-body {
    color: #b0b0c0;
    margin-bottom: 8px;
    font-size: 11.5px;
}
.zt-tip .zt-meta {
    font-size: 11px;
    color: #888;
}
.zt-conf-ok  { color: #4caf50; }
.zt-conf-warn{ color: #ff9800; }
</style>
"""


def _esc(text: str) -> str:
    """Escape HTML special chars for safe embedding in tooltip."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _ticket_html(t: dict) -> str:
    tid = t.get("zendesk_id", "?")
    subject = _esc(t.get("subject", "") or "Sin asunto")
    body = _esc((t.get("body_preview") or "")[:250])
    created = (t.get("created_at") or "")[:19].replace("T", " ")
    channel = _esc(t.get("channel") or "—")
    tags = _esc(", ".join(t.get("tags", [])[:6]) or "—")
    conf = t.get("fase1_confianza", 0)
    conf_pct = f"{conf:.0%}"
    metodo = _esc(t.get("fase1_modelo") or "—")
    conf_cls = "zt-conf-ok" if conf >= 0.8 else "zt-conf-warn"
    conf_icon = "✓" if conf >= 0.8 else "⚠"

    return f"""<span class="zt-ticket">
  <a href="#">#{tid}</a>
  <span class="zt-tip">
    <div class="zt-subject">{subject}</div>
    <div class="zt-body">{body}</div>
    <div class="zt-meta">
      📅 {created}&nbsp;&nbsp;·&nbsp;&nbsp;📡 {channel}<br>
      🏷️ {tags}<br>
      <span class="{conf_cls}">{conf_icon} Fase 1: {conf_pct} ({metodo})</span>
    </div>
  </span>
</span>"""


def render():
    st.markdown(_TOOLTIP_CSS, unsafe_allow_html=True)
    st.title("📊 Clusters de incidencias técnicas")

    storage = Storage()
    all_tickets = storage.get_tickets()
    clusters = storage.get_clusters(estado="abierto")

    if not clusters:
        st.info("No hay clusters activos. Ejecuta el pipeline: `python pipeline.py --horas 24`")
        return

    # ── Métricas globales ──────────────────────────────────────
    tecnicos = [t for t in all_tickets if t.get("fase1_resultado") == "TECNICO"]
    descartados = len(all_tickets) - len(tecnicos)
    total_en_clusters = sum(c.get("ticket_count", 0) for c in clusters)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total tickets", len(all_tickets))
    m2.metric("Técnicos", len(tecnicos))
    m3.metric("Descartados", descartados)
    m4.metric("Clusters activos", len(clusters))
    m5.metric("Tickets en clusters", total_en_clusters)
    st.markdown("---")

    # ── Filtros ────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_sev = st.selectbox("Severidad", ["Todas", "HIGH", "MEDIUM", "LOW"])
    with col2:
        sistemas_disponibles = list({c.get("sistema", "desconocido") for c in clusters})
        filtro_sis = st.selectbox("Sistema", ["Todos"] + sorted(sistemas_disponibles))
    with col3:
        if st.button("🔄 Actualizar"):
            st.rerun()

    filtered = clusters
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

            tickets = storage.get_cluster_tickets(cluster["cluster_id"])
            if tickets:
                st.markdown("**Últimos tickets:**")
                chips = " &nbsp; ".join(_ticket_html(t) for t in tickets[-10:])
                st.markdown(chips, unsafe_allow_html=True)
