import html as _html
import os
import re

import pandas as pd
import streamlit as st

from storage import Storage
from zendesk_client import ZendeskClient

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


_JIRA_SECTION_LABELS = (
    # Template "bug" clásico de Jira
    "Contexto", "Descripción", "Descripcion",
    "Pasos para reproducir", "Pasos a reproducir", "Pasos",
    "Resultado esperado", "Resultado actual",
    "Comportamiento esperado", "Comportamiento actual",
    "Impacto", "Solución", "Solucion",
    "Necesitamos revisar",
    # Template "propuesta/feature"
    "En resumen", "Resumen",
    "Propuesta de mensaje de error", "Propuesta de solución", "Propuesta de solucion",
    "Propuesta de mensaje", "Propuesta",
    "Criterios de aceptación", "Criterios de aceptacion", "Criterios",
    "Requisitos", "Observaciones", "Observación", "Observacion",
    "Objetivo", "Alcance",
    "Motivación", "Motivacion",
    "Justificación", "Justificacion",
    "Precondiciones", "Antecedentes",
    "Nota", "Notas", "Mensaje",
)
# Longer variants first so the alternation doesn't prefer a short prefix
# ("Propuesta" vs "Propuesta de mensaje de error").
_SORTED_LABELS = sorted(_JIRA_SECTION_LABELS, key=len, reverse=True)
_LABEL_BREAK_RE = re.compile(
    r"(?<!\n)\s*(?=(?:" + "|".join(re.escape(s) for s in _SORTED_LABELS) + r")\s*:)",
)
# Period/!/?/: directly followed by capital letter or digit (e.g. a date) → insert space.
_SENTENCE_GLUE_RE = re.compile(r"([.!?:])(?=[A-ZÁÉÍÓÚÑ\d])")


def _format_jira_description(text: str) -> str:
    """Cosmetic fix-up for Jira descriptions that arrive with glued sentences.

    - Splits on common section labels (Contexto, Descripción, Pasos…).
    - Adds a space after `.`/`!`/`?` when followed directly by a capital
      letter or digit (frequent pattern: `...18/03/2026.De hecho...`).
    """
    if not text:
        return ""
    fixed = _SENTENCE_GLUE_RE.sub(r"\1 ", text)
    fixed = _LABEL_BREAK_RE.sub("\n\n", fixed)
    return fixed


def _resolve_cluster_tickets(storage: Storage, cluster: dict) -> list[dict]:
    """Resolve the tickets belonging to a cluster from its `ticket_ids`.

    Sub-clusters created by Fase 3.5 don't rewrite each ticket's
    `fase3_cluster_id` — the parent→child membership lives only on the
    cluster's `ticket_ids`. Going through that list (fallback to the legacy
    reverse filter only when missing) works for both parents and children.
    """
    ids = cluster.get("ticket_ids") or []
    if not ids:
        return storage.get_cluster_tickets(cluster["cluster_id"])
    by_id = storage.get_tickets_by_id()
    return [by_id[i] for i in ids if i in by_id]


def render(cluster_id: str):
    storage = Storage()
    all_clusters = storage.get_clusters()
    cluster = next((c for c in all_clusters if c["cluster_id"] == cluster_id), None)

    if not cluster:
        st.error(f"Cluster `{cluster_id}` no encontrado.")
        if st.button("← Volver"):
            _goto_list()
        return

    if cluster.get("estado") == "refined":
        _render_refined_parent(cluster, all_clusters)
        return

    _render_breadcrumb(cluster)
    _render_header(cluster)
    st.markdown("---")

    tickets = _resolve_cluster_tickets(storage, cluster)
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


# ── Refined parent + breadcrumb ──────────────────────────────
def _render_refined_parent(cluster: dict, all_clusters: list[dict]) -> None:
    st.title(f"🧬 {cluster['nombre']}")
    if st.button("← Volver a clusters"):
        _goto_list()
    hijos = [c for c in all_clusters if c.get("parent_cluster_id") == cluster["cluster_id"]]
    st.warning(
        f"Este cluster se dividió en {len(hijos)} sub-cluster(s) en el paso "
        "de refine (Fase 3.5). Selecciona uno para ver su detalle:"
    )
    if not hijos:
        st.caption("_Sin sub-clusters activos (anomalía)._")
        return
    for h in hijos:
        cid = h["cluster_id"]
        subtipo = h.get("subtipo") or "—"
        nombre = h.get("nombre", "")
        count = h.get("ticket_count", 0)
        st.markdown(
            f"- **[{cid}](?cluster={cid})** — `{subtipo}` · "
            f"{nombre} · {count} tickets"
        )
    st.caption(
        f"Padre refinado `{cluster.get('refined_at', '')[:19]}` · "
        f"`{cluster.get('sistema', '—')}` / `{cluster.get('tipo_problema', '—')}`"
    )


def _render_breadcrumb(cluster: dict) -> None:
    parent = cluster.get("parent_cluster_id")
    if not parent:
        return
    subtipo = cluster.get("subtipo") or "—"
    st.caption(
        f"← [{parent}](?cluster={parent}) / **{cluster['cluster_id']}** "
        f"— subtipo: `{subtipo}`"
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
            width="stretch",
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
                rows.append({"ID": item, "📧": "", "Resumen": "", "Estado": "—", "Conf.": "—"})
            else:
                conf = item.get("confianza")
                conf_str = f"{int(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
                em_flag = "📧" if item.get("email_match") else ""
                rows.append(
                    {
                        "ID": item.get("jira_id", "?"),
                        "📧": em_flag,
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
            width="stretch",
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


# ── Conversation (Zendesk comments) ───────────────────────────
_ROLE_STYLE = {
    "end-user": {"icon": "👤", "label": "Peticionario", "accent": "#0969da", "bg": "#ddf4ff"},
    "agent":    {"icon": "🎧", "label": "Agente",       "accent": "#1a7f37", "bg": "#dafbe1"},
    "admin":    {"icon": "🎧", "label": "Agente",       "accent": "#1a7f37", "bg": "#dafbe1"},
}
_ROLE_FALLBACK = {"icon": "❓", "label": "—", "accent": "#656d76", "bg": "#eaeef2"}
_INTERNAL_BG = "#fff8c5"   # yellow note
_INTERNAL_BORDER = "#d4a72c"


def _load_ticket_comments(ticket_id) -> list[dict] | None:
    """Fetch + memoize comments per ticket. Returns None on error (caller falls back)."""
    if ticket_id is None:
        return None
    cache = st.session_state.setdefault("_zendesk_comments_cache", {})
    if ticket_id in cache:
        return cache[ticket_id]
    try:
        with st.spinner("Cargando conversación desde Zendesk…"):
            client = ZendeskClient()
            comments = client.get_ticket_comments(int(ticket_id))
    except Exception as e:
        st.warning(f"No se pudo cargar la conversación: {e}")
        cache[ticket_id] = None
        return None
    cache[ticket_id] = comments
    return comments


_MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _format_created_at(iso: str) -> tuple[str, str]:
    """Return (date_label, time_label) like ('16 abr 2026', '11:18')."""
    if not iso:
        return ("—", "")
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return (iso[:10], iso[11:16])
    return (f"{dt.day:02d} {_MESES_ES[dt.month - 1]} {dt.year}", dt.strftime("%H:%M"))


def _pick_requester(comments: list[dict] | None, ticket: dict) -> dict | None:
    """Resolve the requester from the loaded conversation.

    Prefer the first `end-user` comment (Zendesk's canonical requester). Fall
    back to the very first comment if no end-user is present (some tickets are
    created API-side by an agent on behalf of someone).
    """
    if not comments:
        rid = ticket.get("requester_id")
        return {"name": "—", "email": "", "id": rid} if rid else None
    for c in comments:
        author = c.get("author") or {}
        if author.get("role") == "end-user":
            return author
    return (comments[0].get("author") or {}) or None


def _render_requester_pill(author: dict) -> None:
    name = _html.escape(author.get("name") or "—")
    email = _html.escape(author.get("email") or "")
    email_html = (
        f'<a href="mailto:{email}" style="color:#0969da;text-decoration:none;">&lt;{email}&gt;</a>'
        if email else '<span style="color:#6e7781;">sin email</span>'
    )
    st.markdown(
        f'<div style="margin:0.35rem 0 0.6rem 0;font-size:0.9rem;">'
        f'<span style="background:#ddf4ff;color:#0969da;padding:3px 10px;'
        f'border-radius:12px;font-weight:600;">👤 Peticionario</span>'
        f'<span style="margin-left:10px;">{name}</span>'
        f'<span style="margin-left:8px;">{email_html}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _role_style(role: str, name: str) -> dict:
    """Pick a visual style. If role is unknown but author looks like a bot/system, flag it."""
    if role in _ROLE_STYLE:
        return _ROLE_STYLE[role]
    lname = (name or "").lower()
    if "bot" in lname or "system" in lname:
        return {"icon": "🤖", "label": "Sistema", "accent": "#8250df", "bg": "#fbefff"}
    return _ROLE_FALLBACK


def _render_conversation(comments: list[dict]) -> None:
    for c in comments:
        author = c.get("author") or {}
        role = author.get("role") or "unknown"
        name = author.get("name") or "—"
        style = _role_style(role, name)
        is_internal = not c.get("public", True)
        accent = _INTERNAL_BORDER if is_internal else style["accent"]
        bg = _INTERNAL_BG if is_internal else style["bg"]

        name_e = _html.escape(name)
        email_e = _html.escape(author.get("email") or "")
        channel_e = _html.escape(c.get("channel") or "")
        date_label, time_label = _format_created_at(c.get("created_at") or "")
        body_escaped = _html.escape(c.get("body") or "")

        badge_color = "#d4a72c" if is_internal else "#1f883d"
        badge_text = "Interna" if is_internal else "Pública"
        badge = (
            f'<span style="background:{badge_color};color:#fff;font-size:0.7rem;'
            f'padding:1px 8px;border-radius:10px;">{badge_text}</span>'
        )
        email_html = (
            f'<span style="color:#57606a;font-size:0.8rem;">&lt;{email_e}&gt;</span>'
            if email_e else ""
        )
        channel_html = (
            f'<span style="background:#eaeef2;color:#57606a;font-size:0.7rem;'
            f'padding:1px 8px;border-radius:10px;">{channel_e}</span>'
            if channel_e else ""
        )
        time_html = (
            f'<span style="color:#24292f;font-size:0.78rem;font-variant-numeric:tabular-nums;">'
            f'📅 {date_label} · 🕒 {time_label}</span>'
        )

        # IMPORTANT: one line, no leading indentation. Streamlit's markdown
        # treats 4-space-indented lines as a code block, which previously
        # exposed the raw <span> HTML inside the cards.
        card = (
            f'<div style="border-left:4px solid {accent};background:{bg};'
            f'padding:0.6rem 0.9rem;margin:0.45rem 0;border-radius:4px;'
            f'max-width:100%;box-sizing:border-box;">'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;'
            f'margin-bottom:0.45rem;font-size:0.85rem;">'
            f'<span style="font-weight:600;">{style["icon"]} {style["label"]}: {name_e}</span>'
            f'{email_html}{badge}{channel_html}'
            f'</div>'
            f'<div style="margin-bottom:0.4rem;">{time_html}</div>'
            f'<div style="font-size:0.88rem;line-height:1.5;color:#24292f;'
            f'white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;'
            f'max-height:260px;overflow-y:auto;background:#ffffffaa;'
            f'padding:0.5rem 0.7rem;border-radius:3px;">{body_escaped}</div>'
            f'</div>'
        )
        st.markdown(card, unsafe_allow_html=True)


def _render_ticket_body(text: str) -> None:
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

    comments = _load_ticket_comments(tid)
    requester = _pick_requester(comments, t)
    if requester:
        _render_requester_pill(requester)
    if comments:
        st.markdown("**Conversación:**")
        _render_conversation(comments)
    else:
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
    meta_cols[1].metric("Confianza del match", conf_str)

    em = item.get("email_match") or []
    if em:
        parts: list[str] = []
        for e in em:
            email = e.get("email", "")
            zid = e.get("zendesk_id")
            if not email:
                continue
            if zid is not None:
                parts.append(f"`{email}` (ticket #{zid})")
            else:
                parts.append(f"`{email}`")
        if parts:
            st.success("📧 **Match por email de usuario:** " + " · ".join(parts))

    pool_entry = (jira_pool_by_id or {}).get(jid) if jira_pool_by_id else None
    description = item.get("description_text") or (pool_entry.get("description_text") if pool_entry else "")
    body_clean = _format_jira_description(_strip_html(description or ""))
    if body_clean:
        st.markdown("**Descripción:**")
        _render_ticket_body(body_clean)

    razon = item.get("razon")
    if razon and not hide_razon:
        st.markdown(f"**Razón del match:** {razon}")

    st.link_button("🔗 Abrir en Jira", url)
