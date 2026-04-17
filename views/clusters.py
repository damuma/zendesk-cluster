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



def render():
    st.title("📊 Clusters de incidencias técnicas")

    storage   = Storage()
    all_tickets = storage.get_tickets()
    clusters    = storage.get_clusters(estado="abierto")
    # Padres refinados (estado="refined") son opcionalmente visibles; por defecto
    # se ocultan del listado principal porque el detalle relevante vive en sus
    # hijos CLU-NNN-A/B/…
    refined_parents = [c for c in storage.get_clusters() if c.get("estado") == "refined"]
    mostrar_refinados = st.sidebar.checkbox(
        f"Mostrar padres refinados ({len(refined_parents)})",
        value=False,
        help="Clusters que la Fase 3.5 dividió en sub-clusters. El contenido real está en los hijos.",
    )
    if mostrar_refinados:
        clusters = clusters + refined_parents

    if not clusters:
        st.info("No hay clusters activos. Ejecuta el pipeline: `python pipeline.py --horas 24`")
        return

    # ── Rango de fechas disponible (por fecha de proceso, no de creación en Zendesk) ──
    # Nota: created_at es la fecha original del ticket en Zendesk, que puede ser antigua.
    # procesado_at es cuando nuestro pipeline lo procesó — es lo relevante para filtrar.
    proc_dates = [_parse_date(t.get("procesado_at")) for t in all_tickets]
    proc_dates = [d for d in proc_dates if d]
    data_min = min(proc_dates) if proc_dates else date.today()
    data_max = max(proc_dates) if proc_dates else date.today()

    st.caption(
        f"⚙️ Procesados: **{data_min.strftime('%d %b %Y')}** → **{data_max.strftime('%d %b %Y')}**"
        + (f"  ·  {(data_max - data_min).days + 1} días" if data_min != data_max else "  ·  hoy")
    )

    jira_meta = storage.get_jira_metadata() or {}
    jira_inicio = _parse_date(jira_meta.get("fecha_inicio"))
    jira_fin    = _parse_date(jira_meta.get("fecha_fin"))
    if jira_inicio and jira_fin:
        st.caption(
            f"🔷 Jira pool: **{jira_inicio.strftime('%d %b %Y')}** → **{jira_fin.strftime('%d %b %Y')}**"
            f"  ·  último sync: {(jira_meta.get('last_sync') or '')[:10]}"
        )
    else:
        st.caption("🔷 Jira pool: _sin descargar_ · ejecuta `python fase0_jira.py --full`")

    # ── Selector de rango ──────────────────────────────────────
    date_range = st.date_input(
        "Filtrar por fecha de proceso",
        value=(data_min, data_max),
        min_value=data_min,
        max_value=data_max,
        format="DD/MM/YYYY",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        rango_inicio, rango_fin = date_range
    else:
        rango_inicio, rango_fin = data_min, data_max

    # ── Filtrar tickets por fecha de proceso ───────────────────
    tickets_en_rango = {
        t["zendesk_id"]
        for t in all_tickets
        if (d := _parse_date(t.get("procesado_at"))) and rango_inicio <= d <= rango_fin
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

    # ── Métricas de Jira ───────────────────────────────────────
    clusters_con_jira = [c for c in clusters_en_rango if c.get("jira_candidatos")]
    total_candidatos  = sum(len(c.get("jira_candidatos") or []) for c in clusters_en_rango)
    jira_pool_total   = jira_meta.get("total_tickets", 0) or 0
    rango_jira = "—"
    if jira_inicio and jira_fin:
        rango_jira = f"{jira_inicio.strftime('%d %b')} → {jira_fin.strftime('%d %b %Y')}"

    j1, j2, j3, j4, j5 = st.columns(5)
    j1.metric("Jira en pool", jira_pool_total)
    j2.markdown(
        "<div style='color:rgba(49,51,63,0.6); font-size:14px; margin-bottom:6px;'>Rango Jira</div>"
        f"<div style='font-size:18px; line-height:1.2; padding-top:6px;'>{rango_jira}</div>",
        unsafe_allow_html=True,
    )
    j3.metric("Clusters con Jira", f"{len(clusters_con_jira)} / {len(clusters_en_rango)}")
    j4.metric("Candidatos totales", total_candidatos)
    j5.empty()
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
        _jira_count = len(cluster.get("jira_candidatos", []) or [])
        jira_badge = f" · 🔷 {_jira_count}" if _jira_count else ""
        cid = cluster["cluster_id"]
        is_refined_parent = cluster.get("estado") == "refined"
        is_child = bool(cluster.get("parent_cluster_id"))
        prefix_icon = "🧬" if is_refined_parent else ("🔬" if is_child else icon)
        subtipo = cluster.get("subtipo")
        subtipo_chip = f" · `{subtipo}`" if subtipo else ""

        with st.expander(
            f"{prefix_icon} **{cluster['nombre']}** · {cluster.get('ticket_count', 0)} tickets {tend}{jira_badge}{subtipo_chip}"
        ):
            col1, col2 = st.columns([3, 1])
            with col1:
                if is_refined_parent:
                    st.info("🧬 Padre refinado — el contenido vive en los sub-clusters hijos.")
                if is_child:
                    parent = cluster.get("parent_cluster_id")
                    st.caption(f"↑ Hijo de [{parent}](?cluster={parent})")
                st.markdown(f"**Resumen:** {cluster.get('resumen', '_Sin resumen_')}")
                st.markdown(f"**Sistema:** `{cluster.get('sistema', '—')}` · **Tipo:** `{cluster.get('tipo_problema', '—')}`")
                st.caption(
                    f"{_jira_count} candidatos Jira · "
                    f"Creado {cluster.get('created_at', '')[:10]} · "
                    f"Actualizado {cluster.get('updated_at', '')[:10]}"
                )
            with col2:
                st.link_button("Ver detalle →", f"?cluster={cid}", width="stretch")
