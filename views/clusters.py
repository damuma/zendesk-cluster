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
                for t in tickets[-5:]:
                    conf = t.get("fase1_confianza", 0)
                    conf_icon = "✓" if conf >= 0.8 else "⚠"
                    st.markdown(f"- `#{t['zendesk_id']}` {conf_icon} _{t.get('subject', '')}_ · confianza: {conf:.0%}")
