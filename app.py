"""Página de apresentação e chat do ApuraMandato."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import streamlit as st

from src import llm
from src.agent import montar_agente, trajetoria
from src.catalog import cobertura
from src.presentation import apresentar_fontes

st.set_page_config(
    page_title="ApuraMandato · O dinheiro público em perspectiva", page_icon="◉", layout="wide"
)
st.html(f"<style>{(Path(__file__).parent / 'assets/style.css').read_text()}</style>")


def abrir_chat():
    st.session_state.pagina = "chat"


def nova_apuracao():
    for chave in ("agente", "messages", "trajetorias", "verificacoes", "thread_id"):
        st.session_state.pop(chave, None)


def marca():
    st.html('<div class="brand"><span class="brand-mark">[·]</span>ApuraMandato</div>')


def apresentar():
    marca()

    texto, demonstracao = st.columns([1.25, 1], gap="large")
    with texto:
        st.html("""<div class="eyebrow">Tecnologia cívica para fazer perguntas melhores</div>
        <div class="hero"><h1>O dinheiro é público.<br>A apuração também<br>pode ser <em>sua.</em></h1>
        <p>Investigue gastos parlamentares, conheça os fornecedores e consulte registros de sanções.
        Conecte dados públicos, compare valores e conheça os fornecedores por trás dos pagamentos.</p></div>""")
        st.button("Teste aqui →", type="primary", on_click=abrir_chat, width=220)
        st.html('<div class="micro">Sem cadastro · Pergunte em português · Confira as fontes</div>')
    with demonstracao:
        st.html("""<div class="preview">
        <div class="preview-top"><span>UMA PERGUNTA, NOVOS CAMINHOS</span><span>● APURAMANDATO</span></div>
        <div class="preview-question">A cota pode pagar<br>hospedagem em Brasília?</div>
        <span class="source-chip">Regras da cota</span><span class="source-chip">Fonte pública</span>
        <div class="preview-answer"><div class="preview-label">DA PERGUNTA À EVIDÊNCIA</div>
        <p>O assistente consulta a base normativa para encontrar os trechos relevantes.
        Você acompanha a fonte e pode continuar a investigação na mesma conversa.</p></div>
        <div style="color:#b8ceaa;font-size:11px;margin-top:18px">Prévia ilustrativa · Abra o chat para consultar</div>
        </div>""")

    st.html("""<div class="trust-strip">FONTES PÚBLICAS &nbsp; / &nbsp;
    Câmara dos Deputados &nbsp; · &nbsp; Despesas da CEAP &nbsp; · &nbsp;
    CEIS e CNEP &nbsp; · &nbsp; BrasilAPI</div>
    <div class="section-kicker">MENOS PLANILHAS ABERTAS. MAIS PERGUNTAS RESPONDIDAS.</div>
    <div class="section-title">Um ponto de partida para o controle social.</div>""")
    for coluna, numero, titulo, descricao in zip(
        st.columns(3, gap="large"),
        ["01 / INVESTIGUE", "02 / CONECTE", "03 / CONFIRA"],
        ["Siga o dinheiro", "Continue a conversa", "Veja de onde veio"],
        [
            "Consulte despesas de deputados, médias por partido e fornecedores. Transforme uma dúvida em uma apuração.",
            "Faça uma pergunta e aprofunde no próximo turno. O assistente mantém o contexto da sua investigação.",
            "Acompanhe as consultas e os resultados da verificação. Use as fontes para avaliar cada resposta.",
        ],
        strict=True,
    ):
        with coluna:
            st.html(f'<div class="feature"><span>{numero}</span><h3>{titulo}</h3><p>{descricao}</p></div>')
    with st.expander("O que posso consultar?"):
        st.write(
            "Gastos parlamentares, comparações entre anos e deputados, fornecedores, regras da cota e registros de sanções CEIS/CNEP."
        )
        st.caption(
            "As bases são preparadas antes da conversa. Confira os anos e as datas disponíveis; a demonstração identifica seus dados sintéticos."
        )
    st.html("""<div class="footer"><strong>ApuraMandato</strong> · Um projeto de tecnologia cívica de Igor Laltuf.<br>
    Dados abertos como ponto de partida. Análise crítica como parte do processo.</div>""")


def mostrar_verificacao(v):
    if "erro" in v:
        st.caption(f"Verificação indisponível: {v['erro']}")
        return
    nli = v["nli"]
    rotulo = {
        "APOIA": "Sustentada pelas referências",
        "REFUTA": "Divergência identificada",
        "NEUTRA": "Inconclusiva",
    }
    titulo = rotulo[nli["nli_verdict"]]
    if v.get("metodo") == "reexecucao_estruturada" and all(
        r["status"] == "CONFIRMADO" for r in v["fact_check"]
    ):
        titulo = "Valores e recorte conferidos"
    with st.expander(f"Verificação da resposta · {titulo}"):
        if v["fact_check"]:
            st.table(v["fact_check"])
        else:
            st.caption("Nenhuma afirmação numérica para checar.")
        st.write(nli["technical_justification"])
        if v.get("truth"):
            with st.expander("Referências da verificação"):
                st.json(v["truth"])


def conversar():
    marca()
    st.html('<div class="chat-heading">Toda apuração começa com uma pergunta.</div>')
    st.html(
        '<div class="chat-subtitle">Consulte, compare e siga as fontes. O contexto acompanha a conversa.</div>'
    )
    st.session_state.setdefault("thread_id", f"sessao_{uuid.uuid4().hex[:8]}")
    for chave, valor in (("messages", []), ("trajetorias", {}), ("verificacoes", {})):
        st.session_state.setdefault(chave, valor)

    with st.sidebar:
        st.button(
            "← Sobre o ApuraMandato",
            on_click=lambda: st.session_state.update(pagina="inicio"),
            width="stretch",
        )
        st.subheader("Sua apuração")
        if any(c.get("status") == "DEMONSTRACAO" for c in cobertura()):
            st.warning("Banco de demonstração: despesas de amostra e sanções fictícias.")
        st.download_button(
            "Exportar conversa",
            data=json.dumps(
                {
                    "mensagens": st.session_state.messages,
                    "trajetorias": st.session_state.trajetorias,
                    "verificacoes": st.session_state.verificacoes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file_name="apuramandato-conversa.json",
            mime="application/json",
            width="stretch",
        )
        st.caption("Cada nova apuração começa uma conversa sem o contexto da anterior.")
        st.button("＋ Nova apuração", on_click=nova_apuracao, width="stretch")
        mostrar = st.toggle("Mostrar verificação", value=True)
        st.caption(
            "A verificação é executada em todas as respostas. Esta opção controla a exibição do painel."
        )
        st.divider()
        st.subheader("Caminho da resposta")
        ultima = st.session_state.trajetorias.get(len(st.session_state.messages) - 1, [])
        if not ultima:
            st.caption("As consultas realizadas aparecerão aqui.")
        for passo in ultima:
            if passo["tipo"] == "decisao":
                with st.expander(passo["ferramenta"]):
                    st.json(passo["args"])
            else:
                st.caption(f"Retorno de {passo['ferramenta']}")
                st.text(passo["conteudo"][:300])

    if llm.offline():
        st.info(
            "Você está na demonstração offline: respostas limitadas a exemplos e dados gravados. A busca nas regras não tem qualidade semântica neste modo."
        )
    else:
        st.caption(
            "Confira fontes, período e verificação. O cadastro empresarial pode usar cache; sanções informam a data da base."
        )

    sugestao = None
    if not st.session_state.messages:
        st.write("**Experimente um ponto de partida**")
        for coluna, titulo, pergunta in zip(
            st.columns(3),
            ["Gastos de uma deputada", "Compare por partido", "Entenda as regras"],
            [
                "Quanto a deputada Tabata Amaral gastou com combustível em 2025?",
                "Qual a média de gasto com combustível por deputado do PSB em 2025?",
                "Qual o limite mensal da cota para um deputado de SP?",
            ],
            strict=True,
        ):
            with coluna:
                if st.button(titulo, width="stretch"):
                    sugestao = pergunta

    for idx, mensagem in enumerate(st.session_state.messages):
        with st.chat_message(mensagem["role"]):
            st.write(
                apresentar_fontes(mensagem["content"])
                if mensagem["role"] == "assistant"
                else mensagem["content"]
            )
            if mostrar and idx in st.session_state.verificacoes:
                mostrar_verificacao(st.session_state.verificacoes[idx])

    pergunta = st.chat_input("O que você quer apurar?") or sugestao
    if pergunta:
        st.chat_message("user").write(pergunta)
        with st.spinner("Consultando as fontes e verificando a resposta…"):
            try:
                if "agente" not in st.session_state:
                    st.session_state.agente = montar_agente(escopo_ampliado=False)
                estado = st.session_state.agente.invoke(
                    {"messages": [("user", pergunta)]},
                    config={"configurable": {"thread_id": st.session_state.thread_id}},
                )
            except Exception:  # noqa: BLE001
                st.error("Não foi possível concluir a consulta. Tente novamente ou inicie uma nova apuração.")
                return
        st.session_state.messages.append({"role": "user", "content": pergunta})
        idx = len(st.session_state.messages)
        st.session_state.messages.append(
            {"role": "assistant", "content": apresentar_fontes(estado["messages"][-1].content)}
        )
        st.session_state.trajetorias[idx] = trajetoria(estado)
        if estado.get("verificacao"):
            st.session_state.verificacoes[idx] = estado["verificacao"]
        st.rerun()


pagina = st.session_state.get("pagina", "inicio")
if pagina == "chat":
    conversar()
else:
    apresentar()
