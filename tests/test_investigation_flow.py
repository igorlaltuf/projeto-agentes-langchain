"""Oráculos pequenos com valores conhecidos: não usam o cálculo de produção para o esperado."""

import json
import sqlite3

import pytest

from src import config, tools
from src.agent import montar_agente, trajetoria
from src.investigation_flow import investigar_fornecedores

A = "11222333000181"
B = "04252011000110"
C = "33927528000160"
D = "29813266000135"


@pytest.fixture
def dados(monkeypatch, banco_amostra, tmp_path):
    path = tmp_path / "investigacao.db"
    with sqlite3.connect(banco_amostra) as a, sqlite3.connect(path) as c:
        a.backup(c)
        c.execute("DELETE FROM despesas_ceap")
        linhas = [
            (1, "Tabata Amaral", A, "ALFA LTDA", 100, 2024, "COMBUSTIVEL"),
            (1, "Tabata Amaral", A, "ALFA LTDA", 200, 2025, "DIVULGACAO"),
            (1, "Tabata Amaral", A, "ALFA LTDA", -20, 2026, "DIVULGACAO"),
            (1, "Tabata Amaral", B, "ALFA LTDA", 100, 2024, "ALUGUEL"),
            (1, "Tabata Amaral", C, "GAMA LTDA", 50, 2025, "COMBUSTIVEL"),
            (1, "Tabata Amaral", D, "DELTA LTDA", 20, 2025, "DIVULGACAO"),
            (1, "Tabata Amaral", "52998224725", "PESSOA FISICA", 50, 2025, "COMBUSTIVEL"),
            (2, "Outro Deputado", A, "ALFA LTDA", 70, 2025, "COMBUSTIVEL"),
            (3, "Mais Um Deputado", A, "ALFA LTDA", 50, 2026, "DIVULGACAO"),
        ]
        for ident, nome, doc, fornecedor, valor, ano, cat in linhas:
            c.execute(
                "INSERT INTO despesas_ceap VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, nome, "PSB", "SP", cat, "", fornecedor, doc, valor, ano, 1, f"{ano}-01-01"),
            )
    monkeypatch.setattr(config, "SQLITE_CEAP", path)
    return path


def consultar(acao, **kw):
    return json.loads(
        investigar_fornecedores.invoke({"acao": acao, "id_deputado": 1, "anos": [2024, 2025, 2026], **kw})
    )


def test_nomes_iguais_cnpjs_distintos_nao_sao_somados(dados):
    r = consultar("ranking_fornecedores")
    assert [(x["cnpj"], x["valor"]) for x in r["dados"]] == [(A, 280), (B, 100), (C, 50), (D, 20)]
    assert sum(x["valor"] for x in r["dados"]) == 450


def test_evolucao_estorno_e_ausencia_de_lancamento(dados):
    r = consultar("evolucao_anual", cnpjs=[A, B])
    vals = {(x["cnpj"], x["ano"]): (x["valor"], x["registros"]) for x in r["dados"]}
    assert vals[(A, 2026)] == (-20, 1)
    assert vals[(B, 2025)] == (0, 0)
    assert sum(x["valor"] for x in r["dados"] if x["cnpj"] == A) == 280


def test_categorias_preservam_identidade(dados):
    r = consultar("categorias", cnpjs=[A])
    assert {(x["categoria"], x["valor"]) for x in r["dados"]} == {("COMBUSTIVEL", 100), ("DIVULGACAO", 180)}


def test_concentracao_denominador_inclui_pessoas_fisicas(dados):
    r = consultar("concentracao")
    assert r["dados"] == [{"top_n": 3, "valor_top": 430, "gasto_total_deputado": 500, "percentual": 86}]


def test_outros_deputados_mesmo_cnpj(dados):
    r = consultar("outros_deputados", cnpjs=[A])
    assert [(x["id_deputado"], x["valor"]) for x in r["dados"]] == [(2, 70), (3, 50)]


def test_participacao_na_ceap_nao_faturamento(dados):
    r = consultar("participacao_ceap", cnpjs=[A])
    assert r["dados"] == [
        {"valor_deputado": 280, "valor_todos_deputados_ceap": 400, "percentual_na_ceap": 70}
    ]
    assert "Não é faturamento" in r["referencias"]["universo_denominador"]


def test_categoria_preservada_nos_dois_lados(dados):
    r = consultar("participacao_ceap", cnpjs=[A], categoria="combustível")
    assert r["dados"] == [
        {"valor_deputado": 100, "valor_todos_deputados_ceap": 170, "percentual_na_ceap": 58.82}
    ]
    r = consultar("outros_deputados", cnpjs=[A], categoria="combustível")
    assert [x["id_deputado"] for x in r["dados"]] == [2]


def test_denominador_zero_nao_vira_percentual(dados):
    with sqlite3.connect(dados) as c:
        c.execute("UPDATE despesas_ceap SET valor=0")
    r = consultar("concentracao")
    assert r["dados"][0]["percentual"] is None


@pytest.mark.parametrize("doc", ["***222333000181", "52998224725", "11222333000180"])
def test_documento_parcial_cpf_ou_invalido_nao_cruza(dados, doc):
    assert consultar("outros_deputados", cnpjs=[doc])["status"] == "INVALIDO"


def test_ano_nao_carregado_nao_vira_zero(dados):
    r = consultar("ranking_fornecedores", anos=[2030])
    assert r["status"] == "INDISPONIVEL" and "dados" not in r


def test_troca_periodo_refaz_ranking_e_exige_escolha(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "periodo-investigacao"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    e = ag.invoke({"messages": [("user", "Refaça o ranking só em 2025.")]}, config=cfg)
    # "ranking" sem fornecedor é contextualizado abaixo pela regra de continuidade.
    chamadas = [x for x in trajetoria(e) if x["tipo"] == "decisao"]
    assert chamadas[-1]["args"].get("anos") == [2025]
    e = ag.invoke({"messages": [("user", "Quem são os sócios?")]}, config=cfg)
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]
    assert any(t in e["messages"][-1].content.lower() for t in ["vários", "qual", "indique"])


def test_periodo_ausente_pede_esclarecimento(dados):
    e = montar_agente(com_rag=False).invoke(
        {"messages": [("user", "Qual a empresa que mais recebeu dinheiro da Tabata Amaral?")]},
        config={"configurable": {"thread_id": "sem-periodo"}},
    )
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]
    assert "período" in e["messages"][-1].content


def test_escolha_cadastro_socios_e_indisponibilidade(dados, monkeypatch):
    def api(url, *a, **kw):
        doc = url.rsplit("/", 1)[-1]
        return {
            "cnpj": doc,
            "razao_social": "ALFA LTDA",
            "qsa": [
                {
                    "nome_socio": "PESSOA SINTÉTICA",
                    "qualificacao_socio": "Administrador",
                    "cnpj_cpf_do_socio": "***123456**",
                }
            ],
        }

    monkeypatch.setattr(tools, "_get_json", api)
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "escolha"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    ag.invoke({"messages": [("user", "Consulte o cadastro do primeiro fornecedor.")]}, config=cfg)
    e = ag.invoke({"messages": [("user", "Quem são os sócios?")]}, config=cfg)
    assert "PESSOA SINTÉTICA" in e["messages"][-1].content
    assert [x for x in trajetoria(e) if x["tipo"] == "decisao"][-1]["args"]["cnpj"] == A
    monkeypatch.setattr(
        tools, "_get_json", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("indisponível"))
    )
    e = ag.invoke({"messages": [("user", "Quem são os sócios?")]}, config=cfg)
    assert "não significa ausência de sócios" in e["messages"][-1].content


@pytest.mark.parametrize(
    "pergunta,trecho",
    [
        ("Isso prova favorecimento ou corrupção?", "não provam"),
        ("Esse percentual representa faturamento?", "não tem o faturamento total"),
    ],
)
def test_nao_inventa_conclusoes(pergunta, trecho, dados):
    e = montar_agente(com_rag=False).invoke(
        {"messages": [("user", pergunta)]}, config={"configurable": {"thread_id": "conclusao"}}
    )
    assert trecho in e["messages"][-1].content.lower()
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]


def test_agora_so_2025_continua_ranking(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "so-ano"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    e = ag.invoke({"messages": [("user", "Agora só 2025.")]}, config=cfg)
    args = [p["args"] for p in trajetoria(e) if p["tipo"] == "decisao"][-1]
    assert args["anos"] == [2025] and args["acao"] == "ranking_fornecedores"


def test_id_e_nome_incompativeis_nao_consultam(dados):
    r = consultar("ranking_fornecedores", nome_deputado="Outro Deputado")
    assert r["status"] == "ESCLARECER" and "dados" not in r


def test_lista_de_outros_deputados_limitada_e_sinalizada(dados):
    r = consultar("outros_deputados", cnpjs=[A], limite=1)
    assert r["total_outros_deputados"] == 2 and r["lista_limitada"]
    assert len(r["dados"]) == 1


def test_ano_apos_sancoes_nao_inventa_historico(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "ano-sancao"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    ag.invoke({"messages": [("user", f"Há sanções para o CNPJ {A}?")]}, config=cfg)
    e = ag.invoke({"messages": [("user", "Agora só 2025.")]}, config=cfg)
    assert not [p for p in trajetoria(e) if p["tipo"] == "decisao"]
    assert "histórico" in e["messages"][-1].content


def test_ambiguidade_novo_deputado_nao_reutiliza_anterior(dados):
    with sqlite3.connect(dados) as c:
        c.execute("UPDATE despesas_ceap SET nome_parlamentar='Alex Silva' WHERE id_deputado=2")
        c.execute("UPDATE despesas_ceap SET nome_parlamentar='Luiz Silva' WHERE id_deputado=3")
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "novo-ambiguo"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    e = ag.invoke({"messages": [("user", "Quais os maiores fornecedores do Silva em 2025?")]}, config=cfg)
    assert "única" in e["messages"][-1].content
    e = ag.invoke({"messages": [("user", "Quais fornecedores dele em 2025?")]}, config=cfg)
    assert not [p for p in trajetoria(e) if p["tipo"] == "decisao"]
    assert "deputado" in e["messages"][-1].content


def test_quantidade_de_fornecedores_e_respeitada(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "dois"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    e = ag.invoke(
        {"messages": [("user", "Quanto os dois primeiros fornecedores receberam por ano?")]}, config=cfg
    )
    ch = [p["args"] for p in trajetoria(e) if p["tipo"] == "decisao"][-1]
    assert ch["cnpjs"] == [A, B]


def test_matriz_e_filial_nao_sao_mesmo_estabelecimento(dados):
    filial = "11222333000262"
    with sqlite3.connect(dados) as c:
        c.execute("UPDATE despesas_ceap SET cnpj_cpf=? WHERE cnpj_cpf=?", (filial, B))
    r = consultar("ranking_fornecedores")
    assert {x["cnpj"] for x in r["dados"]} >= {A, filial}
    assert consultar("participacao_ceap", cnpjs=[A])["dados"][0]["percentual_na_ceap"] == 70


def test_snapshot_atual_nao_certifica_sancao_passada(dados):
    e = montar_agente(com_rag=False).invoke(
        {"messages": [("user", "A empresa estava sancionada em 2024 na época dos pagamentos?")]},
        config={"configurable": {"thread_id": "sancao-passada"}},
    )
    assert "não reconstroem um histórico completo" in e["messages"][-1].content
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]


def test_checagem_reexecuta_e_nao_confirma_texto_alterado(dados):
    from src.verification import verificar_turno

    e = montar_agente(com_rag=False).invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config={"configurable": {"thread_id": "auditar-investigacao"}},
    )
    v = e["verificacao"]
    assert v["metodo"] == "reexecucao_estruturada" and v["fact_check"][0]["status"] == "CONFIRMADO"
    assert v["nli"]["nli_verdict"] == "NEUTRA"
    alterado = e["messages"][-1].content.replace("280,00", "999,00")
    assert verificar_turno(alterado, trajetoria(e))["fact_check"][0]["status"] == "NAO VERIFICADO"
    alterado = e["messages"][-1].content.replace("2024 até 2026", "2020 até 2026")
    assert verificar_turno(alterado, trajetoria(e))["fact_check"][0]["status"] == "NAO VERIFICADO"


def test_dois_cnpjs_explicitos_nao_escolhe_um_arbitrariamente(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "dois-cnpjs"}}
    ag.invoke(
        {"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral entre 2024 e 2026?")]},
        config=cfg,
    )
    e = ag.invoke(
        {"messages": [("user", f"Quanto esses CNPJs {A} e {B} receberam de outros deputados?")]}, config=cfg
    )
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]
    assert "único" in e["messages"][-1].content


def test_detalhamento_grande_nao_e_apresentado_como_completo(dados):
    from src.investigation_flow import apresentar_investigacao

    with sqlite3.connect(dados) as c:
        for doc in [A, B, C, D]:
            for n in range(60):
                c.execute(
                    "INSERT INTO despesas_ceap VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        1,
                        "Tabata Amaral",
                        "PSB",
                        "SP",
                        f"CATEGORIA {n}",
                        "",
                        "ALFA",
                        doc,
                        1,
                        2025,
                        1,
                        "2025-01-01",
                    ),
                )
    r = consultar("categorias", cnpjs=[A, B, C, D])
    assert not r["completo"] and len(r["dados"]) == 200
    assert "resultado parcial" in apresentar_investigacao(json.dumps(r))


def test_esclarecimento_de_periodo_mantem_pergunta_original(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "esclarecer-periodo"}}
    e = ag.invoke({"messages": [("user", "Quais os maiores fornecedores da Tabata Amaral?")]}, config=cfg)
    assert "período" in e["messages"][-1].content
    e = ag.invoke({"messages": [("user", "2024 até 2026")]}, config=cfg)
    ch = [p["args"] for p in trajetoria(e) if p["tipo"] == "decisao"][-1]
    assert ch["anos"] == [2024, 2025, 2026] and ch["nome_deputado"] == "Tabata Amaral"


def test_quadro_atual_nao_vira_historico_societario(dados):
    ag = montar_agente(com_rag=False)
    cfg = {"configurable": {"thread_id": "qsa-historico"}}
    ag.invoke(
        {"messages": [("user", "Qual empresa mais recebeu da Tabata Amaral entre 2024 e 2026?")]}, config=cfg
    )
    e = ag.invoke({"messages": [("user", "Quem eram os sócios na época dos pagamentos?")]}, config=cfg)
    assert "histórico completo de sócios" in e["messages"][-1].content
    assert not [x for x in trajetoria(e) if x["tipo"] == "decisao"]


@pytest.mark.parametrize("filtro", ["ano BETWEEN 2024 AND 2026", "ano IN (2024,2025,2026)"])
def test_fornecedores_apos_ranking_sql_sem_argumento_anos(dados, filtro):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from src.investigation_flow import contexto_investigacao, pedido_investigacao

    mensagens = [
        HumanMessage(content="Qual deputado mais gastou com combustível nos últimos 3 anos?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "ranking",
                    "name": "consultar_ceap",
                    "args": {
                        "query_sql": f"SELECT nome_parlamentar,SUM(valor) AS total FROM despesas_ceap WHERE categoria LIKE '%COMBUST%' AND {filtro} GROUP BY nome_parlamentar ORDER BY total DESC LIMIT 1"
                    },
                }
            ],
        ),
        ToolMessage(
            content="1 registros: [{'nome_parlamentar': 'Tabata Amaral', 'total': 200.0}]",
            name="consultar_ceap",
            tool_call_id="ranking",
        ),
    ]
    quadro = contexto_investigacao(mensagens)
    assert quadro["id_deputado"] == 1
    assert quadro["anos"] == [2024, 2025, 2026]
    assert quadro["categoria"] == "COMBUST"
    mensagens += [
        HumanMessage(content="Quanto ela gastou por ano?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "detalhamento_anual_1",
                    "name": "consultar_ceap",
                    "args": {
                        "query_sql": f"SELECT ano,SUM(valor) AS total FROM despesas_ceap WHERE ({filtro} AND categoria LIKE '%COMBUST%') AND id_deputado=1 GROUP BY ano ORDER BY ano"
                    },
                }
            ],
        ),
        ToolMessage(
            content="2 registros: [{'ano': 2024, 'total': 150.0}, {'ano': 2025, 'total': 50.0}]",
            name="consultar_ceap",
            tool_call_id="detalhamento_anual_1",
        ),
    ]
    quadro = contexto_investigacao(mensagens, quadro)
    pedido = pedido_investigacao("Quais os maiores fornecedores dela?", quadro)
    assert pedido["args"]["id_deputado"] == 1
    assert pedido["args"]["anos"] == [2024, 2025, 2026]
    assert pedido["args"]["categoria"] == "COMBUST"
