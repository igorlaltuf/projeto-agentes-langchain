"""Ferramentas do agente.

Tipos (fase 4 do projeto):
- API externa (web API pública): buscar_deputado, listar_despesas, dados_fornecedor
- Hardcoded (determinística): calcular_estatisticas
- Text-to-query: consultar_ceap (fase 4)
- Semântica + RAG: consultar_regras_cota (fase 2/4)

Em MODO_OFFLINE as ferramentas de API leem respostas gravadas em fixtures/.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from typing import Literal, Optional

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import config


# --------------------------------------------------------------------------- #
# utilidades de rede / fixtures
# --------------------------------------------------------------------------- #
def _chave_fixture(url: str, params: dict | None) -> str:
    bruto = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(bruto.encode()).hexdigest()[:16]


def _get_json(url: str, params: dict | None = None) -> dict:
    """GET com cache em fixtures. Em MODO_OFFLINE só lê o fixture."""
    caminho = config.FIXTURES_DIR / f"{_chave_fixture(url, params)}.json"
    if config.MODO_OFFLINE or caminho.exists():
        if not caminho.exists():
            raise FileNotFoundError(
                f"Modo offline sem fixture para {url} {params}. "
                "Rode scripts/record_fixtures.py com a API disponível."
            )
        return json.loads(caminho.read_text(encoding="utf-8"))
    resp = httpx.get(url, params=params, timeout=30, headers={"Accept": "application/json"})
    resp.raise_for_status()
    dados = resp.json()
    config.FIXTURES_DIR.mkdir(exist_ok=True)
    caminho.write_text(json.dumps({"_url": url, "_params": params, **dados}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return dados


# --------------------------------------------------------------------------- #
# API externa: Câmara dos Deputados
# --------------------------------------------------------------------------- #
class BuscarDeputadoArgs(BaseModel):
    nome: str = Field(..., description="Nome ou parte do nome do deputado.")
    uf: Optional[str] = Field(default=None, description="Sigla da UF para desambiguar (ex: SP).")


@tool(args_schema=BuscarDeputadoArgs)
def buscar_deputado(nome: str, uf: Optional[str] = None) -> str:
    """Busca deputados federais em exercicio pelo nome na API da Camara.
    Retorna id, nome, partido e UF. Se houver mais de um resultado, liste-os
    e peca ao usuario para escolher antes de consultar despesas."""
    params = {"nome": nome, "ordem": "ASC", "ordenarPor": "nome"}
    if uf:
        params["siglaUf"] = uf.upper()
    dados = _get_json(f"{config.API_CAMARA}/deputados", params)
    itens = dados.get("dados", [])
    if not itens:
        return f"Nenhum deputado encontrado para '{nome}'."
    linhas = [f"id={d['id']} | {d['nome']} | {d['siglaPartido']}-{d['siglaUf']}" for d in itens[:10]]
    return "\n".join(linhas)


class ListarDespesasArgs(BaseModel):
    id_deputado: int = Field(..., description="ID numerico do deputado (obtido em buscar_deputado).")
    ano: int = Field(..., description="Ano das despesas, ex: 2025.")
    mes: Optional[int] = Field(default=None, description="Mes (1-12). Omita para o ano inteiro.")
    categoria: Optional[str] = Field(default=None,
                                     description="Filtro por tipo de despesa (ex: 'COMBUSTÍVEIS', 'PASSAGEM AÉREA').")


@tool(args_schema=ListarDespesasArgs)
def listar_despesas(id_deputado: int, ano: int, mes: Optional[int] = None,
                    categoria: Optional[str] = None) -> str:
    """Lista as despesas da cota parlamentar (CEAP) de um deputado na API da Camara,
    por ano e opcionalmente por mes e categoria. Retorna JSON com data, tipo,
    fornecedor, CNPJ e valor liquido de cada despesa, mais o total."""
    params = {"ano": ano, "itens": 100, "ordem": "ASC", "ordenarPor": "dataDocumento"}
    if mes:
        params["mes"] = mes
    dados = _get_json(f"{config.API_CAMARA}/deputados/{id_deputado}/despesas", params)
    itens = dados.get("dados", [])
    if categoria:
        itens = [d for d in itens if categoria.lower() in d.get("tipoDespesa", "").lower()]
    resumo = [
        {
            "data": d.get("dataDocumento"),
            "tipo": d.get("tipoDespesa"),
            "fornecedor": d.get("nomeFornecedor"),
            "cnpj": d.get("cnpjCpfFornecedor"),
            "valor": d.get("valorLiquido"),
        }
        for d in itens
    ]
    total = round(sum(float(d["valor"] or 0) for d in resumo), 2)
    return json.dumps({"total_registros": len(resumo), "total_valor": total, "despesas": resumo[:50]},
                      ensure_ascii=False)


# --------------------------------------------------------------------------- #
# API externa: Brasil API (CNPJ)
# --------------------------------------------------------------------------- #
class FornecedorArgs(BaseModel):
    cnpj: str = Field(..., description="CNPJ do fornecedor, somente digitos ou formatado.")


@tool(args_schema=FornecedorArgs)
def dados_fornecedor(cnpj: str) -> str:
    """Consulta a Brasil API e retorna razao social, situacao cadastral, CNAE
    principal, municipio e data de abertura de um fornecedor pelo CNPJ."""
    digitos = "".join(c for c in cnpj if c.isdigit())
    if len(digitos) != 14:
        return f"CNPJ invalido: {cnpj}"
    d = _get_json(f"{config.API_BRASIL}/cnpj/v1/{digitos}")
    campos = {
        "razao_social": d.get("razao_social"),
        "situacao": d.get("descricao_situacao_cadastral"),
        "cnae_principal": d.get("cnae_fiscal_descricao"),
        "municipio": f"{d.get('municipio')}-{d.get('uf')}",
        "abertura": d.get("data_inicio_atividade"),
    }
    return json.dumps(campos, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Hardcoded: estatísticas (a LLM nunca faz conta de cabeça)
# --------------------------------------------------------------------------- #
class EstatisticasArgs(BaseModel):
    valores: list[float] = Field(..., description="Lista de valores numericos.")
    operacao: Literal["soma", "media", "mediana", "maximo", "minimo", "variacao_pct"] = Field(
        ..., description="Operacao. 'variacao_pct' usa os dois primeiros valores: (v2-v1)/v1*100.")


@tool(args_schema=EstatisticasArgs)
def calcular_estatisticas(valores: list[float], operacao: str) -> str:
    """Calcula soma, media, mediana, maximo, minimo ou variacao percentual de uma
    lista de valores de forma deterministica. Use SEMPRE que precisar de qualquer
    calculo numerico; nunca calcule de cabeca."""
    if not valores:
        return "Erro: lista vazia."
    if operacao == "soma":
        r = sum(valores)
    elif operacao == "media":
        r = statistics.mean(valores)
    elif operacao == "mediana":
        r = statistics.median(valores)
    elif operacao == "maximo":
        r = max(valores)
    elif operacao == "minimo":
        r = min(valores)
    elif operacao == "variacao_pct":
        if len(valores) < 2 or valores[0] == 0:
            return "Erro: variacao_pct exige dois valores e o primeiro diferente de zero."
        r = (valores[1] - valores[0]) / valores[0] * 100
    else:
        return f"Erro: operacao '{operacao}' invalida."
    return f"{operacao} = {round(r, 2)}"


# --------------------------------------------------------------------------- #
# Text-to-query (fase 4) e RAG (fase 2/4): implementados em src/data.py
# e registrados aqui para o toolkit unificado.
# --------------------------------------------------------------------------- #
def toolkit_basico() -> list:
    """Ferramentas da fase 2 (sem SQL e sem RAG)."""
    return [buscar_deputado, listar_despesas, dados_fornecedor, calcular_estatisticas]
