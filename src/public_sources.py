"""Downloads oficiais explícitos: catálogo TSE e arquivo CGU, sem cargas durante o chat."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.catalog import hash_arquivo

CGU_URL = "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO"


def baixar(url: str, destino: Path, atualizar=False) -> Path:
    if destino.exists() and not atualizar:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resposta:
            resposta.raise_for_status()
            with temporario.open("wb") as arquivo:
                for parte in resposta.iter_bytes():
                    arquivo.write(parte)
        if not zipfile.is_zipfile(temporario):
            raise ValueError("O download não é um ZIP válido; arquivo anterior preservado")
        temporario.replace(destino)
        destino.with_suffix(".manifest.json").write_text(
            json.dumps({"url": url, "sha256": hash_arquivo(destino)}, indent=2)
        )
    finally:
        temporario.unlink(missing_ok=True)
    return destino


def recurso_tse(eleicao: int, tipo: str) -> str:
    """Descobre recursos no catálogo oficial, sem adivinhar nomes de pacotes."""
    especificos = set()
    conjuntos = set()
    inicio = 0
    while True:
        busca = httpx.get(
            "https://dadosabertos.tse.jus.br/api/3/action/package_search",
            params={"q": str(eleicao), "rows": 100, "start": inicio},
            timeout=60,
        )
        busca.raise_for_status()
        resultado = busca.json()
        if not resultado.get("success"):
            raise ValueError("Catálogo TSE indisponível")
        pacotes = resultado["result"]["results"]
        for pacote in pacotes:
            if str(eleicao) not in pacote.get("title", ""):
                continue
            for recurso in pacote.get("resources", []):
                url = recurso.get("url", "")
                arquivo = urlparse(url).path.lower()
                if urlparse(url).hostname not in {"cdn.tse.jus.br", "dadosabertos.tse.jus.br"}:
                    continue
                if not arquivo.endswith(".zip") or str(eleicao) not in arquivo:
                    continue
                corresponde = {
                    "candidatos": "consulta_cand_" in arquivo and "complementar" not in arquivo,
                    "bens": "bem_candidato" in arquivo,
                    "receitas": "receitas_candidatos" in arquivo,
                    "despesas": "despesas_contratadas_candidatos" in arquivo,
                }[tipo]
                if corresponde:
                    especificos.add(url)
                if (
                    tipo in {"receitas", "despesas"}
                    and "prestacao_de_contas_eleitorais_candidatos_" in arquivo
                ):
                    conjuntos.add(url)
        inicio += len(pacotes)
        if not pacotes or inicio >= resultado["result"].get("count", inicio):
            break
    candidatos = sorted(especificos or conjuntos)
    if len(candidatos) != 1:
        raise ValueError(
            f"Catálogo retornou {len(candidatos)} recursos para {tipo}/{eleicao}; informe --url oficial ou --arquivo"
        )
    return candidatos[0]


def extrair(zip_path: Path, pasta: Path, tipo: str) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        arquivos = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if tipo in {"ceis", "cnep"}:
            arquivos = [
                n
                for n in arquivos
                if re.fullmatch(rf"(?:[0-9]{{8}}[_-])?{tipo}(?:[_-][0-9]{{8}})?\.csv", Path(n).name.lower())
            ]
            if len(arquivos) != 1:
                raise ValueError("ZIP de sanções deve conter exatamente um CSV do cadastro solicitado")
        elif tipo in {"emendas", "favorecidos"}:
            # O ZIP inclui convênios, parcerias e favorecidos: são tabelas diferentes,
            # não partições do arquivo principal e não devem ser concatenadas.
            nome_esperado = (
                "emendasparlamentares.csv" if tipo == "emendas" else "emendasparlamentares_porfavorecido.csv"
            )
            arquivos = [n for n in arquivos if Path(n).name.casefold() == nome_esperado]
        else:
            prefixos = {
                "candidatos": "consulta_cand_",
                "bens": "bem_candidato_",
                "receitas": "receitas_candidatos_",
                "despesas": "despesas_contratadas_candidatos_",
            }
            arquivos = [n for n in arquivos if re.match(rf"^{prefixos[tipo]}\d{{4}}_", Path(n).name.lower())]
            # Evita importar o arquivo BR junto dos arquivos por UF (duplicação nacional).
            nacionais = [n for n in arquivos if "_brasil.csv" in n.lower()]
            if nacionais:
                arquivos = nacionais
        if not arquivos:
            raise ValueError("Nenhum CSV esperado no ZIP")
        saidas = []
        for nome in arquivos:
            destino = pasta / Path(nome).name
            with z.open(nome) as origem, destino.open("wb") as saida:
                import shutil

                shutil.copyfileobj(origem, saida)
            saidas.append(destino)
    return saidas


def combinar(arquivos: list[Path], destino: Path) -> Path:
    """Concatena partições por UF em um único CSV UTF-8, preservando o cabeçalho."""
    import csv

    from src.catalog import _ler

    colunas = None
    with destino.open("w", encoding="utf-8", newline="") as saida:
        escritor = None
        for arquivo in arquivos:
            for chunk in _ler(arquivo):
                if colunas is None:
                    colunas = list(chunk.columns)
                    escritor = csv.DictWriter(saida, fieldnames=colunas, delimiter=";")
                    escritor.writeheader()
                if list(chunk.columns) != colunas:
                    raise ValueError("Partições têm layouts diferentes")
                escritor.writerows(chunk.to_dict("records"))
    return destino
