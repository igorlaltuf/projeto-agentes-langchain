"""Camada de verificação (fase 5): extração de claims, fact-checking determinístico e NLI.

Roda dentro do agente como último passo antes da resposta final.
"""

from __future__ import annotations

import json
import sqlite3
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
    with sqlite3.connect(config.SQLITE_CEAP) as conn:
        for chave, sql in consultas_sql.items():
            try:
                valor = conn.execute(sql).fetchone()[0]
                truth[chave] = {"value": round(float(valor or 0), 2), "tolerance": tolerancia_brl}
            except Exception as e:  # noqa: BLE001
                truth[chave] = {"value": None, "tolerance": tolerancia_brl, "erro": str(e)}
    return truth


def checar_claims(claims: list[ExtractedClaim], truth: dict[str, dict[str, Any]]) -> list[dict]:
    """Fact-checking deterministico: compara cada claim com a fonte autoritativa dentro da tolerancia."""
    resultado = []
    for c in claims:
        ref = truth.get(c.canonical_key)
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
    for passo in trajetoria:
        if passo.get("tipo") != "decisao":
            continue
        args = passo.get("args", {})
        if passo["ferramenta"] == "listar_despesas" and args.get("ano") == ano:
            radical = tools.normalizar_categoria(args.get("categoria"))
            filtro = f" AND categoria LIKE '%{radical}%'" if radical else ""
            chave = "gasto_categoria" if args.get("categoria") else "gasto_total"
            mes = f" AND mes={args['mes']}" if args.get("mes") else ""
            consultas[chave] = (
                f"SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE id_deputado={int(args['id_deputado'])} "
                f"AND ano={ano}{mes}{filtro}"
            )
        elif passo["ferramenta"] == "consultar_ceap":
            sql = args.get("query_sql", "")
            if "AVG(" in sql.upper():
                chave = "media_uf" if "uf=" in sql.lower() else "media_partido"
                consultas[chave] = sql
    return consultas


def verificar_resposta(
    texto_resposta: str, truth: dict[str, dict[str, Any]], modelo: BaseChatModel | None = None
) -> dict:
    """Tres etapas: extracao estruturada (LLM) -> fact-checking deterministico -> NLI (LLM)."""
    modelo = modelo or llm.chat_model("juiz")

    extrator = modelo.with_structured_output(ClaimExtractionList)
    claims = extrator.invoke(
        "Extraia todas as afirmacoes numericas e factuais do texto e mapeie para as chaves canonicas. "
        f"Responda em portugues.\nTexto: {texto_resposta}"
    )
    fact_check = checar_claims(claims.claims, truth)

    avaliador = modelo.with_structured_output(LogicalCoherenceReport)
    relatorio = avaliador.invoke(
        "Atue como auditor NLI. Compare a resposta do agente com os registros oficiais. "
        "Avalie calculos, periodos, contradicoes premissa-conclusao e violacoes de escopo. "
        "Relatorio exclusivamente em portugues do Brasil.\n"
        f"REGISTROS: {json.dumps(truth, ensure_ascii=False)}\nRESPOSTA: {texto_resposta}"
    )
    return {"fact_check": fact_check, "nli": relatorio.model_dump()}


def verificar_turno(texto_resposta: str, trajetoria: list[dict], modelo: BaseChatModel | None = None) -> dict:
    """Atalho usado pelo Streamlit e pelos notebooks: trajetoria -> truth -> verificacao."""
    consultas = consultas_da_trajetoria(trajetoria)
    truth = fonte_autoritativa(consultas) if consultas else {}
    saida = verificar_resposta(texto_resposta, truth, modelo)
    saida["truth"] = truth
    return saida
