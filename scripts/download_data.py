"""Baixa o CSV anual da CEAP e monta o SQLite.

Uso: python scripts/download_data.py [--ano 2025]

Fonte: https://www.camara.leg.br/cota-parlamentar/ (arquivos Ano-AAAA.csv.zip)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config
from src.data import construir_sqlite
from src.downloads import baixar_csv

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ano", type=int, nargs="+", default=[config.ANO_CEAP])
    ap.add_argument("--atualizar", action="store_true")
    args = ap.parse_args()
    from scripts.load_sources import backup

    backup()
    for ano in args.ano:
        csv = baixar_csv(ano, args.atualizar)
        n = construir_sqlite(csv, ano_esperado=ano)
        print(f"{ano}: {n} linhas carregadas em {config.SQLITE_CEAP}")

    from scripts.load_sources import migrar_ceap
    from src.catalog import inicializar_catalogo

    inicializar_catalogo()
    migrar_ceap()
