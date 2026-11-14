from src import config
from src.periods import anos_relativos


def test_intervalo_inclui_corrente_e_aceita_extenso(monkeypatch):
    monkeypatch.setattr(config, "HOJE", "2026-11-12")
    assert anos_relativos("nos últimos 5 anos") == [2022, 2023, 2024, 2025, 2026]
    assert anos_relativos("nos ultimos cinco anos") == [2022, 2023, 2024, 2025, 2026]
    assert anos_relativos("últimos 5 anos completos") == [2021, 2022, 2023, 2024, 2025]
    assert anos_relativos("em 2025") is None


def test_agente_consulta_intervalo_completo(monkeypatch, tmp_path):
    from pathlib import Path

    import pandas as pd

    from src.agent import montar_agente, trajetoria
    from src.data import construir_sqlite

    monkeypatch.setattr(config, "HOJE", "2026-11-12")
    db = tmp_path / "ceap.db"
    monkeypatch.setattr(config, "SQLITE_CEAP", db)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    df = pd.read_csv(Path(__file__).parent / "dados/ceap_amostra.csv", sep=";", dtype=str)
    for ano in range(2022, 2027):
        df["numAno"] = str(ano)
        csv = tmp_path / f"ceap_{ano}.csv"
        df.to_csv(csv, sep=";", index=False)
        construir_sqlite(csv, db)
    ag = montar_agente(com_rag=False)
    estado = ag.invoke(
        {"messages": [("user", "Quanto a Tabata Amaral gastou com combustível nos últimos 5 anos?")]},
        config={"configurable": {"thread_id": "cinco-anos"}},
    )
    consultas = [
        p for p in trajetoria(estado) if p.get("ferramenta") == "consultar_ceap" and p["tipo"] == "decisao"
    ]
    assert "BETWEEN 2022 AND 2026" in consultas[0]["args"]["query_sql"]
    assert consultas[0]["args"]["anos"] == [2022, 2023, 2024, 2025, 2026]
    resposta = estado["messages"][-1].content
    assert "2022 a 2026" in resposta and "parcial" in resposta
    assert "244.044,15" in resposta  # cinco vezes o combustível da amostra
