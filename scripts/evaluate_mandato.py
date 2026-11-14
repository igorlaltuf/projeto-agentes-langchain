"""Avaliação local ou LangSmith; não fabrica resultados quando serviços estão indisponíveis."""

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--online", action="store_true")
    p.add_argument("--demo", action="store_true", help="Avalia com referências da amostra isolada")
    p.add_argument("--langsmith", action="store_true")
    p.add_argument("--saida", type=Path, default=Path("entregaveis/avaliacao-apuramandato.json"))
    args = p.parse_args()
    if args.demo:
        os.environ["APURAMANDATO_DB"] = str(
            Path(__file__).resolve().parent.parent / "data/demo/apuramandato.db"
        )
    os.environ["MODO_OFFLINE"] = "false" if args.online else "true"
    if not args.online:
        os.environ["MODO_OFFLINE"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
    from src import config
    from src.evaluation import (
        avaliar_localmente,
        avaliar_no_langsmith,
        casos_demonstracao,
        enviar_dataset_langsmith,
        salvar_dataset_local,
    )

    salvar_dataset_local()
    resultado = {
        "executado_em": datetime.now(UTC).isoformat(),
        "modo": "online" if args.online else "offline",
        "banco": str(config.SQLITE_CEAP),
        "status": "PENDENTE",
        "resultados": [],
    }
    try:
        if args.online and not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY ausente")
        casos = casos_demonstracao() if "demo" in str(config.SQLITE_CEAP) else None
        if args.online and casos:
            for caso in casos:
                if caso["nome"] == "sancoes_demo":
                    caso["nome"] = "sancoes_online"
                    caso["pergunta"] = "Há sanções para o CNPJ 33.927.528/0001-60?"
                    caso["resposta_referencia"] = (
                        "Consultar CGU por CNPJ e apresentar o resultado ou indisponibilidade, sem concluir irregularidade da despesa."
                    )
        resultado["resultados"] = avaliar_localmente(casos=casos)
        resultado["status"] = "EXECUTADO"
        if args.langsmith:
            assinatura = hashlib.sha256(json.dumps(casos or [], sort_keys=True).encode()).hexdigest()[:8]
            dataset = f"apuramandato-v1-{resultado['modo']}-{assinatura}"
            enviar_dataset_langsmith(dataset, casos=casos)
            experimento = avaliar_no_langsmith(dataset, "apuramandato")
            resultado["dataset"] = dataset
            resultado["langsmith"] = str(experimento)
    except Exception as exc:  # noqa: BLE001
        resultado["status"] = "INDISPONIVEL"
        resultado["erro"] = type(exc).__name__
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    if resultado["status"] != "EXECUTADO":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
