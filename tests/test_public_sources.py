import zipfile

import httpx
import pytest

from src.public_sources import extrair, recurso_tse


@pytest.mark.parametrize("ano", [2018, 2022, 2026])
@pytest.mark.parametrize("tipo", ["receitas", "despesas"])
def test_contas_tse_no_zip_conjunto(monkeypatch, ano, tipo):
    url = f"https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/prestacao_de_contas_eleitorais_candidatos_{ano}.zip"

    def resposta(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "count": 1,
                    "results": [{"title": f"Prestação de contas {ano}", "resources": [{"url": url}]}],
                },
            },
            request=httpx.Request("GET", "https://example.test"),
        )

    monkeypatch.setattr(httpx, "get", resposta)
    assert recurso_tse(ano, tipo) == url


def test_emendas_so_arquivo_principal(tmp_path):
    arquivo = tmp_path / "fonte.zip"
    with zipfile.ZipFile(arquivo, "w") as z:
        for nome in [
            "EmendasParlamentares.csv",
            "EmendasParlamentares_Convenios.csv",
            "EmendasParlamentares_PorFavorecido.csv",
        ]:
            z.writestr(nome, "a;b\n1;2")
    resultado = extrair(arquivo, tmp_path / "extraidos", "emendas")
    assert [p.name for p in resultado] == ["EmendasParlamentares.csv"]


def test_zip_contas_filtra_familia_e_ignora_duplicacao_uf(tmp_path):
    arquivo = tmp_path / "fonte.zip"
    with zipfile.ZipFile(arquivo, "w") as z:
        for nome in [
            "receitas_candidatos_2022_BRASIL.csv",
            "receitas_candidatos_2022_SP.csv",
            "receitas_candidatos_doador_originario_2022_BRASIL.csv",
            "despesas_contratadas_candidatos_2022_BRASIL.csv",
            "despesas_pagas_candidatos_2022_BRASIL.csv",
        ]:
            z.writestr(nome, "a;b\n1;2")
    receitas = extrair(arquivo, tmp_path / "r", "receitas")
    assert [p.name for p in receitas] == ["receitas_candidatos_2022_BRASIL.csv"]
    despesas = extrair(arquivo, tmp_path / "d", "despesas")
    assert [p.name for p in despesas] == ["despesas_contratadas_candidatos_2022_BRASIL.csv"]


def test_codigo_ausente_em_anos_diferentes_preserva_registros(monkeypatch, tmp_path):
    import csv
    from pathlib import Path

    from src import config
    from src.catalog import banco, importar_emendas

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    rows = []
    with (Path(__file__).resolve().parents[1] / "examples/emendas/emendas.csv").open() as f:
        row = next(csv.DictReader(f, delimiter=";"))
    for ano in [2014, 2015]:
        r = dict(row)
        r.update(
            {
                "Código da Emenda": "Sem informação",
                "Código do Autor da Emenda": "S/I",
                "Nome do Autor da Emenda": "Sem informação",
                "Ano da Emenda": str(ano),
            }
        )
        rows.append(r)
    arquivo = tmp_path / "sem-codigo.csv"
    with arquivo.open("w") as f:
        w = csv.DictWriter(f, fieldnames=rows[0], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    importar_emendas(arquivo)
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM emendas").fetchone()[0] == 2
        assert c.execute("SELECT SUM(pago_centavos) FROM execucoes_emenda").fetchone()[0] == 100000


def test_mesma_emenda_com_tipos_de_execucao_distintos(monkeypatch, tmp_path):
    import csv
    from pathlib import Path

    from src import config
    from src.catalog import banco, importar_emendas

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    with (Path(__file__).resolve().parents[1] / "examples/emendas/emendas.csv").open() as f:
        row = next(csv.DictReader(f, delimiter=";"))
    rows = [dict(row), dict(row)]
    rows[0]["Tipo de Emenda"] = "Emenda Individual - Transferências com Finalidade Definida"
    rows[1]["Tipo de Emenda"] = "Emenda Individual - Transferências Especiais"
    arquivo = tmp_path / "tipos.csv"
    with arquivo.open("w") as f:
        w = csv.DictWriter(f, fieldnames=rows[0], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    importar_emendas(arquivo)
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM emendas").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(DISTINCT tipo) FROM v_emendas").fetchone()[0] == 2
        assert c.execute("SELECT SUM(pago) FROM v_emendas").fetchone()[0] == 1000


@pytest.mark.parametrize("tipo", ["receitas", "despesas"])
@pytest.mark.parametrize("ano", [2018, 2022, 2026])
def test_contas_aceitam_aa_eleicao_sem_assumir_ano(monkeypatch, tmp_path, tipo, ano):
    import csv
    from pathlib import Path

    from src import config
    from src.catalog import banco, importar_tse

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    base = Path(__file__).resolve().parents[1] / "examples/tse"
    importar_tse(base / f"candidatos_{ano}.csv", "candidatos", ano)
    with (base / f"{tipo}_{ano}.csv").open() as f:
        row = next(csv.DictReader(f, delimiter=";"))
    row["AA_ELEICAO"] = row.pop("ANO_ELEICAO")
    arquivo = tmp_path / "contas.csv"
    with arquivo.open("w") as f:
        w = csv.DictWriter(f, fieldnames=row, delimiter=";")
        w.writeheader()
        w.writerow(row)
    resultado = importar_tse(arquivo, tipo, ano)
    assert resultado["registros"] == 1
    tabela = "receitas_eleitorais" if tipo == "receitas" else "despesas_eleitorais"
    with banco() as c:
        assert c.execute(f"SELECT eleicao FROM {tabela}").fetchone()[0] == ano
    with pytest.raises(ValueError, match="difere"):
        importar_tse(arquivo, tipo, 2022 if ano != 2022 else 2018)


def test_duas_colunas_de_ano_conflitantes_rejeitadas(monkeypatch, tmp_path):
    from src import config
    from src.catalog import importar_tse

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    arquivo = tmp_path / "conflito.csv"
    arquivo.write_text("ANO_ELEICAO;AA_ELEICAO;SQ_CANDIDATO;VR_RECEITA\n2018;2022;1;1,00\n")
    with pytest.raises(ValueError, match="conflitantes"):
        importar_tse(arquivo, "receitas", 2018)


def test_mesma_receita_pode_conter_itens_distintos(monkeypatch, tmp_path):
    import csv
    from pathlib import Path

    from src import config
    from src.catalog import banco, importar_tse

    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    base = Path(__file__).resolve().parents[1] / "examples/tse"
    importar_tse(base / "candidatos_2018.csv", "candidatos", 2018)
    with (base / "receitas_2018.csv").open() as f:
        row = next(csv.DictReader(f, delimiter=";"))
    row["AA_ELEICAO"] = row.pop("ANO_ELEICAO")
    rows = []
    for item in ["COMPUTADOR", "IMPRESSORA"]:
        r = dict(row)
        r["DS_RECEITA"] = item
        rows.append(r)
    arquivo = tmp_path / "itens.csv"
    with arquivo.open("w") as f:
        w = csv.DictWriter(f, fieldnames=rows[0], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    importar_tse(arquivo, "receitas", 2018)
    importar_tse(arquivo, "receitas", 2018)
    with banco() as c:
        assert c.execute("SELECT COUNT(*),SUM(valor_centavos) FROM receitas_eleitorais").fetchone() == (
            2,
            200000,
        )
        assert c.execute("SELECT COUNT(DISTINCT linha_origem) FROM receitas_eleitorais").fetchone()[0] == 2
