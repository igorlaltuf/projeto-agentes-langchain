import json

import pytest

from src.tools import (
    _listar_despesas_api,
    buscar_deputado,
    calcular_estatisticas,
    dados_fornecedor,
    legislatura_do_ano,
    listar_despesas,
)


def test_calculadora_soma_e_media():
    assert calcular_estatisticas.invoke({"valores": [1, 2, 3], "operacao": "soma"}) == "soma = 6.0"
    assert calcular_estatisticas.invoke({"valores": [1, 2, 3], "operacao": "media"}) == "media = 2.0"


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


def test_buscar_deputado_homonimos(banco_homonimos):
    saida = buscar_deputado.invoke({"nome": "Silva", "uf": None})
    assert saida.count("id=") >= 2


def test_listar_despesas_pagina_e_agrupa():
    r = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2025}))
    assert r["total_registros"] > 0  #  # mais de uma pagina de 100 itens
    assert r["total_valor"] == 504994.08
    assert "COMBUSTÍVEIS E LUBRIFICANTES." in r["total_por_tipo"]


def test_listar_despesas_filtra_categoria():
    r = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2025, "categoria": "combust"}))
    assert r["total_valor"] == 48808.83


def test_fixture_ausente_em_modo_offline_da_erro_claro():
    with pytest.raises(FileNotFoundError, match="Modo offline"):
        _listar_despesas_api(1, 2019)


def test_cnpj_invalido():
    assert "invalido" in dados_fornecedor.invoke({"cnpj": "123"})


def test_dados_fornecedor_offline():
    d = json.loads(dados_fornecedor.invoke({"cnpj": "33.927.528/0001-60"}))
    assert d["razao_social"] == "SJR IMOVEIS LTDA"


def test_normalizar_categoria_aceita_sinonimos_singular_e_acentos():
    from src.tools import normalizar_categoria

    assert normalizar_categoria("combustível") == "COMBUST"
    assert normalizar_categoria("Combustíveis e lubrificantes") == "COMBUST"
    assert normalizar_categoria("passagens aéreas") == "PASSAGEM"
    assert normalizar_categoria("hotel") == "HOSPED"
    assert normalizar_categoria("aluguel de carro") == "FRETAMENTO DE VE"
    assert normalizar_categoria(None) is None
    # sem correspondencia: devolve o texto em maiusculas sem acento
    assert normalizar_categoria("ração") == "RACAO"


def test_listar_despesas_filtra_categoria_em_linguagem_natural():
    import json

    from src.tools import listar_despesas

    plural = json.loads(
        listar_despesas.invoke({"id_deputado": 204534, "ano": 2025, "categoria": "COMBUSTÍVEIS"})
    )
    singular = json.loads(
        listar_despesas.invoke({"id_deputado": 204534, "ano": 2025, "categoria": "combustível"})
    )
    assert plural["total_registros"] > 0
    assert singular["total_registros"] == plural["total_registros"]


def test_cache_pode_ser_ignorado_para_http_real(monkeypatch, tmp_path):
    from src import config, tools

    url = "https://exemplo.test/api"
    arquivo = tmp_path / f"{tools._chave_fixture(url, None)}.json"
    arquivo.write_text('{"dados": ["cache"]}')
    monkeypatch.setattr(config, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(config, "MODO_OFFLINE", False)
    monkeypatch.setattr(config, "USAR_CACHE_API", False)

    class Resposta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"dados": ["HTTP"]}

    monkeypatch.setattr(tools.httpx, "get", lambda *a, **kw: Resposta())
    assert tools._get_json(url)["dados"] == ["HTTP"]


def test_paginacao_ultrapassa_antigo_limite(monkeypatch):
    from src import tools

    paginas = []

    def resposta(url, params):
        pagina = params["pagina"]
        paginas.append(pagina)
        return {
            "dados": [{"valorLiquido": 10, "nomeFornecedor": str(pagina), "tipoDespesa": "TESTE"}],
            "links": [{"rel": "next"}] if pagina < 15 else [],
        }

    monkeypatch.setattr(tools, "_get_json", resposta)
    resultado = json.loads(_listar_despesas_api(1, 2024))
    assert paginas == list(range(1, 16))
    assert resultado["total_valor"] == 150
    assert resultado["paginas_consultadas"] == 15
    assert resultado["consulta_completa"] is True


def test_pagina_repetida_nao_publica_total_parcial(monkeypatch):
    from src import tools

    monkeypatch.setattr(
        tools, "_get_json", lambda *a: {"dados": [{"valorLiquido": 10}], "links": [{"rel": "next"}]}
    )
    with pytest.raises(RuntimeError, match="consulta incompleta"):
        _listar_despesas_api(1, 2024)


def test_falha_em_pagina_intermediaria_nao_publica_total(monkeypatch):
    from src import tools

    def resposta(url, params):
        if params["pagina"] == 2:
            raise RuntimeError("API indisponível")
        return {"dados": [{"valorLiquido": 10}], "links": [{"rel": "next"}]}

    monkeypatch.setattr(tools, "_get_json", resposta)
    with pytest.raises(RuntimeError, match="API indisponível"):
        _listar_despesas_api(1, 2024)


def test_despesa_historica_nunca_chama_api(monkeypatch):
    from src import tools

    def proibido(*a, **kw):
        raise AssertionError("Consulta histórica não deve chamar HTTP")

    monkeypatch.setattr(tools, "_get_json", proibido)
    resultado = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2025}))
    assert resultado["fonte"] == "Cota para o Exercício da Atividade Parlamentar (CEAP)"
    assert resultado["total_valor"] == 504994.08


def test_despesa_2026_exige_banco_sem_trocar_para_api(monkeypatch):
    from src import tools

    monkeypatch.setattr(tools, "_get_json", lambda *a, **kw: pytest.fail("Não usar API de despesas"))
    retorno = json.loads(listar_despesas.invoke({"id_deputado": 204534, "ano": 2026}))
    assert retorno["status"] == "INDISPONIVEL"
    assert "não carregados" in retorno["erro"]


def test_nome_historico_e_resolvido_sem_api(monkeypatch):
    from src import tools

    monkeypatch.setattr(tools, "_get_json", lambda *a, **kw: pytest.fail("Busca deveria usar o banco"))
    resultado = buscar_deputado.invoke({"nome": "Tabata Amaral", "ano": 2025})
    assert "id=204534" in resultado and "PSB-SP" in resultado
