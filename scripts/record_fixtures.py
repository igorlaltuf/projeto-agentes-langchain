"""Grava respostas reais das APIs em fixtures/ para o modo offline.

Uso: python scripts/record_fixtures.py
Edite a lista CASOS com os deputados, anos e CNPJs usados nos casos de teste.
As chamadas passam por src.tools._get_json, que salva o JSON automaticamente.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tools import buscar_deputado, dados_fornecedor, listar_despesas  # noqa: E402

CASOS = {
    "deputados": [("Tabata Amaral", "SP"), ("Nikolas Ferreira", "MG"), ("Silva", None)],
    "despesas": [],      # preencher com (id_deputado, ano, mes) apos descobrir os ids
    "cnpjs": [],         # preencher com CNPJs de fornecedores que aparecem nos casos
}

if __name__ == "__main__":
    for nome, uf in CASOS["deputados"]:
        print(buscar_deputado.invoke({"nome": nome, "uf": uf}))
    for id_dep, ano, mes in CASOS["despesas"]:
        print(listar_despesas.invoke({"id_deputado": id_dep, "ano": ano, "mes": mes})[:200])
    for cnpj in CASOS["cnpjs"]:
        print(dados_fornecedor.invoke({"cnpj": cnpj}))
    print("Fixtures gravados em fixtures/.")
