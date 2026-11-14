"""Exporta o relatório com Pycairo (python-cairo do sistema), sem navegador/rede.

Uso: python scripts/export_report.py
"""

import re
import textwrap
from pathlib import Path

import cairo

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "entregaveis/igor_laltuf_ai-agents_langchain-agents.pdf"
DESTINO.parent.mkdir(exist_ok=True)
surface = cairo.PDFSurface(str(DESTINO), 595, 842)
surface.set_metadata(cairo.PDF_METADATA_TITLE, "ApuraMandato — relatório técnico")
ctx = cairo.Context(surface)
y = 50
pagina = 1


def fonte(tamanho=10, negrito=False):
    ctx.select_font_face(
        "DejaVu Sans",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD if negrito else cairo.FONT_WEIGHT_NORMAL,
    )
    ctx.set_font_size(tamanho)


def rodape():
    fonte(8)
    ctx.move_to(50, 815)
    ctx.show_text(f"ApuraMandato · Igor Laltuf · {pagina}")


def linha(texto, tamanho=10, negrito=False):
    global y, pagina
    if y + tamanho + 5 > 790:
        rodape()
        ctx.show_page()
        pagina += 1
        y = 50
    fonte(tamanho, negrito)
    ctx.move_to(50, y)
    ctx.show_text(texto)
    y += tamanho + 5


def limpar(texto):
    texto = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", texto)
    return texto.replace("**", "").replace("`", "")


for original in (RAIZ / "docs/RELATORIO.md").read_text().splitlines():
    if not original.strip():
        y += 7
        continue
    if re.fullmatch(r"[| :\-]+", original):
        continue
    nivel = len(original) - len(original.lstrip("#"))
    tamanho = 17 if nivel == 1 else 13 if nivel else 10
    texto = limpar(original.lstrip("# "))
    if texto.startswith("|"):
        texto = " · ".join(c.strip() for c in texto.strip("|").split("|"))
    # Mede palavras para respeitar a largura real da fonte, inclusive URLs longas.
    fonte(tamanho, bool(nivel))
    atual = ""
    for palavra in textwrap.wrap(texto, width=80, break_long_words=True, break_on_hyphens=False):
        for token in palavra.split():
            tentativa = f"{atual} {token}".strip()
            if ctx.text_extents(tentativa).x_advance > 495 and atual:
                linha(atual, tamanho, bool(nivel))
                atual = token
            else:
                atual = tentativa
    if atual:
        linha(atual, tamanho, bool(nivel))
rodape()
surface.finish()
print(DESTINO)
