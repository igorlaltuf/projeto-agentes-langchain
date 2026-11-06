"""Camada de verificação (fase 5): extração de claims, fact-checking determinístico e NLI.

Roda dentro do agente como último passo antes da resposta final.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, List, Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src import config


class ExtractedClaim(BaseModel):
    claim_text: str = Field(description="Trecho exato da afirmacao numerica na resposta.")
    canonical_key: Literal[
        "gasto_total", "gasto_categoria", "media_partido", "media_uf",
        "valor_fornecedor", "variacao_pct", "limite_cota_uf", "artigo_citado", "unmapped_metric",
    ] = Field(description="Chave canonica da metrica afirmada.")
    claimed_value: Optional[float] = Field(default=None, description="Valor numerico afirmado, se houver.")
    unit: str = Field(description="Unidade: BRL, pct, artigo ou texto.")
    contexto: Optional[str] = Field(default=None, description="Deputado/partido/periodo/categoria a que se refere.")


class ClaimExtractionList(BaseModel):
    claims: List[ExtractedClaim]


class LogicalCoherenceReport(BaseModel):
    nli_verdict: Literal["APOIA", "REFUTA", "NEUTRA"]
    is_logically_coherent: bool
    detected_fallacies: List[Literal["Erro de Calculo", "Contradicao Premissa-Conclusao",
                                     "Violacao de Escopo", "Periodo Trocado", "Nenhuma"]]
    technical_justification: str = Field(description="Justificativa em portugues do Brasil.")


def fonte_autoritativa(consultas_sql: dict[str, str], tolerancia_brl: float = 0.01) -> dict[str, dict[str, Any]]:
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


def verificar_resposta(texto_resposta: str, truth: dict[str, dict[str, Any]],
                       llm: ChatOpenAI | None = None) -> dict:
    """Tres etapas: extracao estruturada -> fact-checking deterministico -> NLI."""
    llm = llm or ChatOpenAI(model=config.MODELO_JUIZ, temperature=0)

    extrator = llm.with_structured_output(ClaimExtractionList)
    claims = extrator.invoke(
        "Extraia todas as afirmacoes numericas e factuais do texto e mapeie para as chaves canonicas. "
        f"Responda em portugues.\nTexto: {texto_resposta}"
    )

    fact_check = []
    for c in claims.claims:
        ref = truth.get(c.canonical_key)
        if ref is None or ref.get("value") is None or c.claimed_value is None:
            status = "NAO VERIFICADO"
        else:
            delta = abs(c.claimed_value - ref["value"])
            status = "CONFIRMADO" if delta <= ref["tolerance"] else "CONTRADITORIO"
        fact_check.append({"claim": c.claim_text, "chave": c.canonical_key,
                           "alegado": c.claimed_value, "oficial": (ref or {}).get("value"), "status": status})

    avaliador = llm.with_structured_output(LogicalCoherenceReport)
    relatorio = avaliador.invoke(
        "Atue como auditor NLI. Compare a resposta do agente com os registros oficiais. "
        "Avalie calculos, periodos, contradicoes premissa-conclusao e violacoes de escopo. "
        "Relatorio exclusivamente em portugues do Brasil.\n"
        f"REGISTROS: {json.dumps(truth, ensure_ascii=False)}\nRESPOSTA: {texto_resposta}"
    )
    return {"fact_check": fact_check, "nli": relatorio.model_dump()}
