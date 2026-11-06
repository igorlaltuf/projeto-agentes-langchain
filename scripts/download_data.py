"""Baixa o CSV anual da CEAP e monta o SQLite.

Uso: python scripts/download_data.py [--ano 2025]

Fonte: https://www.camara.leg.br/cota-parlamentar/ (arquivos Ano-AAAA.csv.zip)
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config
from src.data import construir_sqlite

URL_CEAP = "https://www.camara.leg.br/cotas/Ano-{ano}.csv.zip"


def baixar_csv(ano: int) -> Path:
    config.DATA_DIR.mkdir(exist_ok=True)
    destino = config.DATA_DIR / f"ceap_{ano}.csv"
    if destino.exists():
        print(f"CSV ja existe: {destino}")
        return destino
    url = URL_CEAP.format(ano=ano)
    print(f"Baixando {url} ...")
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        conteudo = b"".join(r.iter_bytes())
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        destino.write_bytes(z.read(nome))
    print(f"Salvo em {destino} ({destino.stat().st_size / 1e6:.1f} MB)")
    return destino


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ano", type=int, default=config.ANO_CEAP)
    args = ap.parse_args()
    csv = baixar_csv(args.ano)
    n = construir_sqlite(csv)
    print(f"{n} linhas carregadas em {config.SQLITE_CEAP}")
