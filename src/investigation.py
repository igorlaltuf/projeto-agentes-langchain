"""Ferramentas SQL com evidência estruturada para os três domínios."""

import hashlib
import json
import re
import sqlite3
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import config
from src.catalog import cobertura

VIEWS = {
    "v_despesas_ceap",
    "v_emendas",
    "candidaturas_publicas",
    "v_bens",
    "v_receitas_eleitorais",
    "v_despesas_eleitorais",
    "v_identidades",
    "v_favorecidos_emendas",
    "v_identidades_eleitorais",
}
SCHEMA_MANDATO = """
Views SQL disponíveis em consultar_dados:
 v_despesas_ceap: id_deputado,nome_parlamentar,ano,mes,partido,uf,categoria,fornecedor,cnpj_cpf,valor (reais).
 candidaturas_publicas: id,eleicao,sequencial,nome,nome_urna,partido,uf,situacao,pessoa_id.
 v_bens: candidatura_id,eleicao,nome,nome_urna,uf,partido,descricao,valor (reais).
 v_receitas_eleitorais / v_despesas_eleitorais: candidatura_id,eleicao,nome,nome_urna,partido,uf,data,contraparte_documento,contraparte_nome,valor (reais).
 v_emendas: id,ano,autor,autoria (individual/coletiva),pessoa_id,tipo,localidade,funcao,empenhado,liquidado,pago,restos_inscritos,restos_cancelados,restos_pagos (reais).
 v_favorecidos_emendas: emenda_codigo,autor_codigo,ano,mes,favorecido_codigo,favorecido_nome,tipo_favorecido,documento,documento_valido,natureza_juridica,uf,municipio,valor_recebido,pessoa_id,autor,emenda_identificada,lote_id,linha_origem.
 Favorecidos são recebimentos mensais separados das execuções: NÃO some valor_recebido com pago/restos_pagos de v_emendas. Favorecido não significa fornecedor final. Código de favorecido pode ser CPF mascarado ou unidade gestora; cruzamentos exatos exigem documento_valido=1 e tipo compatível. Documento com * só pode gerar sugestão, jamais JOIN de identidade confirmada.
 v_identidades_eleitorais: candidatura_id,grupo,metodo,eleicao. Mesmo grupo permite cruzar eleições do TSE por CPF validado sem conflito, inclusive pessoas sem mandato na Câmara. Não representa vínculo com a Câmara.
 v_identidades: fonte,identificador,pessoa_id,metodo (somente vínculos documentados).
 Nas views financeiras, lote_id e linha_origem dão proveniência (CEAP usa registro_local).
 Nunca some estágios de emendas ou patrimônio e fluxos. Agregue cada fato ANTES de JOIN para evitar multiplicação.
 CPF exige documento válido e unicidade comprovada nos dois sentidos; CPFs de contrapartes sem essa validação não autorizam associação automática.
 Use identidade documentada para cruzar pessoas; mesmo nome NÃO basta. CNPJ de fornecedores pode ser cruzado por igualdade exata, com agregação prévia de cada domínio.
"""


class ConsultaArgs(BaseModel):
    dominio: Literal["ceap", "emendas", "eleitoral", "cruzamento"]
    query_sql: str = Field(
        description="SELECT SQLite somente sobre as views documentadas; informar período e medida explicitamente."
    )


@tool(args_schema=ConsultaArgs)
def consultar_dados(dominio: str, query_sql: str) -> str:
    """Consulta dados locais de gastos, emendas, eleições ou cruzamentos documentados; retorna dados e evidências. Não baixa arquivos."""
    if not config.SQLITE_CEAP.exists():
        return json.dumps(
            {"status": "INDISPONIVEL", "erro": "Banco não preparado; execute scripts/load_sources.py"}
        )
    q = query_sql.strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", q, re.IGNORECASE) or ";" in q:
        return json.dumps({"status": "INVALIDO", "erro": "Apenas uma consulta de leitura é permitida"})
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        disponiveis = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        referenciadas = {nome for nome in VIEWS if re.search(rf"\b{nome}\b", q, re.IGNORECASE)}
        if not referenciadas or not referenciadas.issubset(disponiveis):
            return json.dumps(
                {
                    "status": "INDISPONIVEL",
                    "erro": "Base ou view não carregada. Use comandos de carga; ausência não é zero.",
                }
            )

        dependencias_autorizadas = set()

        def autorizar(acao, arg1, arg2, banco, origem):
            if acao == sqlite3.SQLITE_READ:
                if arg1 in VIEWS or origem in VIEWS:
                    if origem in VIEWS:
                        dependencias_autorizadas.add(arg1)
                    return sqlite3.SQLITE_OK
                # SQLite pode emitir leitura sem coluna ao otimizar subconsultas de uma view.
                # Só permite dependência já autorizada e nunca uma tabela citada diretamente.
                if (
                    arg2 == ""
                    and arg1 in dependencias_autorizadas
                    and not re.search(rf"\b{re.escape(arg1)}\b", q, re.IGNORECASE)
                ):
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            if acao in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if acao == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in {
                "load_extension",
                "readfile",
                "writefile",
            }:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        # Não confundir tabela vazia/não importada com ausência de despesas.
        def anos_filtrados(texto, coluna):
            anos = set()
            for a, b in re.findall(
                rf"{coluna}\s+BETWEEN\s+['\"]?(20\d{{2}})['\"]?\s+AND\s+['\"]?(20\d{{2}})",
                texto,
                re.IGNORECASE,
            ):
                anos.update(range(int(a), int(b) + 1))
            anos.update(
                int(a) for a in re.findall(rf"{coluna}\s*=\s*['\"]?(20\d{{2}})", texto, re.IGNORECASE)
            )
            for lista in re.findall(rf"{coluna}\s+IN\s*\(([^)]+)\)", texto, re.IGNORECASE):
                anos.update(int(a) for a in re.findall(r"\b20\d{2}\b", lista))
            return anos

        for view in referenciadas - {"v_identidades"}:
            coluna = (
                "eleicao"
                if view
                in {
                    "candidaturas_publicas",
                    "v_bens",
                    "v_receitas_eleitorais",
                    "v_despesas_eleitorais",
                    "v_identidades_eleitorais",
                }
                else "ano"
            )
            if len(referenciadas - {"v_identidades"}) == 1:
                periodos = anos_filtrados(q, rf"\b{coluna}")
            else:
                # Períodos pertencem à fonte/alias, não a todos os anos citados no SQL.
                periodos = anos_filtrados(q, rf"\b{view}\.{coluna}")
                for alias in re.findall(rf"\b(?:FROM|JOIN)\s+{view}\s+(?:AS\s+)?(\w+)", q, re.IGNORECASE):
                    if alias.upper() not in {
                        "WHERE",
                        "JOIN",
                        "GROUP",
                        "ORDER",
                        "LEFT",
                        "RIGHT",
                        "INNER",
                        "LIMIT",
                    }:
                        periodos.update(anos_filtrados(q, rf"\b{alias}\.{coluna}"))
                for trecho in re.findall(
                    rf"\bFROM\s+{view}\b(.*?)(?=\bSELECT\b|\bJOIN\b|$)", q, re.IGNORECASE | re.DOTALL
                ):
                    periodos.update(anos_filtrados(trecho, rf"(?<![\w.]){coluna}"))
            try:
                carregados = {int(r[0]) for r in conn.execute(f"SELECT DISTINCT {coluna} FROM {view}")}
            except sqlite3.Error:
                carregados = set()
            if not carregados or (periodos and not periodos.issubset(carregados)):
                return json.dumps(
                    {
                        "status": "INDISPONIVEL",
                        "erro": f"Cobertura insuficiente em {view}; carregados: {sorted(carregados)}. Prepare os arquivos antes de consultar.",
                    }
                )
        conn.set_authorizer(autorizar)
        passos = 0

        def limite():
            nonlocal passos
            passos += 1
            return int(passos > 20000)

        conn.set_progress_handler(limite, 1000)
        try:
            cursor = conn.execute(q)
            nomes = [c[0] for c in cursor.description]
            linhas = cursor.fetchmany(201)
            dados = [dict(zip(nomes, r)) for r in linhas[:200]]
        except sqlite3.Error as exc:
            return json.dumps({"status": "ERRO", "erro": str(exc), "dados": []}, ensure_ascii=False)
    evidencia = {
        "dominio": dominio,
        "sql": q,
        "consulta_id": hashlib.sha256(q.encode()).hexdigest()[:16],
        "fontes": cobertura(),
        "completo": len(linhas) <= 200,
        "limite": 200,
        "unidade": "reais nas colunas valor/pago/empenhado/liquidado; centavos nas colunas *_centavos",
    }
    return json.dumps({"status": "CONSULTADO", "dados": dados, "evidencia": evidencia}, ensure_ascii=False)
