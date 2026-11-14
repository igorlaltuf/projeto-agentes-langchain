from src.agent import montar_agente, trajetoria


def decisoes(estado):
    return [(t["ferramenta"], t["args"]) for t in trajetoria(estado) if t["tipo"] == "decisao"]


def test_turno_simples_resolve_nome_e_lista_despesas():
    ag = montar_agente()
    est = ag.invoke(
        {"messages": [("user", "Quanto a deputada Tabata Amaral gastou com combustível em 2025?")]},
        config={"configurable": {"thread_id": "a"}},
    )
    nomes = [d[0] for d in decisoes(est)]
    assert nomes == ["buscar_deputado", "listar_despesas"]
    assert "R$ 48.808,83" in est["messages"][-1].content
    assert (
        "Fonte: Cota para o Exercício da Atividade Parlamentar (CEAP), 2025." in est["messages"][-1].content
    )


def test_memoria_entre_turnos_usa_partido_do_contexto():
    ag = montar_agente()
    cfg = {"configurable": {"thread_id": "b"}}
    ag.invoke({"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]}, config=cfg)
    est = ag.invoke({"messages": [("user", "Compare esse gasto com a média do partido dela.")]}, config=cfg)
    (ferramenta, args), *_ = decisoes(est)
    assert ferramenta == "consultar_ceap"
    assert "partido='PSB'" in args["query_sql"] and "COMBUST" in args["query_sql"]
    assert "Media por deputado: R$" in est["messages"][-1].content


def test_threads_sao_isoladas():
    ag = montar_agente()
    ag.invoke(
        {"messages": [("user", "Quanto a Tabata Amaral gastou em 2025?")]},
        config={"configurable": {"thread_id": "c1"}},
    )
    est = ag.invoke(
        {"messages": [("user", "Compare esse gasto com a média do partido dela.")]},
        config={"configurable": {"thread_id": "c2"}},
    )
    assert decisoes(est) == []
    assert "preciso saber o partido" in est["messages"][-1].content


def test_homonimos_pede_desambiguacao_sem_consultar_despesas(banco_homonimos):
    ag = montar_agente(com_rag=False)
    est = ag.invoke(
        {"messages": [("user", "Gastos do Silva em 2025")]}, config={"configurable": {"thread_id": "d"}}
    )
    assert [d[0] for d in decisoes(est)] == ["buscar_deputado"]
    assert "mais de um deputado" in est["messages"][-1].content


def test_regras_usam_rag():
    ag = montar_agente()
    est = ag.invoke(
        {"messages": [("user", "Qual o limite mensal da cota para deputado de SP?")]},
        config={"configurable": {"thread_id": "e"}},
    )
    assert [d[0] for d in decisoes(est)] == ["consultar_regras_cota"]
    assert "Ato da Mesa" in est["messages"][-1].content


def test_memoria_episodica_grava_e_lembra():
    ag = montar_agente()
    cfg = {"configurable": {"thread_id": "f"}}
    ag.invoke({"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]}, config=cfg)
    est = ag.invoke({"messages": [("user", "Guarde essa apuração.")]}, config=cfg)
    assert decisoes(est)[0][0] == "gravar_apuracao"
    est = ag.invoke({"messages": [("user", "O que apuramos anteriormente?")]}, config=cfg)
    assert decisoes(est)[0][0] == "lembrar_apuracao"
    assert "48.808,83" in est["messages"][-1].content


def test_executor_inclui_verificacao_antes_de_retornar():
    ag = montar_agente(com_rag=False)
    estado = ag.invoke(
        {"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]},
        config={"configurable": {"thread_id": "auditado"}},
    )
    assert estado["verificacao"]["fact_check"][0]["status"] == "CONFIRMADO"


def test_indice_ausente_funciona_offline(monkeypatch, tmp_path):
    from src import config

    monkeypatch.setattr(config, "INDICE_ATO_MESA", tmp_path / "inexistente")
    ag = montar_agente()
    assert ag is not None


def test_copia_limpa_prepara_banco_e_indice_sem_rede(monkeypatch, tmp_path):
    import sqlite3

    from src import config

    banco = tmp_path / "data" / "ceap.db"
    monkeypatch.setattr(config, "DATA_DIR", banco.parent)
    monkeypatch.setattr(config, "SQLITE_CEAP", banco)
    monkeypatch.setattr(config, "INDICE_ATO_MESA", tmp_path / "indices" / "ausente")
    ag = montar_agente()
    with sqlite3.connect(banco) as conn:
        assert conn.execute("SELECT COUNT(*) FROM despesas_ceap").fetchone()[0] > 0
    estado = ag.invoke(
        {"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]},
        config={"configurable": {"thread_id": "copia-limpa"}},
    )
    assert estado["verificacao"]["fact_check"][0]["status"] == "CONFIRMADO"


def test_limite_contexto_preserva_turno_atual(monkeypatch):
    from src import config

    ag = montar_agente(com_rag=False)
    monkeypatch.setattr(config, "MAX_TOKENS_CONTEXTO", 1)
    import pytest

    with pytest.raises(ValueError, match="Turno excedeu"):
        ag.invoke(
            {"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]},
            config={"configurable": {"thread_id": "limite"}},
        )
