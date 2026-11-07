import json

import pytest

from src.tools import (
    buscar_deputado,
    calcular_estatisticas,
    dados_fornecedor,
    legislatura_do_ano,
    listar_despesas,
)


def test_calculadora_soma_e_media():
    assert calcular_estatisticas.invoke({"valores": [1, 2, 3], "operacao": "soma"}) == "soma = 6.0"
    assert calcular_estatisticas.invoke({"valores": [1, 2, 3], "operacao": "media"}) == "media = 2"


def test_calculadora_variacao_percentual():
    assert (
        calcular_estatisticas.invoke({"valores": [100, 150], "operacao": "variacao_pct"})
        == "variacao_pct = 50.0"
    )
    assert "Erro" in calcular_estatisticas.invoke({"valores": [0, 1], "operacao": "variacao_pct"})
    assert "Erro" in calcular_estatisticas.invoke({"valores": [], "operacao": "soma"})


@pytest.mark.parametrize("ano,leg", [(1999, 51), (2022, 56), (2023, 57), (2025, 57), (2026, 57), (2027, 58)])
def test_legislatura_do_ano(ano, leg):
    assert legislatura_do_ano(ano) == leg


def test_buscar_deputado_usa_fixture_offline():
    saida = buscar_deputado.invoke({"nome": "Tabata Amaral", "uf": "SP"})
    assert saida == "id=204534 | Tabata Amaral | PSB-SP"


def test_buscar_deputado_homonimos():
    saida = buscar_deputado.invoke({"nome": "Silva", "uf": None})
    assert saida.count("id=") >= 2


def test_listar_despesas_pagina_e_agrupa():
    r = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2025}))
    assert r["total_registros"] == 496  # mais de uma pagina de 100 itens
    assert r["total_valor"] == 530231.55
    assert "COMBUSTÍVEIS E LUBRIFICANTES." in r["total_por_tipo"]


def test_listar_despesas_filtra_categoria():
    r = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2025, "categoria": "combust"}))
    assert r["total_valor"] == 48808.83


def test_fixture_ausente_em_modo_offline_da_erro_claro():
    with pytest.raises(FileNotFoundError, match="Modo offline"):
        listar_despesas.invoke({"id_deputado": 1, "ano": 2019})


def test_cnpj_invalido():
    assert "invalido" in dados_fornecedor.invoke({"cnpj": "123"})


def test_dados_fornecedor_offline():
    d = json.loads(dados_fornecedor.invoke({"cnpj": "33.927.528/0001-60"}))
    assert d["razao_social"] == "SJR IMOVEIS LTDA"
