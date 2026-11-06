"""Interface Streamlit do ApuraCota: chat com o agente, trajetoria de ferramentas e verificacao."""

from __future__ import annotations

import uuid

import streamlit as st
from langchain_community.callbacks.streamlit import StreamlitCallbackHandler

from src.agent import montar_agente, trajetoria

st.set_page_config(page_title="ApuraCota", page_icon="🧾")
st.title("🧾 ApuraCota — apuração de gastos parlamentares")
st.caption("Agente com API da Câmara, SQL da CEAP e regras da cota (Ato da Mesa 43/2009).")

# ----------------------------------------------------------------------------- #
# Estado da sessão (o Streamlit recarrega o script a cada interação)
# ----------------------------------------------------------------------------- #
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"sessao_{uuid.uuid4().hex[:8]}"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "trajetorias" not in st.session_state:
    st.session_state.trajetorias = {}
if "agente" not in st.session_state:
    with st.spinner("Montando o agente..."):
        st.session_state.agente = montar_agente(
            com_sql=st.session_state.get("usar_sql", True),
            com_rag=st.session_state.get("usar_rag", True),
        )

with st.sidebar:
    st.subheader("Sessão")
    st.code(st.session_state.thread_id)
    if st.button("Nova apuração"):
        st.session_state.clear()
        st.rerun()
    st.subheader("Última trajetória")
    ultima = st.session_state.trajetorias.get(len(st.session_state.messages) - 1, [])
    for passo in ultima:
        if passo["tipo"] == "decisao":
            st.markdown(f"➡️ **{passo['ferramenta']}** `{passo['args']}`")
        else:
            st.markdown(f"⬅️ {passo['ferramenta']}: {passo['conteudo'][:120]}…")

# ----------------------------------------------------------------------------- #
# Histórico
# ----------------------------------------------------------------------------- #
for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        st.write(m["content"])

# ----------------------------------------------------------------------------- #
# Novo turno
# ----------------------------------------------------------------------------- #
if pergunta := st.chat_input("Pergunte sobre gastos da cota parlamentar..."):
    st.chat_message("user").write(pergunta)
    st.session_state.messages.append({"role": "user", "content": pergunta})

    with st.chat_message("assistant"):
        callback = StreamlitCallbackHandler(st.container(), expand_new_thoughts=False)
        config = {"configurable": {"thread_id": st.session_state.thread_id}, "callbacks": [callback]}
        estado = st.session_state.agente.invoke({"messages": [("user", pergunta)]}, config=config)
        resposta = estado["messages"][-1].content
        st.write(resposta)

    st.session_state.messages.append({"role": "assistant", "content": resposta})
    st.session_state.trajetorias[len(st.session_state.messages) - 1] = trajetoria(estado)
    st.rerun()
