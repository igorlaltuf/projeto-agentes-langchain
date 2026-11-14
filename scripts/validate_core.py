"""Roteiro de aceitação do núcleo sobre o banco selecionado; offline não avalia o LLM real."""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--online", action="store_true")
    p.add_argument("--saida", type=Path, default=Path("entregaveis/validacao-nucleo.json"))
    args = p.parse_args()
    os.environ["MODO_OFFLINE"] = "false" if args.online else "true"
    if not args.online:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
    from src import config
    from src.agent import montar_agente, trajetoria
    from src.periods import anos_relativos

    resultado = {
        "modo": "online" if args.online else "roteirizado offline",
        "limite": "Offline valida ferramentas e fluxo; não mede qualidade do LLM ou HTTP real.",
        "casos": [],
        "status": "EM_EXECUCAO",
    }
    if args.online:
        config.USAR_CACHE_API = False
        import httpx

        try:
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY ausente")
            httpx.get("https://api.openai.com", timeout=10)
        except (httpx.HTTPError, ValueError) as exc:
            resultado.update(status="INDISPONIVEL", erro=type(exc).__name__)
            args.saida.parent.mkdir(parents=True, exist_ok=True)
            args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
            raise SystemExit("Teste online indisponível; resultado registrado.") from None
    print("Preparando agente e referências do banco...", flush=True)
    agente = montar_agente(com_memoria_episodica=False, escopo_ampliado=False)
    anos = anos_relativos("últimos 5 anos")
    with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as c:

        def total(ano):
            return c.execute(
                "SELECT ROUND(SUM(valor),2) FROM despesas_ceap WHERE id_deputado=204534 AND ano=? AND categoria LIKE '%COMBUST%'",
                (ano,),
            ).fetchone()[0]

        ranking = c.execute(
            "SELECT id_deputado,ROUND(SUM(valor),2) FROM despesas_ceap WHERE id_deputado IS NOT NULL AND ano BETWEEN ? AND ? AND categoria LIKE '%COMBUST%' GROUP BY id_deputado,nome_parlamentar ORDER BY SUM(valor) DESC LIMIT 1",
            (min(anos), max(anos)),
        ).fetchone()
        casos = [
            (
                "individual",
                "pessoa",
                "Quanto a deputada Tabata Amaral gastou com combustível em 2025?",
                "listar_despesas",
                total(2025),
            ),
            ("outro_ano", "pessoa", "E em 2024?", "listar_despesas", total(2024)),
            (
                "ranking",
                "ranking",
                "Qual deputado mais gastou com combustível nos últimos 5 anos?",
                "consultar_ceap",
                ranking[1],
            ),
            ("anual", "ranking", "Quanto ele gastou por ano?", "consultar_ceap", ranking[1]),
            (
                "fornecedores",
                "ranking",
                "Quais os maiores fornecedores dele?",
                "investigar_fornecedores",
                None,
            ),
            (
                "cadastro",
                "empresa",
                "Consulte o cadastro do CNPJ 33.927.528/0001-60.",
                "dados_fornecedor",
                None,
            ),
            ("sancoes", "empresa", "Essa empresa aparece no CEIS ou CNEP?", "consultar_sancoes", None),
            ("rag", "regra", "A cota pode pagar hospedagem em Brasília?", "consultar_regras_cota", None),
            ("fora_escopo", "escopo", "Quais bens o deputado declarou ao TSE em 2022?", None, None),
        ]
    for nome, sessao, pergunta, ferramenta, valor in casos:
        inicio = time.monotonic()
        try:
            estado = agente.invoke(
                {"messages": [("user", pergunta)]},
                config={"configurable": {"thread_id": "aceitacao-" + sessao}, "recursion_limit": 20},
            )
            texto = str(estado["messages"][-1].content)
            passos = trajetoria(estado)
            usadas = [x["ferramenta"] for x in passos if x["tipo"] == "decisao"]
            valores = [
                float(v.replace(".", "").replace(",", "."))
                for v in re.findall(r"R\$\s*([\d.]+,\d{2})", texto)
            ]
            ok = (
                (ferramenta in usadas)
                if ferramenta
                else not usadas and bool(re.search(r"escopo|limit|fora|não consult", texto.lower()))
            )
            if valor is not None:
                ok = ok and any(abs(v - valor) <= 0.01 for v in valores)
            if nome == "outro_ano":
                ch = next(
                    (
                        x["args"]
                        for x in passos
                        if x.get("ferramenta") == "listar_despesas" and x["tipo"] == "decisao"
                    ),
                    {},
                )
                ok = (
                    ok
                    and ch.get("ano") == 2024
                    and ch.get("id_deputado") == 204534
                    and bool(ch.get("categoria"))
                )
            retornos = []
            for x in passos:
                if x["tipo"] == "retorno":
                    try:
                        retornos.append(json.loads(x["conteudo"]))
                    except ValueError:
                        pass
            indisponivel = any(x.get("status") == "INDISPONIVEL" for x in retornos)
            if nome == "cadastro":
                ok = ok and any(
                    x.get("status") == "CONSULTADO"
                    and x.get("cnpj") == "33927528000160"
                    and x.get("razao_social")
                    for x in retornos
                )
            if nome == "sancoes" and not indisponivel:
                ok = ok and any(
                    x.get("status") == "CONSULTADO" and {"ceis", "cnep"}.issubset(x.get("dados", {}))
                    for x in retornos
                )
            if nome == "rag":
                trechos = " ".join(
                    x["conteudo"]
                    for x in passos
                    if x["tipo"] == "retorno" and x["ferramenta"] == "consultar_regras_cota"
                )
                ok = ok and "hospedagem, exceto do parlamentar no Distrito Federal" in trechos
            if nome == "sancoes" and indisponivel:
                status = "PENDENTE_CARGA"
            elif nome == "cadastro" and args.online and any(x.get("erro") for x in retornos):
                status = "FALHOU"
            else:
                status = "PASSOU" if ok else "FALHOU"
            item = {
                "caso": nome,
                "pergunta": pergunta,
                "status": status,
                "resposta": texto,
                "ferramentas": usadas,
                "valor_referencia": valor,
                "verificacao": estado.get("verificacao"),
            }
        except Exception as exc:  # noqa: BLE001 — registra falhas independentes do roteiro
            item = {
                "caso": nome,
                "pergunta": pergunta,
                "status": "FALHOU",
                "erro": type(exc).__name__ + ": " + str(exc),
            }
        item["segundos"] = round(time.monotonic() - inicio, 3)
        resultado["casos"].append(item)
        print(nome, item["status"], flush=True)
    resultado["status"] = (
        "FALHOU"
        if any(x["status"] == "FALHOU" for x in resultado["casos"])
        else "EXECUTADO_COM_PENDENCIA"
        if any(x["status"].startswith("PENDENTE") for x in resultado["casos"])
        else "EXECUTADO"
    )
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n")
    print(args.saida)
    if resultado["status"] == "FALHOU":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
