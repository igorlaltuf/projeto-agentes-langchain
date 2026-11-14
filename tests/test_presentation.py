import pytest

from src.presentation import CEAP, apresentar_fontes


@pytest.mark.parametrize(
    "fonte", ["CSV CEAP (SQLite local)", "CSV CEAP (banco local)", "CSV CEAP", "CEAP (SQLite local)"]
)
def test_citacao_publica_preserva_periodo_valor(fonte):
    assert apresentar_fontes(f"R$ 123,45. Fonte: {fonte}, 2020.") == f"R$ 123,45. Fonte: {CEAP}, 2020."


def test_formatacao_idempotente():
    texto = f"Fonte: {CEAP}, 2022 até 2026."
    assert apresentar_fontes(apresentar_fontes(texto)) == texto
