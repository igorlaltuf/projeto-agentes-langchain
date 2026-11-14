"""Detalhamento anual preservando a identidade e o recorte de uma consulta SQL anterior."""

import ast
import re
import sqlite3
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import config

PREFIXO_ANUAL = "detalhamento_anual_"


def registros_sql(conteudo: str) -> list[dict]:
    if not re.match(r"\d+ registros: ", conteudo):
        return []
    try:
        registros = ast.literal_eval(conteudo.split(": ", 1)[1])
    except (SyntaxError, ValueError):
        return []
    return registros if isinstance(registros, list) else []


def contexto_sql(mensagens) -> dict | None:
    """Obtém sujeito do resultado real e preserva o WHERE original, sem confiar na prosa do modelo."""
    chamadas = {}
    for mensagem in reversed(mensagens):
        if isinstance(mensagem, AIMessage):
            for chamada in mensagem.tool_calls:
                chamadas[chamada["id"]] = chamada
    for mensagem in reversed(mensagens):
        if isinstance(mensagem, ToolMessage) and mensagem.name == "consultar_dados":
            return None  # outro domínio invalida a seleção CEAP anterior
        if not isinstance(mensagem, ToolMessage) or mensagem.name != "consultar_ceap":
            continue
        chamada = chamadas.get(mensagem.tool_call_id)
        if not chamada or mensagem.tool_call_id.startswith(PREFIXO_ANUAL):
            continue
        sql = chamada["args"].get("query_sql", "")
        # Restringe a consultas simples: recortes com joins/subqueries exigem interpretação explícita.
        filtro = re.search(
            r"\bWHERE\s+(.+?)(?=\bGROUP\s+BY|\bORDER\s+BY|\bLIMIT|$)", sql, re.IGNORECASE | re.DOTALL
        )
        if (
            not filtro
            or len(re.findall(r"\bSELECT\b", sql, re.IGNORECASE)) != 1
            or re.search(r"\bJOIN\b", sql, re.IGNORECASE)
        ):
            continue
        linhas = registros_sql(str(mensagem.content))
        if not linhas:
            continue
        primeiro = linhas[0]
        nome = next(
            (
                v
                for k, v in primeiro.items()
                if isinstance(v, str) and re.search(r"nome|deputado|parlamentar", k, re.IGNORECASE)
            ),
            None,
        )
        if not nome:
            continue
        with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
            ids = conn.execute(
                "SELECT DISTINCT id_deputado FROM despesas_ceap WHERE nome_parlamentar=? AND id_deputado IS NOT NULL",
                (nome,),
            ).fetchall()
        if len(ids) != 1:
            continue
        if len(linhas) > 1 and not re.search(r"ORDER\s+BY.+DESC", sql, re.IGNORECASE):
            continue  # não escolhe uma pessoa arbitrariamente de uma lista sem ranking
        categoria = re.search(r"categoria\s+LIKE\s+'([^']+)'", sql, re.IGNORECASE)
        anos = {int(a) for a in re.findall(r"\b20\d{2}\b", sql)}
        for inicio, fim in re.findall(r"BETWEEN\s+(20\d{2})\s+AND\s+(20\d{2})", sql, re.IGNORECASE):
            anos.update(range(int(inicio), int(fim) + 1))
        if not anos:
            continue
        total = next(
            (
                v
                for k, v in primeiro.items()
                if re.search(r"total|gasto|sum", k, re.IGNORECASE) and isinstance(v, (int, float))
            ),
            None,
        )
        return {
            "nome": nome,
            "id": int(ids[0][0]),
            "where": filtro.group(1).strip().rstrip(";"),
            "anos": sorted(anos),
            "categoria": categoria.group(1) if categoria else None,
            "total": total,
        }
    return None


def pedido_anual(mensagens, contexto_anterior=None) -> dict | None:
    pergunta = next((str(m.content) for m in reversed(mensagens) if isinstance(m, HumanMessage)), "")
    if not re.search(
        r"\b(ele|ela|dele|dela)\b|^(?:e\s+)?por ano\b|como.+(?:disse|dises|total)|(?:esse|este|aquele) total",
        pergunta,
        re.IGNORECASE,
    ):
        return None
    if not re.search(r"por ano|anual|ano a ano|como|total|dises", pergunta, re.IGNORECASE):
        return None
    # Não substitui um novo recorte explícito pelo histórico.
    if re.search(r"\b20\d{2}\b|\b(?:passagens|hospedagem|divulgação|telefonia)\b", pergunta, re.IGNORECASE):
        return None
    contexto = contexto_anterior or contexto_sql(mensagens)
    if contexto:
        from src.periods import anos_relativos

        novos_anos = anos_relativos(pergunta)
        if novos_anos is not None and novos_anos != contexto["anos"]:
            return None
    return contexto


def consulta_anual(contexto: dict) -> str:
    return (
        "SELECT ano, ROUND(SUM(valor),2) AS total FROM despesas_ceap "
        f"WHERE ({contexto['where']}) AND id_deputado={contexto['id']} GROUP BY ano ORDER BY ano"
    )


def resposta_anual(contexto: dict, conteudo: str) -> str:
    from src.llm import _brl

    linhas = registros_sql(conteudo)
    if not linhas:
        return (
            "Não foi possível conferir o detalhamento anual. O total anterior não foi substituído. "
            + conteudo
        )
    valores = {int(r["ano"]): Decimal(str(r["total"])) for r in linhas if r.get("total") is not None}
    total = sum(valores.values(), Decimal(0)).quantize(Decimal(".01"))
    categoria = contexto["categoria"].strip("%") if contexto["categoria"] else "todas as categorias"
    categoria = "combustível" if categoria == "COMBUST" else categoria
    resposta = [
        f"**{contexto['nome']} — {categoria}, de {min(contexto['anos'])} a {max(contexto['anos'])}:**",
        "",
    ]
    for ano in contexto["anos"]:
        valor = _brl(float(valores[ano])) if ano in valores else "Nenhum lançamento encontrado neste recorte"
        resposta.append(f"- {ano}: {valor}")
    resposta += ["", f"**Soma dos valores anuais: {_brl(float(total))}.**"]
    if contexto.get("total") is not None:
        confere = abs(total - Decimal(str(contexto["total"]))) <= Decimal(".01")
        resposta.append(
            "A soma confere com o total do ranking anterior."
            if confere
            else "A soma diverge do total retornado no ranking anterior; é necessário revisar a consulta original."
        )
    resposta.append(
        f"Fonte: Cota para o Exercício da Atividade Parlamentar (CEAP), "
        f"{min(contexto['anos'])} até {max(contexto['anos'])}."
    )
    if int(config.HOJE[:4]) in contexto["anos"]:
        resposta.append(f"O ano de {config.HOJE[:4]} é parcial, conforme os dados disponíveis.")
    return "\n".join(resposta)
