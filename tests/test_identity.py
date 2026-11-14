"""Conflitos, máscaras, revisão e publicação atômica de favorecidos."""

import csv
import json

import pytest

from src import config
from src.catalog import banco, importar_favorecidos, inicializar_catalogo
from src.identity import (
    cnpj_valido,
    cpf_valido,
    documentos_compativeis,
    nome_normalizado,
    propor_vinculos,
    revisar_vinculo,
)


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SQLITE_CEAP", tmp_path / "db.sqlite")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    inicializar_catalogo()
    with banco() as c:
        c.execute("INSERT INTO pessoas VALUES(1,'JOSE TESTE')")
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2022:1',2022,'1','José Teste','52998224725',NULL,1,'1980-01-01')"
        )
        c.execute(
            "INSERT INTO evidencias_pessoa VALUES(1,'CAMARA:CSV','teste.csv','JOSE TESTE','52998224725','1980-01-01')"
        )
    return tmp_path


def test_documentos():
    assert nome_normalizado(" José   da-Silva ") == "JOSE DA SILVA"
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("11111111111")
    assert not cpf_valido("***.982.247-**")
    assert cnpj_valido("04.252.011/0001-10")
    assert not cnpj_valido("04252011000111")
    assert documentos_compativeis("***.982.247-**", "52998224725")
    assert not documentos_compativeis("***.982.248-**", "52998224725")
    assert not documentos_compativeis("***********", "52998224725")


def test_cpf_publica_idempotente(db):
    assert propor_vinculos()["CONFIRMADO"] == 1
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (1,)
        assert c.execute("SELECT COUNT(*) FROM historico_identidade").fetchone() == (1,)


@pytest.mark.parametrize("modo", ["duplicado", "nascimento", "cpf_diferente"])
def test_conflitos_nao_publicam(db, modo):
    with banco() as c:
        if modo == "duplicado":
            c.execute("INSERT INTO pessoas VALUES(2,'OUTRA PESSOA')")
            c.execute(
                "INSERT INTO evidencias_pessoa VALUES(2,'CAMARA:CSV','teste.csv','OUTRA PESSOA','52998224725','')"
            )
        elif modo == "nascimento":
            c.execute("UPDATE candidaturas SET nascimento='1990-01-01'")
        else:
            c.execute("UPDATE candidaturas SET cpf='11144477735'")
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)
        assert c.execute("SELECT COUNT(*) FROM revisoes_identidade WHERE status='CONFLITO'").fetchone()[0] > 0


def test_mascara_e_nome_exigem_revisao(db):
    with banco() as c:
        c.execute("UPDATE candidaturas SET cpf='***982247**'")
    propor_vinculos()
    with banco() as c:
        ident, metodo, status = c.execute("SELECT id,metodo,status FROM revisoes_identidade").fetchone()
        assert (metodo, status) == ("nome_nascimento_cpf_parcial", "SUGERIDO")
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (None,)
    revisar_vinculo(ident, True, "Documento oficial inspecionado", "teste")
    with banco() as c:
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (1,)
        assert c.execute("SELECT revisor FROM historico_identidade").fetchone() == ("teste",)


def test_rejeicao_persistente(db):
    with banco() as c:
        c.execute("UPDATE candidaturas SET cpf=''")
    propor_vinculos()
    with banco() as c:
        ident = c.execute("SELECT id FROM revisoes_identidade").fetchone()[0]
    revisar_vinculo(ident, False, "Homônimo confirmado", "teste")
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT status FROM revisoes_identidade").fetchone() == ("REJEITADO",)


def escrever(path, valor="10,00", mes="202501"):
    row = {
        "Código da Emenda": "202512340001",
        "Código do Autor da Emenda": "1234",
        "Ano/Mês": mes,
        "Código do Favorecido": "04252011000110",
        "Favorecido": "EXEMPLO",
        "Tipo Favorecido": "Pessoa Jurídica",
        "Valor Recebido": valor,
    }
    with path.open("w") as f:
        w = csv.DictWriter(f, fieldnames=row, delimiter=";")
        w.writeheader()
        w.writerow(row)


def test_favorecidos_idempotente_e_rollback(db):
    p = db / "fav.csv"
    escrever(p)
    importar_favorecidos(p)
    importar_favorecidos(p)
    with banco() as c:
        assert c.execute(
            "SELECT COUNT(*),SUM(valor_centavos),MAX(documento_valido) FROM pagamentos_favorecidos"
        ).fetchone() == (1, 1000, 1)
        assert c.execute("SELECT emenda_identificada FROM v_favorecidos_emendas").fetchone() == (0,)
    escrever(p, mes="202513")
    with pytest.raises(ValueError):
        importar_favorecidos(p)
    with banco() as c:
        assert c.execute("SELECT SUM(valor_centavos) FROM pagamentos_favorecidos").fetchone() == (1000,)


def test_favorecido_mascarado_nao_documento_exato(db):
    p = db / "fav.csv"
    escrever(p)
    s = p.read_text().replace("04252011000110", "***982247**").replace("Pessoa Jurídica", "Pessoa Fisica")
    p.write_text(s)
    importar_favorecidos(p)
    with banco() as c:
        assert c.execute("SELECT documento,documento_valido FROM pagamentos_favorecidos").fetchone() == (
            "***982247**",
            0,
        )


def test_ferramenta_favorecidos(db):
    from src.investigation import consultar_dados

    p = db / "fav.csv"
    escrever(p)
    importar_favorecidos(p)
    r = json.loads(
        consultar_dados.invoke(
            {
                "dominio": "emendas",
                "query_sql": "SELECT SUM(valor_recebido) AS total FROM v_favorecidos_emendas WHERE ano=2025",
            }
        )
    )
    assert r["status"] == "CONSULTADO"
    assert r["dados"] == [{"total": 10.0}]


def test_nova_evidencia_contraditoria_suspende(db):
    propor_vinculos()
    with banco() as c:
        c.execute(
            "INSERT INTO evidencias_pessoa VALUES(1,'CAMARA:API','outro','JOSE TESTE','11144477735','1980-01-01')"
        )
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (None,)
        assert c.execute("SELECT status FROM revisoes_identidade").fetchone() == ("CONFLITO",)


def test_eleicoes_sem_mandato_e_conflito(db):
    with banco() as c:
        c.execute("DELETE FROM evidencias_pessoa")
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2026:2',2026,'2','José Teste','52998224725',NULL,2,'1980-01-01')"
        )
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(DISTINCT grupo),COUNT(*) FROM identidades_eleitorais").fetchone() == (
            1,
            2,
        )
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)
        c.execute("UPDATE candidaturas SET nascimento='1990-01-01' WHERE eleicao=2026")
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identidades_eleitorais").fetchone() == (0,)


def test_api_camara_id_e_cache(db, monkeypatch):
    import httpx

    from src.identity import enriquecer_camara

    chamadas = []

    def get(self, url):
        chamadas.append(url)
        return httpx.Response(
            200,
            json={
                "dados": {
                    "id": 1,
                    "nomeCivil": "JOSE TESTE",
                    "cpf": "52998224725",
                    "dataNascimento": "1980-01-01",
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", get)
    assert enriquecer_camara()["cadastros"] == 1
    assert enriquecer_camara()["cadastros"] == 1
    assert len(chamadas) == 1
    with banco() as c:
        assert c.execute("SELECT nascimento FROM evidencias_pessoa WHERE fonte='CAMARA:API'").fetchone() == (
            "1980-01-01",
        )


def test_api_camara_rejeita_identidade_errada(db, monkeypatch):
    import httpx

    from src.identity import enriquecer_camara

    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url: httpx.Response(200, json={"dados": {"id": 2}}, request=httpx.Request("GET", url)),
    )
    assert enriquecer_camara()["cadastros"] == 0
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM evidencias_pessoa WHERE fonte='CAMARA:API'").fetchone() == (0,)


def test_extracao_favorecidos_apenas_familia(tmp_path):
    import zipfile

    from src.public_sources import extrair

    p = tmp_path / "cg.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("EmendasParlamentares.csv", "a;b\n1;2")
        z.writestr("EmendasParlamentares_PorFavorecido.csv", "a;b\n3;4")
    assert [p.name for p in extrair(p, tmp_path / "extraidos", "favorecidos")] == [
        "EmendasParlamentares_PorFavorecido.csv"
    ]


def test_reimportacao_candidatura_remove_grupo_sem_perder_vinculo(db):
    from src.catalog import importar_tse

    propor_vinculos()
    p = db / "candidato.csv"
    row = {
        "ANO_ELEICAO": "2022",
        "CD_CARGO": "6",
        "SQ_CANDIDATO": "1",
        "NM_CANDIDATO": "JOSE TESTE",
        "NR_CPF_CANDIDATO": "52998224725",
        "DT_NASCIMENTO": "01/01/1980",
    }
    with p.open("w") as f:
        w = csv.DictWriter(f, fieldnames=row, delimiter=";")
        w.writeheader()
        w.writerow(row)
    importar_tse(p, "candidatos", 2022)
    with banco() as c:
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (1,)
        assert c.execute("SELECT COUNT(*) FROM identidades_eleitorais").fetchone() == (0,)
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identidades_eleitorais").fetchone() == (1,)


def test_cpf_com_nascimento_contraditorio_no_tse_nao_publica(db):
    with banco() as c:
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2026:2',2026,'2','José Teste','52998224725',NULL,2,'1990-01-01')"
        )
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)


def test_revisor_pode_revogar_confirmacao(db):
    propor_vinculos()
    with banco() as c:
        ident = c.execute("SELECT id FROM revisoes_identidade").fetchone()[0]
    revisar_vinculo(ident, False, "Documento revisto; associação rejeitada", "responsável")
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)
        assert c.execute("SELECT pessoa_id FROM candidaturas").fetchone() == (None,)
        assert c.execute("SELECT status FROM revisoes_identidade").fetchone() == ("REJEITADO",)


def test_cpf_mesma_data_nomes_distintos_exige_revisao(db):
    with banco() as c:
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2026:2',2026,'2','OUTRA PESSOA','52998224725',NULL,2,'1980-01-01')"
        )
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT COUNT(*) FROM identificadores_externos").fetchone() == (0,)
        assert c.execute("SELECT COUNT(*) FROM identidades_eleitorais").fetchone() == (0,)


def test_revisao_nao_pode_contornar_cpf_de_outra_pessoa(db):
    from src.catalog import vincular_identidade

    with banco() as c:
        c.execute("INSERT INTO pessoas VALUES(2,'OUTRA PESSOA')")
        c.execute(
            "INSERT INTO evidencias_pessoa VALUES(2,'CAMARA:CSV','outro.csv','OUTRA PESSOA','52998224725','')"
        )
    with pytest.raises(ValueError, match="unicidade"):
        vincular_identidade(1, "TSE", "2022:1", "Tentativa de confirmação manual")


def test_uma_pessoa_nao_pode_receber_dois_cpfs_publicados(db):
    from src.catalog import vincular_identidade

    propor_vinculos()
    with banco() as c:
        c.execute("DELETE FROM evidencias_pessoa")
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2026:2',2026,'2','José Teste','11144477735',NULL,2,'1980-01-01')"
        )
    with pytest.raises(ValueError, match="unicidade"):
        vincular_identidade(1, "TSE", "2026:2", "Tentativa com outro CPF")


def test_revisao_humana_permanece_com_mesmas_evidencias(db):
    with banco() as c:
        c.execute(
            "INSERT INTO candidaturas(id,eleicao,sequencial,nome,cpf,lote_id,linha_origem,nascimento) VALUES('2026:2',2026,'2','JOSE TESTE NOVO NOME','52998224725',NULL,2,'1980-01-01')"
        )
    propor_vinculos()
    with banco() as c:
        ident = c.execute("SELECT id FROM revisoes_identidade WHERE identificador='2026:2'").fetchone()[0]
    revisar_vinculo(ident, True, "Mudança de nome comprovada documentalmente", "revisor")
    propor_vinculos()
    with banco() as c:
        assert c.execute("SELECT pessoa_id FROM candidaturas WHERE id='2026:2'").fetchone() == (1,)
