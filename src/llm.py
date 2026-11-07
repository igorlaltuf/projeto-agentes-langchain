"""Provedor de modelos e embeddings.

Com OPENAI_API_KEY no ambiente usa a OpenAI. Sem chave, ou com MODO_OFFLINE=true, usa um
modelo dummy roteirizado (exigencia do enunciado: o codigo deve rodar sem credenciais).

O dummy nao "entende" linguagem: aplica regras simples sobre a ultima mensagem para
decidir qual ferramenta chamar e formata o retorno da ferramenta como resposta. Serve
para demonstrar o ciclo raciocinio -> acao -> observacao, a memoria por thread, o
Streamlit e os testes automatizados sem custo de API.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from src import config


def offline() -> bool:
    return config.MODO_OFFLINE or not os.getenv("OPENAI_API_KEY")


def chat_model(papel: str = "agente") -> BaseChatModel:
    """papel: 'agente' (barato) ou 'juiz' (modelo mais forte, usado na verificacao)."""
    if offline():
        return ChatRoteirizado()
    from langchain_openai import ChatOpenAI

    modelo = config.MODELO_JUIZ if papel == "juiz" else config.MODELO_AGENTE
    return ChatOpenAI(model=modelo, temperature=0)


def embeddings() -> Embeddings:
    if offline():
        return DeterministicFakeEmbedding(size=256)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=config.MODELO_EMBEDDINGS)


# --------------------------------------------------------------------------- #
# Modelo dummy roteirizado
# --------------------------------------------------------------------------- #
UFS = "AC|AL|AM|AP|BA|CE|DF|ES|GO|MA|MG|MS|MT|PA|PB|PE|PI|PR|RJ|RN|RO|RR|RS|SC|SE|SP|TO"
PARTIDOS = "PT|PL|PSB|PSD|PSDB|PDT|PSOL|MDB|UNIÃO|UNIAO|PP|PODE|REPUBLICANOS|PCDOB|REDE|NOVO|AVANTE|CIDADANIA|PV|SOLIDARIEDADE|PRD"
CATEGORIAS = {
    "combust": "COMBUST",
    "passag": "PASSAGEM",
    "divulg": "DIVULG",
    "telefon": "TELEFON",
    "escrit": "ESCRIT",
    "táxi": "TÁXI",
    "taxi": "TÁXI",
    "aliment": "ALIMENT",
    "hosped": "HOSPED",
    "loca": "LOCAÇ",
}
PALAVRAS_INICIAIS = {
    "Quanto",
    "Qual",
    "Quais",
    "Como",
    "Compare",
    "Gastos",
    "Liste",
    "Me",
    "Mostre",
    "O",
    "A",
    "E",
}


def _ano(texto: str) -> int:
    m = re.search(r"\b(20\d{2})\b", texto)
    return int(m.group(1)) if m else config.ANO_CEAP


def _mes(texto: str) -> int | None:
    meses = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    for i, nome in enumerate(meses, start=1):
        if nome in texto.lower():
            return i
    return None


def _categoria(texto: str) -> str | None:
    t = texto.lower()
    return next((v for k, v in CATEGORIAS.items() if k in t), None)


def _nome_deputado(texto: str) -> str | None:
    """Maior sequencia de palavras capitalizadas que nao abre a frase."""
    tokens = re.findall(r"[A-ZÀ-Ú][\wÀ-ú]+", texto)
    tokens = [t for t in tokens if t not in PALAVRAS_INICIAIS and not re.fullmatch(f"{UFS}|{PARTIDOS}|R", t)]
    if not tokens:
        return None
    # agrupa tokens adjacentes no texto original
    melhor, atual = [], []
    for t in tokens:
        if atual and re.search(rf"{re.escape(atual[-1])}\s+(?:d[aeo]s?\s+)?{re.escape(t)}", texto):
            atual.append(t)
        else:
            atual = [t]
        if len(atual) > len(melhor):
            melhor = list(atual)
    return " ".join(melhor)


def _uf(texto: str) -> str | None:
    m = re.search(rf"\b({UFS})\b", texto)
    return m.group(1) if m else None


def _partido(texto: str) -> str | None:
    m = re.search(rf"\b({PARTIDOS})\b", texto, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _contexto(messages: list[BaseMessage]) -> dict[str, Any]:
    """Recupera o ultimo deputado resolvido e a ultima categoria citada na conversa."""
    ctx: dict[str, Any] = {}
    for m in messages:
        if isinstance(m, ToolMessage) and m.name == "buscar_deputado":
            linhas = [ln for ln in str(m.content).splitlines() if ln.startswith("id=")]
            if len(linhas) == 1:
                mm = re.match(r"id=(\d+) \| (.+?) \| (\S+)-(\w+)", linhas[0])
                if mm:
                    ctx.update(id=int(mm.group(1)), nome=mm.group(2), partido=mm.group(3), uf=mm.group(4))
        if isinstance(m, HumanMessage):
            if cat := _categoria(str(m.content)):
                ctx["categoria"] = cat
            ctx["ano"] = _ano(str(m.content))
    return ctx


def _tool_call(nome: str, args: dict, n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": nome, "args": args, "id": f"call_offline_{n}"}])


def _formatar_sql(conteudo: str) -> str:
    """Formata o retorno de consultar_ceap: valores unicos viram R$, listas viram linhas."""
    import ast

    rodape = f"Fonte: CSV CEAP {config.ANO_CEAP} (banco local)."
    if not conteudo.startswith(("1 registros", "2 registros", "3 registros", "4 registros", "5 registros")):
        return f"Resultado do banco local da CEAP: {conteudo[:500]}\n{rodape}"
    try:
        registros = ast.literal_eval(conteudo.split(": ", 1)[1])
    except (ValueError, SyntaxError):
        return f"Resultado do banco local da CEAP: {conteudo[:500]}\n{rodape}"
    if len(registros) == 1 and len(registros[0]) == 1:
        chave, valor = next(iter(registros[0].items()))
        return f"{chave.replace('_', ' ').capitalize()}: {_brl(float(valor))} em {config.ANO_CEAP}. {rodape}"
    linhas = []
    for r in registros:
        valor = next((v for v in r.values() if isinstance(v, (int, float))), None)
        rotulo = " | ".join(str(v) for v in r.values() if not isinstance(v, (int, float)))
        linhas.append(f"- {rotulo}: {_brl(float(valor))}" if valor is not None else f"- {rotulo}")
    return "Resultado do banco local da CEAP:\n" + "\n".join(linhas) + f"\n{rodape}"


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class ChatRoteirizado(BaseChatModel):
    """Dummy deterministico: escolhe ferramentas por regras e formata observacoes.

    Sem ferramentas vinculadas (chains simples) responde em modo texto: JSON heuristico,
    classificacao de rota, resumo ou eco da pergunta.
    """

    com_ferramentas: bool = False

    @property
    def _llm_type(self) -> str:
        return "roteirizado-offline"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        return self.model_copy(update={"com_ferramentas": True})

    def get_num_tokens_from_messages(self, messages: list[BaseMessage], tools: Any = None) -> int:
        return sum(len(str(m.content)) // 4 for m in messages)

    # ---- saida estruturada ------------------------------------------------- #
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        return RunnableLambda(lambda entrada: _estruturar(schema, _ultimo_humano(entrada)))

    # ---- geracao ----------------------------------------------------------- #
    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        resposta = self._decidir(messages)
        return ChatResult(generations=[ChatGeneration(message=resposta)])

    def _decidir(self, messages: list[BaseMessage]) -> AIMessage:
        n = len(messages)
        ultima = messages[-1]
        if not self.com_ferramentas:
            return AIMessage(content=_responder_texto(messages))
        ctx = _contexto(messages)
        if isinstance(ultima, ToolMessage):
            return self._apos_ferramenta(ultima, ctx, n)
        if not isinstance(ultima, HumanMessage):
            return AIMessage(content="Pode repetir a pergunta?")
        texto = str(ultima.content)
        t = texto.lower()

        if m := re.search(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", texto):
            return _tool_call("dados_fornecedor", {"cnpj": m.group(0)}, n)
        if re.search(r"regra|pode pagar|permitid|limite|prazo|vedad|ato da mesa|reembols[aá]vel", t):
            return _tool_call("consultar_regras_cota", {"query": texto}, n)
        if re.search(r"lembr|passad[ao]|anterior|aquel[ea]", t):
            return _tool_call("lembrar_apuracao", {"consulta": texto, "reporter_id": "reporter"}, n)
        if re.search(r"guard|salv|registr|anot", t):
            resumo = next(
                (str(m.content) for m in reversed(messages) if isinstance(m, AIMessage) and m.content), texto
            )
            return _tool_call("gravar_apuracao", {"resumo": resumo[:400], "reporter_id": "reporter"}, n)
        if re.search(r"m[eé]dia|ranking|maiores|top \d|compar", t):
            return self._consulta_sql(texto, ctx, n)
        if nome := _nome_deputado(texto):
            return _tool_call("buscar_deputado", {"nome": nome, "uf": _uf(texto)}, n)
        if ctx.get("id") and (_categoria(texto) or re.search(r"gast|despes|quanto", t)):
            # pergunta de acompanhamento ("e com passagens?") reaproveita o deputado do contexto
            args: dict[str, Any] = {"id_deputado": ctx["id"], "ano": ctx.get("ano", config.ANO_CEAP)}
            if _categoria(texto):
                args["categoria"] = _categoria(texto)
            return _tool_call("listar_despesas", args, n)
        if re.search(r"\b(oi|olá|ola|bom dia|boa tarde|boa noite)\b", t):
            return AIMessage(
                content="Olá! Sou o ApuraCota (modo offline). Pergunte sobre gastos da cota parlamentar."
            )
        return AIMessage(
            content="Preciso do nome do deputado e do periodo (ou uma pergunta sobre regras da cota) para apurar."
        )

    def _consulta_sql(self, texto: str, ctx: dict, n: int) -> AIMessage:
        partido = _partido(texto) or ctx.get("partido")
        cat = _categoria(texto) or ctx.get("categoria")
        ano = _ano(texto) if re.search(r"\b20\d{2}\b", texto) else ctx.get("ano", config.ANO_CEAP)
        filtro_cat = f" AND categoria LIKE '%{cat}%'" if cat else ""
        if re.search(r"fornecedor", texto.lower()) and ctx.get("id"):
            sql = (
                "SELECT fornecedor, cnpj_cpf, ROUND(SUM(valor),2) AS total FROM despesas_ceap "
                f"WHERE id_deputado={ctx['id']} AND ano={ano}{filtro_cat} GROUP BY 1,2 ORDER BY total DESC LIMIT 5"
            )
        elif partido:
            sql = (
                "SELECT ROUND(AVG(t.total),2) AS media_por_deputado FROM (SELECT id_deputado, SUM(valor) AS total "
                f"FROM despesas_ceap WHERE partido='{partido}' AND ano={ano}{filtro_cat} GROUP BY id_deputado) t"
            )
        elif uf := _uf(texto):
            sql = (
                "SELECT nome_parlamentar, partido, ROUND(SUM(valor),2) AS total FROM despesas_ceap "
                f"WHERE uf='{uf}' AND ano={ano}{filtro_cat} GROUP BY 1,2 ORDER BY total DESC LIMIT 5"
            )
        else:
            return AIMessage(
                content="Para comparar preciso saber o partido, a UF ou o deputado de referencia."
            )
        return _tool_call("consultar_ceap", {"query_sql": sql}, n)

    def _apos_ferramenta(self, obs: ToolMessage, ctx: dict, n: int) -> AIMessage:
        nome, conteudo = obs.name, str(obs.content)
        if nome == "buscar_deputado":
            linhas = [ln for ln in conteudo.splitlines() if ln.startswith("id=")]
            if not linhas:
                return AIMessage(content=conteudo)
            if len(linhas) > 1:
                return AIMessage(
                    content="Encontrei mais de um deputado com esse nome. Qual deles?\n" + "\n".join(linhas)
                )
            id_ = int(re.search(r"id=(\d+)", linhas[0]).group(1))
            args: dict[str, Any] = {"id_deputado": id_, "ano": ctx.get("ano", config.ANO_CEAP)}
            if ctx.get("categoria"):
                args["categoria"] = ctx["categoria"]
            return _tool_call("listar_despesas", args, n)
        if nome == "listar_despesas":
            d = json.loads(conteudo)
            quem = ctx.get("nome", f"deputado {d['id_deputado']}")
            cat = f" com {ctx['categoria'].lower()}" if ctx.get("categoria") else ""
            top = "; ".join(f"{k.title()[:40]}: {_brl(v)}" for k, v in list(d["total_por_tipo"].items())[:3])
            return AIMessage(
                content=(
                    f"{quem} gastou {_brl(d['total_valor'])}{cat} em {d['ano']} "
                    f"({d['total_registros']} lancamentos). Maiores categorias: {top}. "
                    f"Fonte: API da Camara, despesas de {d['ano']}."
                )
            )
        if nome == "consultar_ceap":
            return AIMessage(content=_formatar_sql(conteudo))

        if nome == "consultar_regras_cota":
            return AIMessage(
                content="Segundo o Ato da Mesa 43/2009 (trechos recuperados):\n" + conteudo[:800]
            )
        if nome == "calcular_estatisticas":
            return AIMessage(content=f"Resultado do calculo: {conteudo}")
        if nome == "dados_fornecedor":
            return AIMessage(content=f"Dados do fornecedor (Brasil API): {conteudo}")
        return AIMessage(content=conteudo[:600])


# --------------------------------------------------------------------------- #
# Saida estruturada no modo offline
# --------------------------------------------------------------------------- #
def _responder_texto(messages: list[BaseMessage]) -> str:
    """Modo texto (sem ferramentas): heuristicas para as chains dos notebooks."""
    prompt = "\n".join(str(m.content) for m in messages).lower()
    humano = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    if "classifique" in prompt and "categoria" in prompt:
        return _rota(humano)
    if "relev" in prompt and ("alta" in prompt or "baixa" in prompt):
        return (
            "alta"
            if re.search(r"deputad|gast|cota|fornecedor|partido|R\$", humano, re.IGNORECASE)
            else "baixa"
        )
    if "resum" in prompt:
        return "Resumo (offline): " + re.sub(r"\s+", " ", humano)[:300]
    if "json" in prompt:
        return json.dumps(_campos_heuristicos(humano), ensure_ascii=False)
    return "Resposta offline: " + humano[:300]


def _rota(texto: str) -> str:
    t = texto.lower()
    if re.search(r"regra|pode pagar|permitid|limite|prazo|vedad", t):
        return "regra"
    if re.search(r"m[eé]dia|ranking|maiores|compar|top \d", t):
        return "sql"
    if _nome_deputado(texto) or re.search(r"gast|despes", t):
        return "api"
    return "conversa"


def _campos_heuristicos(texto: str) -> dict[str, Any]:
    nome = _nome_deputado(texto)
    rota = _rota(texto)
    intencao = {"regra": "regra_cota", "sql": "comparar", "api": "consultar_gasto", "conversa": "conversa"}[
        rota
    ]
    if intencao == "conversa" and _categoria(texto):
        intencao = "consultar_gasto"
    if re.search(r"fornecedor", texto.lower()):
        intencao = "ranking_fornecedor"
    if re.search(r"resum|guard", texto.lower()):
        intencao = "resumo"
    faltante = None
    if intencao in {"consultar_gasto", "ranking_fornecedor"} and not nome:
        faltante = "deputado"
    tem_ano = bool(re.search(r"\b20\d{2}\b", texto))
    return {
        "intencao": intencao,
        "nome": nome,
        "deputado": nome,
        "partido": _partido(texto),
        "uf": _uf(texto),
        "ano": _ano(texto) if tem_ano else None,
        "mes": _mes(texto),
        "categoria": _categoria(texto),
        "fornecedor": None,
        "periodo": str(_ano(texto)) if tem_ano else None,
        "dado_faltante": faltante,
        "rota": rota,
    }


def _ultimo_humano(entrada: Any) -> str:
    if isinstance(entrada, list):
        return next(
            (str(m.content) for m in reversed(entrada) if isinstance(m, HumanMessage)), _texto_de(entrada)
        )
    if hasattr(entrada, "to_messages"):
        return _ultimo_humano(entrada.to_messages())
    return str(entrada)


def _texto_de(entrada: Any) -> str:
    if isinstance(entrada, str):
        return entrada
    if isinstance(entrada, list):
        return "\n".join(str(getattr(m, "content", m)) for m in entrada)
    if hasattr(entrada, "to_messages"):
        return _texto_de(entrada.to_messages())
    return str(entrada)


def _estruturar(schema: Any, texto: str) -> Any:
    nome = getattr(schema, "__name__", "")
    if nome == "ClaimExtractionList":
        return _extrair_claims(schema, texto)
    if nome == "LogicalCoherenceReport":
        return schema(
            nli_verdict="NEUTRA",
            is_logically_coherent=True,
            detected_fallacies=["Nenhuma"],
            technical_justification="Modo offline: NLI nao executado; considere apenas o fact-checking deterministico.",
        )
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return _instancia_generica(schema, texto)
    return {"resposta_offline": texto[:200]}


def _extrair_claims(schema: Any, texto: str):
    claims = []
    for m in re.finditer(r"R\$ ?([\d.]+,\d{2})", texto):
        valor = float(m.group(1).replace(".", "").replace(",", "."))
        antes = texto[max(0, m.start() - 80) : m.start()].lower()
        depois = texto[m.end() : m.end() + 40].lower()
        if "média" in antes or "media" in antes:
            chave = "media_partido"
        elif "limite" in antes:
            chave = "limite_cota_uf"
        elif _categoria(antes) or _categoria(depois):
            chave = "gasto_categoria"
        elif "gastou" in antes or "total" in antes:
            chave = "gasto_total"
        else:
            chave = "unmapped_metric"
        claims.append(
            {"claim_text": m.group(0), "canonical_key": chave, "claimed_value": valor, "unit": "BRL"}
        )
    for m in re.finditer(r"Art\.? ?(\d+)", texto):
        claims.append(
            {
                "claim_text": m.group(0),
                "canonical_key": "artigo_citado",
                "claimed_value": float(m.group(1)),
                "unit": "artigo",
            }
        )
    return schema(claims=claims)


def _instancia_generica(schema: type[BaseModel], texto: str) -> BaseModel:
    """Preenche um modelo Pydantic qualquer: campos conhecidos por heuristica, o resto com valores neutros."""
    heur = _campos_heuristicos(texto)
    valores: dict[str, Any] = {}
    for nome, campo in schema.model_fields.items():
        tipo = campo.annotation
        origem = getattr(tipo, "__origin__", tipo)
        if nome in heur and (heur[nome] is not None or not campo.is_required()):
            valores[nome] = heur[nome]
            continue
        if not campo.is_required():
            continue
        if origem is Literal:
            valores[nome] = tipo.__args__[0]
        elif tipo is str:
            valores[nome] = texto[:120] if nome in {"texto", "pergunta", "resumo"} else "offline"
        elif tipo is int:
            valores[nome] = 0
        elif tipo is float:
            valores[nome] = 0.0
        elif tipo is bool:
            valores[nome] = False
        elif origem is list:
            valores[nome] = []
        elif isinstance(tipo, type) and issubclass(tipo, BaseModel):
            valores[nome] = _instancia_generica(tipo, texto)
        else:
            valores[nome] = None
    return schema(**valores)
