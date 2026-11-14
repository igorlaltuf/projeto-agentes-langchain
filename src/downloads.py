"""Download do arquivo anual oficial da CEAP, independente do provedor de LLM."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx

from src import config

URL_CEAP = "https://www.camara.leg.br/cotas/Ano-{ano}.csv.zip"


def baixar_csv(ano: int, atualizar: bool = False) -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    destino = config.DATA_DIR / f"ceap_{ano}.csv"
    if destino.exists() and not atualizar:
        print(f"CSV ja existe: {destino}")
        return destino
    url = URL_CEAP.format(ano=ano)
    print(f"Baixando {url} ...")
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        conteudo = b"".join(r.iter_bytes())
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        temporario = destino.with_suffix(".csv.tmp")
        temporario.write_bytes(z.read(nome))
        temporario.replace(destino)
    print(f"Salvo em {destino} ({destino.stat().st_size / 1e6:.1f} MB)")
    return destino
