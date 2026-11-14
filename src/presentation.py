"""Nomes públicos de fontes nas respostas; preserva período e conteúdo financeiro."""

import re

CEAP = "Cota para o Exercício da Atividade Parlamentar (CEAP)"


def apresentar_fontes(texto: str) -> str:
    for padrao in (
        r"\bCSV\s+CEAP(?:\s*\((?:SQLite\s+local|banco\s+local)\))?",
        r"\bCEAP\s*\((?:SQLite\s+local|banco\s+local)\)",
    ):
        texto = re.sub(padrao, lambda _: CEAP, texto, flags=re.IGNORECASE)
    return texto
