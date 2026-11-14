"""Resolução auditável de identidades: documentos, evidências e revisão humana."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import httpx

from src import config

SCHEMA_IDENTIDADE = """
CREATE TABLE IF NOT EXISTS evidencias_pessoa(
 pessoa_id INTEGER NOT NULL REFERENCES pessoas(id), fonte TEXT NOT NULL, referencia TEXT NOT NULL,
 nome TEXT NOT NULL, cpf TEXT NOT NULL DEFAULT '', nascimento TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(pessoa_id,fonte,referencia,nome,cpf,nascimento));
CREATE TABLE IF NOT EXISTS revisoes_identidade(
 id INTEGER PRIMARY KEY, fonte TEXT NOT NULL, identificador TEXT NOT NULL,
 pessoa_id INTEGER NOT NULL REFERENCES pessoas(id), metodo TEXT NOT NULL, evidencia TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('SUGERIDO','CONFIRMADO','REJEITADO','CONFLITO')),
 criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revisado_em TEXT, revisor TEXT,
 assinatura TEXT NOT NULL DEFAULT '',
 UNIQUE(fonte,identificador,pessoa_id));
CREATE TABLE IF NOT EXISTS identidades_eleitorais(
 candidatura_id TEXT PRIMARY KEY REFERENCES candidaturas(id), grupo TEXT NOT NULL,
 metodo TEXT NOT NULL, eleicao INTEGER NOT NULL, criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE VIEW IF NOT EXISTS v_identidades_eleitorais AS
 SELECT candidatura_id,grupo,metodo,eleicao FROM identidades_eleitorais;
CREATE TABLE IF NOT EXISTS historico_identidade(
 id INTEGER PRIMARY KEY, fonte TEXT NOT NULL, identificador TEXT NOT NULL, pessoa_id INTEGER NOT NULL,
 acao TEXT NOT NULL, evidencia TEXT NOT NULL, revisor TEXT NOT NULL,
 criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def nome_normalizado(nome: str) -> str:
    texto = "".join(c for c in unicodedata.normalize("NFKD", nome.upper()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", texto).split())


def documento_posicional(texto: str) -> str:
    """Retém posições ocultas; jamais completa zeros ou reconstrói CPF mascarado."""
    texto = str(texto).strip()
    if texto.upper() in {"", "#NULO", "#NE", "-1", "-3", "-4", "S/I"}:
        return ""
    return re.sub(r"[.\-/\s]", "", texto)


def cpf_valido(texto: str) -> bool:
    d = documento_posicional(texto)
    if not re.fullmatch(r"\d{11}", d) or len(set(d)) == 1:
        return False
    for n in (9, 10):
        digito = (sum(int(d[i]) * (n + 1 - i) for i in range(n)) * 10) % 11 % 10
        if digito != int(d[n]):
            return False
    return True


def cnpj_valido(texto: str) -> bool:
    d = documento_posicional(texto)
    if not re.fullmatch(r"\d{14}", d) or len(set(d)) == 1:
        return False
    for n, pesos in [
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ]:
        resto = sum(int(d[i]) * pesos[i] for i in range(n)) % 11
        if int(d[n]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def documentos_compativeis(a: str, b: str) -> bool:
    a, b = documento_posicional(a), documento_posicional(b)
    return (
        len(a) == len(b) == 11
        and bool(re.fullmatch(r"[0-9*]{11}", a))
        and bool(re.fullmatch(r"[0-9*]{11}", b))
        and any(x.isdigit() and y.isdigit() for x, y in zip(a, b))
        and all(x == y or "*" in (x, y) for x, y in zip(a, b))
    )


def nascimento_normalizado(texto: str) -> str:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(texto).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def enriquecer_locais() -> dict:
    """Reaproveita CSVs e proveniência já baixados; não faz chamadas externas."""
    from src.catalog import _ler, banco, inicializar_catalogo

    inicializar_catalogo()
    registros = 0
    with banco() as c:
        for path in sorted(config.DATA_DIR.glob("ceap_*.csv")):
            print(f"Identidades: lendo {path.name}...", flush=True)
            vistos = set()
            referencia = str(path.relative_to(config.DATA_DIR))
            c.execute(
                "DELETE FROM evidencias_pessoa WHERE fonte='CAMARA:CSV' AND referencia=?", (referencia,)
            )
            for chunk in _ler(path):
                for row in chunk.to_dict("records"):
                    ident = str(row.get("ideCadastro", "")).strip()
                    if not ident.isdigit():
                        continue
                    item = (
                        int(ident),
                        "CAMARA:CSV",
                        referencia,
                        row.get("txNomeParlamentar", ""),
                        documento_posicional(row.get("cpf", "")),
                        "",
                    )
                    if item in vistos:
                        continue
                    vistos.add(item)
                    if c.execute("SELECT 1 FROM pessoas WHERE id=?", (item[0],)).fetchone():
                        c.execute("INSERT OR IGNORE INTO evidencias_pessoa VALUES(?,?,?,?,?,?)", item)
                        registros += 1
        print("Identidades: recuperando CPF e nascimento do TSE...", flush=True)
        # Restaura campos novos sem reimportar nem alterar as finanças existentes.
        for ident, lote, linha in c.execute("SELECT id,lote_id,linha_origem FROM candidaturas").fetchall():
            r = c.execute(
                "SELECT conteudo FROM registros_origem WHERE lote_id=? AND linha=?", (lote, linha)
            ).fetchone()
            if r:
                row = json.loads(r[0])
                c.execute(
                    "UPDATE candidaturas SET cpf=?,nascimento=? WHERE id=?",
                    (
                        documento_posicional(row.get("NR_CPF_CANDIDATO", "")),
                        nascimento_normalizado(row.get("DT_NASCIMENTO", "")),
                        ident,
                    ),
                )
    return {"evidencias_camara": registros}


def enriquecer_camara() -> dict:
    """Consulta cada ID existente uma vez, com cache local e transação por pessoa."""
    from src.catalog import banco, inicializar_catalogo

    inicializar_catalogo()
    cache = config.DATA_DIR / "cadastros/camara"
    cache.mkdir(parents=True, exist_ok=True)
    with banco() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM pessoas")]
    erros = []
    ok = 0
    with httpx.Client(timeout=60, follow_redirects=True, headers={"Accept": "application/json"}) as client:
        for ident in ids:
            url = f"{config.API_CAMARA}/deputados/{ident}"
            p = cache / f"{ident}.json"
            try:
                if p.exists():
                    row = json.loads(p.read_text())
                else:
                    resposta = client.get(url)
                    resposta.raise_for_status()
                    row = resposta.json()["dados"]
                    if int(row["id"]) != ident:
                        raise ValueError("ID retornado diverge do solicitado")
                    p.write_text(json.dumps(row, ensure_ascii=False))
                if int(row["id"]) != ident:
                    raise ValueError("ID do cache diverge")
                with banco() as c:
                    c.execute(
                        "DELETE FROM evidencias_pessoa WHERE pessoa_id=? AND fonte='CAMARA:API'", (ident,)
                    )
                    c.execute(
                        "INSERT INTO evidencias_pessoa VALUES(?,?,?,?,?,?)",
                        (
                            ident,
                            "CAMARA:API",
                            url,
                            row.get("nomeCivil") or row["ultimoStatus"]["nome"],
                            documento_posicional(row.get("cpf", "")),
                            nascimento_normalizado(row.get("dataNascimento", "")),
                        ),
                    )
                ok += 1
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                erros.append({"id": ident, "erro": type(exc).__name__})
                if isinstance(exc, httpx.ConnectError):
                    break
    return {"cadastros": ok, "falhas": erros, "pendentes": len(ids) - ok - len(erros)}


def propor_vinculos() -> dict:
    """Só publica CPF válido único e coerente; demais evidências exigem revisão."""
    from src.catalog import banco, inicializar_catalogo, vincular_identidade

    inicializar_catalogo()
    propostas = []
    with banco() as c:
        # Identidade dentro do TSE independe de a pessoa ter exercido mandato na Câmara.
        grupos = defaultdict(list)
        nomes_por_cpf = defaultdict(set)
        for ident, cpf, nasc, eleicao, nome in c.execute(
            "SELECT id,cpf,nascimento,eleicao,nome FROM candidaturas"
        ):
            if cpf_valido(cpf):
                grupos[cpf].append((ident, nasc, eleicao))
                nomes_por_cpf[cpf].add(nome_normalizado(nome))
        c.execute("DELETE FROM identidades_eleitorais")
        cpfs_conflitantes = set()
        for cpf_grupo, registros in grupos.items():
            datas = {r[1] for r in registros if r[1]}
            nomes_documento = nomes_por_cpf[cpf_grupo]
            eleicoes = [r[2] for r in registros]
            if len(datas) > 1 or len(nomes_documento) > 1 or len(eleicoes) != len(set(eleicoes)):
                cpfs_conflitantes.add(cpf_grupo)
                continue
            grupo = min(r[0] for r in registros)
            c.executemany(
                "INSERT INTO identidades_eleitorais(candidatura_id,grupo,metodo,eleicao) VALUES(?,?,'cpf_validado_sem_conflito',?)",
                [(r[0], grupo, r[2]) for r in registros],
            )
        evidencias = defaultdict(list)
        por_cpf = defaultdict(set)
        por_nome = defaultdict(set)
        for pessoa, fonte, ref, nome, cpf, nasc in c.execute("SELECT * FROM evidencias_pessoa"):
            evidencias[pessoa].append((nome, cpf, nasc, fonte, ref))
            if cpf_valido(cpf):
                por_cpf[cpf].add(pessoa)
            if nome_normalizado(nome):
                por_nome[nome_normalizado(nome)].add(pessoa)
        # Nome parlamentar e civil são alternativas de busca, não provas isoladas.
        for pessoa, nome in c.execute("SELECT id,nome FROM pessoas"):
            if nome_normalizado(nome):
                por_nome[nome_normalizado(nome)].add(pessoa)
        alvos = [
            ("TSE", i, n, d, b) for i, n, d, b in c.execute("SELECT id,nome,cpf,nascimento FROM candidaturas")
        ]
        alvos += [
            ("CGU", i, n, "", "")
            for i, n in c.execute(
                "SELECT id,nome FROM autores_emenda WHERE tipo='individual' AND id NOT LIKE 'sem-identificacao:%'"
            )
        ]
        for fonte, ident, nome, cpf, nasc in alvos:
            candidatos = por_nome.get(nome_normalizado(nome), set()) | por_cpf.get(cpf, set())
            for pessoa in sorted(candidatos):
                ev = evidencias[pessoa]
                docs = {d for _, d, _, _, _ in ev if cpf_valido(d)}
                nascs = {b for _, _, b, _, _ in ev if b}
                igual = cpf_valido(cpf) and cpf in docs
                conflito = (
                    cpf in cpfs_conflitantes
                    or len(docs) > 1
                    or len(nascs) > 1
                    or (cpf_valido(cpf) and bool(docs) and cpf not in docs)
                    or (bool(nasc) and bool(nascs) and nasc not in nascs)
                    or any(
                        d and cpf and len(d) == len(cpf) == 11 and not documentos_compativeis(d, cpf)
                        for _, d, _, _, _ in ev
                    )
                )
                nome_ok = pessoa in por_nome.get(nome_normalizado(nome), set())
                mascara = nome_ok and any(
                    "*" in (d + cpf) and documentos_compativeis(d, cpf) for _, d, _, _, _ in ev
                )
                metodo = (
                    "cpf_completo"
                    if igual
                    else "nome_nascimento_cpf_parcial"
                    if mascara and nasc in nascs
                    else "nome_cpf_parcial"
                    if mascara
                    else "nome_nascimento"
                    if nasc and nasc in nascs and nome_ok
                    else "nome_normalizado"
                )
                status = (
                    "CONFLITO"
                    if conflito or (igual and len(por_cpf[cpf]) != 1)
                    else "CONFIRMADO"
                    if igual
                    else "SUGERIDO"
                )
                evidencia = json.dumps(
                    {
                        "regra": metodo,
                        "alvo": {"fonte": fonte, "id": ident},
                        "fontes": [{"fonte": f, "referencia": r} for _, _, _, f, r in ev],
                    },
                    ensure_ascii=False,
                )
                publicado = c.execute(
                    "SELECT pessoa_id FROM identificadores_externos WHERE fonte=? AND identificador=?",
                    (fonte, ident),
                ).fetchone()
                if publicado and publicado[0] != pessoa:
                    status = "CONFLITO"
                assinatura = hashlib.sha256(
                    json.dumps(
                        {
                            "alvo": [nome, cpf, nasc],
                            "evidencias": sorted(ev),
                            "pessoas_cpf": sorted(por_cpf.get(cpf, set())),
                            "grupo_tse": grupos.get(cpf, []),
                            "nomes_tse": sorted(nomes_por_cpf.get(cpf, set())),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                anterior = c.execute(
                    "SELECT status,assinatura,revisor FROM revisoes_identidade WHERE fonte=? AND identificador=? AND pessoa_id=?",
                    (fonte, ident, pessoa),
                ).fetchone()
                if anterior and anterior[0] == "REJEITADO":
                    continue
                if anterior and anterior[0] == "CONFIRMADO":
                    if (
                        anterior[1] == assinatura
                        and anterior[2]
                        and not anterior[2].startswith("automatico:")
                    ):
                        continue  # Revisão humana da mesma evidência permanece válida.
                    if status != "CONFLITO":
                        continue
                    # Evidência nova contraditória suspende o vínculo publicado.
                    c.execute(
                        "DELETE FROM identificadores_externos WHERE fonte=? AND identificador=? AND pessoa_id=?",
                        (fonte, ident, pessoa),
                    )
                    tabela = "candidaturas" if fonte == "TSE" else "autores_emenda"
                    c.execute(
                        f"UPDATE {tabela} SET pessoa_id=NULL WHERE id=? AND pessoa_id=?", (ident, pessoa)
                    )
                    c.execute(
                        "INSERT INTO historico_identidade(fonte,identificador,pessoa_id,acao,evidencia,revisor) VALUES(?,?,?,'SUSPENSO_CONFLITO',?,'automatico:auditoria')",
                        (fonte, ident, pessoa, evidencia),
                    )
                c.execute(
                    "INSERT INTO revisoes_identidade(fonte,identificador,pessoa_id,metodo,evidencia,status,assinatura) VALUES(?,?,?,?,?,?,?) ON CONFLICT(fonte,identificador,pessoa_id) DO UPDATE SET metodo=excluded.metodo,evidencia=excluded.evidencia,status=excluded.status,assinatura=excluded.assinatura",
                    (
                        fonte,
                        ident,
                        pessoa,
                        metodo,
                        evidencia,
                        "SUGERIDO" if status == "CONFIRMADO" else status,
                        assinatura,
                    ),
                )
                if status == "CONFIRMADO":
                    propostas.append((pessoa, fonte, ident, evidencia))
    for pessoa, fonte, ident, evidencia in propostas:
        vincular_identidade(pessoa, fonte, ident, evidencia, revisor="automatico:cpf_validado")
    with banco() as c:
        return dict(c.execute("SELECT status,COUNT(*) FROM revisoes_identidade GROUP BY status").fetchall())


def revisar_vinculo(revisao: int, aprovar: bool, evidencia: str, revisor: str):
    from src.catalog import banco, vincular_identidade

    if not evidencia.strip() or not revisor.strip():
        raise ValueError("Informe evidência e revisor")
    with banco() as c:
        row = c.execute(
            "SELECT fonte,identificador,pessoa_id FROM revisoes_identidade WHERE id=?", (revisao,)
        ).fetchone()
        if not row:
            raise ValueError("Revisão não encontrada")
        if not aprovar:
            if c.execute(
                "SELECT 1 FROM identificadores_externos WHERE fonte=? AND identificador=? AND pessoa_id=?",
                row,
            ).fetchone():
                c.execute(
                    "DELETE FROM identificadores_externos WHERE fonte=? AND identificador=? AND pessoa_id=?",
                    row,
                )
                tabela = "candidaturas" if row[0] == "TSE" else "autores_emenda"
                c.execute(f"UPDATE {tabela} SET pessoa_id=NULL WHERE id=? AND pessoa_id=?", (row[1], row[2]))
            c.execute(
                "UPDATE revisoes_identidade SET status='REJEITADO',evidencia=?,revisor=?,revisado_em=CURRENT_TIMESTAMP WHERE id=?",
                (evidencia, revisor, revisao),
            )
            c.execute(
                "INSERT INTO historico_identidade(fonte,identificador,pessoa_id,acao,evidencia,revisor) VALUES(?,?,?,?,?,?)",
                (*row, "REJEITADO", evidencia, revisor),
            )
    if aprovar:
        vincular_identidade(row[2], row[0], row[1], evidencia, revisor=revisor)
