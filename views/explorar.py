import subprocess
import sys
from pathlib import Path
import streamlit as st
from storage import Storage


def render():
    st.title("🔍 Explorar taxonomía y estadísticas")
    storage = Storage()

    tab1, tab2 = st.tabs(["📋 Taxonomía (conceptos.json)", "📈 Estadísticas pipeline"])

    with tab1:
        conceptos = storage.get_conceptos()
        if not conceptos:
            st.warning("No hay conceptos.json. Ejecuta: `python pipeline.py --fase0 --days 30`")
            return

        st.caption(f"Generado: {conceptos.get('generated_at', '—')} · Muestra: {conceptos.get('muestra_tickets', '—')} tickets")

        st.subheader("Sistemas detectados")
        for sistema, config in conceptos.get("sistemas", {}).items():
            with st.expander(f"`{sistema}`"):
                st.write("Keywords:", config.get("keywords", []))

        st.subheader("Tipos de problema")
        for tipo, config in conceptos.get("tipos_problema", {}).items():
            with st.expander(f"`{tipo}` — severidad default: {config.get('severidad_default')}"):
                st.write("Keywords:", config.get("keywords", []))

        st.subheader("Top keywords frecuentes")
        kw = conceptos.get("keywords_frecuentes", {})
        if kw:
            top = sorted(kw.items(), key=lambda x: -x[1])[:20]
            st.bar_chart({k: v for k, v in top})

        st.subheader("Co-ocurrencias más fuertes")
        cooc = conceptos.get("coocurrencias_top", {})
        if cooc:
            for pair, count in list(cooc.items())[:10]:
                st.markdown(f"- **{pair}**: {count} apariciones juntas")

    with tab2:
        tickets = storage.get_tickets()
        clusters = storage.get_clusters()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total tickets procesados", len(tickets))
        tecnicos = [t for t in tickets if t.get("fase1_resultado") == "TECNICO"]
        col2.metric("Técnicos", len(tecnicos))
        col3.metric("Descartados", len(tickets) - len(tecnicos))
        col4.metric("Clusters activos", len([c for c in clusters if c.get("estado") == "abierto"]))

        if tecnicos:
            via_ancla = [t for t in tecnicos if t.get("fase2_anclas", {}).get("sistemas")]
            via_llm = [t for t in tecnicos if t.get("fase3_resumen_llm")]
            st.markdown(f"**Ancla directa (sin LLM remoto):** {len(via_ancla)} tickets ({len(via_ancla)/len(tecnicos):.0%})")
            st.markdown(f"**Via GPT-4o:** {len(via_llm)} tickets ({len(via_llm)/len(tecnicos):.0%})")

        st.subheader("Re-ejecutar exploración")
        days = st.number_input("Días de histórico", min_value=1, max_value=90, value=30)
        if st.button("🔄 Regenerar conceptos.json"):
            with st.spinner(f"Procesando {days} días de tickets..."):
                script = str(Path(__file__).parent.parent / "fase0_explorar.py")
                result = subprocess.run(
                    [sys.executable, script, "--days", str(days)],
                    capture_output=True, text=True
                )
            if result.returncode == 0:
                st.success("conceptos.json regenerado correctamente")
                st.code(result.stdout)
            else:
                st.error(f"Error: {result.stderr}")
