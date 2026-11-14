"""Investigação de fornecedores com identidade e período explícitos em cada cálculo."""

from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import config
from src.identity import cnpj_valido, documento_posicional, nome_normalizado

ACOES = Literal[
    "ranking_fornecedores",
    "evolucao_anual",
    "categorias",
    "concentracao",
    "outros_deputados",
    "participacao_ceap",
]
PREFIXO = "investigacao_fornecedores_"


class InvestigacaoArgs(BaseModel):
    acao: ACOES
    anos: list[int] = Field(
        min_length=1, description="Anos explícitos do recorte; incluir todos os anos do intervalo."
    )
    id_deputado: int | None = None
    nome_deputado: str | None = None
    cnpjs: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Documentos completos dos fornecedores selecionados; nunca inferir da prosa.",
    )
    limite: int = Field(default=10, ge=1, le=20)
    top_n: int = Field(default=3, ge=1, le=20)
    categoria: str | None = None


def _centavos(valor):
    return int((Decimal(str(valor or 0)) * 100).quantize(Decimal(1)))


def _pct(a, b):
    return float((Decimal(a) * 100 / Decimal(b)).quantize(Decimal(".01"))) if b else None


@tool(args_schema=InvestigacaoArgs)
def investigar_fornecedores(
    acao: str,
    anos: list[int],
    id_deputado: int | None = None,
    nome_deputado: str | None = None,
    cnpjs: list[str] | None = None,
    limite: int = 10,
    top_n: int = 3,
    categoria: str | None = None,
) -> str:
    """Investiga empresas fornecedoras na CEAP: ranking, evolução, categorias, concentração e pagamentos de outros deputados. Usa CNPJ exato; participação é na CEAP, nunca no faturamento da empresa."""
    from src.data import anos_disponiveis, buscar_deputado_local

    def retorno(d):
        return json.dumps(d, ensure_ascii=False)

    anos = sorted(set(anos))
    cnpjs = [documento_posicional(d) for d in (cnpjs or [])]
    if not set(anos).issubset(anos_disponiveis()):
        return retorno(
            {
                "status": "INDISPONIVEL",
                "erro": "Faltam anos da CEAP para esse recorte; ausência de carga não é gasto zero.",
            }
        )
    if any(not cnpj_valido(d) for d in cnpjs):
        return retorno(
            {
                "status": "INVALIDO",
                "erro": "Selecione CNPJ completo válido; nome ou documento mascarado não confirma identidade.",
            }
        )
    cnpjs = list(dict.fromkeys(cnpjs))
    if cnpjs and acao in {"ranking_fornecedores", "concentracao"}:
        return retorno(
            {
                "status": "ESCLARECER",
                "erro": "Esse ranking ou concentração usa os maiores fornecedores do recorte completo. Para empresas específicas, selecione evolução ou categorias; não vou ignorar os CNPJs informados.",
            }
        )
    if id_deputado is None or nome_deputado:
        if not nome_deputado:
            return retorno({"status": "ESCLARECER", "erro": "Qual deputado deseja investigar?"})
        encontrados = buscar_deputado_local(nome_deputado, None, None)
        ids = re.findall(r"^id=(\d+) \| ([^|]+)", encontrados, re.MULTILINE)
        if len(ids) != 1:
            return retorno(
                {
                    "status": "ESCLARECER",
                    "erro": "Não há correspondência única para o deputado. Informe nome e/ou UF.",
                    "opcoes": encontrados,
                }
            )
        if id_deputado is not None and id_deputado != int(ids[0][0]):
            return retorno(
                {
                    "status": "ESCLARECER",
                    "erro": "O ID informado não corresponde ao deputado selecionado; confirme a identidade antes do cruzamento.",
                }
            )
        id_deputado = int(ids[0][0])
    total_outros = None
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as c:
        c.execute("PRAGMA query_only=ON")
        c.create_function("cnpj_valido", 1, lambda d: int(cnpj_valido(str(d or ""))), deterministic=True)
        nome = c.execute(
            "SELECT nome_parlamentar FROM despesas_ceap WHERE id_deputado=? ORDER BY ano DESC LIMIT 1",
            (id_deputado,),
        ).fetchone()
        if not nome:
            return retorno({"status": "ESCLARECER", "erro": "Deputado não encontrado na CEAP."})
        filtro = f"id_deputado=? AND ano IN ({','.join('?' for _ in anos)})"
        params = [id_deputado, *anos]
        if categoria:
            from src.tools import normalizar_categoria

            categoria = normalizar_categoria(categoria)
            filtro += " AND categoria LIKE ?"
            params.append("%" + categoria + "%")
        ranking = [
            {"cnpj": d, "fornecedor": n, "valor": float(v), "valor_centavos": _centavos(v), "registros": q}
            for d, n, v, q in c.execute(
                f"SELECT cnpj_cpf,MAX(fornecedor),ROUND(SUM(valor),2),COUNT(*) FROM despesas_ceap WHERE {filtro} AND cnpj_valido(cnpj_cpf)=1 GROUP BY cnpj_cpf ORDER BY SUM(valor) DESC,cnpj_cpf LIMIT ?",
                [*params, limite],
            )
        ]
        selecionados = []
        dados = []
        refs = {}
        if acao == "ranking_fornecedores":
            dados = ranking
        elif acao == "concentracao":
            top = [
                {"cnpj": d, "fornecedor": n, "valor": float(v), "valor_centavos": _centavos(v)}
                for d, n, v in c.execute(
                    f"SELECT cnpj_cpf,MAX(fornecedor),ROUND(SUM(valor),2) FROM despesas_ceap WHERE {filtro} AND cnpj_valido(cnpj_cpf)=1 GROUP BY cnpj_cpf ORDER BY SUM(valor) DESC,cnpj_cpf LIMIT ?",
                    [*params, top_n],
                )
            ]
            numerador = sum(r["valor_centavos"] for r in top)
            denominador = _centavos(
                c.execute(f"SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE {filtro}", params).fetchone()[
                    0
                ]
            )
            dados = [
                {
                    "top_n": top_n,
                    "valor_top": numerador / 100,
                    "gasto_total_deputado": denominador / 100,
                    "percentual": _pct(numerador, denominador),
                }
            ]
            selecionados = top
            refs = {
                "numerador_centavos": numerador,
                "denominador_centavos": denominador,
                "universo_denominador": "Todas as despesas CEAP do deputado no mesmo período, incluindo pessoas físicas e estornos.",
            }
        else:
            if not cnpjs:
                return retorno(
                    {
                        "status": "ESCLARECER",
                        "erro": "Selecione um ou mais CNPJs do ranking antes desse cruzamento.",
                    }
                )
            for doc in cnpjs:
                n = c.execute(
                    f"SELECT MAX(fornecedor) FROM despesas_ceap WHERE {filtro} AND cnpj_cpf=?", params + [doc]
                ).fetchone()[0]
                if not n:
                    return retorno(
                        {
                            "status": "ESCLARECER",
                            "erro": "O CNPJ selecionado não aparece nas despesas desse deputado no período. Não substitua por outra empresa.",
                        }
                    )
                selecionados.append({"cnpj": doc, "fornecedor": n})
            docs_sql = ",".join("?" for _ in cnpjs)
            if acao == "evolucao_anual":
                valores = {
                    (d, a): (v, q)
                    for d, a, v, q in c.execute(
                        f"SELECT cnpj_cpf,ano,ROUND(SUM(valor),2),COUNT(*) FROM despesas_ceap WHERE {filtro} AND cnpj_cpf IN ({docs_sql}) GROUP BY cnpj_cpf,ano",
                        params + cnpjs,
                    )
                }
                for s in selecionados:
                    for a in anos:
                        v, q = valores.get((s["cnpj"], a), (0, 0))
                        dados.append({**s, "ano": a, "valor": float(v), "registros": q})
            elif acao == "categorias":
                dados = [
                    {"cnpj": d, "categoria": cat, "valor": float(v), "registros": q}
                    for d, cat, v, q in c.execute(
                        f"SELECT cnpj_cpf,categoria,ROUND(SUM(valor),2),COUNT(*) FROM despesas_ceap WHERE {filtro} AND cnpj_cpf IN ({docs_sql}) GROUP BY cnpj_cpf,categoria ORDER BY cnpj_cpf,SUM(valor) DESC",
                        params + cnpjs,
                    )
                ]
            elif acao in {"outros_deputados", "participacao_ceap"}:
                if len(cnpjs) != 1:
                    return retorno(
                        {"status": "ESCLARECER", "erro": "Escolha um único CNPJ para comparar os deputados."}
                    )
                doc = cnpjs[0]
                filtro_categoria = " AND categoria LIKE ?" if categoria else ""
                filtro_params = ["%" + categoria + "%"] if categoria else []
                if acao == "outros_deputados":
                    total_outros = c.execute(
                        f"SELECT COUNT(DISTINCT id_deputado) FROM despesas_ceap WHERE cnpj_cpf=? AND ano IN ({','.join('?' for _ in anos)}) AND id_deputado<>?{filtro_categoria}",
                        [doc, *anos, id_deputado, *filtro_params],
                    ).fetchone()[0]
                    dados = [
                        {"id_deputado": i, "deputado": n, "valor": float(v)}
                        for i, n, v in c.execute(
                            f"SELECT id_deputado,MAX(nome_parlamentar),ROUND(SUM(valor),2) FROM despesas_ceap WHERE cnpj_cpf=? AND ano IN ({','.join('?' for _ in anos)}) AND id_deputado<>?{filtro_categoria} GROUP BY id_deputado ORDER BY SUM(valor) DESC LIMIT ?",
                            [doc, *anos, id_deputado, *filtro_params, limite],
                        )
                    ]
                else:
                    numerador = _centavos(
                        c.execute(
                            f"SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE {filtro} AND cnpj_cpf=?",
                            params + [doc],
                        ).fetchone()[0]
                    )
                    denominador = _centavos(
                        c.execute(
                            f"SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE cnpj_cpf=? AND ano IN ({','.join('?' for _ in anos)}){filtro_categoria}",
                            [doc, *anos, *filtro_params],
                        ).fetchone()[0]
                    )
                    dados = [
                        {
                            "valor_deputado": numerador / 100,
                            "valor_todos_deputados_ceap": denominador / 100,
                            "percentual_na_ceap": _pct(numerador, denominador),
                        }
                    ]
                    refs = {
                        "numerador_centavos": numerador,
                        "denominador_centavos": denominador,
                        "universo_denominador": "Despesas CEAP vinculadas ao mesmo CNPJ, de todos os deputados, no mesmo período e categoria quando filtrada. Não é faturamento total.",
                    }
    return retorno(
        {
            "status": "CONSULTADO",
            "acao": acao,
            "id_deputado": id_deputado,
            "deputado": nome[0],
            "anos": anos,
            "limite": limite,
            "ranking": ranking,
            "selecionados": selecionados,
            "dados": dados[:200],
            "completo": len(dados) <= 200,
            "total_outros_deputados": total_outros,
            "lista_limitada": total_outros is not None and total_outros > len(dados),
            "referencias": refs,
            "categoria": categoria,
            "fonte": "Cota para o Exercício da Atividade Parlamentar (CEAP)",
            "ano_corrente_parcial": int(config.HOJE[:4]) in anos,
            "aviso": "Ranking de empresas com CNPJ completo válido. Os valores são líquidos, incluindo estornos. Não representa faturamento, prova de serviço ou irregularidade.",
        }
    )


def pedido_investigacao(pergunta, contexto):
    """Roteia intenções recorrentes; solicita campos ausentes em vez de presumir recorte."""
    t = nome_normalizado(pergunta).lower()
    if (
        contexto
        and contexto.get("pendente")
        and re.fullmatch(r"(?:20\d{2}|[0-9]+|de|em|entre|a|ate|e|nos|ultimos|anos|completos|[ .?])+", t)
    ):
        pergunta = contexto["pendente"]["pergunta"] + " " + pergunta
        t = nome_normalizado(pergunta).lower()
    if re.search(
        r"prov[ae].*(corrup|favorec|fraud)|(?:corrup|fraud).*(prov|certeza)|prova de favorecimento", t
    ):
        return {
            "resposta": "Esses cruzamentos, por si só, não provam favorecimento, fraude ou corrupção. Identificam relações e valores para investigação, que exigem documentos e análise do contexto. Não é possível concluir irregularidade apenas com esses dados."
        }
    if re.search(r"sancionad|inidone|punid", t) and re.search(
        r"na epoca|na data dos|quando recebeu|quando houve|em 20\d{2}", t
    ):
        return {
            "resposta": "As bases CEIS/CNEP consultadas são versões datadas e não reconstroem um histórico completo. Não posso certificar que a empresa estava sancionada na época de cada pagamento apenas com esses arquivos. Posso apresentar os registros, categorias, órgãos e datas informados, sem presumir vigência retroativa."
        }
    if "faturamento" in t:
        return {
            "resposta": "A ferramenta não tem o faturamento total da empresa. A participação calculada se refere apenas às despesas da CEAP vinculadas ao mesmo CNPJ e período; não deve ser interpretada como participação no faturamento."
        }
    if re.search(r"regras|permitid|pode pagar|limite.*cota|ato da mesa", t):
        return None
    acao = None
    if re.search(r"percent|concentra", t) and re.search(r"tres|3|maiores", t):
        acao = "concentracao"
    elif re.search(r"categor|tipo de despesa", t) and re.search(
        r"fornecedor|primeir|esses|essas|eles|elas|dess|empres", t
    ):
        acao = "categorias"
    elif re.search(r"por ano|anual|ano a ano", t) and re.search(
        r"fornecedor|primeir|cada um|cada uma|empres", t
    ):
        acao = "evolucao_anual"
    elif re.search(r"outros deputado|outros parlamentar|quais deputado|demais deputado", t) and not re.search(
        r"percent|particip|quanto das", t
    ):
        acao = "outros_deputados"
    elif re.search(r"percent|particip|quanto das|comparado aos demais", t) and re.search(
        r"cnpj|fornecedor|empresa|tabata|deputad", t
    ):
        acao = "participacao_ceap"
    elif re.search(r"fornecedor|empresa.*mais recebeu|maiores benefici", t) and not re.search(
        r"cadastro|socio|ceis|cnep|sanc|quem e|regras", t
    ):
        acao = "ranking_fornecedores"
    if not acao and contexto and re.search(r"ranking", t):
        acao = "ranking_fornecedores"
    if not acao and contexto and re.fullmatch(r"(?:agora|somente|apenas|so|e em)[a-z ]*20\d{2}[ .?]*", t):
        if contexto.get("ultima_ferramenta") in {"consultar_sancoes", "dados_fornecedor"}:
            return {
                "resposta": "Deseja alterar o período dos gastos? Cadastros e sanções têm suas próprias datas de referência e não formam histórico completo."
            }
        acao = contexto.get("acao")
    if not acao:
        return None
    quadro = dict(contexto or {})
    if re.search(r"todas as categorias|sem filtro de categoria|todas as despesas", t):
        quadro["categoria"] = None
    from src.periods import anos_relativos

    anos = anos_relativos(pergunta)
    if not anos:
        anos = sorted({int(a) for a in re.findall(r"\b20\d{2}\b", pergunta)})
        if len(anos) == 2 and re.search(r"entre|ate| a ", t):
            anos = list(range(anos[0], anos[-1] + 1))
    anos = anos or quadro.get("anos")
    if not anos:
        return {
            "resposta": "Qual período deseja investigar? Informe o ano ou intervalo para manter os valores comparáveis.",
            "pendente": {"pergunta": pergunta},
        }
    from src.llm import _nome_deputado

    nome = _nome_deputado(pergunta)
    if quadro.get("id_deputado"):
        if not nome or not re.search(
            r"\b(?:da|do|deputada|deputado)\s+" + re.escape(nome_normalizado(nome).lower()) + r"\b", t
        ):
            nome = None
        else:
            quadro["id_deputado"] = None
    if not quadro.get("id_deputado") and not nome:
        return {"resposta": "Qual deputado deseja investigar nesse período?"}
    quantidade = 3
    numeros = {"dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "dez": 10}
    if m := re.search(
        r"\b(\d+|dois|duas|tres|quatro|cinco|dez)\s+(?:maiores|primeiros|primeiras|fornecedores)", t
    ):
        quantidade = numeros[m.group(1)] if m.group(1) in numeros else int(m.group(1))
    if not 1 <= quantidade <= 20:
        return {"resposta": "Selecione entre 1 e 20 empresas por detalhamento."}
    ranking = quadro.get("ranking", [])
    docs = []
    explicitos = re.findall(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", pergunta)
    if explicitos:
        docs = list(dict.fromkeys(documento_posicional(d) for d in explicitos))
    elif acao not in {"ranking_fornecedores", "concentracao"}:
        if re.search(r"dois|duas|tres|quatro|cinco|dez|\b[2-9]\b|primeiros|primeiras", t):
            docs = [r["cnpj"] for r in ranking[:quantidade]]
        elif re.search(r"primeiro|primeira", t) and ranking:
            docs = [ranking[0]["cnpj"]]
        elif quadro.get("selecionados") and len(quadro["selecionados"]) == 1:
            docs = [quadro["selecionados"][0]["cnpj"]]
        elif len(ranking) == 1:
            docs = [ranking[0]["cnpj"]]
        else:
            return {
                "resposta": "Qual fornecedor ou CNPJ deseja comparar? Indique o primeiro ou selecione uma empresa do ranking."
            }
    if acao in {"outros_deputados", "participacao_ceap"} and len(docs) != 1:
        return {"resposta": "Escolha um único fornecedor ou CNPJ para comparar os deputados."}
    limite = 10
    if re.search(r"cinco|\b5\b", t):
        limite = 5
    if re.search(r"dez|\b10\b", t):
        limite = 10
    if re.search(r"empresa.*mais recebeu|maior fornecedor", t):
        limite = 1
    return {
        "args": {
            "acao": acao,
            "id_deputado": quadro.get("id_deputado"),
            "nome_deputado": nome,
            "anos": anos,
            "cnpjs": docs,
            "limite": limite,
            "top_n": quantidade,
            "categoria": quadro.get("categoria"),
        }
    }


def apresentar_investigacao(conteudo):
    from src.llm import _brl

    try:
        d = json.loads(conteudo)
    except ValueError:
        return "Não foi possível interpretar a consulta; não publiquei valores."
    if d.get("status") != "CONSULTADO":
        return d.get("erro", "Consulta indisponível.") + ("\n" + d["opcoes"] if d.get("opcoes") else "")
    if not d.get("completo"):
        return "O detalhamento excede o limite da resposta. Selecione menos fornecedores para não apresentar um resultado parcial."
    linhas = [f"**{d['deputado']} — {min(d['anos'])} até {max(d['anos'])}**"]
    acao = d["acao"]
    for r in d["dados"]:
        if acao == "ranking_fornecedores":
            linhas.append(f"- {r['fornecedor']} — CNPJ {r['cnpj']}: {_brl(r['valor'])}")
        elif acao == "evolucao_anual":
            linhas.append(
                f"- {r['fornecedor']} — CNPJ {r['cnpj']}, {r['ano']}: {_brl(r['valor'])}"
                if r["registros"]
                else f"- {r['fornecedor']}, {r['ano']}: nenhum lançamento nesse recorte."
            )
        elif acao == "categorias":
            linhas.append(f"- CNPJ {r['cnpj']} — {r['categoria']}: {_brl(r['valor'])}")
        elif acao == "concentracao":
            linhas.append(
                f"Os {r['top_n']} maiores fornecedores somam {_brl(r['valor_top'])} de {_brl(r['gasto_total_deputado'])} em despesas do deputado: {format(r['percentual'], '.2f').replace('.', ',')}%."
                if r["percentual"] is not None
                else "Não foi possível calcular o percentual: denominador zero."
            )
        elif acao == "outros_deputados":
            linhas.append(f"- {r['deputado']}: {_brl(r['valor'])}")
        elif acao == "participacao_ceap":
            linhas.append(
                f"Do mesmo CNPJ, {_brl(r['valor_deputado'])} estão vinculados ao deputado, de {_brl(r['valor_todos_deputados_ceap'])} para todos os deputados: {format(r['percentual_na_ceap'], '.2f').replace('.', ',')}% na CEAP. Não é participação no faturamento."
                if r["percentual_na_ceap"] is not None
                else "Não foi possível calcular: denominador zero."
            )
    if d.get("lista_limitada"):
        linhas.append(
            f"Exibindo os {len(d['dados'])} maiores entre {d['total_outros_deputados']} outros deputados. A lista é limitada; os percentuais globais usam todos os registros."
        )
    if not d["dados"]:
        linhas.append(
            "Nenhum lançamento encontrado nesse cruzamento e período; a conclusão é limitada à CEAP carregada."
        )
    if d.get("categoria"):
        linhas.append(f"Categoria preservada: {d['categoria']}.")
    if d["selecionados"]:
        linhas.append(
            "Empresas selecionadas: "
            + "; ".join(f"{s['fornecedor']} — CNPJ {s['cnpj']}" for s in d["selecionados"])
        )
    linhas.append("Recorte de empresas com CNPJ completo válido; valores líquidos incluem estornos.")
    linhas.append(f"Fonte: {d['fonte']}, {min(d['anos'])} até {max(d['anos'])}.")
    if d["ano_corrente_parcial"]:
        linhas.append(f"O ano de {config.HOJE[:4]} é parcial.")
    return "\n\n".join(linhas)


def contexto_investigacao(mensagens, anterior=None):
    from langchain_core.messages import AIMessage, ToolMessage

    from src.followups import contexto_sql, registros_sql

    quadro = dict(anterior or {})
    chamadas = {t["id"]: t for m in mensagens if isinstance(m, AIMessage) for t in m.tool_calls}
    for indice, m in enumerate(mensagens):
        if isinstance(m, AIMessage) and m.additional_kwargs.get("investigacao_pendente"):
            quadro["pendente"] = m.additional_kwargs["investigacao_pendente"]
        if not isinstance(m, ToolMessage):
            continue
        chamada = chamadas.get(m.tool_call_id, {}).get("args", {})
        quadro["ultima_ferramenta"] = m.name
        if m.name == "investigar_fornecedores":
            try:
                d = json.loads(str(m.content))
            except ValueError:
                continue
            if d.get("status") != "CONSULTADO" and chamada.get("nome_deputado"):
                quadro = {"anos": chamada.get("anos", []), "identidade_pendente": True}
            if d.get("status") == "CONSULTADO":
                quadro = {
                    k: d[k]
                    for k in [
                        "id_deputado",
                        "deputado",
                        "anos",
                        "ranking",
                        "selecionados",
                        "acao",
                        "categoria",
                    ]
                    if k in d
                }
        elif m.name == "buscar_deputado":
            ids = re.findall(r"^id=(\d+) \| ([^|]+)", str(m.content), re.MULTILINE)
            if len(ids) != 1:
                quadro = {"anos": quadro.get("anos", []), "identidade_pendente": True}
            elif int(ids[0][0]) != quadro.get("id_deputado"):
                quadro = {
                    "id_deputado": int(ids[0][0]),
                    "deputado": ids[0][1].strip(),
                    "anos": quadro.get("anos", []),
                    "categoria": quadro.get("categoria"),
                }
        elif m.name in {"dados_fornecedor", "consultar_sancoes"}:
            try:
                d = json.loads(str(m.content))
            except ValueError:
                continue
            if cnpj_valido(d.get("cnpj", "")):
                doc = d["cnpj"]
                quadro["selecionados"] = [
                    {
                        "cnpj": doc,
                        "fornecedor": d.get("razao_social")
                        or next(
                            (r["fornecedor"] for r in quadro.get("ranking", []) if r["cnpj"] == doc),
                            "Empresa selecionada",
                        ),
                    }
                ]
        elif m.name == "listar_despesas":
            try:
                d = json.loads(str(m.content))
            except ValueError:
                continue
            if d.get("id_deputado"):
                quadro = {
                    "id_deputado": d["id_deputado"],
                    "anos": [d["ano"]],
                    "categoria": chamada.get("categoria"),
                }
        elif m.name == "consultar_ceap":
            rows = registros_sql(str(m.content))
            if not rows:
                continue
            sql = chamada.get("query_sql", "")
            # O argumento anos é opcional; o filtro SQL executado é a referência.
            from src.periods import anos_do_sql

            anos = anos_do_sql(sql)
            ident = rows[0].get("id_deputado")
            if not ident:
                filtro_id = re.search(r"\bid_deputado\s*=\s*(\d+)", sql, re.IGNORECASE)
                if filtro_id:
                    ident = int(filtro_id.group(1))
            # Rankings podem retornar apenas nome e total. Resolve o nome real
            # e único no banco, sem usar a resposta textual do modelo.
            if not ident:
                contexto = contexto_sql(mensagens[: indice + 1])
                if contexto:
                    ident = contexto["id"]
            if ident and anos:
                quadro = {
                    "id_deputado": int(ident),
                    "anos": anos,
                    "categoria": None,
                }
                if cat := re.search(r"categoria\s+LIKE\s+'%([^%']+)%'", sql, re.IGNORECASE):
                    quadro["categoria"] = cat.group(1)
    if mensagens:
        ultimo = next((m for m in reversed(mensagens) if isinstance(m, ToolMessage)), None)
        if ultimo:
            quadro["ultima_ferramenta"] = ultimo.name
    return quadro


def conferir_resposta_estruturada(texto, passos):
    """Reexecuta a consulta e confere o renderizador fixo; não simula um julgamento NLI."""
    chamadas = [
        p
        for p in passos
        if p.get("tipo") == "decisao"
        and p.get("ferramenta") == "investigar_fornecedores"
        and p.get("id", "").startswith(PREFIXO)
    ]
    retornos = [
        p
        for p in passos
        if p.get("tipo") == "retorno"
        and p.get("ferramenta") == "investigar_fornecedores"
        and p.get("tool_call_id", "").startswith(PREFIXO)
    ]
    if not chamadas or not retornos:
        return None
    try:
        original = json.loads(retornos[-1]["conteudo"])
        if original.get("status") != "CONSULTADO" or not original.get("completo"):
            return None
        novo = json.loads(investigar_fornecedores.invoke(chamadas[-1]["args"]))
        campos = [
            "status",
            "acao",
            "id_deputado",
            "anos",
            "categoria",
            "dados",
            "selecionados",
            "referencias",
            "completo",
            "lista_limitada",
            "total_outros_deputados",
        ]
        confere = (
            novo.get("status") == "CONSULTADO"
            and all(original.get(k) == novo.get(k) for k in campos)
            and texto == apresentar_investigacao(json.dumps(novo, ensure_ascii=False))
        )
    except (ValueError, sqlite3.Error):
        return {"erro": "Não foi possível reexecutar a investigação para checagem.", "status": "INDISPONIVEL"}
    return {
        "metodo": "reexecucao_estruturada",
        "fact_check": [
            {
                "claim": "Identidade, período, documentos e valores da resposta estruturada",
                "chave": "investigacao_fornecedores",
                "alegado": "Resposta apresentada",
                "oficial": "Consulta CEAP reexecutada e apresentação de referência",
                "status": "CONFIRMADO" if confere else "NAO VERIFICADO",
            }
        ],
        "nli": {
            "nli_verdict": "NEUTRA",
            "is_logically_coherent": confere,
            "detected_fallacies": ["Nenhuma"],
            "technical_justification": "Checagem determinística: consulta reexecutada e resposta comparada com a apresentação dos dados. Não é auditoria independente nem julgamento NLI por modelo."
            if confere
            else "A resposta ou os dados diferem da reexecução; não foram confirmados. Não se presume contradição sem análise.",
        },
        "truth": {
            "fonte": novo.get("fonte"),
            "anos": novo.get("anos"),
            "id_deputado": novo.get("id_deputado"),
            "dados": novo.get("dados"),
            "referencias": novo.get("referencias"),
        },
    }
