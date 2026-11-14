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
import re
import statistics
import unicodedata
from datetime import UTC, datetime
from typing import Literal

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import config

# --------------------------------------------------------------------------- #
# categorias de despesa
# --------------------------------------------------------------------------- #
# Radicais SEM acento que ocorrem tanto em 'tipoDespesa' (API) quanto em 'categoria' (CSV/SQLite),
# para que o mesmo filtro sirva ao LIKE do SQL e ao filtro em Python. Ordem importa.
CATEGORIAS_CEAP: list[tuple[str, str]] = [
    (r"combust|gasolin|etanol|diesel", "COMBUST"),
    (r"aeronave|avi[aã]o|jatinho", "FRETAMENTO DE AERONAVES"),
    (r"embarca|lancha|barco", "FRETAMENTO DE EMBARCA"),
    (r"terrestre|mar[ií]tim|fluvia|[oô]nibus", "PASSAGENS TERRESTRES"),
    (r"passag|a[eé]re|voo|bilhete", "PASSAGEM"),
    (r"divulg|publicid|propag|m[ií]dia|redes sociais", "DIVULG"),
    (r"telefon|celular", "TELEFON"),
    (r"escrit|aluguel de sala", "ESCRIT"),
    (r"t[aá]xi|ped[aá]gio|estacion|uber", "ESTACIONAMENTO"),
    (r"aliment|refei|restaur|comida", "ALIMENT"),
    (r"hosped|hotel|pousada", "HOSPED"),
    (r"ve[ií]cul|carro|fretamento|loca[cç][aã]o|aluguel", "FRETAMENTO DE VE"),
    (r"segur", "SEGURAN"),
    (r"moradia", "MORADIA"),
    (r"postal|correio", "POSTAIS"),
    (r"assinatura|publica[cç]|jornal|revista", "PUBLICA"),
    (r"curso|palestra|evento", "CURSO"),
    (r"token|certificado", "TOKENS"),
]


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar_categoria(texto: str | None) -> str | None:
    """Converte um termo livre ('combustível', 'passagens aéreas', 'hotel') no radical canonico
    usado nas categorias da CEAP. Sem correspondencia, devolve o texto em maiusculas sem acento."""
    if not texto:
        return None
    t = texto.lower()
    for padrao, radical in CATEGORIAS_CEAP:
        if re.search(padrao, t):
            return radical
    return sem_acento(texto).upper().strip()


# --------------------------------------------------------------------------- #
# utilidades de rede / fixtures
# --------------------------------------------------------------------------- #
def _chave_fixture(url: str, params: dict | None) -> str:
    bruto = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(bruto.encode()).hexdigest()[:16]


def _get_json(url: str, params: dict | None = None) -> dict:
    """GET com cache em fixtures. Em MODO_OFFLINE só lê o fixture."""
    caminho = config.FIXTURES_DIR / f"{_chave_fixture(url, params)}.json"
    import time

    fresco = caminho.exists() and time.time() - caminho.stat().st_mtime < 86400
    if config.MODO_OFFLINE or (config.USAR_CACHE_API and fresco):
        if not caminho.exists():
            raise FileNotFoundError(
                f"Modo offline sem fixture para {url} {params}. "
                "Rode scripts/record_fixtures.py com a API disponível."
            )
        return json.loads(caminho.read_text(encoding="utf-8"))
    resp = httpx.get(url, params=params, timeout=30, headers={"Accept": "application/json"})
    resp.raise_for_status()
    dados = resp.json()
    dados["_consultado_em"] = datetime.now(UTC).isoformat()
    config.FIXTURES_DIR.mkdir(exist_ok=True)
    caminho.write_text(
        json.dumps({"_url": url, "_params": params, **dados}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return dados


# --------------------------------------------------------------------------- #
# API externa: Câmara dos Deputados
# --------------------------------------------------------------------------- #
class BuscarDeputadoArgs(BaseModel):
    nome: str = Field(..., description="Nome ou parte do nome do deputado.")
    uf: str | None = Field(default=None, description="Sigla da UF para desambiguar (ex: SP).")
    ano: int | None = Field(
        default=None, description="Ano da apuração, para resolver nomes e partidos históricos."
    )


@tool(args_schema=BuscarDeputadoArgs)
def buscar_deputado(nome: str, uf: str | None = None, ano: int | None = None) -> str:
    """Busca deputados pelo nome no banco histórico da CEAP, sem chamadas externas.
    Retorna id, nome, partido e UF. Se houver mais de um resultado, liste-os
    e peca ao usuario para escolher antes de consultar despesas."""
    from src.data import buscar_deputado_local

    encontrados = buscar_deputado_local(nome, uf, ano)
    if encontrados:
        return encontrados
    return f"Nenhum deputado encontrado para '{nome}' na CEAP carregada. Confira nome, UF e período."


def legislatura_do_ano(ano: int) -> int:
    """Legislaturas de 4 anos: a 51a comecou em 1999, a 57a em 2023."""
    return 51 + (ano - 1999) // 4


class ListarDespesasArgs(BaseModel):
    id_deputado: int = Field(..., description="ID numerico do deputado (obtido em buscar_deputado).")
    ano: int = Field(..., description="Ano das despesas, ex: 2025.")
    mes: int | None = Field(default=None, description="Mes (1-12). Omita para o ano inteiro.")
    categoria: str | None = Field(
        default=None,
        description="Filtro por tipo de despesa em linguagem natural (ex: 'combustível', 'passagem aérea', "
        "'divulgação', 'hospedagem'); singular/plural e acentos sao normalizados.",
    )


@tool(args_schema=ListarDespesasArgs)
def listar_despesas(id_deputado: int, ano: int, mes: int | None = None, categoria: str | None = None) -> str:
    """Lista despesas de um deputado: todos os anos usam CSV/SQLite local.
    Filtra por ano, mês e categoria. Retorna a fonte efetiva, data, tipo,
    fornecedor, CNPJ e valor liquido de cada despesa, mais o total."""
    from src.data import despesas_individuais_locais

    try:
        return despesas_individuais_locais(id_deputado, ano, mes, categoria)
    except (RuntimeError, ValueError) as exc:
        return json.dumps({"status": "INDISPONIVEL", "ano": ano, "erro": str(exc)}, ensure_ascii=False)


def _listar_despesas_api(
    id_deputado: int, ano: int, mes: int | None = None, categoria: str | None = None
) -> str:
    # A API so devolve despesas quando recebe tambem a legislatura (57 = 2023-2026).
    params = {
        "idLegislatura": legislatura_do_ano(ano),
        "ano": ano,
        "itens": 100,
        "ordem": "ASC",
        "ordenarPor": "dataDocumento",
    }
    if mes:
        params["mes"] = mes
    # Percorre todas as páginas. Falhas interrompem a consulta sem publicar um total parcial.
    itens: list[dict] = []
    pagina = 1
    paginas_vistas = set()
    while True:
        dados = _get_json(
            f"{config.API_CAMARA}/deputados/{id_deputado}/despesas", {**params, "pagina": pagina}
        )
        lote = dados.get("dados", [])
        assinatura = json.dumps(lote, sort_keys=True)
        if lote and assinatura in paginas_vistas:
            raise RuntimeError("A API repetiu uma página; consulta incompleta, total não calculado.")
        paginas_vistas.add(assinatura)
        itens += lote
        if not any(link.get("rel") == "next" for link in dados.get("links", [])):
            break
        if not lote:
            raise RuntimeError("A API retornou página vazia com continuação; consulta incompleta.")
        pagina += 1
    if categoria:
        radical = normalizar_categoria(categoria)
        itens = [d for d in itens if radical in sem_acento(d.get("tipoDespesa", "")).upper()]
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
    por_tipo: dict[str, float] = {}
    for d in resumo:
        por_tipo[d["tipo"]] = round(por_tipo.get(d["tipo"], 0) + float(d["valor"] or 0), 2)
    return json.dumps(
        {
            "id_deputado": id_deputado,
            "ano": ano,
            "mes": mes,
            "fonte": "API da Camara",
            "consulta_completa": True,
            "paginas_consultadas": pagina,
            "total_registros": len(resumo),
            "total_valor": total,
            "total_por_tipo": dict(sorted(por_tipo.items(), key=lambda kv: -kv[1])),
            "amostra_despesas": resumo[:30],
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
# API externa: Brasil API (CNPJ)
# --------------------------------------------------------------------------- #
class FornecedorArgs(BaseModel):
    cnpj: str = Field(..., description="CNPJ do fornecedor, somente digitos ou formatado.")


@tool(args_schema=FornecedorArgs)
def dados_fornecedor(cnpj: str) -> str:
    """Consulta a Brasil API e retorna razao social, situacao cadastral, CNAE
    principal, municipio, abertura e quadro de sócios e administradores pelo CNPJ."""
    from src.identity import cnpj_valido, documento_posicional

    digitos = documento_posicional(cnpj)
    if not cnpj_valido(digitos):
        return f"CNPJ invalido: {cnpj}"
    try:
        d = _get_json(f"{config.API_BRASIL}/cnpj/v1/{digitos}")
        if not isinstance(d, dict) or not d.get("razao_social"):
            raise ValueError("Cadastro não identificado")
        if d.get("cnpj") and documento_posicional(str(d["cnpj"])) != digitos:
            raise ValueError("Documento retornado diverge do solicitado")
    except (httpx.HTTPError, FileNotFoundError, ValueError):
        return json.dumps(
            {
                "status": "INDISPONIVEL",
                "cnpj": digitos,
                "erro": "Cadastro empresarial indisponível. Não foi possível confirmar os dados da empresa.",
            },
            ensure_ascii=False,
        )
    campos = {
        "status": "CONSULTADO",
        "cnpj": digitos,
        "razao_social": d.get("razao_social"),
        "situacao": d.get("descricao_situacao_cadastral"),
        "cnae_principal": d.get("cnae_fiscal_descricao"),
        "municipio": f"{d.get('municipio')}-{d.get('uf')}",
        "abertura": d.get("data_inicio_atividade"),
        "qsa_disponivel": isinstance(d.get("qsa"), list),
        "socios": [
            {
                "nome": s.get("nome_socio"),
                "qualificacao": s.get("qualificacao_socio"),
                "entrada": s.get("data_entrada_sociedade"),
                "documento": s.get("cnpj_cpf_do_socio"),
            }
            for s in d.get("qsa", [])
            if isinstance(s, dict)
        ]
        if isinstance(d.get("qsa"), list)
        else [],
        "fonte": "Fixture sintética de demonstração"
        if d.get("_demonstracao")
        else "BrasilAPI / Minha Receita",
        "consultado_em": d.get(
            "_consultado_em", "fixture gravada" if config.MODO_OFFLINE else datetime.now(UTC).isoformat()
        ),
    }
    return json.dumps(campos, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Hardcoded: estatísticas (a LLM nunca faz conta de cabeça)
# --------------------------------------------------------------------------- #
class EstatisticasArgs(BaseModel):
    valores: list[float] = Field(..., description="Lista de valores numericos.")
    operacao: Literal["soma", "media", "mediana", "maximo", "minimo", "variacao_pct"] = Field(
        ..., description="Operacao. 'variacao_pct' usa os dois primeiros valores: (v2-v1)/v1*100."
    )


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


class SancoesArgs(BaseModel):
    cnpj: str = Field(description="CNPJ do fornecedor, com ou sem pontuação")


@tool(args_schema=SancoesArgs)
def consultar_sancoes(cnpj: str) -> str:
    """Consulta CEIS e CNEP carregados por CNPJ completo válido, sem rede ou chave CGU.
    Retorna a data de cada base e os registros, sem certificar regularidade ou vigência.
    """
    from src.sanctions import consultar_sancoes_locais

    return json.dumps(consultar_sancoes_locais(cnpj), ensure_ascii=False)
