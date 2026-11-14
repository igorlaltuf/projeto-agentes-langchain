"""Continuação societária por documento do resultado, sem inferir CNPJ da prosa."""

import json
import re
import sqlite3

from langchain_core.messages import HumanMessage, ToolMessage

from src import config
from src.followups import registros_sql
from src.identity import cnpj_valido, documento_posicional

PREFIXO_SOCIOS = "socios_confirmados_"


def pedido_socios(mensagens):
    pergunta = next((str(m.content) for m in reversed(mensagens) if isinstance(m, HumanMessage)), "")
    if not re.search(r"s[oó]ci[oa]s?|quadro societ[aá]rio|administradores|\bqsa\b", pergunta, re.IGNORECASE):
        return None
    if re.search(
        r"\beram\b|na [eé]poca|na data dos pagamentos|quando recebeu|hist[oó]rico societ[aá]rio|\bem\s+20\d{2}",
        pergunta,
        re.IGNORECASE,
    ):
        return {
            "erro": "O cadastro consultado não reconstitui o histórico completo de sócios. Não posso afirmar quem integrava a sociedade na época dos pagamentos apenas com o QSA atual. Posso apresentar o quadro da versão disponível, com as qualificações e datas informadas."
        }
    if m := re.search(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", pergunta):
        doc = documento_posicional(m.group(0))
        return {"cnpj": doc} if cnpj_valido(doc) else {"erro": "Informe um CNPJ completo e válido."}
    cadastro_anterior = None
    primeira_escolhida = False
    for msg in reversed(mensagens):
        if isinstance(msg, HumanMessage):
            if re.search(r"primeir[oa]", str(msg.content), re.IGNORECASE):
                primeira_escolhida = True
            explicito = re.search(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", str(msg.content))
            if explicito:
                doc = documento_posicional(explicito.group(0))
                if cnpj_valido(doc):
                    return {"cnpj": doc}
        if not isinstance(msg, ToolMessage):
            continue
        if msg.name in {"dados_fornecedor", "consultar_sancoes"}:
            try:
                d = json.loads(str(msg.content))
            except ValueError:
                continue
            if (
                cadastro_anterior is None
                and d.get("status") == "CONSULTADO"
                and cnpj_valido(d.get("cnpj", ""))
            ):
                cadastro_anterior = {"cnpj": d["cnpj"], "nome": d.get("razao_social", "")}
            # O resultado CEAP ou a seleção explícita prevalece sobre cadastro antigo.
        if msg.name in {"consultar_ceap", "investigar_fornecedores"}:
            if msg.name == "investigar_fornecedores":
                try:
                    d = json.loads(str(msg.content))
                except ValueError:
                    continue
                linhas = d.get("selecionados") or (
                    d.get("dados", []) if d.get("acao") == "ranking_fornecedores" else []
                )
                linhas = [{**r, "cnpj_cpf": r.get("cnpj")} for r in linhas]
            else:
                linhas = registros_sql(str(msg.content))
            fornecedores = [
                r
                for r in linhas
                if any(r.get(k) for k in ["fornecedor", "nome_fornecedor", "empresa", "cnpj_cpf", "cnpj"])
            ]
            if not fornecedores:
                continue
            if len(fornecedores) > 1:
                if primeira_escolhida or re.search(r"primeir[oa]", pergunta, re.IGNORECASE):
                    fornecedores = fornecedores[:1]
                else:
                    return {
                        "erro": "Há vários fornecedores no resultado. Indique a empresa ou o CNPJ, ou escolha o primeiro da lista."
                    }
            row = fornecedores[0]
            nome = next((row[k] for k in ["fornecedor", "nome_fornecedor", "empresa"] if row.get(k)), "")
            doc = documento_posicional(str(row.get("cnpj_cpf") or row.get("cnpj") or ""))
            if cnpj_valido(doc):
                return {"nome": nome, "cnpj": doc}
            # Quando o ranking omite o documento, recupera somente por nome exato e único.
            if nome and config.SQLITE_CEAP.exists():
                with sqlite3.connect(f"file:{config.SQLITE_CEAP}?mode=ro", uri=True) as c:
                    docs = {
                        documento_posicional(r[0])
                        for r in c.execute(
                            "SELECT DISTINCT cnpj_cpf FROM despesas_ceap WHERE fornecedor=?", (nome,)
                        )
                        if cnpj_valido(r[0])
                    }
                if len(docs) == 1:
                    return {"nome": nome, "cnpj": docs.pop()}
            return {
                "erro": "Não foi possível confirmar um CNPJ único e válido desse fornecedor na CEAP. Indique qual estabelecimento deseja consultar."
            }
    return cadastro_anterior or {"erro": "Qual empresa ou CNPJ você quer consultar?"}


def resposta_socios(alvo, conteudo):
    try:
        d = json.loads(conteudo)
    except ValueError:
        d = {}
    nome = alvo.get("nome") or d.get("razao_social") or "Empresa selecionada"
    doc = alvo["cnpj"]
    if d.get("status") != "CONSULTADO":
        return f"Não foi possível consultar o quadro de sócios de {nome} agora. O CNPJ confirmado na investigação é {doc}. A indisponibilidade não significa ausência de sócios."
    if documento_posicional(str(d.get("cnpj", ""))) != doc:
        return f"O cadastro retornado não corresponde ao CNPJ confirmado {doc}. Não atribuí sócios de outra empresa."
    if not d.get("qsa_disponivel"):
        return f"A consulta de {nome}, CNPJ {doc}, não trouxe o quadro de sócios. Não é possível concluir quem são os integrantes com esse retorno."
    linhas = [f"**{d.get('razao_social') or nome} — CNPJ {doc}**"]
    if not d["socios"]:
        linhas.append(
            "A versão consultada não apresenta integrantes no quadro de sócios e administradores. Isso não comprova ausência de sócios."
        )
    if d["socios"]:
        linhas.append("Quadro de sócios e administradores da versão consultada:")
    for s in d["socios"]:
        linha = f"- {s.get('nome') or 'Nome não informado'} — {s.get('qualificacao') or 'Qualificação não informada'}"
        if s.get("entrada"):
            linha += f"; entrada: {s['entrada']}"
        if s.get("documento"):
            linha += f"; documento publicado: {s['documento']}"
        linhas.append(linha)
    linhas.append(
        f"Fonte: Cadastro de CNPJ — BrasilAPI / Minha Receita. Consulta: {d.get('consultado_em', 'data não informada')}."
    )
    linhas.append(
        "O cadastro consultado não comprova a composição societária na data de cada pagamento. Documentos mascarados não confirmam identidade pessoal."
    )
    return "\n\n".join(linhas)
