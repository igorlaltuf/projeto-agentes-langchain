"""Dados estruturados (CSV da CEAP -> SQLite) e base normativa (Ato da Mesa -> FAISS).

Expõe as ferramentas 'consultar_ceap' (text-to-query, somente leitura) e
'consultar_regras_cota' (retriever sobre índice FAISS salvo).

Uso:
    python -m src.data --build-db      # carrega data/ceap_2025.csv em data/ceap.db
    python -m src.data --build-index   # monta indices/ato_mesa a partir de data/ato_mesa.txt
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import threading
from pathlib import Path

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from src import config, llm

TABELA = "despesas_ceap"
_CARGA_LOCK = threading.RLock()

SCHEMA_CEAP = f"""
Tabela SQLite '{TABELA}' (uma linha por despesa reembolsada pela cota):
  id_deputado INTEGER (mesmo id da API da Camara; NULL para liderancas partidarias),
  nome_parlamentar TEXT, partido TEXT, uf TEXT, categoria TEXT, especificacao TEXT,
  fornecedor TEXT, cnpj_cpf TEXT (somente digitos), valor REAL (liquido; negativo = estorno),
  ano INTEGER, mes INTEGER, data TEXT (AAAA-MM-DD)
Categorias (valores exatos de 'categoria'): 'COMBUSTÍVEIS E LUBRIFICANTES.', 'PASSAGEM AÉREA - SIGEPA',
  'MANUTENÇÃO DE ESCRITÓRIO DE APOIO À ATIVIDADE PARLAMENTAR', 'SERVIÇO DE TÁXI, PEDÁGIO E ESTACIONAMENTO',
  'DIVULGAÇÃO DA ATIVIDADE PARLAMENTAR.', 'TELEFONIA', 'FORNECIMENTO DE ALIMENTAÇÃO DO PARLAMENTAR',
  'HOSPEDAGEM ,EXCETO DO PARLAMENTAR NO DISTRITO FEDERAL.', 'LOCAÇÃO OU FRETAMENTO DE VEÍCULOS AUTOMOTORES',
  entre outras. Prefira LIKE '%COMBUST%' a igualdade exata. Nomes estao como 'Tabata Amaral' (use LIKE).
Exemplos de consulta:
  -- gasto de um deputado por categoria
  SELECT categoria, ROUND(SUM(valor),2) AS total FROM {TABELA}
   WHERE nome_parlamentar LIKE '%FULANO%' AND ano=2025 GROUP BY categoria ORDER BY total DESC;
  -- media por deputado dentro de um partido
  SELECT ROUND(AVG(t.total),2) AS media FROM (
    SELECT nome_parlamentar, SUM(valor) AS total FROM {TABELA}
     WHERE partido='PT' AND ano=2025 AND categoria LIKE '%COMBUST%' GROUP BY nome_parlamentar) t;
  -- ranking de fornecedores
  SELECT fornecedor, cnpj_cpf, ROUND(SUM(valor),2) AS total FROM {TABELA}
   WHERE ano=2025 GROUP BY fornecedor, cnpj_cpf ORDER BY total DESC LIMIT 10;
"""

# Mapeamento das colunas do CSV oficial da CEAP para o schema local
COLUNAS_CSV = {
    "ideCadastro": "id_deputado",
    "txNomeParlamentar": "nome_parlamentar",
    "sgPartido": "partido",
    "sgUF": "uf",
    "txtDescricao": "categoria",
    "txtDescricaoEspecificacao": "especificacao",
    "txtFornecedor": "fornecedor",
    "txtCNPJCPF": "cnpj_cpf",
    "vlrLiquido": "valor",
    "numAno": "ano",
    "numMes": "mes",
    "datEmissao": "data",
}


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #
def construir_sqlite(csv: Path | None = None, db: Path | None = None, ano_esperado: int | None = None) -> int:
    """Carrega o CSV da CEAP no SQLite. Retorna o número de linhas."""
    # Caminhos resolvidos em tempo de chamada para respeitar config alterada (ex.: testes).
    csv = csv or config.CSV_CEAP
    db = db or config.SQLITE_CEAP
    # O arquivo oficial vem com BOM, separador ';' e decimal com ponto.
    df = pd.read_csv(csv, sep=";", encoding="utf-8-sig", low_memory=False, dtype=str)
    df = df[list(COLUNAS_CSV)].rename(columns=COLUNAS_CSV)
    df["valor"] = pd.to_numeric(df["valor"].str.replace(",", "."), errors="coerce").fillna(0).round(2)
    df["id_deputado"] = pd.to_numeric(df["id_deputado"], errors="coerce").astype("Int64")
    for col in ("ano", "mes"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["cnpj_cpf"] = df["cnpj_cpf"].fillna("").str.replace(r"\D", "", regex=True)
    df["data"] = df["data"].fillna("").str.slice(0, 10)
    if df.empty or df["ano"].isna().any():
        raise ValueError("CSV sem despesas ou com ano inválido; banco preservado.")
    anos = [int(a) for a in df["ano"].unique()]
    if ano_esperado is not None and set(anos) != {ano_esperado}:
        raise ValueError(f"CSV não corresponde ao ano {ano_esperado}; banco preservado")
    db.parent.mkdir(parents=True, exist_ok=True)
    with _CARGA_LOCK, sqlite3.connect(db, timeout=60) as conn:
        # A tabela de estágio permite substituir apenas os anos importados em transação.
        df.to_sql("carga_ceap", conn, if_exists="replace", index=False)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABELA} AS SELECT * FROM carga_ceap WHERE 0")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"DELETE FROM {TABELA} WHERE ano IN ({','.join('?' for _ in anos)})", anos)
        conn.execute(f"INSERT INTO {TABELA} SELECT * FROM carga_ceap")
        conn.execute("DROP TABLE carga_ceap")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_nome ON {TABELA}(nome_parlamentar)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_id ON {TABELA}(id_deputado, ano, mes)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_partido ON {TABELA}(partido, uf, ano)")
    return len(df)


class ConsultaCEAPArgs(BaseModel):
    anos: list[int] | None = Field(
        default=None, description="Todos os anos necessários, inclusive intervalos."
    )
    query_sql: str = Field(
        ...,
        description="Consulta SQL SQLite valida, apenas SELECT. "
        "Pode usar JOIN, GROUP BY, subqueries e agregacoes.",
    )


PALAVRAS_PROIBIDAS = [
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "TRUNCATE",
    "REPLACE",
    "ATTACH",
    "PRAGMA",
]


@tool(args_schema=ConsultaCEAPArgs)
def consultar_ceap(query_sql: str, anos: list[int] | None = None) -> str:
    """Executa uma consulta SQL somente leitura no banco local de despesas da cota
    parlamentar (CEAP). Use para agregacoes: medias por partido/UF, rankings de
    fornecedores, series mensais e comparacoes entre varios deputados."""
    q = query_sql.strip().rstrip(";")
    maiuscula = q.upper()
    if not maiuscula.startswith("SELECT") or any(p in maiuscula for p in PALAVRAS_PROIBIDAS):
        return "Erro de seguranca: apenas consultas SELECT sao permitidas."
    from src.periods import anos_do_sql

    periodos_sql = set(anos_do_sql(q))
    if (
        not periodos_sql
        and re.search(r"\b(?:SUM|AVG)\s*\(", q, re.IGNORECASE)
        and re.search(r"\bFROM\s+despesas_ceap\b", q, re.IGNORECASE)
    ):
        return "Período ausente: defina o filtro de ano antes de agregar gastos. Para todos os anos, informe explicitamente a cobertura completa."
    if anos and set(anos) != periodos_sql:
        return "Período inconsistente: o filtro de ano do SQL precisa corresponder exatamente aos anos informados. Corrija WHERE ano e não publique um total de outro intervalo."
    solicitados = set(anos or []) | periodos_sql
    disponiveis = set(anos_disponiveis())
    faltantes = solicitados - disponiveis
    if faltantes:
        return f"Dados indisponíveis: anos {sorted(faltantes)} não carregados. Não interprete como gasto zero. Execute scripts/download_data.py --ano {' '.join(map(str, sorted(faltantes)))}."
    if " LIMIT " not in maiuscula:
        q += " LIMIT 200"
    print(f"[AUDITORIA SQL] {q}")
    try:
        with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")

            def autorizar_ceap(acao, tabela, coluna, banco, origem):
                if acao == sqlite3.SQLITE_READ:
                    return sqlite3.SQLITE_OK if tabela == TABELA else sqlite3.SQLITE_DENY
                if acao in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                    return sqlite3.SQLITE_OK
                if acao == sqlite3.SQLITE_FUNCTION and (coluna or "").lower() not in {
                    "load_extension",
                    "readfile",
                    "writefile",
                }:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY

            conn.set_authorizer(autorizar_ceap)
            cur = conn.execute(q)
            colunas = [c[0] for c in cur.description]
            linhas = cur.fetchmany(201)
            if len(linhas) > 200:
                return "Resultado excedeu 200 registros; refine o período ou agregue a consulta. Não publique totais de uma lista parcial."
    except Exception as e:  # noqa: BLE001
        return f"Erro SQL: {e}"
    if not linhas:
        return "Nenhum registro encontrado."
    registros = [dict(zip(colunas, l)) for l in linhas]
    return f"{len(registros)} registros: {registros}"


# --------------------------------------------------------------------------- #
# FAISS: Ato da Mesa 43/2009 + FAQ da cota
# --------------------------------------------------------------------------- #
def _embeddings():
    return llm.embeddings()


def _chunks(texto_normas: Path | None = None) -> list[Document]:
    """Corta a base normativa em chunks de 500 caracteres (100 de sobreposicao) com chunk_id."""
    texto = (texto_normas or config.TEXTO_NORMAS).read_text(encoding="utf-8")
    doc = Document(page_content=texto, metadata={"fonte": "Ato da Mesa 43/2009 (consolidado)"})
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500, chunk_overlap=100, separators=["\n\n", "\n", ". ", " ", ""]
    )
    docs = [doc]
    dicionario = config.RAIZ / "data/normas/metodologia_dados.txt"
    if dicionario.exists():
        docs.append(
            Document(
                page_content=dicionario.read_text(),
                metadata={"fonte": "Metodologia e dicionário do ApuraMandato"},
            )
        )
    chunks = splitter.split_documents(docs)
    for i, c in enumerate(chunks):
        c.metadata["chunk_id"] = f"CHK_{i:03d}"
    return chunks


def construir_indice(texto_normas: Path | None = None, destino: Path | None = None) -> int:
    """Monta e salva o índice FAISS da base normativa. Retorna o número de chunks."""
    destino = destino or config.INDICE_ATO_MESA
    chunks = _chunks(texto_normas)
    indice = FAISS.from_documents(chunks, _embeddings())
    indice.save_local(str(destino))
    return len(chunks)


def carregar_indice(origem: Path | None = None) -> FAISS:
    """Carrega o índice FAISS existente (fase 4: não reconstruir).

    O indice salvo tem a dimensao do modelo de embeddings que o gerou (1536 na OpenAI). Em modo
    offline os embeddings fake tem outra dimensao; nesse caso o indice e refeito em memoria a partir
    do mesmo texto (105 chunks, instantaneo), sem alterar o arquivo salvo. Online, a divergencia e
    erro de configuracao e pede 'devbox run index'.
    """
    origem = origem or config.INDICE_ATO_MESA
    emb = _embeddings()
    if not (origem / "index.faiss").exists() and llm.offline():
        return FAISS.from_documents(_chunks(), emb)
    indice = FAISS.load_local(str(origem), emb, allow_dangerous_deserialization=True)
    dim_consulta = len(emb.embed_query("dimensao"))
    if indice.index.d == dim_consulta:
        return indice
    if llm.offline():
        print(
            f"[INDICE] salvo com {indice.index.d} dims, embeddings offline com {dim_consulta}: refazendo em memoria"
        )
        return FAISS.from_documents(_chunks(), emb)
    raise RuntimeError(
        f"Indice em {origem} tem {indice.index.d} dimensoes, mas {config.MODELO_EMBEDDINGS} produz "
        f"{dim_consulta}. Reconstrua com: devbox run index"
    )


def ferramenta_regras_cota(k: int = 3):
    """Retriever sobre o índice salvo, como ferramenta do agente."""

    # Construção preguiçosa: índice indisponível não impede consultar SQL/APIs.
    @tool("consultar_regras_cota")
    def consultar(query: str) -> str:
        """Busca no Ato da Mesa e dicionário metodológico. Cite fontes; índice ausente não é evidência normativa."""
        try:
            indice = carregar_indice()
        except (RuntimeError, OSError) as exc:
            return (
                f"Recuperação indisponível: {exc}. Reconstrua o índice com python -m src.data --build-index."
            )
        # Complementa FAISS com termos exatos: evita perder exceções normativas curtas.
        import math
        import re
        import unicodedata

        def normalizar(t):
            return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()

        texto = normalizar(query).replace("brasilia", "distrito federal").replace("hotel", "hospedagem")
        termos = {
            t
            for t in re.findall(r"[a-z]{2,}", texto)
            if t
            not in {
                "pode",
                "para",
                "como",
                "qual",
                "quais",
                "pagar",
                "cota",
                "deputado",
                "parlamentar",
                "despesas",
            }
        }
        docs = _chunks()
        normalizados = [normalizar(d.page_content) for d in docs]
        pesos = {t: math.log((len(docs) + 1) / (1 + sum(t in s for s in normalizados))) for t in termos}
        candidatos = sorted(
            zip(docs, normalizados),
            key=lambda par: sum(pesos[t] for t in termos if t in par[1]),
            reverse=True,
        )
        lexicos = [d for d, t in candidatos if any(p in t for p in termos)][:k]
        semanticos = indice.as_retriever(search_kwargs={"k": k}).invoke(query)
        escolhidos = []
        vistos = set()
        for doc in lexicos + semanticos:
            if doc.page_content not in vistos:
                escolhidos.append(doc)
                vistos.add(doc.page_content)
            if len(escolhidos) == k:
                break
        return "\n\n".join(
            f"Fonte: {doc.metadata.get('fonte', 'Ato da Mesa 43/2009')}\n{doc.page_content}"
            for doc in escolhidos
        )

    return consultar


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-db", action="store_true")
    ap.add_argument("--build-index", action="store_true")
    args = ap.parse_args()
    if args.build_db:
        print(f"{construir_sqlite()} linhas carregadas em {config.SQLITE_CEAP}")
    if args.build_index:
        print(f"{construir_indice()} chunks salvos em {config.INDICE_ATO_MESA}")


def preparar_offline() -> None:
    """Prepara a amostra versionada sem rede; preserva um banco completo existente."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not config.SQLITE_CEAP.exists():
        construir_sqlite(config.RAIZ / "tests/dados/ceap_amostra.csv", config.SQLITE_CEAP)


def anos_disponiveis() -> list[int]:
    if not config.SQLITE_CEAP.exists():
        return []
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
        if not conn.execute("SELECT name FROM sqlite_master WHERE name=?", (TABELA,)).fetchone():
            return []
        return [int(r[0]) for r in conn.execute(f"SELECT DISTINCT ano FROM {TABELA} ORDER BY ano")]


def garantir_anos(anos: list[int]) -> None:
    """Importa CSVs anuais ausentes antes da consulta; preserva os anos já carregados."""
    from datetime import date

    if any(a < 2009 or a > date.today().year for a in anos):
        raise ValueError("Informe anos entre 2009 e o ano corrente")
    faltantes = sorted(set(anos) - set(anos_disponiveis()))
    if faltantes:
        raise RuntimeError(
            f"Anos {faltantes} não carregados. Prepare a base por scripts/download_data.py; o chat não baixa arquivos"
        )


def despesas_individuais_locais(id_deputado: int, ano: int, mes: int | None, categoria: str | None) -> str:
    """Consulta histórica determinística; nunca recorre à API para despesas de ano fechado."""
    import json

    from src.tools import normalizar_categoria

    garantir_anos([ano])
    where = "id_deputado=? AND ano=?"
    params = [id_deputado, ano]
    if mes is not None:
        where += " AND mes=?"
        params.append(mes)
    if categoria:
        where += " AND categoria LIKE ?"
        params.append(f"%{normalizar_categoria(categoria)}%")
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        total, quantidade = conn.execute(
            f"SELECT ROUND(SUM(valor),2), COUNT(*) FROM despesas_ceap WHERE {where}", params
        ).fetchone()
        categorias = dict(
            conn.execute(
                f"SELECT categoria, ROUND(SUM(valor),2) AS total FROM despesas_ceap WHERE {where} "
                "GROUP BY categoria ORDER BY total DESC",
                params,
            ).fetchall()
        )
        conn.row_factory = sqlite3.Row
        amostra = [
            dict(r)
            for r in conn.execute(
                f"SELECT data, categoria AS tipo, fornecedor, cnpj_cpf AS cnpj, valor FROM despesas_ceap "
                f"WHERE {where} ORDER BY data LIMIT 30",
                params,
            )
        ]
    return json.dumps(
        {
            "id_deputado": id_deputado,
            "ano": ano,
            "mes": mes,
            "fonte": "Cota para o Exercício da Atividade Parlamentar (CEAP)",
            "consulta_completa": True,
            "total_registros": quantidade,
            "total_valor": total if quantidade else None,
            "total_por_tipo": categorias,
            "amostra_despesas": amostra,
            "aviso": None
            if quantidade
            else "Nenhum lançamento no recorte; não conclua que o gasto foi zero.",
        },
        ensure_ascii=False,
    )


def buscar_deputado_local(nome: str, uf: str | None = None, ano: int | None = None) -> str:
    """Resolve inclusive ex-deputados presentes no CSV, sem depender dos mandatos atuais."""
    if ano is not None:
        garantir_anos([ano])
    if not anos_disponiveis():
        return ""
    from src.tools import sem_acento

    where = "id_deputado IS NOT NULL AND sem_acento(nome_parlamentar) LIKE ?"
    params = [f"%{sem_acento(nome).upper()}%"]
    if ano is not None:
        where += " AND ano=?"
        params.append(ano)
    if uf:
        where += " AND uf=?"
        params.append(uf.upper())
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as conn:
        conn.create_function("sem_acento", 1, lambda texto: sem_acento(texto or "").upper())
        linhas = conn.execute(
            f"SELECT DISTINCT id_deputado, nome_parlamentar, partido, uf, ano, mes FROM despesas_ceap "
            f"WHERE {where} ORDER BY ano DESC, mes DESC",
            params,
        ).fetchall()
    encontrados = {}
    for id_dep, nome_dep, partido, estado, *_ in linhas:
        encontrados.setdefault(id_dep, f"id={id_dep} | {nome_dep} | {partido}-{estado}")
    return "\n".join(encontrados.values())
