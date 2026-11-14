"""Fixtures comuns: tudo roda em MODO_OFFLINE com banco e indice temporarios."""

from __future__ import annotations

import os

os.environ["MODO_OFFLINE"] = "true"
os.environ.pop("OPENAI_API_KEY", None)

from pathlib import Path

import pytest

from src import config

AMOSTRA = Path(__file__).parent / "dados" / "ceap_amostra.csv"


@pytest.fixture(scope="session")
def banco_amostra(tmp_path_factory) -> Path:
    from src.data import construir_sqlite

    db = tmp_path_factory.mktemp("db") / "ceap_teste.db"
    construir_sqlite(AMOSTRA, db)
    return db


@pytest.fixture(scope="session")
def indice_teste(tmp_path_factory) -> Path:
    from src.data import construir_indice

    destino = tmp_path_factory.mktemp("idx") / "ato_mesa"
    construir_indice(config.TEXTO_NORMAS, destino)
    return destino


@pytest.fixture(autouse=True)
def _usa_recursos_de_teste(monkeypatch, banco_amostra, indice_teste):
    monkeypatch.setattr(config, "SQLITE_CEAP", banco_amostra)
    monkeypatch.setattr(config, "DATA_DIR", banco_amostra.parent)
    monkeypatch.setattr(config, "INDICE_ATO_MESA", indice_teste)
    monkeypatch.setattr(config, "MODO_OFFLINE", True)


@pytest.fixture
def banco_homonimos(monkeypatch, banco_amostra, tmp_path):
    import sqlite3

    destino = tmp_path / "homonimos.db"
    with sqlite3.connect(banco_amostra) as original, sqlite3.connect(destino) as copia:
        original.backup(copia)
        for ident, nome in [(900001, "Alex Silva"), (900002, "Luiz Silva")]:
            copia.execute(
                "INSERT INTO despesas_ceap SELECT ?,?,partido,uf,categoria,especificacao,fornecedor,cnpj_cpf,valor,ano,mes,data FROM despesas_ceap LIMIT 1",
                (ident, nome),
            )
    monkeypatch.setattr(config, "SQLITE_CEAP", destino)
    return destino
