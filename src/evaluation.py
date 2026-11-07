"""Avaliacao do agente integrador (fase 5): casos de teste, avaliadores de trajetoria e de
qualidade, execucao local e, com chave, no LangSmith.

Avaliadores (mesma assinatura usada pelo langsmith.evaluate):
- trajetoria_correta ... as ferramentas esperadas foram chamadas na ordem esperada?
- valor_correto ........ a resposta cita o valor de referencia (tolerancia de centavos)?
- juiz_llm ............. LLM-as-a-judge (CORRETO/INCORRETO), comparado com o avaliador deterministico
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src import config, llm
from src.agent import montar_agente, trajetoria

ARQUIVO_DATASET = config.RAIZ / "docs" / "dataset_avaliacao.json"

CASOS_TESTE: list[dict[str, Any]] = [
    {
        "nome": "gasto_categoria_api",
        "pergunta": "Quanto a deputada Tabata Amaral gastou com combustível em 2025?",
        "ferramentas_esperadas": ["buscar_deputado", "listar_despesas"],
        "valor_esperado": 48808.83,
        "resposta_referencia": "Tabata Amaral gastou R$ 48.808,83 com combustíveis em 2025 (API da Câmara).",
    },
    {
        "nome": "gasto_total_api",
        "pergunta": "Qual foi o gasto total do deputado Nikolas Ferreira em 2025?",
        "ferramentas_esperadas": ["buscar_deputado", "listar_despesas"],
        "valor_esperado": 463433.18,
        "resposta_referencia": "Nikolas Ferreira gastou R$ 463.433,18 em 2025 (API da Câmara).",
    },
    {
        "nome": "media_partido_sql",
        "pergunta": "Qual a média de gasto com combustível por deputado do PSB em 2025?",
        "ferramentas_esperadas": ["consultar_ceap"],
        "valor_esperado": 48849.91,
        "resposta_referencia": "A média por deputado do PSB com combustíveis em 2025 foi R$ 48.849,91 (CSV CEAP).",
    },
    {
        "nome": "homonimos",
        "pergunta": "Gastos do Silva em 2025",
        "ferramentas_esperadas": ["buscar_deputado"],
        "ferramentas_proibidas": ["listar_despesas"],
        "resposta_referencia": "Ha varios deputados com o nome Silva; o agente deve pedir qual deles.",
    },
    {
        "nome": "regra_rag",
        "pergunta": "Qual o limite mensal da cota para um deputado de SP?",
        "ferramentas_esperadas": ["consultar_regras_cota"],
        "valor_esperado": 42837.33,
        "resposta_referencia": "O limite mensal para SP e R$ 42.837,33 (Anexo do Ato da Mesa 43/2009).",
    },
    {
        "nome": "fornecedor_api",
        "pergunta": "Quem é o fornecedor de CNPJ 33.927.528/0001-60?",
        "ferramentas_esperadas": ["dados_fornecedor"],
        "resposta_referencia": "SJR IMOVEIS LTDA, corretagem de imoveis, Sao Paulo-SP (Brasil API).",
    },
]


def _valores_brl(texto: str) -> list[float]:
    return [float(v.replace(".", "").replace(",", ".")) for v in re.findall(r"R\$ ?([\d.]+,\d{2})", texto)]


# --------------------------------------------------------------------------- #
# Avaliadores
# --------------------------------------------------------------------------- #
def trajetoria_correta(run, example) -> dict:
    usadas = [p["ferramenta"] for p in run.outputs.get("trajetoria", []) if p["tipo"] == "decisao"]
    esperadas = example.outputs.get("ferramentas_esperadas", [])
    proibidas = example.outputs.get("ferramentas_proibidas", [])
    ok = all(f in usadas for f in esperadas) and not any(f in usadas for f in proibidas)
    # ordem relativa das esperadas
    pos = [usadas.index(f) for f in esperadas if f in usadas]
    ok = ok and pos == sorted(pos)
    return {"key": "trajetoria_correta", "score": int(ok), "comment": f"usadas={usadas}"}


def valor_correto(run, example) -> dict:
    esperado = example.outputs.get("valor_esperado")
    if esperado is None:
        return {"key": "valor_correto", "score": None, "comment": "caso sem valor numerico"}
    valores = _valores_brl(run.outputs.get("resposta", ""))
    ok = any(abs(v - esperado) <= 0.01 for v in valores)
    return {"key": "valor_correto", "score": int(ok), "comment": f"encontrados={valores}"}


def juiz_llm(run, example) -> dict:
    """LLM-as-a-judge. Em modo offline devolve None (sem juiz), para nao fabricar nota."""
    if llm.offline():
        return {"key": "juiz_llm", "score": None, "comment": "modo offline: juiz nao executado"}
    modelo = llm.chat_model("juiz")
    veredito = (
        modelo.invoke(
            "Compare a RESPOSTA com a REFERENCIA. Responda apenas CORRETO ou INCORRETO.\n"
            f"PERGUNTA: {example.inputs['pergunta']}\nREFERENCIA: {example.outputs['resposta_referencia']}\n"
            f"RESPOSTA: {run.outputs.get('resposta', '')}"
        )
        .content.strip()
        .upper()
    )
    # cuidado: 'CORRETO' tambem esta contido em 'INCORRETO'
    return {"key": "juiz_llm", "score": int(veredito.startswith("CORRETO")), "comment": veredito}


AVALIADORES = [trajetoria_correta, valor_correto, juiz_llm]


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #
def executar_caso(pergunta: str, agente=None, thread_id: str | None = None) -> dict:
    agente = agente or montar_agente()
    cfg = {"configurable": {"thread_id": thread_id or f"eval_{abs(hash(pergunta)) % 10**6}"}}
    estado = agente.invoke({"messages": [("user", pergunta)]}, config=cfg)
    return {"resposta": estado["messages"][-1].content, "trajetoria": trajetoria(estado)}


class _Run:
    def __init__(self, outputs: dict):
        self.outputs = outputs


class _Example:
    def __init__(self, caso: dict):
        self.inputs = {"pergunta": caso["pergunta"]}
        self.outputs = {k: v for k, v in caso.items() if k != "pergunta"}


def avaliar_localmente(agente=None, casos: list[dict] | None = None) -> list[dict]:
    """Roda os casos no agente e aplica os avaliadores sem depender do LangSmith."""
    agente = agente or montar_agente()
    linhas = []
    for caso in casos or CASOS_TESTE:
        saida = executar_caso(caso["pergunta"], agente, thread_id=f"eval_{caso['nome']}")
        linha = {"caso": caso["nome"], "resposta": saida["resposta"][:90]}
        for av in AVALIADORES:
            r = av(_Run(saida), _Example(caso))
            linha[r["key"]] = r["score"]
        linhas.append(linha)
    return linhas


def salvar_dataset_local(caminho: Path = ARQUIVO_DATASET) -> Path:
    caminho.write_text(json.dumps(CASOS_TESTE, ensure_ascii=False, indent=1), encoding="utf-8")
    return caminho


def langsmith_disponivel() -> bool:
    return bool(os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY"))


def enviar_dataset_langsmith(nome: str = "apuracota-casos"):
    """Cria (ou reutiliza) o dataset no LangSmith com os casos de teste."""
    from langsmith import Client

    client = Client()
    existente = next((d for d in client.list_datasets(dataset_name=nome)), None)
    dataset = existente or client.create_dataset(dataset_name=nome, description="Casos de teste do ApuraCota")
    if not existente:
        client.create_examples(
            inputs=[{"pergunta": c["pergunta"]} for c in CASOS_TESTE],
            outputs=[{k: v for k, v in c.items() if k != "pergunta"} for c in CASOS_TESTE],
            dataset_id=dataset.id,
        )
    return dataset


def avaliar_no_langsmith(nome_dataset: str = "apuracota-casos", prefixo: str = "apuracota"):
    """langsmith.evaluate com os tres avaliadores sobre o agente integrador."""
    from langsmith import evaluate

    agente = montar_agente()
    return evaluate(
        lambda entrada: executar_caso(entrada["pergunta"], agente),
        data=nome_dataset,
        evaluators=AVALIADORES,
        experiment_prefix=prefixo,
        metadata={"modelo": config.MODELO_AGENTE, "offline": llm.offline()},
    )
