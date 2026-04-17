import os
import re

import pandas as pd
import streamlit as st

from storage import Storage

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}


def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = (
        clean.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", clean).strip()


def render(cluster_id: str):
    storage = Storage()
    cluster = next(
        (c for c in storage.get_clusters() if c["cluster_id"] == cluster_id),
        None,
    )

    if not cluster:
        st.error(f"Cluster `{cluster_id}` no encontrado.")
        if st.button("← Volver"):
            del st.session_state["selected_cluster"]
            st.rerun()
        return

    _render_header(cluster)
    st.markdown("---")

    tickets = storage.get_cluster_tickets(cluster_id)
    jira_items = cluster.get("jira_candidatos", []) or []

    col_z, col_j = st.columns(2)
    z_selected = _render_zendesk_table(col_z, tickets, cluster_id)
    j_selected = _render_jira_table(col_j, jira_items, cluster_id)

    _render_detail_panels(
        tickets=tickets,
        jira_items=jira_items,
        z_idx=z_selected,
        j_idx=j_selected,
    )


# ── Header ────────────────────────────────────────────────────
def _render_header(cluster: dict):
    sev = cluster.get("severidad", "MEDIUM")
    icon = SEVERIDAD_COLOR.get(sev, "⚪")
    st.title(f"{icon} {cluster['nombre']}")

    if st.button("← Volver a clusters"):
        _clear_selection_state()
        del st.session_state["selected_cluster"]
        st.rerun()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tickets Zendesk", cluster.get("ticket_count", 0))
    c2.metric("Severidad", sev)
    c3.metric("Sistema", cluster.get("sistema") or "—")
    c4.metric("Candidatos Jira", len(cluster.get("jira_candidatos", []) or []))

    st.markdown(f"**Resumen:** {cluster.get('resumen', '_Sin resumen_')}")
    st.caption(
        f"Tipo: `{cluster.get('tipo_problema', '—')}` · "
        f"Estado: `{cluster.get('estado', '—')}` · "
        f"Creado {cluster.get('created_at', '')[:10]} · "
        f"Actualizado {cluster.get('updated_at', '')[:10]}"
    )


def _clear_selection_state():
    for k in list(st.session_state.keys()):
        if k.startswith(("z_df_", "j_df_")):
            del st.session_state[k]


# ── Tables ────────────────────────────────────────────────────
def _render_zendesk_table(col, tickets: list[dict], cluster_id: str) -> int | None:
    with col:
        st.subheader(f"🎫 Tickets Zendesk ({len(tickets)})")
        if not tickets:
            st.info("No hay tickets asociados.")
            return None

        ordered = list(reversed(tickets))
        df = pd.DataFrame(
            [
                {
                    "#": t.get("zendesk_id"),
                    "Asunto": (t.get("subject") or "").strip()[:80],
                    "Prioridad": t.get("priority") or "—",
                    "Creado": (t.get("created_at") or "")[:10],
                }
                for t in ordered
            ]
        )
        event = st.dataframe(
            df,
            key=f"z_df_{cluster_id}",
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            use_container_width=True,
            height=360,
        )
        rows = event.selection.rows if event else []
        return rows[0] if rows else None


def _render_jira_table(col, jira_items: list, cluster_id: str) -> int | None:
    with col:
        st.subheader(f"🔷 Candidatos Jira ({len(jira_items)})")
        if not jira_items:
            st.info("Sin candidatos. Ejecuta `python fase4_jira.py`.")
            return None

        rows = []
        for item in jira_items:
            if isinstance(item, str):
                rows.append({"ID": item, "Resumen": "", "Estado": "—", "Conf.": "—"})
            else:
                conf = item.get("confianza")
                conf_str = f"{int(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
                rows.append(
                    {
                        "ID": item.get("jira_id", "?"),
                        "Resumen": (item.get("summary") or "").strip()[:80],
                        "Estado": item.get("status") or "—",
                        "Conf.": conf_str,
                    }
                )
        df = pd.DataFrame(rows)
        event = st.dataframe(
            df,
            key=f"j_df_{cluster_id}",
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            use_container_width=True,
            height=360,
        )
        sel = event.selection.rows if event else []
        return sel[0] if sel else None


# ── Detail panels ─────────────────────────────────────────────
def _render_detail_panels(tickets, jira_items, z_idx, j_idx):
    if z_idx is None and j_idx is None:
        st.caption("👆 Selecciona una fila de Zendesk y/o de Jira para ver su detalle aquí abajo y compararlos.")
        return

    st.markdown("---")
    st.subheader("📖 Detalle")
    left, right = st.columns(2)

    with left:
        if z_idx is not None:
            ticket = list(reversed(tickets))[z_idx]
            _render_zendesk_detail(ticket)
        else:
            st.caption("_Selecciona un ticket Zendesk para ver su detalle_")

    with right:
        if j_idx is not None:
            item = jira_items[j_idx]
            _render_jira_detail(item)
        else:
            st.caption("_Selecciona un candidato Jira para ver su detalle_")


def _render_zendesk_detail(t: dict):
    subdomain = os.environ.get("ZENDESK_SUBDOMAIN", "eldiarioeshelp")
    tid = t.get("zendesk_id", "?")
    url = t.get("zendesk_url") or f"https://{subdomain}.zendesk.com/agent/tickets/{tid}"

    st.markdown(f"### 🎫 Zendesk #{tid}")
    st.markdown(f"**{t.get('subject') or 'Sin asunto'}**")

    meta_cols = st.columns(3)
    meta_cols[0].markdown(f"**Prioridad:** `{t.get('priority') or '—'}`")
    meta_cols[1].markdown(f"**Tipo:** `{t.get('ticket_type') or '—'}`")
    meta_cols[2].markdown(f"**Canal:** `{t.get('channel') or '—'}`")

    conf = t.get("fase1_confianza", 0)
    st.caption(
        f"Creado {(t.get('created_at') or '')[:19]}"
        f" · Procesado {(t.get('procesado_at') or '')[:19]}"
        f" · Fase1 {conf:.0%} ({t.get('fase1_modelo') or '—'})"
    )

    body_clean = _strip_html(t.get("body_preview") or "")
    if body_clean:
        st.markdown("**Cuerpo del ticket:**")
        st.text(body_clean)

    if t.get("fase3_resumen_llm"):
        st.markdown(f"**Resumen LLM:** {t['fase3_resumen_llm']}")

    tags = t.get("tags", [])
    if tags:
        st.caption("Tags: " + ", ".join(f"`{tag}`" for tag in tags[:10]))

    st.link_button("🔗 Abrir en Zendesk", url)


def _render_jira_detail(item):
    jira_host = os.environ.get("JIRA_HOST", "eldiario.atlassian.net")
    if isinstance(item, str):
        jid = item
        url = f"https://{jira_host}/browse/{jid}"
        st.markdown(f"### 🔷 {jid}")
        st.caption("(Candidato legacy — sin detalle enriquecido. Ejecuta `python fase4_jira.py` para actualizarlo.)")
        st.link_button("🔗 Abrir en Jira", url)
        return

    jid = item.get("jira_id", "?")
    url = item.get("url") or f"https://{jira_host}/browse/{jid}"
    status = item.get("status") or "—"
    conf = item.get("confianza")
    conf_str = f"{int(conf * 100)}%" if isinstance(conf, (int, float)) else "—"

    st.markdown(f"### 🔷 {jid}")
    st.markdown(f"**{item.get('summary') or 'Sin título'}**")

    meta_cols = st.columns(2)
    meta_cols[0].markdown(f"**Estado:** `{status}`")
    meta_cols[1].markdown(f"**Confianza del match:** `{conf_str}`")

    razon = item.get("razon")
    if razon:
        st.markdown(f"**Razón del match:** {razon}")

    st.link_button("🔗 Abrir en Jira", url)
