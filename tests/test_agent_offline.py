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
    assert "Fonte: API da Camara" in est["messages"][-1].content


def test_memoria_entre_turnos_usa_partido_do_contexto():
    ag = montar_agente()
    cfg = {"configurable": {"thread_id": "b"}}
    ag.invoke({"messages": [("user", "Quanto a Tabata Amaral gastou com combustível em 2025?")]}, config=cfg)
    est = ag.invoke({"messages": [("user", "Compare esse gasto com a média do partido dela.")]}, config=cfg)
    (ferramenta, args), *_ = decisoes(est)
    assert ferramenta == "consultar_ceap"
    assert "partido='PSB'" in args["query_sql"] and "COMBUST" in args["query_sql"]
    assert "48849.91" in est["messages"][-1].content or "media_por_deputado" in est["messages"][-1].content


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


def test_homonimos_pede_desambiguacao_sem_consultar_despesas():
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
