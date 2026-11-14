import json
import sqlite3

import pytest

from src import config
from src.agent import montar_agente, trajetoria
from src.catalog import banco, importar_tse, inicializar_catalogo
from src.investigation import consultar_dados


@pytest.fixture
def copia(monkeypatch, banco_amostra, tmp_path):
    destino = tmp_path / "copia.db"
    with sqlite3.connect(banco_amostra) as original, sqlite3.connect(destino) as c:
        original.backup(c)
    monkeypatch.setattr(config, "SQLITE_CEAP", destino)
    inicializar_catalogo()
    return destino


@pytest.mark.parametrize("pergunta", ["Quais bens declarados ao TSE em 2022?", "Quanto em emendas em 2026?"])
def test_nucleo_nao_consulta_fontes_ampliadas(pergunta):
    estado = montar_agente(com_rag=False).invoke(
        {"messages": [("user", pergunta)]}, config={"configurable": {"thread_id": "escopo"}}
    )
    assert not [p for p in trajetoria(estado) if p["tipo"] == "decisao"]
    assert "escopo" in estado["messages"][-1].content


def test_muda_ano_preserva_deputado_categoria():
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "ano-curto"}}
    ag.invoke({"messages": [("user", "Quanto Tabata Amaral gastou com combustível em 2025?")]}, config=cfg)
    estado = ag.invoke({"messages": [("user", "E em 2024?")]}, config=cfg)
    chamadas = [p for p in trajetoria(estado) if p["tipo"] == "decisao"]
    assert chamadas[0]["ferramenta"] == "listar_despesas"
    assert chamadas[0]["args"] == {"id_deputado": 204534, "ano": 2024, "categoria": "COMBUST"}


def test_fornecedores_preservam_recorte():
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "fornecedores"}}
    ag.invoke({"messages": [("user", "Quanto Tabata Amaral gastou com combustível em 2025?")]}, config=cfg)
    estado = ag.invoke({"messages": [("user", "Quais os maiores fornecedores dela?")]}, config=cfg)
    args = next(p["args"] for p in trajetoria(estado) if p["tipo"] == "decisao")
    assert args["id_deputado"] == 204534 and args["anos"] == [2025] and args["categoria"] == "COMBUST"
    assert args["acao"] == "ranking_fornecedores"


def test_cnpj_explicito_preservado_para_sancoes():
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "empresa"}}
    ag.invoke({"messages": [("user", "Consulte o cadastro do CNPJ 33.927.528/0001-60.")]}, config=cfg)
    estado = ag.invoke({"messages": [("user", "Essa empresa aparece no CEIS ou CNEP?")]}, config=cfg)
    ch = next(p for p in trajetoria(estado) if p["tipo"] == "decisao")
    assert ch["ferramenta"] == "consultar_sancoes" and ch["args"]["cnpj"] == "33927528000160"


def test_autorizacao_subconsulta_metadados_sem_liberar_tabelas(copia):
    with banco() as c:
        c.execute("CREATE TABLE ceap_lotes(ano INTEGER PRIMARY KEY,lote_id INTEGER)")
        c.execute("INSERT INTO ceap_lotes VALUES(2025,1)")
        c.execute("DROP VIEW v_despesas_ceap")
        c.execute(
            "CREATE VIEW v_despesas_ceap AS SELECT *, (SELECT lote_id FROM ceap_lotes l WHERE l.ano=despesas_ceap.ano) AS lote_id FROM despesas_ceap"
        )
    q = "SELECT SUM(valor) AS gasto_total FROM v_despesas_ceap WHERE ano=2025"
    assert json.loads(consultar_dados.invoke({"dominio": "ceap", "query_sql": q}))["status"] == "CONSULTADO"
    q = "SELECT COUNT(*) FROM ceap_lotes, v_despesas_ceap"
    assert json.loads(consultar_dados.invoke({"dominio": "ceap", "query_sql": q}))["status"] == "ERRO"


def test_cobertura_por_fonte_em_periodos_distintos(copia):
    for tipo in ["candidatos", "receitas"]:
        importar_tse(config.RAIZ / f"examples/tse/{tipo}_2022.csv", tipo, 2022, "DEMONSTRACAO")
    q = "WITH g AS (SELECT SUM(valor) AS gasto_total FROM v_despesas_ceap WHERE ano=2025), r AS (SELECT SUM(valor) AS receita_eleitoral FROM v_receitas_eleitorais WHERE eleicao=2022) SELECT * FROM g,r"
    assert (
        json.loads(consultar_dados.invoke({"dominio": "cruzamento", "query_sql": q}))["status"]
        == "CONSULTADO"
    )


def test_rag_recupera_excecao_hospedagem():
    from src.data import ferramenta_regras_cota

    texto = ferramenta_regras_cota().invoke({"query": "A cota pode pagar hospedagem em Brasília?"})
    assert "hospedagem, exceto do parlamentar no Distrito Federal" in texto
    assert "Ato da Mesa" in texto


def test_cadastro_indisponivel_nao_interrompe(monkeypatch):
    from src import tools

    monkeypatch.setattr(
        tools, "_get_json", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("Sem fixture"))
    )
    retorno = json.loads(tools.dados_fornecedor.invoke({"cnpj": "33927528000160"}))
    assert retorno["status"] == "INDISPONIVEL"
    assert "razao_social" not in retorno


def test_cadastro_nao_atribui_outro_cnpj(monkeypatch):
    from src import tools

    monkeypatch.setattr(
        tools, "_get_json", lambda *a, **kw: {"cnpj": "04252011000110", "razao_social": "OUTRA EMPRESA"}
    )
    assert json.loads(tools.dados_fornecedor.invoke({"cnpj": "33927528000160"}))["status"] == "INDISPONIVEL"


def test_rag_preserva_uf_curta_na_busca_de_limite():
    from src.data import ferramenta_regras_cota

    texto = ferramenta_regras_cota().invoke({"query": "Qual o limite mensal da cota para um deputado de SP?"})
    assert "42.837,33" in texto
    assert "SP" in texto
