"""Confere HTTP real de CNPJ e a disponibilidade das bases locais de sanções sem gravar ou imprimir credenciais."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config, tools

config.MODO_OFFLINE = False
config.USAR_CACHE_API = False
resultado = {}
for nome, ferramenta in [("cnpj", tools.dados_fornecedor), ("sancoes", tools.consultar_sancoes)]:
    try:
        resposta = json.loads(ferramenta.invoke({"cnpj": "33927528000160"}))
        resultado[nome] = resposta
    except Exception as exc:  # noqa: BLE001
        resultado[nome] = {"status": "INDISPONIVEL", "erro": type(exc).__name__}
config.DATA_DIR.mkdir(parents=True, exist_ok=True)
saida = config.RAIZ / "entregaveis/validacao-online.json"
saida.parent.mkdir(exist_ok=True)
saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
print(saida)
for nome, retorno in resultado.items():
    print(nome, retorno.get("status", "CONSULTADO"))
if any(r.get("status") == "INDISPONIVEL" for r in resultado.values()):
    raise SystemExit(1)
