"""Sanções locais: integridade, atualização atômica e ausência limitada ao snapshot."""

import csv
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from src import config, tools
from src.catalog import banco
from src.public_sources import extrair
from src.sanctions import baixar_snapshot, consultar_sancoes_locais, importar_sancoes

EX = Path(__file__).resolve().parents[1] / "examples/sancoes"
CNPJ = "11222333000181"


@pytest.fixture
def sancoes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "banco.db")
    for cad in ("ceis", "cnep"):
        importar_sancoes(EX / f"{cad}.csv", cad, "2026-01-01", "https://fonte.test/" + cad)
    return tmp_path


def gravar(path, cadastro="ceis", **mudancas):
    with (EX / f"{cadastro}.csv").open() as f:
        row = next(csv.DictReader(f, delimiter=";"))
    row.update(mudancas)
    with path.open("w") as f:
        w = csv.DictWriter(f, fieldnames=row, delimiter=";")
        w.writeheader()
        w.writerow(row)
    return path


def test_sancoes_locais_sem_rede_ou_chave(sancoes, monkeypatch):
    monkeypatch.delenv("CGU_API_KEY", raising=False)
    monkeypatch.setattr(config, "MODO_OFFLINE", False)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("Não deve consultar API"))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("Não deve baixar no chat"))
    r = json.loads(tools.consultar_sancoes.invoke({"cnpj": "11.222.333/0001-81"}))
    assert r["status"] == "CONSULTADO"
    assert len(r["dados"]["ceis"]) == len(r["dados"]["cnep"]) == 1
    assert r["cargas"]["ceis"]["data_referencia"] == "2026-01-01"
    assert r["dados"]["cnep"][0]["multa_centavos"] == 10000
    assert "vigência" in r["aviso"]


def test_ambas_cargas_obrigatorias(sancoes):
    with banco() as c:
        c.execute("DELETE FROM cargas_sancoes WHERE cadastro='cnep'")
    r = consultar_sancoes_locais(CNPJ)
    assert r["status"] == "INDISPONIVEL"
    assert "dados" not in r


def test_sem_banco_nao_e_ausencia(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "inexistente.db")
    assert consultar_sancoes_locais(CNPJ)["status"] == "INDISPONIVEL"
    assert not config.SQLITE_CEAP.exists()


@pytest.mark.parametrize("cnpj", ["***222333000181", "11222333000180", "11111111111111", "123", ""])
def test_cnpj_invalido_nao_consulta(cnpj):
    assert consultar_sancoes_locais(cnpj)["status"] == "INVALIDO"


def test_sem_sancao_limitado_a_versao(sancoes):
    r = consultar_sancoes_locais("04252011000110")
    assert r["status"] == "CONSULTADO" and r["dados"] == {"ceis": [], "cnep": []}
    assert "não certifica regularidade" in r["aviso"]


def test_reimportacao_idempotente(sancoes):
    importar_sancoes(EX / "ceis.csv", "ceis", "2026-01-02")
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM sancoes_locais WHERE cadastro='ceis'").fetchone() == (1,)
    assert consultar_sancoes_locais(CNPJ)["cargas"]["ceis"]["data_referencia"] == "2026-01-02"


@pytest.mark.parametrize(
    "campo,valor", [("CADASTRO", "CNEP"), ("DATA INÍCIO SANÇÃO", "32/13/2026"), ("CÓDIGO DA SANÇÃO", "")]
)
def test_falha_preserva_snapshot(sancoes, campo, valor):
    p = gravar(sancoes / "ruim.csv", **{campo: valor})
    with pytest.raises(ValueError):
        importar_sancoes(p, "ceis", "2026-01-02")
    assert consultar_sancoes_locais(CNPJ)["cargas"]["ceis"]["data_referencia"] == "2026-01-01"
    assert len(consultar_sancoes_locais(CNPJ)["dados"]["ceis"]) == 1


def test_documento_mascarado_preservado_sem_associacao(sancoes):
    p = gravar(
        sancoes / "cpf.csv",
        **{"CPF OU CNPJ DO SANCIONADO": "***.222.333-**", "TIPO DE PESSOA": "Pessoa Física"},
    )
    importar_sancoes(p, "ceis", "2026-01-01")
    assert consultar_sancoes_locais(CNPJ)["dados"]["ceis"] == []
    with banco() as c:
        assert c.execute(
            "SELECT documento,documento_valido FROM sancoes_locais WHERE cadastro='ceis'"
        ).fetchone() == ("***222333**", 0)


def test_codigo_duplicado_nao_publica(sancoes):
    p = gravar(sancoes / "dup.csv")
    s = p.read_text()
    p.write_text(s + s.splitlines(keepends=True)[1])
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        importar_sancoes(p, "ceis", "2026-01-02")
    assert consultar_sancoes_locais(CNPJ)["cargas"]["ceis"]["data_referencia"] == "2026-01-01"


def test_extracao_apenas_cadastro(tmp_path):
    p = tmp_path / "fonte.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("20260101_CEIS.csv", "a;b\n1;2")
        z.writestr("leia-me.csv", "x;y")
        z.writestr("CNEP.csv", "x;y")
    assert [p.name for p in extrair(p, tmp_path / "extraidos", "ceis")] == ["20260101_CEIS.csv"]


def test_download_datado_cache_e_404(monkeypatch, tmp_path):
    from src import public_sources

    urls = []

    def baixar(url, p, atualizar=False):
        urls.append(url)
        if len(urls) == 1:
            resposta = httpx.Response(404, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("Ausente", request=resposta.request, response=resposta)
        p.write_bytes(b"zip-simulado")
        return p

    monkeypatch.setattr(public_sources, "baixar", baixar)
    p, url, ref = baixar_snapshot("ceis", tmp_path)
    assert len(urls) == 2 and p.exists() and url.endswith(ref.replace("-", ""))
    assert baixar_snapshot("ceis", tmp_path) == (p, url, ref)
    assert len(urls) == 2


def test_erro_rede_nao_reutiliza_como_atual(monkeypatch, tmp_path):
    from src import public_sources

    monkeypatch.setattr(
        public_sources, "baixar", lambda *a: (_ for _ in ()).throw(httpx.ConnectError("Sem rede"))
    )
    with pytest.raises(httpx.ConnectError):
        baixar_snapshot("ceis", tmp_path, atualizar=True)
    assert not (tmp_path / "snapshot.json").exists()
