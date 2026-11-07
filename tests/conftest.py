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
    monkeypatch.setattr(config, "INDICE_ATO_MESA", indice_teste)
    monkeypatch.setattr(config, "MODO_OFFLINE", True)
