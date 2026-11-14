from src.data import carregar_indice, consultar_ceap, ferramenta_regras_cota


def test_select_funciona_e_limita():
    saida = consultar_ceap.invoke({"query_sql": "SELECT nome_parlamentar, valor FROM despesas_ceap"})
    assert saida.startswith("200 registros")  # LIMIT 200 forcado


def test_bloqueia_escrita():
    for sql in [
        "DROP TABLE despesas_ceap",
        "DELETE FROM despesas_ceap",
        "SELECT 1; DROP TABLE despesas_ceap",
    ]:
        assert "Erro de seguranca" in consultar_ceap.invoke({"query_sql": sql})


def test_erro_sql_retorna_mensagem():
    assert "Erro SQL" in consultar_ceap.invoke({"query_sql": "SELECT * FROM tabela_inexistente"})


def test_agregacao_por_categoria():
    saida = consultar_ceap.invoke(
        {
            "query_sql": "SELECT ROUND(SUM(valor),2) AS total FROM despesas_ceap "
            "WHERE id_deputado=204534 AND ano=2025 AND categoria LIKE '%COMBUST%'"
        }
    )
    assert "48808.83" in saida


def test_indice_com_outra_dimensao_e_refeito_em_memoria_no_modo_offline(tmp_path):
    """Indice salvo por embeddings reais (outra dimensao) nao pode quebrar o modo offline."""
    from langchain_community.vectorstores import FAISS
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from src.data import _chunks

    FAISS.from_documents(_chunks(), DeterministicFakeEmbedding(size=64)).save_local(str(tmp_path / "idx"))
    indice = carregar_indice(tmp_path / "idx")
    assert indice.similarity_search("limite mensal", k=1)


def test_indice_carrega_e_retriever_responde():
    indice = carregar_indice()
    docs = indice.similarity_search("limite mensal da cota por UF", k=2)
    assert len(docs) == 2 and docs[0].metadata["fonte"].startswith("Ato da Mesa")
    ferramenta = ferramenta_regras_cota(k=2)
    assert ferramenta.name == "consultar_regras_cota"
    assert ferramenta.invoke({"query": "prazo"})


def test_importacao_multiano_preserva_e_nao_duplica(tmp_path):
    import sqlite3
    from pathlib import Path

    import pandas as pd

    from src.data import construir_sqlite

    amostra = Path(__file__).parent / "dados/ceap_amostra.csv"
    anterior = tmp_path / "2024.csv"
    df = pd.read_csv(amostra, sep=";", dtype=str)
    df["numAno"] = "2024"
    df.to_csv(anterior, sep=";", index=False)
    db = tmp_path / "ceap.db"
    n = construir_sqlite(amostra, db)
    assert construir_sqlite(anterior, db) == n
    construir_sqlite(anterior, db)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT ano, COUNT(*) FROM despesas_ceap GROUP BY ano ORDER BY ano"
        ).fetchall() == [(2024, n), (2025, n)]


def test_sql_exige_carga_explicita_antes_de_agregar(monkeypatch, tmp_path):
    from pathlib import Path

    import pandas as pd

    from src import config
    from src.data import anos_disponiveis

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    df = pd.read_csv(Path(__file__).parent / "dados/ceap_amostra.csv", sep=";", dtype=str)
    df["numAno"] = "2024"
    df.to_csv(tmp_path / "ceap_2024.csv", sep=";", index=False)
    saida = consultar_ceap.invoke(
        {"query_sql": "SELECT ROUND(SUM(valor),2) AS total FROM despesas_ceap WHERE ano=2024", "anos": [2024]}
    )
    assert "Dados indisponíveis" in saida
    from src.data import construir_sqlite

    construir_sqlite(tmp_path / "ceap_2024.csv")
    assert anos_disponiveis() == [2024, 2025]


def test_ano_ausente_offline_nao_vira_zero():
    saida = consultar_ceap.invoke({"query_sql": "SELECT SUM(valor) FROM despesas_ceap WHERE ano=2023"})
    assert "Dados indisponíveis" in saida and "Não interprete como gasto zero" in saida


def test_chat_online_nao_faz_download(monkeypatch, tmp_path):
    from pathlib import Path

    import pandas as pd

    from src import config, downloads, llm
    from src.data import anos_disponiveis

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "ceap.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "offline", lambda: False)
    chamadas = []

    def baixar(ano):
        chamadas.append(ano)
        df = pd.read_csv(Path(__file__).parent / "dados/ceap_amostra.csv", sep=";", dtype=str)
        df["numAno"] = str(ano)
        destino = tmp_path / f"ceap_{ano}.csv"
        df.to_csv(destino, sep=";", index=False)
        return destino

    monkeypatch.setattr(downloads, "baixar_csv", baixar)
    saida = consultar_ceap.invoke(
        {"query_sql": "SELECT SUM(valor) AS total FROM despesas_ceap WHERE ano=2024"}
    )
    assert "Dados indisponíveis" in saida and chamadas == []
    consultar_ceap.invoke({"query_sql": "SELECT SUM(valor) FROM despesas_ceap WHERE ano=2024"})
    assert chamadas == []
    assert anos_disponiveis() == []


def test_csv_incorreto_preserva_banco(tmp_path):
    import sqlite3
    from pathlib import Path

    import pytest

    from src.data import construir_sqlite

    amostra = Path(__file__).parent / "dados/ceap_amostra.csv"
    db = tmp_path / "ceap.db"
    n = construir_sqlite(amostra, db)
    with pytest.raises(ValueError, match="banco preservado"):
        construir_sqlite(amostra, db, ano_esperado=2024)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM despesas_ceap").fetchone()[0] == n
