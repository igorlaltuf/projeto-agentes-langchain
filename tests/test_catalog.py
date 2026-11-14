import csv
import json
import sqlite3
from pathlib import Path

import pytest

from src import config
from src.catalog import (
    banco,
    centavos,
    importar_emendas,
    importar_tse,
    inicializar_catalogo,
    vincular_identidade,
)
from src.investigation import consultar_dados

EX = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def catalogo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "teste.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    inicializar_catalogo()
    for ano in [2018, 2022, 2026]:
        for tipo in ["candidatos", "bens", "receitas", "despesas"]:
            importar_tse(EX / f"tse/{tipo}_{ano}.csv", tipo, ano, "DEMONSTRACAO")
    importar_emendas(EX / "emendas/emendas.csv", "DEMONSTRACAO")
    from src.sanctions import importar_sancoes

    for cadastro in ("ceis", "cnep"):
        importar_sancoes(EX / f"sancoes/{cadastro}.csv", cadastro, "2026-01-01", "DEMONSTRACAO")
    return tmp_path / "teste.db"


@pytest.mark.parametrize(
    "texto,valor", [("1.234,56", 123456), ("1234.56", 123456), ("-10,50", -1050), ("0,00", 0)]
)
def test_valores_centavos(texto, valor):
    assert centavos(texto) == valor


@pytest.mark.parametrize("texto", ["", "#NULO", "NaN", "abc"])
def test_valor_invalido_nao_vira_zero(texto):
    with pytest.raises(ValueError):
        centavos(texto)


def test_tres_dominios_e_proveniencia(catalogo):
    r = json.loads(
        consultar_dados.invoke(
            {
                "dominio": "emendas",
                "query_sql": "SELECT SUM(pago) AS emenda_paga FROM v_emendas WHERE ano=2026",
            }
        )
    )
    assert r["status"] == "CONSULTADO" and r["dados"][0]["emenda_paga"] == 1500
    assert r["evidencia"]["completo"] and r["evidencia"]["fontes"][0]["sha256"]
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM candidaturas").fetchone()[0] == 3
        assert c.execute("SELECT SUM(valor_centavos) FROM despesas_eleitorais").fetchone()[0] == 75150
        assert c.execute("SELECT COUNT(*) FROM registros_origem").fetchone()[0] == 17


def test_recarga_nao_duplica_e_preserva_outras_eleicoes(catalogo):
    importar_tse(EX / "tse/bens_2022.csv", "bens", 2022)
    importar_emendas(EX / "emendas/emendas.csv")
    with banco() as c:
        assert c.execute("SELECT SUM(valor_centavos) FROM bens_declarados").fetchone()[0] == 3000000
        assert c.execute("SELECT COUNT(*) FROM emendas").fetchone()[0] == 3


def test_falha_restabelece_carga_anterior(catalogo, tmp_path):
    arquivo = tmp_path / "ruim.csv"
    arquivo.write_text((EX / "emendas/emendas.csv").read_text().replace("500,00", "ERRADO", 1))
    with pytest.raises(ValueError):
        importar_emendas(arquivo)
    with banco() as c:
        assert c.execute("SELECT SUM(pago_centavos) FROM execucoes_emenda").fetchone()[0] == 150000


def test_erros_tse_preservam_particao(catalogo, tmp_path):
    arquivo = tmp_path / "ruim.csv"
    arquivo.write_text((EX / "tse/bens_2022.csv").read_text().replace("10.000,00", "ERRADO"))
    with pytest.raises(ValueError):
        importar_tse(arquivo, "bens", 2022)
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM bens_declarados WHERE eleicao=2022").fetchone()[0] == 1


def test_nome_igual_nao_vincula_e_vinculo_documental_sobrevive_recarga(catalogo):
    with banco() as c:
        c.execute("INSERT INTO pessoas VALUES(1,'Pessoa de Demonstração')")
        assert c.execute("SELECT pessoa_id FROM candidaturas LIMIT 1").fetchone()[0] is None
    vincular_identidade(1, "TSE", "2022:900001", "Correspondência documental testada")
    importar_tse(EX / "tse/candidatos_2022.csv", "candidatos", 2022)
    with banco() as c:
        assert c.execute("SELECT pessoa_id FROM candidaturas WHERE id='2022:900001'").fetchone()[0] == 1
    with pytest.raises(ValueError, match="coletiva"):
        vincular_identidade(1, "CGU", "900002", "Documento")
    with banco() as c:
        assert not c.execute("SELECT 1 FROM identificadores_externos WHERE fonte='CGU'").fetchone()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM candidaturas",
        "SELECT * FROM candidaturas",
        "SELECT * FROM registros_origem",
        "SELECT load_extension('x') FROM v_bens",
        "SELECT * FROM v_bens; DROP TABLE pessoas",
    ],
)
def test_sql_so_le_views(catalogo, sql):
    r = json.loads(consultar_dados.invoke({"dominio": "eleitoral", "query_sql": sql}))
    assert r["status"] in {"INVALIDO", "ERRO", "INDISPONIVEL"}


def test_ano_ausente_nao_e_zero(catalogo):
    r = json.loads(
        consultar_dados.invoke(
            {"dominio": "emendas", "query_sql": "SELECT SUM(pago) FROM v_emendas WHERE ano=2024"}
        )
    )
    assert r["status"] == "INDISPONIVEL"


def test_cruzamento_agrega_antes_de_join(catalogo):
    r = json.loads(
        consultar_dados.invoke(
            {
                "dominio": "cruzamento",
                "query_sql": "WITH despesas AS (SELECT contraparte_documento,SUM(valor) AS total FROM v_despesas_eleitorais GROUP BY contraparte_documento) SELECT * FROM despesas",
            }
        )
    )
    assert r["dados"][0]["total"] == 751.5


def test_recurso_uf_nao_duplica_nacional(tmp_path):
    import zipfile

    from src.public_sources import extrair

    zip_path = tmp_path / "teste.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("consulta_cand_2022_BRASIL.csv", "A;B\n1;2")
        z.writestr("consulta_cand_2022_SP.csv", "A;B\n1;2")
    assert len(extrair(zip_path, tmp_path / "extraidos", "candidatos")) == 1


def test_multiplas_localidades_na_mesma_emenda(catalogo, tmp_path):
    with (EX / "emendas/emendas.csv").open() as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    rows = [rows[0].copy(), rows[0].copy()]
    rows[0]["Localidade de aplicação do recurso"] = "A"
    rows[1]["Localidade de aplicação do recurso"] = "B"
    arquivo = tmp_path / "municipios.csv"
    with arquivo.open("w") as f:
        w = csv.DictWriter(f, fieldnames=rows[0], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    importar_emendas(arquivo)
    with banco() as c:
        assert c.execute("SELECT localidade,pago FROM v_emendas ORDER BY localidade").fetchall() == [
            ("A", 500.0),
            ("B", 500.0),
        ]


def test_agente_eleitoral_cnpj_sancoes_preserva_contexto(catalogo):
    from src.agent import montar_agente, trajetoria

    agente = montar_agente(com_rag=False, escopo_ampliado=True)
    cfg = {"configurable": {"thread_id": "tse-cnpj-cgu"}}
    for pergunta, ferramenta in [
        ("Quais despesas de campanha em 2026?", "consultar_dados"),
        ("Quem é esse fornecedor?", "dados_fornecedor"),
        ("Ele tem sanções?", "consultar_sancoes"),
    ]:
        estado = agente.invoke({"messages": [("user", pergunta)]}, config=cfg)
        assert [p["ferramenta"] for p in trajetoria(estado) if p["tipo"] == "decisao"] == [ferramenta]
    assert "DEMONSTRACAO" in estado["messages"][-1].content


def test_checagem_novo_dominio_utiliza_evidencia(catalogo):
    from src.verification import verificar_turno

    conteudo = consultar_dados.invoke(
        {"dominio": "emendas", "query_sql": "SELECT SUM(pago) AS emenda_paga FROM v_emendas WHERE ano=2026"}
    )
    traj = [{"tipo": "retorno", "ferramenta": "consultar_dados", "conteudo": conteudo}]
    resultado = verificar_turno("O valor de emenda paga foi R$ 1.500,00.", traj)
    assert any(r.get("metric") == "emenda_paga" for r in resultado["truth"].values())
    assert resultado["fact_check"][0]["status"] == "CONFIRMADO"


def test_backup_preserva_base_e_migracao_e_aditiva(monkeypatch, tmp_path):
    from scripts.load_sources import backup, migrar_ceap
    from src.data import construir_sqlite

    db = tmp_path / "ceap.db"
    monkeypatch.setattr(config, "SQLITE_CEAP", db)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    construir_sqlite(config.RAIZ / "tests/dados/ceap_amostra.csv", db)
    backup()
    inicializar_catalogo()
    migrar_ceap()
    with sqlite3.connect(db) as c, sqlite3.connect(tmp_path / "backups/antes-apuramandato.db") as antigo:
        assert (
            c.execute("SELECT COUNT(*) FROM despesas_ceap").fetchone()
            == antigo.execute("SELECT COUNT(*) FROM despesas_ceap").fetchone()
        )
        assert c.execute("SELECT valor_centavos FROM v_despesas_ceap LIMIT 1").fetchone()[0] is not None


def test_importacao_rejeita_linha_duplicada(catalogo, tmp_path):
    arquivo = tmp_path / "duplicado.csv"
    texto = (EX / "emendas/emendas.csv").read_text().splitlines()
    arquivo.write_text("\n".join(texto + [texto[1]]) + "\n")
    with pytest.raises(ValueError, match="duplicada"):
        importar_emendas(arquivo)
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM execucoes_emenda").fetchone()[0] == 3


def test_pedido_estruturado_ampliado():
    from src.chains import chain_investigacao

    pedido = chain_investigacao().invoke({"pergunta": "Quais emendas em 2022?"})
    assert pedido.dominio == "emendas"
    assert pedido.anos == [2022]
    assert pedido.pergunta_esclarecimento


def test_ferramenta_legada_nao_contorna_restricao_das_views(catalogo):
    from src.data import consultar_ceap

    resposta = consultar_ceap.invoke({"query_sql": "SELECT * FROM candidaturas"})
    assert "Erro SQL" in resposta
