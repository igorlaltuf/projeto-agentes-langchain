"""Aceitação de investigação completa: banco real, oráculos SQL e conversa; offline não é prova do LLM online."""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--online", action="store_true")
    p.add_argument("--saida", type=Path, default=Path("entregaveis/investigacao-completa.json"))
    args = p.parse_args()
    os.environ["MODO_OFFLINE"] = "false" if args.online else "true"
    if not args.online:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
    from decimal import Decimal

    from src import config
    from src.agent import montar_agente, trajetoria

    def _centavos(v):
        return int(Decimal(str(v)) * 100)

    def _pct(a, b):
        return float((Decimal(a) * 100 / Decimal(b)).quantize(Decimal(".01"))) if b else None

    rel = {
        "executado_em": datetime.now(UTC).isoformat(),
        "banco": str(config.SQLITE_CEAP),
        "modo": "online" if args.online else "roteirizado offline",
        "limites": "Banco CEAP real; cadastro offline é fixture. LLM/HTTP real e fontes ausentes não recebem aprovação simulada.",
        "casos": [],
    }
    if args.online:
        config.USAR_CACHE_API = False
        import httpx

        try:
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("Sem chave")
            httpx.get("https://api.openai.com", timeout=10)
        except (httpx.HTTPError, ValueError) as e:
            rel.update(status="INDISPONIVEL", erro=type(e).__name__)
            args.saida.parent.mkdir(parents=True, exist_ok=True)
            args.saida.write_text(json.dumps(rel, ensure_ascii=False, indent=2))
            raise SystemExit(1) from None
    print("Preparando investigação de Tabata Amaral, 2024–2026...", flush=True)
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as c:
        base = [
            (doc, nome, round(v, 2))
            for doc, nome, v in c.execute(
                "SELECT cnpj_cpf,MAX(fornecedor),SUM(valor) FROM despesas_ceap WHERE id_deputado=204534 AND ano BETWEEN 2024 AND 2026 AND length(cnpj_cpf)=14 GROUP BY cnpj_cpf ORDER BY SUM(valor) DESC LIMIT 10"
            )
        ]
        doc = base[0][0]
        total = c.execute(
            "SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE id_deputado=204534 AND ano BETWEEN 2024 AND 2026"
        ).fetchone()[0]
        top3 = sum(_centavos(r[2]) for r in base[:3])
        universo = c.execute(
            "SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE cnpj_cpf=? AND ano BETWEEN 2024 AND 2026",
            (doc,),
        ).fetchone()[0]
        anuais = {
            (d, a): round(v, 2)
            for d, a, v in c.execute(
                "SELECT cnpj_cpf,ano,SUM(valor) FROM despesas_ceap WHERE id_deputado=204534 AND ano BETWEEN 2024 AND 2026 GROUP BY cnpj_cpf,ano"
            )
        }
        cat = {
            (d, k): round(v, 2)
            for d, k, v in c.execute(
                "SELECT cnpj_cpf,categoria,SUM(valor) FROM despesas_ceap WHERE id_deputado=204534 AND ano BETWEEN 2024 AND 2026 GROUP BY cnpj_cpf,categoria"
            )
        }
        outros = {
            i: round(v, 2)
            for i, v in c.execute(
                "SELECT id_deputado,SUM(valor) FROM despesas_ceap WHERE cnpj_cpf=? AND ano BETWEEN 2024 AND 2026 AND id_deputado<>204534 GROUP BY id_deputado",
                (doc,),
            )
        }
        rank2025 = [
            (d, round(v, 2))
            for d, v in c.execute(
                "SELECT cnpj_cpf,SUM(valor) FROM despesas_ceap WHERE id_deputado=204534 AND ano=2025 AND length(cnpj_cpf)=14 GROUP BY cnpj_cpf ORDER BY SUM(valor) DESC LIMIT 10"
            )
        ]
    agente = montar_agente(com_memoria_episodica=False)
    perguntas = [
        ("ranking", "Quais foram os dez maiores fornecedores da Tabata Amaral entre 2024 e 2026?"),
        ("anual", "Quanto cada um dos três primeiros fornecedores recebeu por ano?"),
        ("categorias", "Em quais categorias esses três fornecedores aparecem?"),
        ("concentracao", "Qual percentual do gasto total dela foi destinado aos três maiores fornecedores?"),
        ("cadastro", "Consulte o cadastro do primeiro fornecedor."),
        ("socios", "Quem são os sócios?"),
        (
            "outros",
            "Esse mesmo CNPJ também recebeu por despesas de outros deputados nesse período? Quais e quanto?",
        ),
        (
            "participacao",
            "Qual a participação da Tabata nas despesas desse CNPJ na CEAP, comparada aos demais deputados?",
        ),
        ("sancoes", "Essa empresa aparece no CEIS ou no CNEP?"),
        ("faturamento", "Esse percentual representa a participação no faturamento da empresa?"),
        ("acusacao", "Isso prova favorecimento ou corrupção?"),
        ("regras", "Quais regras da cota se aplicam à locação de escritório?"),
        ("muda_periodo", "Refaça o ranking de fornecedores apenas em 2025."),
        ("ambiguo", "Quem são os sócios?"),
    ]
    for nome, pergunta in perguntas:
        t = time.monotonic()
        try:
            e = agente.invoke(
                {"messages": [("user", pergunta)]},
                config={"configurable": {"thread_id": "investigacao-2024-2026"}, "recursion_limit": 20},
            )
            texto = str(e["messages"][-1].content)
            passos = trajetoria(e)
            dados = None
            retornos = []
            for x in passos:
                if x["tipo"] == "retorno":
                    try:
                        r = json.loads(x["conteudo"])
                        retornos.append(r)
                    except ValueError:
                        continue
                    if x["ferramenta"] == "investigar_fornecedores":
                        dados = r
            status = "PASSOU"
            if nome in {
                "ranking",
                "anual",
                "categorias",
                "concentracao",
                "outros",
                "participacao",
                "muda_periodo",
            }:
                assert dados and dados["status"] == "CONSULTADO", (
                    "Consulta estruturada ausente ou indisponível"
                )
                assert dados["id_deputado"] == 204534, "Deputado trocado"
                assert dados["anos"] == ([2025] if nome == "muda_periodo" else [2024, 2025, 2026]), (
                    "Período trocado"
                )
                rows = dados["dados"]
                if nome == "ranking":
                    assert [(r["cnpj"], r["valor"]) for r in rows] == [(d, v) for d, _, v in base], (
                        "Ranking diferente do SQL independente"
                    )
                if nome == "anual":
                    assert all(
                        abs(r["valor"] - anuais.get((r["cnpj"], r["ano"]), 0)) < 0.01 for r in rows
                    ) and {r["cnpj"] for r in rows} == {r[0] for r in base[:3]}, "Anos ou seleção divergentes"
                if nome == "categorias":
                    assert all(abs(r["valor"] - cat[(r["cnpj"], r["categoria"])]) < 0.01 for r in rows), (
                        "Categorias divergentes"
                    )
                if nome == "concentracao":
                    assert (
                        rows[0]["percentual"] == _pct(top3, _centavos(total))
                        and rows[0]["gasto_total_deputado"] == total
                    ), "Denominador ou percentual incorreto"
                if nome == "outros":
                    assert all(
                        r["id_deputado"] != 204534 and abs(r["valor"] - outros[r["id_deputado"]]) < 0.01
                        for r in rows
                    ), "Outro deputado ou documento incorreto"
                if nome == "outros":
                    esperado = dict(
                        sorted(outros.items(), key=lambda x: x[1], reverse=True)[: dados["limite"]]
                    )
                    assert {r["id_deputado"]: r["valor"] for r in rows} == esperado, (
                        "Outros deputados omitidos ou inventados"
                    )
                if nome == "participacao":
                    assert (
                        rows[0]["percentual_na_ceap"] == _pct(_centavos(base[0][2]), _centavos(universo))
                        and rows[0]["valor_todos_deputados_ceap"] == universo
                    ), "Participação com denominador incorreto"
                if nome == "muda_periodo":
                    assert [(r["cnpj"], r["valor"]) for r in rows] == rank2025, (
                        "Ranking não acompanhou a troca de período"
                    )
            elif nome == "cadastro":
                assert any(x.get("status") == "CONSULTADO" and x.get("cnpj") == doc for x in retornos), (
                    "Cadastro incorreto ou indisponível"
                )
            elif nome == "socios":
                ch = [x for x in passos if x["tipo"] == "decisao"]
                assert ch and ch[-1]["args"].get("cnpj") == doc, "CNPJ errado na continuidade societária"
                if any(x.get("status") == "INDISPONIVEL" or not x.get("qsa_disponivel") for x in retornos):
                    status = "PENDENTE_FONTE_QSA"
            elif nome == "sancoes":
                assert any(x.get("cnpj") == doc or x.get("status") == "INDISPONIVEL" for x in retornos), (
                    "Consulta de sanções ausente"
                )
                if any(x.get("status") == "INDISPONIVEL" for x in retornos):
                    status = "PENDENTE_CARGA"
            elif nome == "regras":
                trechos = " ".join(
                    p["conteudo"]
                    for p in passos
                    if p["tipo"] == "retorno" and p["ferramenta"] == "consultar_regras_cota"
                )
                from src.identity import nome_normalizado

                assert "ESCRITORIO" in nome_normalizado(trechos) and "Ato da Mesa" in trechos, (
                    "Recuperação de norma ausente ou sem o tema solicitado"
                )
            elif nome == "faturamento":
                assert "não tem" in texto.lower() and "faturamento total" in texto.lower(), (
                    "Faturamento indevidamente inferido"
                )
            elif nome == "acusacao":
                assert "não provam" in texto.lower() and not [x for x in passos if x["tipo"] == "decisao"], (
                    "Acusação sem suporte ou consulta desnecessária"
                )
            elif nome == "ambiguo":
                assert not [x for x in passos if x["tipo"] == "decisao"] and any(
                    s in texto.lower() for s in ["vários", "qual", "escolh", "indique"]
                ), "Seleção arbitrária de empresa"
            item = {
                "caso": nome,
                "pergunta": pergunta,
                "status": status,
                "resposta": texto,
                "decisoes": [x for x in passos if x["tipo"] == "decisao"],
                "retornos": retornos,
                "evidencias_textuais": [
                    p["conteudo"]
                    for p in passos
                    if p["tipo"] == "retorno" and p["ferramenta"] == "consultar_regras_cota"
                ],
            }
        except Exception as exc:  # noqa: BLE001 — mantém evidências dos casos independentes
            item = {"caso": nome, "pergunta": pergunta, "status": "FALHOU", "erro": str(exc)}
        item["segundos"] = round(time.monotonic() - t, 3)
        rel["casos"].append(item)
        print(nome, item["status"], item.get("erro", ""), flush=True)
    rel["status"] = (
        "FALHOU"
        if any(x["status"] == "FALHOU" for x in rel["casos"])
        else "EXECUTADO_COM_PENDENCIA"
        if any(x["status"].startswith("PENDENTE") for x in rel["casos"])
        else "EXECUTADO"
    )
    rel["referencias"] = {
        "top10": base,
        "cnpj_primeiro": doc,
        "gasto_total": total,
        "total_mesmo_cnpj_todos_deputados": universo,
        "nota": "As referências são consultas independentes na mesma CEAP; não auditoria independente da fonte. A função de arredondamento percentual é compartilhada.",
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(rel, ensure_ascii=False, indent=2) + "\n")
    print(args.saida)
    if rel["status"] == "FALHOU":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
