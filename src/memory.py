"""Memória do agente segundo a taxonomia CoALA.

- STM / working memory ..... MemorySaver (checkpointer) por thread_id
- Cache semântico ........... respostas recentes de API (ver _get_json em tools.py, cache em fixtures/)
- LTM semântica ............. regras da cota em FAISS (data.py)
- LTM episódica ............. apurações anteriores com data, neste módulo
- LTM procedural ............ system prompt PTCF e docstrings das ferramentas (prompts.py / tools.py)
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.messages import SystemMessage, trim_messages
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from src import config


# ----------------------------------------------------------------------------- #
# Curto prazo
# ----------------------------------------------------------------------------- #
def novo_checkpointer() -> MemorySaver:
    return MemorySaver()


def podar_historico(mensagens, llm, max_tokens: int = 3000):
    """Sliding window por tokens, preservando o system prompt."""
    return trim_messages(
        mensagens,
        max_tokens=max_tokens,
        strategy="last",
        token_counter=llm,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )


# ----------------------------------------------------------------------------- #
# Longo prazo episódico: apurações anteriores
# ----------------------------------------------------------------------------- #
_ltm_episodica: InMemoryVectorStore | None = None


def _store() -> InMemoryVectorStore:
    """Cria o vector store episódico sob demanda (evita exigir a chave da OpenAI no import)."""
    global _ltm_episodica
    if _ltm_episodica is None:
        _ltm_episodica = InMemoryVectorStore(embedding=OpenAIEmbeddings(model=config.MODELO_EMBEDDINGS))
    return _ltm_episodica


class GravarApuracaoArgs(BaseModel):
    resumo: str = Field(
        ..., description="Resumo factual do que foi apurado (deputado, periodo, valores, achados)."
    )
    reporter_id: str = Field(..., description="Identificador do reporter.")


@tool(args_schema=GravarApuracaoArgs)
def gravar_apuracao(resumo: str, reporter_id: str) -> str:
    """Salva o resumo de uma apuracao na memoria de longo prazo do reporter, com a data.
    Use quando o usuario pedir para guardar, resumir ou fechar uma apuracao."""
    _store().add_documents(
        [Document(page_content=resumo, metadata={"reporter": reporter_id, "data": config.HOJE})]
    )
    return f"Apuracao salva para '{reporter_id}' em {config.HOJE}."


class LembrarApuracaoArgs(BaseModel):
    consulta: str = Field(..., description="O que procurar nas apuracoes passadas.")
    reporter_id: str = Field(..., description="Identificador do reporter.")


@tool(args_schema=LembrarApuracaoArgs)
def lembrar_apuracao(consulta: str, reporter_id: str) -> str:
    """Recupera apuracoes anteriores do reporter por similaridade semantica.
    Use quando o usuario fizer referencia ao passado ('aquele fornecedor', 'semana passada')."""
    docs = _store().similarity_search(
        consulta, k=3, filter=lambda d: d.metadata.get("reporter") == reporter_id
    )
    if not docs:
        return "Nenhuma apuracao anterior encontrada."
    return "\n".join(f"* [{d.metadata['data']}] {d.page_content}" for d in docs)


SYSTEM_RESUMO = SystemMessage(
    content="Resuma a conversa a seguir em ate 5 linhas, mantendo nomes, periodos e valores."
)
