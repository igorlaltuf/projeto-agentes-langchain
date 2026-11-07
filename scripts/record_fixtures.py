"""Grava respostas reais das APIs em fixtures/ para o modo offline.

Uso: python scripts/record_fixtures.py
As chamadas passam por src.tools._get_json, que salva o JSON automaticamente
(uma chave por URL+parametros). Em MODO_OFFLINE=true as ferramentas leem so daqui.

Casos de teste do projeto (mesmos do dataset LangSmith e do video):
- Tabata Amaral (PSB-SP, id 204534): gastos por categoria, comparacao com a media do partido
- Nikolas Ferreira (PL-MG, id 209787): comparacao entre deputados
- 'Silva': homonimos -> o agente deve pedir desambiguacao
- Fornecedores: maiores fornecedores da Tabata em 2025 (consulta a Brasil API)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tools import buscar_deputado, dados_fornecedor, listar_despesas

CASOS = {
    "deputados": [
        ("Tabata Amaral", "SP"),
        ("Tabata Amaral", None),
        ("Nikolas Ferreira", "MG"),
        ("Nikolas Ferreira", None),
        ("Silva", None),
    ],
    "despesas": [(204534, 2025, None), (204534, 2024, None), (204534, 2025, 3), (209787, 2025, None)],
    "cnpjs": ["33927528000160", "10951191000120", "62570543000100", "43731926000160"],
}

if __name__ == "__main__":
    for nome, uf in CASOS["deputados"]:
        print(f"\n# buscar_deputado({nome!r}, {uf!r})")
        print(buscar_deputado.invoke({"nome": nome, "uf": uf}))
    for id_dep, ano, mes in CASOS["despesas"]:
        print(f"\n# listar_despesas({id_dep}, {ano}, {mes})")
        r = json.loads(listar_despesas.invoke({"id_deputado": id_dep, "ano": ano, "mes": mes}))
        print({k: v for k, v in r.items() if k != "amostra_despesas"})
    for cnpj in CASOS["cnpjs"]:
        print(f"\n# dados_fornecedor({cnpj})")
        print(dados_fornecedor.invoke({"cnpj": cnpj}))
    print("\nFixtures gravados em fixtures/.")
