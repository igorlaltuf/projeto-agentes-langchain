from src.chains import PedidoApuracao, pedido_ou_esclarecimento


def test_ano_completo_nao_exige_mes():
    pedido = PedidoApuracao(
        intencao="consultar_gasto", deputado="Tabata Amaral", ano=2025, dado_faltante="mes"
    )
    assert pedido.dado_faltante is None
    resultado = pedido_ou_esclarecimento.invoke("Quanto a Tabata Amaral gastou em 2025?")
    assert isinstance(resultado, PedidoApuracao)
    assert resultado.dado_faltante is None


def test_comparacao_2026_usa_csv_nos_dois_lados(monkeypatch, tmp_path):
    from pathlib import Path

    import pandas as pd

    from src import config, tools
    from src.chains import chain_paralela
    from src.data import construir_sqlite

    df = pd.read_csv(Path(__file__).parent / "dados/ceap_amostra.csv", sep=";", dtype=str)
    df["numAno"] = "2026"
    csv = tmp_path / "2026.csv"
    df.to_csv(csv, sep=";", index=False)
    db = tmp_path / "ceap.db"
    construir_sqlite(csv, db)
    monkeypatch.setattr(config, "SQLITE_CEAP", db)

    def proibido(*args, **kwargs):
        raise AssertionError("Comparação deve usar exclusivamente o banco")

    monkeypatch.setattr(tools, "_get_json", proibido)
    resultado = chain_paralela().invoke(
        "Compare o gasto da Tabata Amaral com combustível com a média do PSB em 2026"
    )
    assert "Cota para o Exercício da Atividade Parlamentar (CEAP)" in resultado["despesas"]
    assert "40954.73" in resultado["media_partido"]  # média da amostra versionada
