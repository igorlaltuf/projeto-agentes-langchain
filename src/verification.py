"""Camada de verificação (fase 5): extração de claims, fact-checking determinístico e NLI.

Roda dentro do agente como último passo antes da resposta final.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from src import config, llm, tools


class ExtractedClaim(BaseModel):
    claim_text: str = Field(description="Trecho exato da afirmacao numerica na resposta.")
    canonical_key: Literal[
        "gasto_total",
        "gasto_categoria",
        "media_partido",
        "media_uf",
        "valor_fornecedor",
        "variacao_pct",
        "limite_cota_uf",
        "artigo_citado",
        "unmapped_metric",
        "emenda_paga",
        "emenda_empenhado",
        "emenda_liquidado",
        "patrimonio_declarado",
        "receita_eleitoral",
        "despesa_eleitoral",
    ] = Field(description="Chave canonica da metrica afirmada.")
    claimed_value: float | None = Field(default=None, description="Valor numerico afirmado, se houver.")
    unit: str = Field(description="Unidade: BRL, pct, artigo ou texto.")
    contexto: str | None = Field(
        default=None, description="Deputado/partido/periodo/categoria a que se refere."
    )


class ClaimExtractionList(BaseModel):
    claims: list[ExtractedClaim]


class LogicalCoherenceReport(BaseModel):
    nli_verdict: Literal["APOIA", "REFUTA", "NEUTRA"]
    is_logically_coherent: bool
    detected_fallacies: list[
        Literal[
            "Erro de Calculo",
            "Contradicao Premissa-Conclusao",
            "Violacao de Escopo",
            "Periodo Trocado",
            "Nenhuma",
        ]
    ]
    technical_justification: str = Field(description="Justificativa em portugues do Brasil.")


def fonte_autoritativa(
    consultas_sql: dict[str, str], tolerancia_brl: float = 0.01
) -> dict[str, dict[str, Any]]:
    """Recalcula no SQLite os valores oficiais das chaves canonicas.

    consultas_sql: {chave_canonica: "SELECT ... AS valor ..."} montadas pelo agente a partir do contexto.
    """
    truth: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        for chave, sql in consultas_sql.items():
            ref = {"value": None, "tolerance": tolerancia_brl, "metric": chave.split("#", 1)[0]}
            try:
                cursor = conn.execute(sql)
                colunas = [c[0] for c in cursor.description]
                registros = []
                for linha in cursor.fetchall():
                    indices = [
                        i
                        for i, nome in enumerate(colunas)
                        if re.search(r"total|media|média|valor|gasto|sum\s*\(|avg\s*\(", nome, re.IGNORECASE)
                        and isinstance(linha[i], (int, float))
                    ]
                    if not indices and len(colunas) == 1 and isinstance(linha[0], (int, float)):
                        indices = [0]
                    if len(indices) != 1:
                        raise ValueError("Coluna numérica da métrica ausente ou ambígua")
                    indice = indices[0]
                    registros.append(
                        {
                            "value": round(float(linha[indice]), 2),
                            "context": {
                                nome: linha[i]
                                for i, nome in enumerate(colunas)
                                if i != indice
                                and nome
                                in {
                                    "nome_parlamentar",
                                    "nome",
                                    "ano",
                                    "fornecedor",
                                    "partido",
                                    "uf",
                                    "categoria",
                                }
                            },
                            "tolerance": tolerancia_brl,
                        }
                    )
                if len(registros) > 1 and re.search(r"GROUP\s+BY\s+ano\b", sql, re.IGNORECASE):
                    from decimal import Decimal

                    registros.append(
                        {
                            "value": float(sum((Decimal(str(r["value"])) for r in registros), Decimal(0))),
                            "context": {"escopo": "soma dos valores anuais"},
                            "tolerance": tolerancia_brl,
                        }
                    )
                ref["records"] = registros
                if len(registros) == 1:
                    ref["value"] = registros[0]["value"]
            except Exception as e:  # noqa: BLE001
                ref["erro"] = str(e)
            truth[chave] = ref
    return truth


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def _referencia_claim(claim: ExtractedClaim, truth: dict) -> dict | None:
    candidatos = []
    texto = f" {_normalizar(claim.contexto or '')} {_normalizar(claim.claim_text)} "
    for chave, ref in truth.items():
        if ref.get("metric", chave.split("#", 1)[0]) != claim.canonical_key or ref.get("erro"):
            continue
        for registro in ref.get("records", [ref]):
            contexto = registro.get("context", {})
            if contexto:
                rotulos = [_normalizar(str(v)) for v in contexto.values() if v is not None]
                if not rotulos or not all(f" {rotulo} " in texto for rotulo in rotulos):
                    continue
            candidatos.append(registro)
    return candidatos[0] if len(candidatos) == 1 else None


def checar_claims(claims: list[ExtractedClaim], truth: dict[str, dict[str, Any]]) -> list[dict]:
    """Fact-checking deterministico: compara cada claim com a fonte autoritativa dentro da tolerancia."""
    resultado = []
    for c in claims:
        ref = _referencia_claim(c, truth)
        if ref is None or ref.get("value") is None or c.claimed_value is None:
            status = "NAO VERIFICADO"
        else:
            delta = abs(c.claimed_value - ref["value"])
            status = "CONFIRMADO" if delta <= ref["tolerance"] else "CONTRADITORIO"
        resultado.append(
            {
                "claim": c.claim_text,
                "chave": c.canonical_key,
                "alegado": c.claimed_value,
                "oficial": (ref or {}).get("value"),
                "status": status,
            }
        )
    return resultado


def consultas_da_trajetoria(trajetoria: list[dict], ano: int = config.ANO_CEAP) -> dict[str, str]:
    """Monta as consultas SQL da fonte autoritativa a partir das ferramentas que o agente usou.

    - listar_despesas(id, ano[, categoria]) -> gasto_total ou gasto_categoria recalculados no CSV local
    - consultar_ceap(SELECT AVG ...)        -> media_partido / media_uf reexecutadas
    """
    consultas: dict[str, str] = {}

    def adicionar(chave: str, sql: str) -> None:
        identificador = chave if chave not in consultas else f"{chave}#{len(consultas)}"
        consultas[identificador] = sql

    for passo in trajetoria:
        if passo.get("tipo") != "decisao":
            continue
        args = passo.get("args", {})
        if passo["ferramenta"] == "listar_despesas" and args.get("ano") is not None:
            radical = tools.normalizar_categoria(args.get("categoria"))
            filtro = f" AND categoria LIKE '%{radical}%'" if radical else ""
            chave = "gasto_categoria" if args.get("categoria") else "gasto_total"
            mes = f" AND mes={args['mes']}" if args.get("mes") else ""
            adicionar(
                chave,
                (
                    f"SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE id_deputado={int(args['id_deputado'])} "
                    f"AND ano={int(args['ano'])}{mes}{filtro}"
                ),
            )
        elif passo["ferramenta"] == "consultar_ceap":
            sql = args.get("query_sql", "")
            if re.search(r"\bAVG\s*\(", sql, re.IGNORECASE):
                chave = "media_uf" if re.search(r"\buf\b", sql, re.IGNORECASE) else "media_partido"
                adicionar(chave, sql)
            elif re.search(r"\bSUM\s*\(", sql, re.IGNORECASE):
                chave = (
                    "gasto_categoria"
                    if re.search(r"\bcategoria\s+(?:LIKE|=)|GROUP\s+BY\s+categoria", sql, re.IGNORECASE)
                    else "gasto_total"
                )
                adicionar(chave, sql)
    return consultas


def verificar_resposta(
    texto_resposta: str, truth: dict[str, dict[str, Any]], modelo: BaseChatModel | None = None
) -> dict:
    """Tres etapas: extracao estruturada (LLM) -> fact-checking deterministico -> NLI (LLM)."""
    modelo = modelo or llm.chat_model("juiz")

    extrator = modelo.with_structured_output(ClaimExtractionList)
    claims = extrator.invoke(
        "Extraia todas as afirmacoes numericas e factuais do texto e mapeie para as chaves canonicas. "
        "Preencha contexto com os identificadores de cada afirmação: partido/deputado, "
        "UF, ano, mês e categoria quando presentes. Preserve os rótulos do texto. "
        f"Responda em portugues.\nTexto: {texto_resposta}"
    )
    fact_check = checar_claims(claims.claims, truth)

    if not any(c["status"] != "NAO VERIFICADO" for c in fact_check):
        return {
            "fact_check": fact_check,
            "nli": {
                "nli_verdict": "NEUTRA",
                "is_logically_coherent": True,
                "detected_fallacies": ["Nenhuma"],
                "technical_justification": "Não foi possível verificar os valores com as referências disponíveis. "
                "Isso não comprova erro de cálculo nem confirma a coerência da resposta.",
            },
        }

    avaliador = modelo.with_structured_output(LogicalCoherenceReport)
    relatorio = avaliador.invoke(
        "Atue como auditor NLI. Compare a resposta do agente com os registros oficiais. "
        "Avalie calculos, periodos, contradicoes premissa-conclusao e violacoes de escopo. "
        "Falhas de consulta, referências ausentes e NAO VERIFICADO são limitações da verificação, "
        "não evidências de falsidade, erro de cálculo ou período incorreto. "
        "Não generalize divergências para afirmações não verificadas. "
        "Relatorio exclusivamente em portugues do Brasil.\n"
        f"COMPARACOES: {json.dumps(fact_check, ensure_ascii=False)}\n"
        f"REGISTROS: {json.dumps(truth, ensure_ascii=False)}\nRESPOSTA: {texto_resposta}"
    )
    return {"fact_check": fact_check, "nli": relatorio.model_dump()}


def verificar_turno(texto_resposta: str, trajetoria: list[dict], modelo: BaseChatModel | None = None) -> dict:
    """Atalho usado pelo Streamlit e pelos notebooks: trajetoria -> truth -> verificacao."""
    from src.investigation_flow import conferir_resposta_estruturada

    estruturada = conferir_resposta_estruturada(texto_resposta, trajetoria)
    if estruturada is not None:
        return estruturada
    consultas = consultas_da_trajetoria(trajetoria)
    truth = fonte_autoritativa(consultas) if consultas else {}
    # Referências de consultar_dados provêm do retorno estruturado efetivo da ferramenta.
    for passo in trajetoria:
        if passo.get("tipo") != "retorno" or passo.get("ferramenta") != "consultar_dados":
            continue
        try:
            retorno = json.loads(passo["conteudo"])
        except (ValueError, KeyError):
            continue
        if retorno.get("status") != "CONSULTADO" or not retorno.get("evidencia", {}).get("completo"):
            continue
        metricas = {
            "emenda_paga",
            "emenda_empenhado",
            "emenda_liquidado",
            "patrimonio_declarado",
            "receita_eleitoral",
            "despesa_eleitoral",
            "gasto_total",
            "gasto_categoria",
            "media_partido",
        }
        for indice, row in enumerate(retorno.get("dados", [])):
            for chave in metricas & row.keys():
                valor = row[chave]
                if isinstance(valor, (float, int)):
                    contexto = {
                        k: v for k, v in row.items() if k in {"nome", "autor", "ano", "eleicao", "categoria"}
                    }
                    truth[f"{chave}#{len(truth)}"] = {
                        "metric": chave,
                        "records": [{"value": valor, "context": contexto, "tolerance": 0.01}],
                    }
    saida = verificar_resposta(texto_resposta, truth, modelo)
    saida["truth"] = truth
    return saida
