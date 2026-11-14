import json

from scripts.setup_project import rodar


def test_setup_continua_fontes_independentes_e_bloqueia_dependentes(tmp_path):
    chamados = []

    def executar(nome, comando, pasta):
        chamados.append(nome)
        return {"etapa": nome, "status": "FALHOU" if nome == "candidatos" else "CONCLUIDO"}

    r = rodar(
        [
            ("candidatos", ["python"], None),
            ("bens", ["python"], "candidatos"),
            ("emendas", ["python"], None),
            ("indice", None, None),
        ],
        tmp_path,
        executar,
    )
    assert chamados == ["candidatos", "emendas"]
    assert [s["status"] for s in r] == ["FALHOU", "BLOQUEADO", "CONCLUIDO", "PENDENTE"]
    assert json.loads((tmp_path / "resumo.json").read_text()) == r


def test_setup_planeja_so_o_que_falta(monkeypatch):
    from scripts.setup_project import planejar
    from src import catalog, data

    monkeypatch.setattr(data, "anos_disponiveis", lambda: list(range(2018, 2027)))
    monkeypatch.setattr(
        catalog,
        "cobertura",
        lambda: [
            {"fonte": "TSE:candidatos", "cobertura": "2022", "status": "PUBLICADO"},
            {"fonte": "CGU:emendas", "cobertura": "arquivo completo", "status": "PUBLICADO"},
        ],
    )
    etapas = {nome: (cmd, dep) for nome, cmd, dep in planejar(ampliado=True)}
    assert "ceap" not in etapas and "emendas" not in etapas
    assert "tse-2022-candidatos" not in etapas
    assert etapas["tse-2022-bens"][1] is None
    renovacao = {nome for nome, *_ in planejar(True, ampliado=True)}
    assert {"ceap", "emendas", "tse-2022-candidatos"} <= renovacao


def test_setup_principal_nao_planeja_fontes_ampliadas():
    from scripts.setup_project import planejar

    nomes = {nome for nome, *_ in planejar()}
    assert not any(nome.startswith("tse-") for nome in nomes)
    assert not {"emendas", "favorecidos", "identidades"} & nomes
