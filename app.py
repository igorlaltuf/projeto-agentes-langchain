"""Interface Streamlit do ApuraCota: chat com o agente, trajetoria de ferramentas e verificacao."""

from __future__ import annotations

import uuid

import streamlit as st
from langchain_community.callbacks.streamlit import StreamlitCallbackHandler

from src import llm
from src.agent import montar_agente, trajetoria
from src.verification import verificar_turno

st.set_page_config(page_title="ApuraCota", page_icon="🧾")
st.title("🧾 ApuraCota — apuração de gastos parlamentares")
st.caption("Agente com API da Câmara, SQL da CEAP e regras da cota (Ato da Mesa 43/2009).")
if llm.offline():
    st.warning("Modo offline: modelo dummy roteirizado e fixtures gravados (sem chave da OpenAI).", icon="🔌")

# ----------------------------------------------------------------------------- #
# Estado da sessão (o Streamlit recarrega o script a cada interação)
# ----------------------------------------------------------------------------- #
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"sessao_{uuid.uuid4().hex[:8]}"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "trajetorias" not in st.session_state:
    st.session_state.trajetorias = {}
if "verificacoes" not in st.session_state:
    st.session_state.verificacoes = {}
if "agente" not in st.session_state:
    with st.spinner("Montando o agente..."):
        st.session_state.agente = montar_agente()

with st.sidebar:
    st.subheader("Sessão")
    st.code(st.session_state.thread_id)
    if st.button("Nova apuração"):
        st.session_state.clear()
        st.rerun()
    verificar = st.toggle("Verificar respostas (fact-check + NLI)", value=True)
    st.subheader("Última trajetória")
    ultima = st.session_state.trajetorias.get(len(st.session_state.messages) - 1, [])
    if not ultima:
        st.caption("Nenhuma ferramenta usada ainda.")
    for passo in ultima:
        if passo["tipo"] == "decisao":
            st.markdown(f"➡️ **{passo['ferramenta']}** `{passo['args']}`")
        else:
            st.markdown(f"⬅️ {passo['ferramenta']}: {passo['conteudo'][:120]}…")


def mostrar_verificacao(v: dict) -> None:
    nli = v["nli"]
    icone = {"APOIA": "✅", "REFUTA": "❌", "NEUTRA": "➖"}[nli["nli_verdict"]]
    with st.expander(f"{icone} Verificação: NLI {nli['nli_verdict']} · {len(v['fact_check'])} claim(s)"):
        if v["fact_check"]:
            st.table(v["fact_check"])
        else:
            st.caption("Nenhuma afirmação numérica para checar.")
        st.markdown(
            f"**Coerência lógica:** {nli['is_logically_coherent']} · falácias: {nli['detected_fallacies']}"
        )
        st.caption(nli["technical_justification"])
        if v.get("truth"):
            st.json(v["truth"], expanded=False)


# ----------------------------------------------------------------------------- #
# Histórico
# ----------------------------------------------------------------------------- #
for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        st.write(m["content"])
        if idx in st.session_state.verificacoes:
            mostrar_verificacao(st.session_state.verificacoes[idx])

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
        traj = trajetoria(estado)
        if verificar:
            with st.spinner("Verificando a resposta..."):
                try:
                    verificacao = verificar_turno(resposta, traj)
                except Exception as e:  # noqa: BLE001
                    verificacao = None
                    st.caption(f"Verificação indisponível: {e}")

    idx = len(st.session_state.messages)
    st.session_state.messages.append({"role": "assistant", "content": resposta})
    st.session_state.trajetorias[idx] = traj
    if verificar and verificacao:
        st.session_state.verificacoes[idx] = verificacao
    st.rerun()
