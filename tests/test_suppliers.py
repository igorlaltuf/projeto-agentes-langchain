import json
import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import config, tools
from src.agent import montar_agente, trajetoria
from src.data import consultar_ceap
from src.suppliers import pedido_socios, resposta_socios

NOME = "FAROL CONSULTORIA EM PUBLICIDADE LTDA"
CNPJ = "29813266000135"


@pytest.fixture
def farol(monkeypatch, banco_amostra, tmp_path):
    destino = tmp_path / "fornecedores.db"
    with sqlite3.connect(banco_amostra) as a, sqlite3.connect(destino) as c:
        a.backup(c)
        c.execute("UPDATE despesas_ceap SET fornecedor=?,cnpj_cpf=? WHERE id_deputado=204534", (NOME, CNPJ))
    monkeypatch.setattr(config, "SQLITE_CEAP", destino)
    return destino


def mensagens(linhas=None):
    return [
        HumanMessage(content="Qual empresa mais recebeu em 2025?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "consultar_ceap",
                    "args": {
                        "query_sql": "SELECT fornecedor,SUM(valor) AS total FROM despesas_ceap WHERE ano=2025 GROUP BY fornecedor ORDER BY total DESC LIMIT 1",
                        "anos": [2025],
                    },
                    "id": "ranking",
                }
            ],
        ),
        ToolMessage(
            content=f"1 registros: {linhas or [{'fornecedor': NOME, 'total': 1000}]}",
            tool_call_id="ranking",
            name="consultar_ceap",
        ),
        AIMessage(content=f"A empresa foi {NOME}."),
        HumanMessage(content="Quem são os sócios?"),
    ]


def test_documento_recuperado_por_nome_exato_unico(farol):
    assert pedido_socios(mensagens())["cnpj"] == CNPJ


def test_socios_usa_cnpj_do_ranking_e_preserva_mascara(farol, monkeypatch):
    vistos = []

    def get(url, *a, **kw):
        vistos.append(url)
        return {
            "cnpj": CNPJ,
            "razao_social": NOME,
            "qsa": [
                {
                    "nome_socio": "PESSOA FICTÍCIA",
                    "qualificacao_socio": "Sócio-Administrador",
                    "cnpj_cpf_do_socio": "***123456**",
                    "data_entrada_sociedade": "2020-01-01",
                }
            ],
        }

    monkeypatch.setattr(tools, "_get_json", get)
    estado = montar_agente(com_rag=False).invoke(
        {"messages": mensagens()}, config={"configurable": {"thread_id": "farol"}}
    )
    assert vistos == [f"{config.API_BRASIL}/cnpj/v1/{CNPJ}"]
    resposta = estado["messages"][-1].content
    assert "PESSOA FICTÍCIA" in resposta and "***123456**" in resposta and CNPJ in resposta
    assert "data de cada pagamento" in resposta
    chamadas = [p for p in trajetoria(estado) if p["tipo"] == "decisao"]
    assert chamadas[-1]["args"]["cnpj"] == CNPJ


def test_varias_empresas_pede_escolha(farol):
    linhas = [{"fornecedor": NOME, "cnpj_cpf": CNPJ}, {"fornecedor": "OUTRA", "cnpj_cpf": "33927528000160"}]
    assert "erro" in pedido_socios(mensagens(linhas))


def test_nome_com_documentos_distintos_nao_inventa(farol):
    with sqlite3.connect(farol) as c:
        c.execute(
            "UPDATE despesas_ceap SET cnpj_cpf='33927528000160' WHERE rowid=(SELECT MIN(rowid) FROM despesas_ceap WHERE fornecedor=?)",
            (NOME,),
        )
    assert "erro" in pedido_socios(mensagens())


def test_indisponibilidade_nao_diz_ausencia_socios():
    resposta = resposta_socios({"nome": NOME, "cnpj": CNPJ}, json.dumps({"status": "INDISPONIVEL"}))
    assert "não significa ausência de sócios" in resposta and CNPJ in resposta


def test_resposta_outro_cnpj_nao_atribui_socios():
    resposta = resposta_socios(
        {"nome": NOME, "cnpj": CNPJ},
        json.dumps(
            {
                "status": "CONSULTADO",
                "cnpj": "33927528000160",
                "socios": [{"nome": "OUTRA PESSOA"}],
                "qsa_disponivel": True,
            }
        ),
    )
    assert "OUTRA PESSOA" not in resposta


@pytest.mark.parametrize("qsa,disponivel", [(None, False), ([], True)])
def test_qsa_ausente_diferente_de_vazio(monkeypatch, qsa, disponivel):
    monkeypatch.setattr(tools, "_get_json", lambda *a, **kw: {"cnpj": CNPJ, "razao_social": NOME, "qsa": qsa})
    r = json.loads(tools.dados_fornecedor.invoke({"cnpj": CNPJ}))
    assert r["qsa_disponivel"] == disponivel and r["socios"] == []


def test_sql_periodo_informado_exige_filtro_compativel():
    r = consultar_ceap.invoke(
        {"query_sql": "SELECT SUM(valor) FROM despesas_ceap", "anos": [2024, 2025, 2026]}
    )
    assert "Período inconsistente" in r or "Período ausente" in r
    r = consultar_ceap.invoke(
        {"query_sql": "SELECT SUM(valor) FROM despesas_ceap WHERE ano=2025", "anos": [2024, 2025]}
    )
    assert "Período inconsistente" in r or "Período ausente" in r


def test_agregado_sem_periodo_nao_publica_total():
    r = consultar_ceap.invoke(
        {
            "query_sql": "SELECT fornecedor,SUM(valor) AS total FROM despesas_ceap GROUP BY fornecedor ORDER BY total DESC LIMIT 1"
        }
    )
    assert "Período ausente" in r


def test_cadastro_antigo_de_outra_empresa_nao_substitui_ranking(farol):
    msgs = mensagens()
    msgs.insert(
        -1,
        ToolMessage(
            content=json.dumps(
                {"status": "CONSULTADO", "cnpj": "33927528000160", "razao_social": "OUTRA EMPRESA"}
            ),
            name="dados_fornecedor",
            tool_call_id="cadastro-antigo",
        ),
    )
    assert pedido_socios(msgs)["cnpj"] == CNPJ
