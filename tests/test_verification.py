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
