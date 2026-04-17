import os
import re

import pandas as pd
import streamlit as st

from storage import Storage

SEVERIDAD_COLOR = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}


_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|li|tr|h[1-6]|blockquote|pre|section|article)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&#39;": "'",
    "&quot;": '"',
}


def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = _BLOCK_TAG_RE.sub("\n", text)
    clean = _TAG_RE.sub("", clean)
    for entity, repl in _HTML_ENTITIES.items():
        clean = clean.replace(entity, repl)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in clean.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def render(cluster_id: str):
    storage = Storage()
    cluster = next(
        (c for c in storage.get_clusters() if c["cluster_id"] == cluster_id),
        None,
    )

    if not cluster:
        st.error(f"Cluster `{cluster_id}` no encontrado.")
        if st.button("← Volver"):
            _goto_list()
        return

    _render_header(cluster)
    st.markdown("---")

    tickets = storage.get_cluster_tickets(cluster_id)
    jira_items = cluster.get("jira_candidatos", []) or []
    jira_pool_by_id = {t["jira_id"]: t for t in storage.get_jira_tickets()}

    col_z, col_j = st.columns(2)
    z_selected = _render_zendesk_table(col_z, tickets, cluster_id)
    j_selected = _render_jira_table(col_j, jira_items, cluster_id)

    _render_detail_panels(
        tickets=tickets,
        jira_items=jira_items,
        z_idx=z_selected,
        j_idx=j_selected,
        jira_pool_by_id=jira_pool_by_id,
    )


# ── Header ────────────────────────────────────────────────────
def _render_header(cluster: dict):
    sev = cluster.get("severidad", "MEDIUM")
    icon = SEVERIDAD_COLOR.get(sev, "⚪")
    st.title(f"{icon} {cluster['nombre']}")

    if st.button("← Volver a clusters"):
        _goto_list()

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


def _goto_list():
    _clear_selection_state()
    st.query_params.clear()
    st.rerun()


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
def _render_detail_panels(tickets, jira_items, z_idx, j_idx, jira_pool_by_id):
    if z_idx is None and j_idx is None:
        st.caption("👆 Selecciona una fila de Zendesk y/o de Jira para ver su detalle aquí abajo y compararlos.")
        return

    st.markdown("---")
    st.subheader("📖 Detalle")

    both_selected = z_idx is not None and j_idx is not None
    if both_selected:
        jira_item = jira_items[j_idx]
        razon = jira_item.get("razon") if isinstance(jira_item, dict) else None
        if razon:
            st.markdown(f"**Razón del match:** {razon}")

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
            _render_jira_detail(item, jira_pool_by_id, hide_razon=both_selected)
        else:
            st.caption("_Selecciona un candidato Jira para ver su detalle_")


def _render_ticket_body(text: str) -> None:
    import html as _html

    escaped = _html.escape(text)
    st.markdown(
        f"""
<div style="
    border-left: 3px solid #d0d7de;
    background: #f6f8fa;
    padding: 0.75rem 1rem;
    margin: 0.25rem 0 0.75rem 0;
    border-radius: 4px;
    font-size: 0.9rem;
    line-height: 1.5;
    color: #24292f;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
    max-height: 320px;
    overflow-y: auto;
    overflow-x: hidden;
    max-width: 100%;
    box-sizing: border-box;
">{escaped}</div>
""",
        unsafe_allow_html=True,
    )


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
        _render_ticket_body(body_clean)

    if t.get("fase3_resumen_llm"):
        st.markdown(f"**Resumen LLM:** {t['fase3_resumen_llm']}")

    tags = t.get("tags", [])
    if tags:
        st.caption("Tags: " + ", ".join(f"`{tag}`" for tag in tags[:10]))

    st.link_button("🔗 Abrir en Zendesk", url)


def _render_jira_detail(item, jira_pool_by_id: dict | None = None, hide_razon: bool = False):
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

    pool_entry = (jira_pool_by_id or {}).get(jid) if jira_pool_by_id else None
    description = item.get("description_text") or (pool_entry.get("description_text") if pool_entry else "")
    body_clean = _strip_html(description or "")
    if body_clean:
        st.markdown("**Descripción:**")
        _render_ticket_body(body_clean)

    razon = item.get("razon")
    if razon and not hide_razon:
        st.markdown(f"**Razón del match:** {razon}")

    st.link_button("🔗 Abrir en Jira", url)
