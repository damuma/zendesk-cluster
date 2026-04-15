import streamlit as st
from storage import Storage

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}


def render(cluster_id: str):
    storage = Storage()
    clusters = storage.get_clusters()
    cluster = next((c for c in clusters if c["cluster_id"] == cluster_id), None)

    if not cluster:
        st.error(f"Cluster `{cluster_id}` no encontrado.")
        if st.button("← Volver"):
            del st.session_state["selected_cluster"]
            st.rerun()
        return

    sev = cluster.get("severidad", "MEDIUM")
    icon = SEVERIDAD_COLOR.get(sev, "⚪")

    st.title(f"{icon} {cluster['nombre']}")

    if st.button("← Volver a clusters"):
        del st.session_state["selected_cluster"]
        st.rerun()

    col1, col2, col3 = st.columns(3)
    col1.metric("Tickets", cluster.get("ticket_count", 0))
    col2.metric("Severidad", sev)
    col3.metric("Sistema", cluster.get("sistema") or "—")

    st.markdown("---")
    st.markdown(f"**Resumen:** {cluster.get('resumen', '_Sin resumen_')}")
    st.markdown(f"**Tipo de problema:** `{cluster.get('tipo_problema', '—')}`")
    st.markdown(f"**Estado:** `{cluster.get('estado', '—')}`")
    st.markdown(f"**Creado:** {cluster.get('created_at', '')[:10]}  ·  **Actualizado:** {cluster.get('updated_at', '')[:10]}")

    jira_ids = cluster.get("jira_candidatos", [])
    if jira_ids:
        st.subheader("🔗 Jira candidatos")
        jira_host = __import__("os").environ.get("JIRA_HOST", "eldiario.atlassian.net")
        for jid in jira_ids:
            st.markdown(f"- [{jid}](https://{jira_host}/browse/{jid})")

    st.subheader("🎫 Tickets en este cluster")
    tickets = storage.get_cluster_tickets(cluster_id)
    if not tickets:
        st.info("No hay tickets asociados todavía.")
        return

    for t in reversed(tickets):
        conf = t.get("fase1_confianza", 0)
        conf_icon = "✓" if conf >= 0.8 else "⚠"
        metodo = t.get("fase1_modelo", "—")
        with st.expander(f"`#{t['zendesk_id']}` {conf_icon} {t.get('subject', '')}"):
            st.markdown(f"**Confianza Fase 1:** {conf:.0%} ({metodo})")
            st.markdown(f"**Procesado:** {t.get('procesado_at', '')[:19]}")
            if t.get("fase3_resumen_llm"):
                st.markdown(f"**Resumen LLM:** {t['fase3_resumen_llm']}")
            if t.get("body_preview"):
                st.text_area("Cuerpo del ticket", t["body_preview"], height=120, disabled=True, key=f"body_{t['zendesk_id']}")
