"""Chains das fases 1 e 2: entrada/saida estruturadas, Runnable, LCEL, @chain, roteamento,
sequencial/paralela, parser com reparo, callbacks de logging e NLU (fase 3).

Nota de equivalencia (rubrica 2.3): 'RouterChain', 'SequentialChain' e 'LLMChain' sao legados.
O roteamento dinamico e feito com uma chain classificadora + RunnableLambda, e as chains
sequenciais/paralelas com LCEL (pipe) e RunnableParallel.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel, RunnablePassthrough, chain
from pydantic import BaseModel, Field

from src import llm, prompts, tools


# --------------------------------------------------------------------------- #
# Fase 1: saida estruturada
# --------------------------------------------------------------------------- #
class PedidoApuracao(BaseModel):
    """Contrato entre a pergunta livre do reporter e o resto do sistema."""

    intencao: Literal["consultar_gasto", "comparar", "ranking_fornecedor", "regra_cota", "resumo", "conversa"]
    deputado: str | None = Field(default=None, description="Nome do deputado citado.")
    partido: str | None = None
    uf: str | None = None
    ano: int | None = None
    mes: int | None = None
    categoria: str | None = Field(default=None, description="Categoria de despesa (ex: combustivel).")
    dado_faltante: str | None = Field(
        default=None, description="Campo obrigatorio ausente para executar o pedido (ex: 'deputado')."
    )


def chain_normalizar_nome() -> Runnable:
    """PromptTemplate (texto simples) | llm | StrOutputParser."""
    return prompts.PROMPT_NORMALIZAR_NOME | llm.chat_model() | StrOutputParser()


def chain_pedido() -> Runnable:
    """LCEL declarativo: ChatPromptTemplate | llm.with_structured_output(PedidoApuracao)."""
    return prompts.PROMPT_PEDIDO_APURACAO | llm.chat_model().with_structured_output(PedidoApuracao)


@chain
def pedido_ou_esclarecimento(pergunta: str) -> PedidoApuracao | str:
    """Composicao imperativa com @chain: controle de fluxo em Python.

    Se faltar um dado obrigatorio, devolve uma pergunta de esclarecimento em vez do JSON.
    """
    pedido = chain_pedido().invoke({"pergunta": pergunta})
    if pedido.dado_faltante:
        return f"Para seguir preciso saber: {pedido.dado_faltante}. Pode informar?"
    return pedido


# --------------------------------------------------------------------------- #
# Fase 2: parser JSON com tratamento de erro (rubrica 2.6)
# --------------------------------------------------------------------------- #
parser_pedido = PydanticOutputParser(pydantic_object=PedidoApuracao)

PROMPT_REPARO = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Corrija o JSON a seguir para obedecer ao schema. Responda apenas o JSON.\n{format_instructions}",
        ),
        ("human", "JSON invalido: {saida_bruta}\nErro: {erro}"),
    ]
)


def parse_com_reparo(saida_bruta: str, max_tentativas: int = 2) -> PedidoApuracao | None:
    """Tenta o parser; em OutputParserException pede ao modelo para reparar o JSON."""
    for tentativa in range(max_tentativas + 1):
        try:
            return parser_pedido.parse(saida_bruta)
        except OutputParserException as e:
            if tentativa == max_tentativas:
                print(f"[parser] desisti apos {tentativa} reparos: {e.args[0][:80]}")
                return None
            print(f"[parser] saida invalida, pedindo reparo ({tentativa + 1}): {e.args[0][:80]}")
            saida_bruta = (PROMPT_REPARO | llm.chat_model() | StrOutputParser()).invoke(
                {
                    "saida_bruta": saida_bruta,
                    "erro": str(e)[:300],
                    "format_instructions": parser_pedido.get_format_instructions(),
                }
            )
            if llm.offline():  # o dummy nao conserta JSON: simula um reparo valido
                saida_bruta = json.dumps({"intencao": "conversa"})
    return None


# --------------------------------------------------------------------------- #
# Fase 2: roteamento dinamico e chains sequencial / paralela (rubrica 2.3)
# --------------------------------------------------------------------------- #
def chain_roteador() -> Runnable:
    """Classificadora (temperature=0) -> 'api' | 'sql' | 'regra' | 'conversa'."""
    return (
        prompts.PROMPT_ROTEADOR
        | llm.chat_model()
        | StrOutputParser()
        | RunnableLambda(lambda s: s.strip().lower())
    )


def _responder_conversa(entrada: dict) -> str:
    return "Sou o ApuraCota. Pergunte sobre gastos da cota parlamentar de um deputado, comparacoes ou regras."


def _despesas_por_pedido(pedido: PedidoApuracao) -> dict:
    """Etapa determinista da chain sequencial: resolve nome -> id -> despesas."""
    if not pedido.deputado:
        return {"erro": "deputado nao informado"}
    achados = tools.buscar_deputado.invoke({"nome": pedido.deputado, "uf": pedido.uf})
    linhas = [ln for ln in achados.splitlines() if ln.startswith("id=")]
    if len(linhas) != 1:
        return {"erro": "homonimos" if linhas else "nao encontrado", "opcoes": linhas}
    id_dep = int(linhas[0].split("|")[0].split("=")[1])
    args = {"id_deputado": id_dep, "ano": pedido.ano or 2025}
    if pedido.categoria:
        args["categoria"] = pedido.categoria
    return {"deputado": linhas[0], **json.loads(tools.listar_despesas.invoke(args))}


def _formatar_despesas(r: dict) -> str:
    if "erro" in r:
        return f"Nao foi possivel apurar: {r['erro']} {r.get('opcoes', '')}".strip()
    from src.llm import _brl

    return f"{r['deputado']}: {_brl(r['total_valor'])} em {r['ano']} ({r['total_registros']} lancamentos). Fonte: API da Camara."


def chain_sequencial() -> Runnable:
    """pergunta -> pedido estruturado -> API da Camara -> texto (LCEL encadeado)."""
    return (
        {"pergunta": RunnablePassthrough()}
        | chain_pedido()
        | RunnableLambda(_despesas_por_pedido)
        | RunnableLambda(_formatar_despesas)
    )


def chain_paralela() -> Runnable:
    """RunnableParallel: despesas do deputado (API) e media do partido (SQL) ao mesmo tempo."""
    from src.data import consultar_ceap

    def media_partido(pedido: PedidoApuracao) -> str:
        if not pedido.partido:
            return "partido nao informado"
        filtro = f" AND categoria LIKE '%{pedido.categoria.upper()}%'" if pedido.categoria else ""
        sql = (
            "SELECT ROUND(AVG(t.total),2) AS media FROM (SELECT id_deputado, SUM(valor) AS total FROM despesas_ceap "
            f"WHERE partido='{pedido.partido.upper()}' AND ano={pedido.ano or 2025}{filtro} GROUP BY id_deputado) t"
        )
        return consultar_ceap.invoke({"query_sql": sql})

    return (
        {"pergunta": RunnablePassthrough()}
        | chain_pedido()
        | RunnableParallel(
            despesas=RunnableLambda(_despesas_por_pedido) | RunnableLambda(_formatar_despesas),
            media_partido=RunnableLambda(media_partido),
        )
    )


def chain_com_roteamento() -> Runnable:
    """Despacho dinamico: a rota escolhida pela classificadora define a chain executada."""
    from src.data import ferramenta_regras_cota

    def despachar(entrada: dict) -> str:
        rota = entrada["rota"]
        if rota == "api":
            return chain_sequencial().invoke(entrada["pergunta"])
        if rota == "sql":
            r = chain_paralela().invoke(entrada["pergunta"])
            return f"{r['despesas']} | Media do partido: {r['media_partido']}"
        if rota == "regra":
            return ferramenta_regras_cota(k=2).invoke({"query": entrada["pergunta"]})[:500]
        return _responder_conversa(entrada)

    return {"pergunta": RunnablePassthrough(), "rota": chain_roteador()} | RunnableLambda(despachar)


# --------------------------------------------------------------------------- #
# Fase 2: callbacks de logging (rubrica 2.6)
# --------------------------------------------------------------------------- #
class LogCallbackHandler(BaseCallbackHandler):
    """Registra chamadas de LLM e de ferramentas com horario, argumentos e duracao."""

    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []
        self._inicio: dict[str, float] = {}

    def _marca(self, tipo: str, run_id: Any, **dados: Any) -> None:
        self.eventos.append({"hora": datetime.now().strftime("%H:%M:%S.%f")[:-3], "tipo": tipo, **dados})

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._inicio[str(run_id)] = time.perf_counter()
        self._marca("llm_start", run_id, n_mensagens=len(messages[0]))

    def on_llm_end(self, response, *, run_id, **kwargs):
        dur = time.perf_counter() - self._inicio.pop(str(run_id), time.perf_counter())
        msg = response.generations[0][0].message
        chamadas = [tc["name"] for tc in getattr(msg, "tool_calls", [])]
        self._marca("llm_end", run_id, duracao_s=round(dur, 3), tool_calls=chamadas)

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        self._inicio[str(run_id)] = time.perf_counter()
        self._marca("tool_start", run_id, ferramenta=serialized.get("name"), args=inputs or input_str)

    def on_tool_end(self, output, *, run_id, **kwargs):
        dur = time.perf_counter() - self._inicio.pop(str(run_id), time.perf_counter())
        self._marca(
            "tool_end", run_id, duracao_s=round(dur, 3), saida=str(getattr(output, "content", output))[:120]
        )

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._marca("tool_error", run_id, erro=str(error)[:200])

    def como_json(self) -> str:
        return json.dumps(self.eventos, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# Fase 3: NLU com historico (rubrica 3.5)
# --------------------------------------------------------------------------- #
class AnaliseNLU(BaseModel):
    intencao: Literal["consultar_gasto", "comparar", "ranking_fornecedor", "regra_cota", "resumo", "conversa"]
    deputado: str | None = None
    partido: str | None = None
    uf: str | None = None
    periodo: str | None = Field(default=None, description="Ano ou ano/mes, inclusive herdado do historico.")
    categoria: str | None = None
    fornecedor: str | None = None
    dado_faltante: str | None = None
    herdado_do_historico: list[str] = Field(
        default_factory=list, description="Entidades que vieram de turnos anteriores, nao da mensagem atual."
    )


PROMPT_NLU = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Voce faz NLU para uma redacao: identifique a intencao e as entidades da ULTIMA mensagem do usuario, "
            "herdando do historico as entidades que ela nao repete (deputado, periodo, categoria). "
            "Liste em herdado_do_historico o que foi herdado.",
        ),
        MessagesPlaceholder("historico"),
        ("human", "{mensagem}"),
    ]
)


def chain_nlu() -> Runnable:
    return PROMPT_NLU | llm.chat_model().with_structured_output(AnaliseNLU)


def analisar_turno(historico: list[BaseMessage], mensagem: str) -> AnaliseNLU:
    analise = chain_nlu().invoke({"historico": historico, "mensagem": mensagem})
    if llm.offline():  # heranca simples de entidades no dummy
        from src.llm import _campos_heuristicos

        for m in reversed(historico):
            prev = _campos_heuristicos(str(m.content))
            for campo in ("deputado", "periodo", "categoria", "partido", "uf"):
                if getattr(analise, campo) is None and prev.get(campo):
                    setattr(analise, campo, prev[campo])
                    analise.herdado_do_historico.append(campo)
            if analise.deputado and analise.dado_faltante == "deputado":
                analise.dado_faltante = None
    return analise
