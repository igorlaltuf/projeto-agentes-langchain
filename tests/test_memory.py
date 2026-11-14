from src.memory import gravar_apuracao, lembrar_apuracao


def test_memoria_episodica_filtra_por_reporter():
    gravar_apuracao.invoke({"resumo": "Tabata gastou 48 mil com combustivel em 2025", "reporter_id": "ana"})
    gravar_apuracao.invoke({"resumo": "Nikolas gastou 150 mil com locacao de veiculos", "reporter_id": "bia"})
    ana = lembrar_apuracao.invoke({"consulta": "combustivel", "reporter_id": "ana"})
    assert "Tabata" in ana and "Nikolas" not in ana
    assert (
        lembrar_apuracao.invoke({"consulta": "x", "reporter_id": "ninguem"})
        == "Nenhuma apuracao anterior encontrada."
    )


def test_memoria_persistida_pode_ser_reaberta():
    from src.memory import _banco_memorias

    gravar_apuracao.invoke({"resumo": "Achado persistente", "reporter_id": "persistencia"})
    with _banco_memorias() as conn:
        assert (
            conn.execute("SELECT resumo FROM apuracoes WHERE reporter=?", ("persistencia",)).fetchone()[0]
            == "Achado persistente"
        )
    assert "Achado persistente" in lembrar_apuracao.invoke(
        {"consulta": "Achado", "reporter_id": "persistencia"}
    )
