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
        "nome": "gasto_categoria_local",
        "pergunta": "Quanto a deputada Tabata Amaral gastou com combustível em 2025?",
        "ferramentas_esperadas": ["buscar_deputado", "listar_despesas"],
        "valor_esperado": 48808.83,
        "resposta_referencia": "Tabata Amaral gastou R$ 48.808,83 com combustíveis em 2025 (CSV CEAP).",
    },
    {
        "nome": "gasto_total_local",
        "pergunta": "Qual foi o gasto total do deputado Nikolas Ferreira em 2025?",
        "ferramentas_esperadas": ["buscar_deputado", "listar_despesas"],
        "valor_esperado": 389263.25,
        "resposta_referencia": "Nikolas Ferreira gastou R$ 389.263,25 em 2025 (CSV CEAP).",
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
        "resposta_referencia": "Consultar a busca local antes de esclarecer ambiguidade; se houver vários deputados, pedir qual. Se não houver correspondência, informar ausência na base.",
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


CASOS_TESTE.extend(
    [
        {
            "nome": "emendas_pagamentos",
            "pergunta": "Quais emendas foram pagas em 2026?",
            "ferramentas_esperadas": ["consultar_dados"],
            "valor_esperado": 500.0,
            "resposta_referencia": "Amostra sintética: três autores distintos com R$ 500,00 cada; autoria individual e coletiva separadas.",
        },
        {
            "nome": "tse_bens",
            "pergunta": "Quais bens declarados em 2022?",
            "ferramentas_esperadas": ["consultar_dados"],
            "valor_esperado": 10000.0,
            "resposta_referencia": "Amostra sintética: Pessoa de Demonstração declarou R$ 10.000,00 em bens em 2022.",
        },
        {
            "nome": "tse_receitas",
            "pergunta": "Quais receitas de campanha em 2018?",
            "ferramentas_esperadas": ["consultar_dados"],
            "valor_esperado": 1000.0,
            "resposta_referencia": "Amostra sintética: R$ 1.000,00 em receitas eleitorais de 2018.",
        },
        {
            "nome": "tse_despesas",
            "pergunta": "Quais despesas de campanha em 2026?",
            "ferramentas_esperadas": ["consultar_dados"],
            "valor_esperado": 250.5,
            "resposta_referencia": "Amostra sintética: R$ 250,50 em despesa de campanha, fornecedor fictício.",
        },
        {
            "nome": "sancoes_demo",
            "pergunta": "Há sanções para o CNPJ 11.222.333/0001-81?",
            "ferramentas_esperadas": ["consultar_sancoes"],
            "resposta_referencia": "Fixture sintética de demonstração, não consulta factual; sem conclusão de irregularidade.",
        },
        {
            "nome": "emenda_sem_medida",
            "pergunta": "Quanto em emendas em 2026?",
            "ferramentas_esperadas": [],
            "ferramentas_proibidas": ["consultar_dados"],
            "resposta_referencia": "Perguntar se deseja pago, empenhado ou liquidado antes de consultar.",
        },
    ]
)


# As fontes ampliadas ficam preservadas, fora da avaliação padrão do núcleo.
CASOS_AMPLIADOS = CASOS_TESTE[6:]
CASOS_TESTE = CASOS_TESTE[:6] + [
    {
        "nome": "sancoes_locais",
        "pergunta": "Há sanções para o CNPJ 33.927.528/0001-60?",
        "ferramentas_esperadas": ["consultar_sancoes"],
        "resposta_referencia": "Consultar CEIS/CNEP locais, informar datas ou indisponibilidade, sem certificar regularidade.",
    },
    {
        "nome": "escopo_eleitoral",
        "pergunta": "Quais bens declarados em 2022?",
        "ferramentas_esperadas": [],
        "ferramentas_proibidas": ["consultar_dados"],
        "resposta_referencia": "Explicar que dados eleitorais estão fora do escopo principal e oferecer consulta CEAP.",
    },
]


def _valores_brl(texto: str) -> list[float]:
    """Extrai valores apos 'R$' aceitando 48.808,83 (pt-BR), 48,808.83 (en-US) e 48808.83.

    O avaliador de valor julga o numero, nao o formato; o formato e cobrado pelo juiz LLM.
    """
    valores = []
    for bruto in re.findall(r"R\$ ?(\d[\d.,]*)", texto):
        v = bruto.rstrip(".,")
        if "," in v and "." in v:
            v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
        elif "," in v:
            v = v.replace(",", ".") if re.search(r",\d{1,2}$", v) else v.replace(",", "")
        elif v.count(".") > 1 or re.search(r"\.\d{3}$", v):
            v = v.replace(".", "")
        try:
            valores.append(float(v))
        except ValueError:
            continue
    return valores


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
    return {
        "resposta": estado["messages"][-1].content,
        "trajetoria": trajetoria(estado),
        "verificacao": estado.get("verificacao"),
    }


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


def enviar_dataset_langsmith(nome: str = "apuramandato-v1", casos: list[dict] | None = None):
    """Cria (ou reutiliza) o dataset no LangSmith com os casos de teste."""
    from langsmith import Client

    client = Client()
    existente = next((d for d in client.list_datasets(dataset_name=nome)), None)
    dataset = existente or client.create_dataset(
        dataset_name=nome, description="Casos de teste do ApuraMandato"
    )
    if not existente:
        client.create_examples(
            inputs=[{"pergunta": c["pergunta"]} for c in (casos or CASOS_TESTE)],
            outputs=[{k: v for k, v in c.items() if k != "pergunta"} for c in (casos or CASOS_TESTE)],
            dataset_id=dataset.id,
        )
    return dataset


def comparar_experimentos(nome_dataset: str = "apuramandato-v1") -> list[dict]:
    """Le no LangSmith todos os experimentos do dataset e resume notas, tokens, custo e latencia."""
    from langsmith import Client

    client = Client()
    dataset = next(client.list_datasets(dataset_name=nome_dataset))
    linhas = []
    for proj in sorted(client.list_projects(reference_dataset_id=dataset.id), key=lambda p: p.start_time):
        meta = (proj.extra or {}).get("metadata", {})
        runs = list(client.list_runs(project_id=proj.id, is_root=True))
        notas: dict[str, list[float]] = {}
        tokens = custo = 0.0
        latencias = []
        for r in runs:
            for f in client.list_feedback(run_ids=[r.id]):
                if f.score is not None:
                    notas.setdefault(f.key, []).append(float(f.score))
            tokens += r.total_tokens or 0
            custo += float(r.total_cost or 0)
            if r.end_time:
                latencias.append((r.end_time - r.start_time).total_seconds())
        linha = {
            "experimento": proj.name,
            "modelo": "dummy (offline)" if meta.get("offline") else meta.get("modelo"),
            "casos": len(runs),
            "erros": sum(1 for r in runs if r.error),
            "tokens": int(tokens),
            "custo_usd": round(custo, 4),
            "latencia_media_s": round(sum(latencias) / len(latencias), 1) if latencias else None,
            "url": proj.url,
        }
        for chave in ("trajetoria_correta", "valor_correto", "juiz_llm"):
            linha[chave] = round(sum(notas[chave]) / len(notas[chave]), 2) if notas.get(chave) else None
        linhas.append(linha)
    return linhas


def avaliar_no_langsmith(nome_dataset: str = "apuramandato-v1", prefixo: str = "apuramandato"):
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


def casos_demonstracao() -> list[dict]:
    """Referências da amostra, separadas das referências do CSV completo."""
    import copy

    casos = copy.deepcopy(CASOS_TESTE)
    for caso in casos:
        if caso["nome"] == "media_partido_sql":
            caso["valor_esperado"] = 40954.73
            caso["resposta_referencia"] = "Amostra CEAP: média do PSB de R$ 40.954,73 em 2025."
    return casos
