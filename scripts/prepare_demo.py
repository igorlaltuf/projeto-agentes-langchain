"""Prepara banco ISOLADO de demonstração; nunca altera ceap.db de produção."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config
from src.catalog import banco, importar_emendas, importar_tse, inicializar_catalogo
from src.data import construir_sqlite


def preparar(destino: Path, ampliado=False):
    if destino.resolve() == (config.RAIZ / "data/ceap.db").resolve():
        raise ValueError("Use um banco separado da produção")
    config.SQLITE_CEAP = destino
    config.DATA_DIR = destino.parent
    destino.parent.mkdir(parents=True, exist_ok=True)
    construir_sqlite(config.RAIZ / "tests/dados/ceap_amostra.csv", destino)
    inicializar_catalogo()
    if ampliado:
        for ano in [2018, 2022, 2026]:
            for tipo in ["candidatos", "bens", "receitas", "despesas"]:
                importar_tse(config.RAIZ / f"examples/tse/{tipo}_{ano}.csv", tipo, ano, "DEMONSTRACAO")
        importar_emendas(config.RAIZ / "examples/emendas/emendas.csv", "DEMONSTRACAO")
    from src.sanctions import importar_sancoes

    for cadastro in ("ceis", "cnep"):
        importar_sancoes(
            config.RAIZ / f"examples/sancoes/{cadastro}.csv", cadastro, "2026-01-01", "DEMONSTRACAO"
        )
    with banco() as conn:
        conn.execute("UPDATE lotes_importacao SET status='DEMONSTRACAO'")
    print(destino)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=config.RAIZ / "data/demo/apuramandato.db")
    p.add_argument("--ampliado", action="store_true")
    args = p.parse_args()
    preparar(args.db, args.ampliado)
