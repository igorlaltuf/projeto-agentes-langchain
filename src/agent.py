"""Montagem do agente ApuraMandato (ReAct com create_agent + checkpointer).

Nota de equivalencia para o relatorio: 'initialize_agent' / 'AgentExecutor' /
'RunnableWithMessageHistory' foram descontinuados; 'create_agent' implementa o mesmo
ciclo raciocinio -> acao -> observacao sobre LangGraph, e o checkpointer cumpre o
papel do historico por sessao.
"""

from __future__ import annotations

from typing import NotRequired

from langchain.agents import create_agent
from langchain.agents.middleware import after_agent, wrap_model_call, wrap_tool_call
from langchain.agents.middleware.types import AgentState, ModelResponse
from langchain_core.messages.utils import count_tokens_approximately

from src import config, llm, prompts, tools
from src.memory import gravar_apuracao, lembrar_apuracao, novo_checkpointer


def montar_agente(
    com_sql: bool = True,
    com_rag: bool = True,
    com_memoria_episodica: bool = True,
    escopo_ampliado: bool | None = None,
):
    ampliado = config.ESCOPO_AMPLIADO if escopo_ampliado is None else escopo_ampliado
    if llm.offline():
        from src.data import preparar_offline

        preparar_offline()
    modelo = llm.chat_model("agente")
    toolkit = tools.toolkit_basico()
    toolkit.append(tools.consultar_sancoes)
    from src.investigation_flow import investigar_fornecedores

    if com_sql:
        from src.catalog import inicializar_catalogo
        from src.data import consultar_ceap
        from src.investigation import consultar_dados

        inicializar_catalogo()

        toolkit.extend([consultar_ceap, investigar_fornecedores])
        if ampliado:
            toolkit.append(consultar_dados)
    if com_rag:
        from src.data import ferramenta_regras_cota

        toolkit.append(ferramenta_regras_cota())
    if com_memoria_episodica:
        toolkit += [gravar_apuracao, lembrar_apuracao]

    system_prompt = prompts.SYSTEM_PROMPT_PTCF
    if ampliado:
        system_prompt += prompts.PROMPT_DOMINIOS_AMPLIADOS
    if com_sql:
        from src.data import SCHEMA_CEAP, anos_disponiveis
        from src.investigation import SCHEMA_MANDATO

        system_prompt += "\n# SCHEMA DO BANCO LOCAL\n" + SCHEMA_CEAP
        if ampliado:
            system_prompt += SCHEMA_MANDATO
        system_prompt += f"\nAnos carregados ao iniciar: {anos_disponiveis()}. Anos ausentes exigem carga explícita por comando antes da consulta."

    return create_agent(
        model=modelo,
        tools=toolkit,
        system_prompt=system_prompt,
        checkpointer=novo_checkpointer(),
        middleware=[limitar_contexto, validar_periodo_ferramenta, auditar_resposta],
    )


def trajetoria(estado: dict) -> list[dict]:
    """Extrai decisoes e retornos de ferramentas do ultimo turno (para logs, Streamlit e LangSmith)."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    msgs = estado["messages"]
    inicio = next((i for i, m in reversed(list(enumerate(msgs))) if isinstance(m, HumanMessage)), 0)
    saida = []
    for m in msgs[inicio:]:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                saida.append(
                    {"tipo": "decisao", "ferramenta": tc["name"], "args": tc["args"], "id": tc["id"]}
                )
        elif isinstance(m, ToolMessage):
            saida.append(
                {
                    "tipo": "retorno",
                    "ferramenta": m.name,
                    "conteudo": str(m.content),
                    "tool_call_id": m.tool_call_id,
                }
            )
    return saida


class EstadoAuditado(AgentState):
    verificacao: NotRequired[dict]
    contexto_apuracao: NotRequired[dict]
    contexto_investigacao: NotRequired[dict]
    evidencias: NotRequired[list]


@wrap_model_call
def limitar_contexto(request, handler):
    """Limita o contexto enviado ao modelo sem apagar o histórico da sessão."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from src.followups import PREFIXO_ANUAL, consulta_anual, pedido_anual, resposta_anual
    from src.suppliers import PREFIXO_SOCIOS, pedido_socios, resposta_socios

    ultima = request.messages[-1]
    mensagens_socios = (
        request.messages[:-1]
        if isinstance(ultima, ToolMessage) and ultima.tool_call_id.startswith(PREFIXO_SOCIOS)
        else request.messages
    )
    socios = pedido_socios(mensagens_socios)
    if socios:
        if isinstance(ultima, HumanMessage):
            if socios.get("erro"):
                return ModelResponse(result=[AIMessage(content=socios["erro"])])
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "dados_fornecedor",
                                "args": {"cnpj": socios["cnpj"]},
                                "id": PREFIXO_SOCIOS + (ultima.id or str(len(request.messages))),
                            }
                        ],
                    )
                ]
            )
        if isinstance(ultima, ToolMessage) and ultima.tool_call_id.startswith(PREFIXO_SOCIOS):
            return ModelResponse(result=[AIMessage(content=resposta_socios(socios, str(ultima.content)))])

    from src.investigation_flow import (
        PREFIXO,
        apresentar_investigacao,
        contexto_investigacao,
        pedido_investigacao,
    )

    quadro = contexto_investigacao(request.messages, request.state.get("contexto_investigacao"))
    if isinstance(ultima, ToolMessage) and ultima.tool_call_id.startswith(PREFIXO):
        return ModelResponse(result=[AIMessage(content=apresentar_investigacao(str(ultima.content)))])
    if isinstance(ultima, HumanMessage):
        pedido = pedido_investigacao(str(ultima.content), quadro)
        if pedido:
            disponivel = any(
                getattr(t, "name", None) == "investigar_fornecedores"
                or (
                    isinstance(t, dict)
                    and (t.get("name") or t.get("function", {}).get("name")) == "investigar_fornecedores"
                )
                for t in request.tools
            )
            if not disponivel and pedido.get("args"):
                return ModelResponse(
                    result=[
                        AIMessage(
                            content="Esta configuração não oferece investigação estruturada de fornecedores. Use o agente principal com SQL habilitado."
                        )
                    ]
                )
            if pedido.get("resposta"):
                return ModelResponse(
                    result=[
                        AIMessage(
                            content=pedido["resposta"],
                            additional_kwargs={"investigacao_pendente": pedido["pendente"]}
                            if pedido.get("pendente")
                            else {},
                        )
                    ]
                )
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "investigar_fornecedores",
                                "args": pedido["args"],
                                "id": PREFIXO + (ultima.id or str(len(request.messages))),
                            }
                        ],
                    )
                ]
            )

    contexto = pedido_anual(request.messages, request.state.get("contexto_apuracao"))
    if contexto:
        ultima = request.messages[-1]
        if isinstance(ultima, HumanMessage):
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "consultar_ceap",
                                "args": {"query_sql": consulta_anual(contexto), "anos": contexto["anos"]},
                                "id": PREFIXO_ANUAL + ultima.id
                                if ultima.id
                                else PREFIXO_ANUAL + str(len(request.messages)),
                            }
                        ],
                    )
                ]
            )
        if isinstance(ultima, ToolMessage) and ultima.tool_call_id.startswith(PREFIXO_ANUAL):
            return ModelResponse(result=[AIMessage(content=resposta_anual(contexto, str(ultima.content)))])

    from src.memory import podar_historico

    mensagens = podar_historico(
        request.messages, count_tokens_approximately, max_tokens=config.MAX_TOKENS_CONTEXTO
    )
    # Não descarta a pergunta nem ferramentas do turno atual se ele sozinho exceder a janela.
    from langchain_core.messages import HumanMessage

    inicio = next(
        (i for i, m in reversed(list(enumerate(request.messages))) if isinstance(m, HumanMessage)), 0
    )
    turno = request.messages[inicio:]
    if len(mensagens) < len(turno):
        raise ValueError("Turno excedeu o limite de contexto; inicie uma nova apuração.")
    from langchain_core.messages import SystemMessage

    from src.periods import anos_relativos, ultima_pergunta

    anos = anos_relativos(ultima_pergunta(request.messages))
    if anos:
        orientacao = (
            f"\nPERÍODO OBRIGATÓRIO DESTE TURNO: {anos[0]} a {anos[-1]}, todos os anos {anos}. "
            "Use consultar_ceap, com filtro SQL BETWEEN e argumento anos cobrindo o intervalo inteiro. "
            "Nunca substitua o intervalo por 2025. Explique na resposta o período adotado; "
            "se incluir o ano corrente, informe que ele é parcial. Use CSV nos dois lados de comparações."
        )
        system = str(request.system_message.content) if request.system_message else ""
        return handler(
            request.override(messages=mensagens, system_message=SystemMessage(content=system + orientacao))
        )
    return handler(request.override(messages=mensagens))


@after_agent(state_schema=EstadoAuditado)
def auditar_resposta(state, runtime):
    """Audita cada resposta antes que invoke a devolva à interface ou à avaliação."""
    from src.presentation import apresentar_fontes
    from src.verification import verificar_turno

    resposta = state["messages"][-1]
    texto = apresentar_fontes(resposta.content) if isinstance(resposta.content, str) else resposta.content
    try:
        verificacao = verificar_turno(texto, trajetoria(state))
    except Exception as exc:  # noqa: BLE001
        verificacao = {"erro": str(exc), "status": "INDISPONIVEL"}
    import json

    from src.followups import contexto_sql

    evidencias = []
    for passo in trajetoria(state):
        if passo.get("tipo") == "retorno" and passo.get("ferramenta") == "consultar_dados":
            try:
                retorno = json.loads(passo["conteudo"])
                if retorno.get("status") == "CONSULTADO":
                    evidencias.append(retorno["evidencia"])
            except ValueError:
                pass
    from src.investigation_flow import contexto_investigacao

    quadro = contexto_investigacao(state["messages"], state.get("contexto_investigacao"))
    contexto = contexto_sql(state["messages"])
    return {
        "messages": [resposta.model_copy(update={"content": texto})],
        "verificacao": verificacao,
        "contexto_apuracao": contexto or {},
        "contexto_investigacao": quadro,
        "evidencias": evidencias,
    }


@wrap_tool_call
def validar_periodo_ferramenta(request, handler):
    """Rejeita consultas que reduzam um intervalo relativo a um único ano."""
    import re

    from langchain_core.messages import ToolMessage

    from src.periods import anos_relativos, ultima_pergunta

    anos = anos_relativos(ultima_pergunta(request.state["messages"]))
    chamada = request.tool_call
    if anos and chamada["name"] in {"listar_despesas", "consultar_ceap"}:
        args = chamada["args"]
        sql = args.get("query_sql", "")
        literais = {int(a) for a in re.findall(r"\b(?:19|20)\d{2}\b", sql)}
        for inicio, fim in re.findall(r"BETWEEN\s+(\d{4})\s+AND\s+(\d{4})", sql, re.IGNORECASE):
            literais.update(range(int(inicio), int(fim) + 1))
        if chamada["name"] != "consultar_ceap" or not set(anos).issubset(literais):
            return ToolMessage(
                content=f"Período incorreto. Consulte TODOS os anos de {anos[0]} a {anos[-1]} "
                f"com consultar_ceap, WHERE ano BETWEEN {anos[0]} AND {anos[-1]}, anos={anos}.",
                tool_call_id=chamada["id"],
                name=chamada["name"],
            )
    return handler(request)
