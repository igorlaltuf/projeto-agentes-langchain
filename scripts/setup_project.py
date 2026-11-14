"""Preparação retomável do ApuraMandato: devbox run setup [--atualizar|--dry-run]."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def executar(nome: str, comando: list[str], pasta: Path) -> dict:
    """Mostra progresso e grava um log por etapa, sem passar chaves na linha de comando."""
    inicio = time.monotonic()
    log = pasta / f"{nome}.log"
    print(f"\n→ {nome}", flush=True)
    with log.open("w") as arquivo:
        processo = subprocess.Popen(
            comando, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for linha in processo.stdout:
            print(linha, end="", flush=True)
            arquivo.write(linha)
        codigo = processo.wait()
    return {
        "etapa": nome,
        "status": "CONCLUIDO" if codigo == 0 else "FALHOU",
        "codigo": codigo,
        "log": str(log.relative_to(RAIZ)),
        "segundos": round(time.monotonic() - inicio, 2),
    }


def planejar(atualizar=False, ampliado=False):
    from src import config
    from src.catalog import cobertura
    from src.data import anos_disponiveis

    anos = list(range(2018, 2027))
    existentes = set(anos_disponiveis())
    faltantes = anos if atualizar else [a for a in anos if a not in existentes]
    etapas = []
    python = sys.executable
    if faltantes:
        etapas.append(
            (
                "ceap",
                [
                    python,
                    "scripts/download_data.py",
                    "--ano",
                    *map(str, faltantes),
                    *(["--atualizar"] if atualizar else []),
                ],
                None,
            )
        )
    etapas.append(("migrar", [python, "scripts/load_sources.py", "migrar"], "ceap" if faltantes else None))
    cargas = {(c["fonte"], c["cobertura"]) for c in cobertura() if c.get("status") == "PUBLICADO"}
    if ampliado:
        if atualizar or ("CGU:emendas", "arquivo completo") not in cargas:
            etapas.append(
                (
                    "emendas",
                    [python, "scripts/load_sources.py", "emendas", *(["--atualizar"] if atualizar else [])],
                    None,
                )
            )
        for ano in [2018, 2022, 2026]:
            candidato = None
            for tipo in ["candidatos", "bens", "receitas", "despesas"]:
                if not atualizar and ("TSE:" + tipo, str(ano)) in cargas:
                    continue
                nome = f"tse-{ano}-{tipo}"
                etapas.append(
                    (
                        nome,
                        [
                            python,
                            "scripts/load_sources.py",
                            "tse",
                            "--eleicao",
                            str(ano),
                            "--tipo",
                            tipo,
                            *(["--atualizar"] if atualizar else []),
                        ],
                        candidato,
                    )
                )
                if tipo == "candidatos":
                    candidato = nome
        if atualizar or ("CGU:favorecidos", "arquivo completo") not in cargas:
            etapas.append(
                (
                    "favorecidos",
                    [
                        python,
                        "scripts/load_sources.py",
                        "favorecidos",
                        *(["--atualizar"] if atualizar else []),
                    ],
                    "emendas" if any(e[0] == "emendas" for e in etapas) else None,
                )
            )
    for cadastro in ("ceis", "cnep"):
        if atualizar or ("CGU:" + cadastro, "arquivo completo") not in cargas:
            etapas.append(
                (
                    cadastro,
                    [python, "scripts/load_sources.py", cadastro, *(["--atualizar"] if atualizar else [])],
                    None,
                )
            )
    if ampliado:
        etapas.append(("identidades", [python, "scripts/load_sources.py", "identidades"], None))
    if os.getenv("OPENAI_API_KEY") and not config.MODO_OFFLINE:
        etapas.append(("indice", [python, "-m", "src.data", "--build-index"], None))
    else:
        etapas.append(("indice", None, None))
    return etapas


def rodar(etapas, pasta: Path, runner=executar) -> list[dict]:
    resultados = []
    for nome, comando, dependencia in etapas:
        anteriores = {r["etapa"]: r["status"] for r in resultados}
        if dependencia and anteriores.get(dependencia) != "CONCLUIDO":
            resultado = {"etapa": nome, "status": "BLOQUEADO", "motivo": f"Etapa {dependencia} não concluiu"}
        elif comando is None:
            resultado = {
                "etapa": nome,
                "status": "PENDENTE",
                "motivo": "Configure OPENAI_API_KEY e MODO_OFFLINE=false para embeddings reais",
            }
        else:
            resultado = runner(nome, comando, pasta)
        resultados.append(resultado)
        temporario = pasta / "resumo.tmp"
        temporario.write_text(json.dumps(resultados, ensure_ascii=False, indent=2))
        temporario.replace(pasta / "resumo.json")
    return resultados


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--atualizar", action="store_true", help="Renova downloads e reimporta as bases já carregadas"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Mostra etapas sem criar arquivos ou baixar dados"
    )
    parser.add_argument(
        "--ampliado", action="store_true", help="Inclui fontes eleitorais, emendas e resolução de identidade"
    )
    args = parser.parse_args()
    env = RAIZ / ".env"
    if not args.dry_run and not env.exists():
        # Criação exclusiva: nunca sobrescreve credenciais já configuradas.
        with env.open("x") as arquivo:
            arquivo.write((RAIZ / ".env.example").read_text())
        print(".env criado a partir do exemplo. Preencha as chaves quando disponíveis.")
    etapas = planejar(args.atualizar, args.ampliado)
    if args.dry_run:
        for nome, comando, dependencia in etapas:
            print(
                nome,
                "→",
                " ".join(comando) if comando else "pendente: embeddings exigem chave",
                f"(depende de {dependencia})" if dependencia else "",
            )
        return
    pasta = RAIZ / "logs/setup"
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        resultados = rodar(etapas, pasta)
    except KeyboardInterrupt:
        print("\nInterrompido. Rode devbox run setup novamente para retomar; veja logs/setup/resumo.json.")
        raise SystemExit(130) from None
    print("\nResumo da preparação:")
    for r in resultados:
        print(f"  {r['etapa']}: {r['status']}")
    print("Registro: logs/setup/resumo.json")
    print("A chave LangSmith é configurada manualmente no .env; CEIS/CNEP locais não exigem chave CGU.")
    if any(r["status"] != "CONCLUIDO" for r in resultados):
        print("Há etapas pendentes ou com falha. Corrija a conexão/configuração e repita devbox run setup.")
        raise SystemExit(1)
    print("Preparação concluída. Abra o produto com devbox run app.")


if __name__ == "__main__":
    main()
