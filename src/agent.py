"""Montagem do agente ApuraCota (ReAct com create_agent + checkpointer).

Nota de equivalencia para o relatorio: 'initialize_agent' / 'AgentExecutor' /
'RunnableWithMessageHistory' foram descontinuados; 'create_agent' implementa o mesmo
ciclo raciocinio -> acao -> observacao sobre LangGraph, e o checkpointer cumpre o
papel do historico por sessao.
"""
from __future__ import annotations

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from src import config, prompts, tools
from src.memory import gravar_apuracao, lembrar_apuracao, novo_checkpointer


def montar_agente(com_sql: bool = True, com_rag: bool = True, com_memoria_episodica: bool = True):
    llm = ChatOpenAI(model=config.MODELO_AGENTE, temperature=0)
    toolkit = tools.toolkit_basico()
    if com_sql:
        from src.data import consultar_ceap
        toolkit.append(consultar_ceap)
    if com_rag:
        from src.data import ferramenta_regras_cota
        toolkit.append(ferramenta_regras_cota())
    if com_memoria_episodica:
        toolkit += [gravar_apuracao, lembrar_apuracao]

    system_prompt = prompts.SYSTEM_PROMPT_PTCF
    if com_sql:
        from src.data import SCHEMA_CEAP
        system_prompt += "\n# SCHEMA DO BANCO LOCAL\n" + SCHEMA_CEAP

    return create_agent(
        model=llm,
        tools=toolkit,
        system_prompt=system_prompt,
        checkpointer=novo_checkpointer(),
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
                saida.append({"tipo": "decisao", "ferramenta": tc["name"], "args": tc["args"]})
        elif isinstance(m, ToolMessage):
            saida.append({"tipo": "retorno", "ferramenta": m.name, "conteudo": str(m.content)[:300]})
    return saida
