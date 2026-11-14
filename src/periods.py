"""Resolução explícita de intervalos relativos em anos-calendário."""

import re
from datetime import date

from src import config


def anos_relativos(texto: str) -> list[int] | None:
    texto = texto.lower()
    numeros = {"um": 1, "dois": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "dez": 10}
    padrao = r"(?:últimos|ultimos|últimas|ultimas)\s+(\d+|um|dois|três|tres|quatro|cinco|dez)\s+anos"
    match = re.search(padrao, texto)
    if not match:
        return None
    token = match.group(1)
    quantidade = int(token) if token.isdigit() else numeros[token]
    atual = date.fromisoformat(config.HOJE).year
    fim = atual - 1 if re.search(r"(?:completos|fechados|encerrados)", texto) else atual
    if quantidade < 1 or quantidade > fim - 2009 + 1:
        raise ValueError("Intervalo fora da cobertura: informe anos a partir de 2009.")
    return list(range(fim - quantidade + 1, fim + 1))


def ultima_pergunta(mensagens) -> str:
    from langchain_core.messages import HumanMessage

    return next((str(m.content) for m in reversed(mensagens) if isinstance(m, HumanMessage)), "")


def anos_do_sql(sql: str) -> list[int]:
    """Lê apenas filtros da coluna ano; não interpreta valores ou documentos como datas."""
    anos = {int(a) for a in re.findall(r"\bano\s*=\s*['\"]?((?:19|20)\d{2})", sql, re.IGNORECASE)}
    for lista in re.findall(r"\bano\s+IN\s*\(([^)]+)\)", sql, re.IGNORECASE):
        anos.update(int(a) for a in re.findall(r"\b(?:19|20)\d{2}\b", lista))
    for inicio, fim in re.findall(r"\bano\s+BETWEEN\s+(\d{4})\s+AND\s+(\d{4})", sql, re.IGNORECASE):
        anos.update(range(int(inicio), int(fim) + 1))
    return sorted(anos)
