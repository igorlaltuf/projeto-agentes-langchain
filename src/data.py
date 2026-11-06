"""Dados estruturados (CSV da CEAP -> SQLite) e base normativa (Ato da Mesa -> FAISS).

Expõe as ferramentas 'consultar_ceap' (text-to-query, somente leitura) e
'consultar_regras_cota' (retriever sobre índice FAISS salvo).

Uso:
    python -m src.data --build-db      # carrega data/ceap_2025.csv em data/ceap.db
    python -m src.data --build-index   # monta indices/ato_mesa a partir de data/ato_mesa.txt
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.tools import create_retriever_tool, tool
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from src import config

TABELA = "despesas_ceap"

SCHEMA_CEAP = f"""
Tabela SQLite '{TABELA}' (uma linha por despesa reembolsada pela cota):
  nome_parlamentar TEXT, partido TEXT, uf TEXT, categoria TEXT,
  fornecedor TEXT, cnpj_cpf TEXT, valor REAL, ano INTEGER, mes INTEGER, data TEXT
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
    "txNomeParlamentar": "nome_parlamentar",
    "sgPartido": "partido",
    "sgUF": "uf",
    "txtDescricao": "categoria",
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
def construir_sqlite(csv: Path = config.CSV_CEAP, db: Path = config.SQLITE_CEAP) -> int:
    """Carrega o CSV da CEAP no SQLite. Retorna o número de linhas."""
    df = pd.read_csv(csv, sep=";", encoding="utf-8", low_memory=False)
    df = df[list(COLUNAS_CSV)].rename(columns=COLUNAS_CSV)
    df["valor"] = pd.to_numeric(df["valor"].astype(str).str.replace(",", "."), errors="coerce").fillna(0)
    df["cnpj_cpf"] = df["cnpj_cpf"].astype(str).str.replace(r"\D", "", regex=True)
    with sqlite3.connect(db) as conn:
        df.to_sql(TABELA, conn, if_exists="replace", index=False)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_nome ON {TABELA}(nome_parlamentar)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_partido ON {TABELA}(partido, ano)")
    return len(df)


class ConsultaCEAPArgs(BaseModel):
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
def consultar_ceap(query_sql: str) -> str:
    """Executa uma consulta SQL somente leitura no banco local de despesas da cota
    parlamentar (CEAP). Use para agregacoes: medias por partido/UF, rankings de
    fornecedores, series mensais e comparacoes entre varios deputados."""
    q = query_sql.strip().rstrip(";")
    maiuscula = q.upper()
    if not maiuscula.startswith("SELECT") or any(p in maiuscula for p in PALAVRAS_PROIBIDAS):
        return "Erro de seguranca: apenas consultas SELECT sao permitidas."
    if " LIMIT " not in maiuscula:
        q += " LIMIT 200"
    print(f"[AUDITORIA SQL] {q}")
    try:
        with sqlite3.connect(config.SQLITE_CEAP) as conn:
            cur = conn.execute(q)
            colunas = [c[0] for c in cur.description]
            linhas = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        return f"Erro SQL: {e}"
    if not linhas:
        return "Nenhum registro encontrado."
    registros = [dict(zip(colunas, l)) for l in linhas]
    return f"{len(registros)} registros: {registros}"


# --------------------------------------------------------------------------- #
# FAISS: Ato da Mesa 43/2009 + FAQ da cota
# --------------------------------------------------------------------------- #
def _embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=config.MODELO_EMBEDDINGS)


def construir_indice(
    texto_normas: Path = config.DATA_DIR / "ato_mesa.txt", destino: Path = config.INDICE_ATO_MESA
) -> int:
    """Monta e salva o índice FAISS da base normativa. Retorna o número de chunks."""
    texto = texto_normas.read_text(encoding="utf-8")
    doc = Document(page_content=texto, metadata={"fonte": "Ato da Mesa 43/2009 (consolidado)"})
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500, chunk_overlap=100, separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = splitter.split_documents([doc])
    for i, c in enumerate(chunks):
        c.metadata["chunk_id"] = f"CHK_{i:03d}"
    indice = FAISS.from_documents(chunks, _embeddings())
    indice.save_local(str(destino))
    return len(chunks)


def carregar_indice(origem: Path = config.INDICE_ATO_MESA) -> FAISS:
    """Carrega o índice FAISS existente (fase 4: não reconstruir)."""
    return FAISS.load_local(str(origem), _embeddings(), allow_dangerous_deserialization=True)


def ferramenta_regras_cota(k: int = 3):
    """Retriever sobre o índice salvo, como ferramenta do agente."""
    retriever = carregar_indice().as_retriever(search_kwargs={"k": k})
    return create_retriever_tool(
        retriever,
        name="consultar_regras_cota",
        description=(
            "Busca nas regras da cota parlamentar (Ato da Mesa 43/2009 e FAQ) o que a cota "
            "pode ou nao pagar, limites mensais por UF e prazos de prestacao de contas. "
            "Reescreva a pergunta do usuario em termos tecnicos antes de buscar."
        ),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-db", action="store_true")
    ap.add_argument("--build-index", action="store_true")
    args = ap.parse_args()
    if args.build_db:
        print(f"{construir_sqlite()} linhas carregadas em {config.SQLITE_CEAP}")
    if args.build_index:
        print(f"{construir_indice()} chunks salvos em {config.INDICE_ATO_MESA}")
