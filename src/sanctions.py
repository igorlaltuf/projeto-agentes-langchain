"""CEIS/CNEP: snapshots oficiais, publicação atômica e consulta sem rede."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from src import config
from src.identity import cnpj_valido, documento_posicional, nascimento_normalizado, nome_normalizado

NOMES = {
    "ceis": "Cadastro Nacional de Empresas Inidôneas e Suspensas (CEIS)",
    "cnep": "Cadastro Nacional de Empresas Punidas (CNEP)",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS cargas_sancoes(
 cadastro TEXT PRIMARY KEY CHECK(cadastro IN ('ceis','cnep')), lote_id INTEGER NOT NULL,
 data_referencia TEXT NOT NULL, registros INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sancoes_locais(
 cadastro TEXT NOT NULL, codigo TEXT NOT NULL, documento TEXT NOT NULL,
 documento_valido INTEGER NOT NULL, tipo_pessoa TEXT NOT NULL, nome TEXT NOT NULL,
 categoria TEXT, inicio TEXT, fim TEXT, publicacao TEXT, orgao TEXT, abrangencia TEXT,
 fundamento TEXT, processo TEXT, multa_centavos INTEGER, observacoes TEXT,
 lote_id INTEGER NOT NULL, linha_origem INTEGER NOT NULL, PRIMARY KEY(cadastro,codigo));
CREATE INDEX IF NOT EXISTS idx_sancoes_documento ON sancoes_locais(documento);
CREATE VIEW IF NOT EXISTS v_sancoes AS
 SELECT s.*,c.data_referencia FROM sancoes_locais s
 JOIN cargas_sancoes c ON c.cadastro=s.cadastro AND c.lote_id=s.lote_id;
"""


def inicializar_sancoes():
    from src.catalog import banco, inicializar_catalogo

    inicializar_catalogo()
    with banco() as c:
        c.executescript(SCHEMA)


def data_snapshot(texto: str) -> str:
    for formato in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Data da carga deve ser AAAA-MM-DD ou AAAAMMDD")


def baixar_snapshot(cadastro: str, pasta: Path, referencia=None, atualizar=False):
    """Usa snapshot datado; tenta os últimos sete dias só se o servidor responder 404."""
    from src.public_sources import baixar

    if cadastro not in NOMES:
        raise ValueError("Cadastro inválido")
    pasta.mkdir(parents=True, exist_ok=True)
    manifesto = pasta / "snapshot.json"
    if not atualizar and referencia is None and manifesto.exists():
        m = json.loads(manifesto.read_text())
        p = pasta / m["arquivo"]
        if p.exists():
            return p, m["url"], data_snapshot(m["data_referencia"])
    datas = (
        [date.fromisoformat(data_snapshot(referencia))]
        if referencia
        else [date.today() - timedelta(days=i) for i in range(7)]
    )
    for dia in datas:
        url = f"https://portaldatransparencia.gov.br/download-de-dados/{cadastro}/{dia:%Y%m%d}"
        p = pasta / f"{dia:%Y%m%d}_{cadastro}.zip"
        try:
            baixar(url, p, atualizar)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and referencia is None:
                continue
            raise
        m = {"arquivo": p.name, "url": url, "data_referencia": dia.isoformat()}
        temp = manifesto.with_suffix(".tmp")
        temp.write_text(json.dumps(m))
        temp.replace(manifesto)
        return p, url, dia.isoformat()
    raise ValueError("Nenhum snapshot encontrado nos últimos sete dias; informe --data ou --arquivo")


def importar_sancoes(path: Path, cadastro: str, referencia: str, url="") -> dict:
    from src.catalog import _ler, _lote, banco, centavos

    if cadastro not in NOMES:
        raise ValueError("Cadastro inválido")
    referencia = data_snapshot(referencia)
    inicializar_sancoes()
    desconhecidos = {"", "-1", "-3", "#NULO", "#NE", "SEM INFORMACAO", "NAO INFORMADO"}

    def vazio(v):
        return nome_normalizado(v) in desconhecidos or v.strip() in desconhecidos

    def data_campo(v):
        if vazio(v):
            return ""
        d = nascimento_normalizado(v)
        if not d:
            raise ValueError("Data de sanção inválida")
        return d

    with banco() as c:
        lote = _lote(c, "CGU:" + cadastro, path, "arquivo completo", url)
        c.execute("DELETE FROM sancoes_locais WHERE cadastro=?", (cadastro,))
        total = 0
        for chunk in _ler(path):
            mapa = {nome_normalizado(k): k for k in chunk.columns}
            obrigatorias = [
                "CADASTRO",
                "CODIGO DA SANCAO",
                "TIPO DE PESSOA",
                "CPF OU CNPJ DO SANCIONADO",
                "NOME DO SANCIONADO",
                "CATEGORIA DA SANCAO",
            ]
            if any(k not in mapa for k in obrigatorias):
                raise ValueError("Layout de sanções incompatível: faltam colunas obrigatórias")
            for row in chunk.to_dict("records"):
                total += 1

                def campo(*nomes, row=row, mapa=mapa):
                    return next(
                        (str(row[mapa[n]]).strip() for n in nomes if n in mapa and str(row[mapa[n]]).strip()),
                        "",
                    )

                if campo("CADASTRO").lower() != cadastro:
                    raise ValueError("Cadastro do arquivo diverge do solicitado")
                codigo = campo("CODIGO DA SANCAO")
                if vazio(codigo):
                    raise ValueError("Código da sanção ausente")
                doc = documento_posicional(campo("CPF OU CNPJ DO SANCIONADO"))
                tipo = campo("TIPO DE PESSOA")
                valido = cnpj_valido(doc) and nome_normalizado(tipo) in {"PESSOA JURIDICA", "JURIDICA", "J"}
                multa = campo("VALOR DA MULTA", "VALOR MULTA")
                c.execute(
                    "INSERT INTO sancoes_locais VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cadastro,
                        codigo,
                        doc,
                        int(valido),
                        tipo,
                        campo("NOME DO SANCIONADO"),
                        campo("CATEGORIA DA SANCAO"),
                        data_campo(campo("DATA INICIO SANCAO")),
                        data_campo(campo("DATA FINAL SANCAO")),
                        data_campo(campo("DATA PUBLICACAO")),
                        campo("ORGAO SANCIONADOR"),
                        campo(
                            "ABRAGENCIA DA SANCAO",
                            "ABRANGENCIA DA SANCAO",
                            "ABRANGENCIA DEFINIDA EM DECISAO JUDICIAL",
                        ),
                        campo("FUNDAMENTACAO LEGAL"),
                        campo("NUMERO DO PROCESSO"),
                        None if vazio(multa) else centavos(multa),
                        campo("OBSERVACOES"),
                        lote,
                        total,
                    ),
                )
                c.execute(
                    "INSERT INTO registros_origem VALUES(?,?,?)",
                    (lote, total, json.dumps(row, ensure_ascii=False)),
                )
        if not total:
            raise ValueError("Arquivo de sanções vazio; carga anterior preservada")
        c.execute(
            "INSERT INTO cargas_sancoes VALUES(?,?,?,?) ON CONFLICT(cadastro) DO UPDATE SET lote_id=excluded.lote_id,data_referencia=excluded.data_referencia,registros=excluded.registros",
            (cadastro, lote, referencia, total),
        )
        c.execute(
            "UPDATE lotes_importacao SET linhas=?,status=? WHERE id=?",
            (total, "DEMONSTRACAO" if url == "DEMONSTRACAO" else "PUBLICADO", lote),
        )
    return {"fonte": "CGU:" + cadastro, "data_referencia": referencia, "registros": total, "lote_id": lote}


def consultar_sancoes_locais(cnpj: str) -> dict:
    doc = documento_posicional(cnpj)
    if not cnpj_valido(doc):
        return {"status": "INVALIDO", "erro": "Informe CNPJ completo com dígitos verificadores válidos"}
    if not config.SQLITE_CEAP.exists():
        return {"status": "INDISPONIVEL", "erro": "CEIS/CNEP não carregados. Execute devbox run setup."}
    try:
        with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            cargas = {
                r["cadastro"]: dict(r)
                for r in c.execute(
                    "SELECT c.*,l.status,l.criado_em,l.sha256,l.url FROM cargas_sancoes c JOIN lotes_importacao l ON l.id=c.lote_id AND l.status IN ('PUBLICADO','DEMONSTRACAO')"
                )
            }
            if set(cargas) != {"ceis", "cnep"}:
                return {
                    "status": "INDISPONIVEL",
                    "erro": "CEIS e CNEP precisam estar carregados; não conclua ausência de sanções.",
                }
            dados = {
                cad: [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM sancoes_locais WHERE cadastro=? AND documento=? AND documento_valido=1 ORDER BY codigo",
                        (cad, doc),
                    )
                ]
                for cad in NOMES
            }
    except sqlite3.Error:
        return {"status": "INDISPONIVEL", "erro": "Bases de sanções indisponíveis; execute devbox run setup."}
    return {
        "status": "CONSULTADO",
        "fonte": "CEIS e CNEP — Portal da Transparência (CGU)",
        "cnpj": doc,
        "dados": dados,
        "cargas": {
            cad: {
                **cargas[cad],
                "nome_base": NOMES[cad],
                "dias_desde_referencia": (
                    date.today() - date.fromisoformat(cargas[cad]["data_referencia"])
                ).days,
            }
            for cad in NOMES
        },
        "aviso": "Resultado limitado às versões carregadas. Ausência de registro não certifica regularidade. Não infira vigência apenas pela data final; considere categoria, órgão, abrangência e observações originais. A carga não é histórico completo de sanções.",
        "consulta_completa": True,
        "demonstracao": any(x["status"] == "DEMONSTRACAO" for x in cargas.values()),
    }


def main():
    """Atualiza os dois cadastros independentemente; não reprocessa outras bases."""
    import argparse

    from scripts.load_sources import backup
    from src.public_sources import extrair

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atualizar", action="store_true")
    args = parser.parse_args()
    backup()
    falhas = []
    for cadastro in NOMES:
        try:
            pasta = config.DATA_DIR / "originais/sancoes" / cadastro
            print(f"Preparando {cadastro.upper()}...", flush=True)
            arquivo, url, referencia = baixar_snapshot(cadastro, pasta, atualizar=args.atualizar)
            path = extrair(arquivo, pasta / "extraidos", cadastro)[0]
            print(
                json.dumps(importar_sancoes(path, cadastro, referencia, url), ensure_ascii=False), flush=True
            )
        except (httpx.HTTPError, ValueError, OSError, sqlite3.Error) as exc:
            falhas.append(cadastro)
            print(
                f"{cadastro.upper()}: falha ({type(exc).__name__}: {exc}). Carga anterior preservada.",
                flush=True,
            )
    if falhas:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
