from src.verification import (
    ExtractedClaim,
    checar_claims,
    consultas_da_trajetoria,
    fonte_autoritativa,
    verificar_turno,
)

TRAJ = [
    {"tipo": "decisao", "ferramenta": "buscar_deputado", "args": {"nome": "Tabata Amaral"}},
    {
        "tipo": "decisao",
        "ferramenta": "listar_despesas",
        "args": {"id_deputado": 204534, "ano": 2025, "categoria": "COMBUST"},
    },
]


def test_checar_claims_tolerancia():
    truth = {"gasto_total": {"value": 100.0, "tolerance": 0.01}}
    claims = [
        ExtractedClaim(
            claim_text="R$ 100,00", canonical_key="gasto_total", claimed_value=100.005, unit="BRL"
        ),
        ExtractedClaim(claim_text="R$ 120,00", canonical_key="gasto_total", claimed_value=120, unit="BRL"),
        ExtractedClaim(claim_text="Art. 4", canonical_key="artigo_citado", claimed_value=4, unit="artigo"),
    ]
    status = [c["status"] for c in checar_claims(claims, truth)]
    assert status == ["CONFIRMADO", "CONTRADITORIO", "NAO VERIFICADO"]


def test_consultas_da_trajetoria_e_fonte_autoritativa():
    consultas = consultas_da_trajetoria(TRAJ)
    assert set(consultas) == {"gasto_categoria"}
    truth = fonte_autoritativa(consultas)
    assert truth["gasto_categoria"]["value"] == 48808.83


def test_fonte_autoritativa_sql_invalido_nao_quebra():
    truth = fonte_autoritativa({"x": "SELECT nada FROM nada"})
    assert truth["x"]["value"] is None and "erro" in truth["x"]


def test_verificar_turno_confirma_e_contradiz():
    ok = verificar_turno("Tabata Amaral gastou R$ 48.808,83 com combustível em 2025.", TRAJ)
    assert ok["fact_check"][0]["status"] == "CONFIRMADO"
    ruim = verificar_turno("Tabata Amaral gastou R$ 99.000,00 com combustível em 2025.", TRAJ)
    assert ruim["fact_check"][0]["status"] == "CONTRADITORIO"
    assert ruim["nli"]["nli_verdict"] in {"APOIA", "REFUTA", "NEUTRA"}


def test_ranking_partidos_e_deputados(monkeypatch, tmp_path):
    import sqlite3

    from src import config

    db = tmp_path / "ranking.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE despesas_ceap(partido TEXT, nome_parlamentar TEXT, valor REAL)")
        conn.executemany(
            "INSERT INTO despesas_ceap VALUES (?, ?, ?)",
            [("PRD", "José Airton", 54017.81), ("UNIÃO", "Professor Alcides", 52795.05)],
        )
    monkeypatch.setattr(config, "SQLITE_CEAP", db)
    traj = [
        {"tipo": "decisao", "ferramenta": "consultar_ceap", "args": {"query_sql": sql}}
        for sql in [
            "SELECT partido, AVG(valor) AS media FROM despesas_ceap GROUP BY partido",
            "SELECT nome_parlamentar, SUM(valor) AS total FROM despesas_ceap GROUP BY nome_parlamentar",
        ]
    ]
    truth = fonte_autoritativa(consultas_da_trajetoria(traj))
    claims = [
        ExtractedClaim(claim_text=texto, canonical_key=chave, claimed_value=valor, unit="BRL")
        for texto, chave, valor in [
            ("PRD: R$ 54.017,81", "media_partido", 54017.81),
            ("UNIÃO: R$ 52.795,05", "media_partido", 52795.05),
            ("José Airton: R$ 54.017,81", "gasto_total", 54017.81),
            ("Professor Alcides: R$ 99.000,00", "gasto_total", 99000),
            ("PSDB: R$ 54.017,81", "media_partido", 54017.81),
        ]
    ]
    resultados = checar_claims(claims, truth)
    assert [r["status"] for r in resultados] == [
        "CONFIRMADO",
        "CONFIRMADO",
        "CONFIRMADO",
        "CONTRADITORIO",
        "NAO VERIFICADO",
    ]
    assert resultados[3]["oficial"] == 52795.05


def test_consultas_repetidas_nao_sobrescrevem():
    traj = [
        {"tipo": "decisao", "ferramenta": "consultar_ceap", "args": {"query_sql": sql}}
        for sql in [
            "SELECT AVG(valor) FROM despesas_ceap WHERE partido='PRD'",
            "SELECT AVG(valor) FROM despesas_ceap WHERE partido='PT'",
        ]
    ]
    assert len(consultas_da_trajetoria(traj)) == 2


def test_falha_na_fonte_nao_acusa_erro_de_calculo():
    from src.verification import verificar_resposta

    truth = fonte_autoritativa({"gasto_total": "SELECT nada FROM nada"})
    resultado = verificar_resposta("O total gastou R$ 100,00.", truth)
    assert resultado["fact_check"][0]["status"] == "NAO VERIFICADO"
    assert resultado["nli"]["nli_verdict"] == "NEUTRA"
    assert resultado["nli"]["detected_fallacies"] == ["Nenhuma"]


def test_referencia_ambigua_nao_escolhe_pelo_valor():
    truth = {
        "gasto_total": {"value": 100, "tolerance": 0.01},
        "gasto_total#1": {"value": 200, "tolerance": 0.01},
    }
    claim = ExtractedClaim(
        claim_text="Total R$ 100,00", canonical_key="gasto_total", claimed_value=100, unit="BRL"
    )
    assert checar_claims([claim], truth)[0]["status"] == "NAO VERIFICADO"


def test_2026_agora_e_consultado_no_csv():
    traj = [
        {"tipo": "decisao", "ferramenta": "listar_despesas", "args": {"id_deputado": 204534, "ano": 2026}}
    ]
    assert "ano=2026" in consultas_da_trajetoria(traj)["gasto_total"]
