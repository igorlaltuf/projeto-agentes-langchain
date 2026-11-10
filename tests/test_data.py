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
            "WHERE id_deputado=204534 AND categoria LIKE '%COMBUST%'"
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
