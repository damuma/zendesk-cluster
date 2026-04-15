import streamlit as st

st.set_page_config(
    page_title="Zendesk Triage — elDiario.es",
    page_icon="🎫",
    layout="wide",
)

page = st.sidebar.radio(
    "Navegación",
    ["📊 Clusters", "🔍 Explorar"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.caption("Zendesk Triage PoC · elDiario.es")

if page == "📊 Clusters":
    from views.clusters import render
    render()
elif page == "🔍 Explorar":
    from views.explorar import render
    render()
