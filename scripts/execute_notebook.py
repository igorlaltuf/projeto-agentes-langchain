"""Executa células via IPython sem abrir sockets de kernel, preservando saídas reais."""

import argparse
import os
from pathlib import Path

import nbformat
from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("APURAMANDATO_INDEX", str(Path.cwd() / "indices/demo"))
    notebook = nbformat.read(args.notebook, as_version=4)
    shell = InteractiveShell.instance()
    contador = 0
    for celula in notebook.cells:
        if celula.cell_type != "code":
            continue
        contador += 1
        with capture_output() as captured:
            resultado = shell.run_cell(celula.source, store_history=True)
        if resultado.error_before_exec or resultado.error_in_exec:
            raise RuntimeError(f"Célula {contador}: {resultado.error_before_exec or resultado.error_in_exec}")
        celula.execution_count = contador
        celula.outputs = []
        for nome, texto in [("stdout", captured.stdout), ("stderr", captured.stderr)]:
            if texto:
                celula.outputs.append(nbformat.v4.new_output("stream", name=nome, text=texto))
        for saida in captured.outputs:
            celula.outputs.append(
                nbformat.v4.new_output("display_data", data=saida.data, metadata=saida.metadata)
            )
    nbformat.write(notebook, args.notebook)
    print(args.notebook.name, contador, "células executadas")


if __name__ == "__main__":
    main()
