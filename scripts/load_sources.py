"""Cargas explícitas, backup e vínculos documentados do ApuraMandato."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config
from src.catalog import (
    banco,
    cobertura,
    importar_emendas,
    importar_favorecidos,
    importar_tse,
    inicializar_catalogo,
    vincular_identidade,
)
from src.public_sources import CGU_URL, baixar, combinar, extrair, recurso_tse


def backup():
    if not config.SQLITE_CEAP.exists():
        return
    destino = config.DATA_DIR / "backups" / "antes-apuramandato.db"
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as origem,
            sqlite3.connect(destino) as copia,
        ):
            origem.backup(copia)
        print("Backup:", destino)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="comando", required=True)
    for nome in ["emendas", "favorecidos", "tse", "ceis", "cnep"]:
        p = subs.add_parser(nome)
        p.add_argument("--arquivo", type=Path)
        p.add_argument("--url")
        p.add_argument("--atualizar", action="store_true")
        if nome in {"ceis", "cnep"}:
            p.add_argument(
                "--data",
                help="Data de referência do snapshot: AAAA-MM-DD; obrigatória com --arquivo ou --url",
            )
        if nome == "tse":
            p.add_argument("--eleicao", type=int, choices=[2018, 2022, 2026], required=True)
            p.add_argument("--tipo", choices=["candidatos", "bens", "receitas", "despesas"], required=True)
    subs.add_parser("catalogo")
    subs.add_parser("migrar")
    ident = subs.add_parser("identidades")
    ident.add_argument("--consultar-camara", action="store_true")
    subs.add_parser("revisoes")
    rev = subs.add_parser("revisar")
    rev.add_argument("--id", type=int, required=True)
    rev.add_argument("--decisao", choices=["aprovar", "rejeitar"], required=True)
    rev.add_argument("--evidencia", required=True)
    rev.add_argument("--revisor", required=True)
    vinculo = subs.add_parser("vincular")
    vinculo.add_argument("--pessoa", type=int, required=True)
    vinculo.add_argument("--fonte", choices=["TSE", "CGU"], required=True)
    vinculo.add_argument("--identificador", required=True)
    vinculo.add_argument("--evidencia", required=True)
    args = parser.parse_args()
    if args.comando == "catalogo":
        print(json.dumps(cobertura(), ensure_ascii=False, indent=2))
        return
    backup()
    inicializar_catalogo()
    if args.comando == "identidades":
        from src.identity import enriquecer_camara, enriquecer_locais, propor_vinculos

        print(json.dumps(enriquecer_locais(), ensure_ascii=False))
        if args.consultar_camara:
            resultado = enriquecer_camara()
            print(json.dumps(resultado, ensure_ascii=False))
        print(json.dumps(propor_vinculos(), ensure_ascii=False))
        if args.consultar_camara and resultado["falhas"]:
            raise SystemExit(1)
        return
    if args.comando == "revisoes":
        with banco() as c:
            c.row_factory = sqlite3.Row
            print(
                json.dumps(
                    [dict(r) for r in c.execute("SELECT * FROM revisoes_identidade ORDER BY id")],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return
    if args.comando == "revisar":
        from src.identity import revisar_vinculo

        revisar_vinculo(args.id, args.decisao == "aprovar", args.evidencia, args.revisor)
        return
    if args.comando == "vincular":
        vincular_identidade(args.pessoa, args.fonte, args.identificador, args.evidencia)
        return
    if args.comando == "migrar":
        migrar_ceap()
        return
    path = args.arquivo
    url = args.url or ""
    if args.comando in {"ceis", "cnep"}:
        from src.sanctions import baixar_snapshot, importar_sancoes

        pasta = config.DATA_DIR / "originais/sancoes" / args.comando
        referencia = args.data
        if (path is not None or url) and not referencia:
            parser.error("--data é obrigatória com --arquivo ou --url para registrar a data da base")
        if path is None:
            if url:
                arquivo = baixar(url, pasta / "fonte.zip", args.atualizar)
            else:
                arquivo, url, referencia = baixar_snapshot(args.comando, pasta, referencia, args.atualizar)
            path = extrair(arquivo, pasta / "extraidos", args.comando)[0]
        print(json.dumps(importar_sancoes(path, args.comando, referencia, url), ensure_ascii=False, indent=2))
        return
    if path is None:
        tipo = args.comando if args.comando in {"emendas", "favorecidos"} else args.tipo
        periodo = "geral" if tipo in {"emendas", "favorecidos"} else str(args.eleicao)
        url = url or (CGU_URL if tipo in {"emendas", "favorecidos"} else recurso_tse(args.eleicao, tipo))
        pasta = config.DATA_DIR / "originais" / args.comando / periodo / tipo
        # Receitas e despesas compartilham o mesmo ZIP nacional de contas.
        # Guarde uma única cópia do download; cada importador extrai sua família.
        if args.comando == "tse" and "prestacao_de_contas_eleitorais_candidatos_" in url:
            arquivo_zip = config.DATA_DIR / "originais/tse" / periodo / "contas/fonte.zip"
        else:
            arquivo_zip = pasta / "fonte.zip"
        if tipo == "favorecidos":
            arquivo_zip = config.DATA_DIR / "originais/emendas/geral/emendas/fonte.zip"
        zip_path = baixar(url, arquivo_zip, args.atualizar)
        pasta.mkdir(parents=True, exist_ok=True)
        path = combinar(extrair(zip_path, pasta / "extraidos", tipo), pasta / "carga.csv")
    resultado = (
        importar_favorecidos(path, url)
        if args.comando == "favorecidos"
        else importar_emendas(path, url)
        if args.comando == "emendas"
        else importar_tse(path, args.tipo, args.eleicao, url)
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


def migrar_ceap():
    from src.catalog import _lote

    with banco() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ceap_lotes(ano INTEGER PRIMARY KEY,lote_id INTEGER NOT NULL)"
        )
        for (ano,) in conn.execute("SELECT DISTINCT ano FROM despesas_ceap").fetchall():
            csv = config.DATA_DIR / f"ceap_{ano}.csv"
            if not csv.exists():
                continue
            lote = _lote(conn, "CAMARA:ceap", csv, ano, f"https://www.camara.leg.br/cotas/Ano-{ano}.csv.zip")
            conn.execute(
                "INSERT INTO ceap_lotes VALUES(?,?) ON CONFLICT(ano) DO UPDATE SET lote_id=excluded.lote_id",
                (ano, lote),
            )
        for ident, nome in conn.execute(
            "SELECT id_deputado,MAX(nome_parlamentar) FROM despesas_ceap WHERE id_deputado IS NOT NULL GROUP BY id_deputado"
        ).fetchall():
            conn.execute("INSERT OR IGNORE INTO pessoas VALUES(?,?)", (int(ident), nome))
            conn.execute(
                "INSERT OR IGNORE INTO identificadores_externos VALUES(?,?,?,?)",
                ("CAMARA", str(ident), int(ident), "ID oficial da Câmara no CSV CEAP"),
            )
        conn.execute("DROP VIEW IF EXISTS v_despesas_ceap")
        conn.execute("""CREATE VIEW v_despesas_ceap AS SELECT d.rowid AS registro_local,d.*,CAST(ROUND(d.valor*100) AS INTEGER) AS valor_centavos,l.lote_id
                      FROM despesas_ceap d LEFT JOIN ceap_lotes l ON l.ano=d.ano""")
    print("Migração aditiva concluída; tabela CEAP e memória preservadas.")


if __name__ == "__main__":
    main()
