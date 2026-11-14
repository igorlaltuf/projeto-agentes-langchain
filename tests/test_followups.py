import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import config
from src.agent import montar_agente, trajetoria
from src.followups import contexto_sql, pedido_anual


@pytest.fixture
def banco_ranking(monkeypatch, tmp_path):
    db = tmp_path / "ceap.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE despesas_ceap(id_deputado INTEGER, nome_parlamentar TEXT, ano INTEGER, categoria TEXT, valor REAL)"
        )
        conn.executemany(
            "INSERT INTO despesas_ceap VALUES (121948, 'Adriano do Baldy', ?, 'COMBUSTÍVEIS E LUBRIFICANTES.', ?)",
            [(2022, 63645.26), (2023, 101923.71), (2024, 101076.57), (2025, 105304.96), (2026, 82258.68)],
        )
        conn.execute(
            "INSERT INTO despesas_ceap VALUES (2, 'Outra Pessoa', 2025, 'COMBUSTÍVEIS E LUBRIFICANTES.', 100)"
        )
    monkeypatch.setattr(config, "SQLITE_CEAP", db)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "HOJE", "2026-11-12")
    return db


def test_ranking_seguido_de_detalhamento_e_reconciliacao(banco_ranking, monkeypatch):
    from src import tools

    def proibido(*args, **kwargs):
        pytest.fail("Detalhamento do ranking não pode trocar para API")

    monkeypatch.setattr(tools, "_get_json", proibido)
    agente = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "ranking-anual"}}
    primeiro = agente.invoke(
        {"messages": [("user", "qual deputado mais gastou com combustivel nos ultimos 5 anos?")]}, config=cfg
    )
    assert "454.209,18" in primeiro["messages"][-1].content
    for pergunta in [
        "quanto ele gastou por ano?",
        "entoa como voce dises que ele gastou 454.209,18 nos últimos 5 anos?",
    ]:
        estado = agente.invoke({"messages": [("user", pergunta)]}, config=cfg)
        resposta = estado["messages"][-1].content
        for valor in ["63.645,26", "101.923,71", "101.076,57", "105.304,96", "82.258,68", "454.209,18"]:
            assert valor in resposta
        assert "A soma confere" in resposta
        assert "2026" in resposta and "parcial" in resposta
        decisoes = [p for p in trajetoria(estado) if p["tipo"] == "decisao"]
        assert [p["ferramenta"] for p in decisoes] == ["consultar_ceap"]
        assert "id_deputado=121948" in decisoes[0]["args"]["query_sql"]
        assert "GROUP BY ano" in decisoes[0]["args"]["query_sql"]


def test_contexto_deriva_do_resultado_nao_da_prosa(banco_ranking):
    sql = "SELECT nome_parlamentar, SUM(valor) AS total FROM despesas_ceap WHERE ano BETWEEN 2022 AND 2026 AND categoria LIKE '%COMBUST%' GROUP BY nome_parlamentar ORDER BY total DESC LIMIT 1"
    mensagens = [
        HumanMessage(content="quem mais gastou?"),
        AIMessage(
            content="", tool_calls=[{"name": "consultar_ceap", "args": {"query_sql": sql}, "id": "consulta"}]
        ),
        ToolMessage(
            content="1 registros: [{'nome_parlamentar': 'Adriano do Baldy', 'total': 454209.18}]",
            name="consultar_ceap",
            tool_call_id="consulta",
        ),
        AIMessage(content="Foi Outra Pessoa com 10 reais."),
        HumanMessage(content="quanto ele gastou por ano?"),
    ]
    contexto = pedido_anual(mensagens)
    assert contexto["id"] == 121948 and contexto["nome"] == "Adriano do Baldy"
    assert contexto["anos"] == [2022, 2023, 2024, 2025, 2026]
    mensagens[-1] = HumanMessage(content="Quanto ele gastou em 2024?")
    assert pedido_anual(mensagens) is None
    assert contexto_sql(mensagens)["total"] == 454209.18


def test_detalhamento_nao_transforma_ausencia_em_zero():
    from src.followups import resposta_anual

    contexto = {"nome": "Pessoa", "anos": [2022, 2023], "categoria": "%COMBUST%", "total": 100}
    resposta = resposta_anual(contexto, "1 registros: [{'ano': 2022, 'total': 100}]")
    assert "2023: Nenhum lançamento encontrado" in resposta
    assert "2023: R$ 0,00" not in resposta
    assert "A soma confere" in resposta


def test_reconciliacao_sinaliza_divergencia_sem_fabricar_dados():
    from src.followups import resposta_anual

    contexto = {"nome": "Pessoa", "anos": [2022, 2023], "categoria": "%COMBUST%", "total": 500}
    resposta = resposta_anual(
        contexto, "2 registros: [{'ano': 2022, 'total': 100}, {'ano': 2023, 'total': 200}]"
    )
    assert "R$ 300,00" in resposta
    assert "diverge" in resposta
    assert "A soma confere" not in resposta


def test_erro_sql_nao_derruba_total_anterior():
    from src.followups import resposta_anual

    resposta = resposta_anual({}, "Erro SQL: falha de consulta")
    assert "O total anterior não foi substituído" in resposta
