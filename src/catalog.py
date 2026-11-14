"""SQLite normalizado, proveniência e importação transacional das fontes públicas."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from src import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lotes_importacao(
 id INTEGER PRIMARY KEY, fonte TEXT NOT NULL, arquivo TEXT NOT NULL, sha256 TEXT NOT NULL,
 criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, cobertura TEXT NOT NULL, status TEXT NOT NULL,
 url TEXT NOT NULL DEFAULT '', linhas INTEGER NOT NULL DEFAULT 0, versao_importador TEXT NOT NULL DEFAULT '1');
CREATE TABLE IF NOT EXISTS pessoas(id INTEGER PRIMARY KEY, nome TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identificadores_externos(
 fonte TEXT NOT NULL, identificador TEXT NOT NULL, pessoa_id INTEGER REFERENCES pessoas(id),
 metodo TEXT NOT NULL, PRIMARY KEY(fonte,identificador));
CREATE TABLE IF NOT EXISTS pendencias_identidade(
 fonte TEXT NOT NULL, identificador TEXT NOT NULL, nome TEXT NOT NULL, motivo TEXT NOT NULL,
 PRIMARY KEY(fonte,identificador));
CREATE TABLE IF NOT EXISTS candidaturas(
 id TEXT PRIMARY KEY, eleicao INTEGER NOT NULL, sequencial TEXT NOT NULL, nome TEXT NOT NULL,
 nome_urna TEXT, cpf TEXT, partido TEXT, uf TEXT, situacao TEXT, pessoa_id INTEGER REFERENCES pessoas(id),
 lote_id INTEGER REFERENCES lotes_importacao(id), linha_origem INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bens_declarados(
 id TEXT PRIMARY KEY, eleicao INTEGER, candidatura_id TEXT NOT NULL, descricao TEXT,
 valor_centavos INTEGER NOT NULL, lote_id INTEGER, linha_origem INTEGER);
CREATE TABLE IF NOT EXISTS receitas_eleitorais(
 id TEXT PRIMARY KEY, eleicao INTEGER, candidatura_id TEXT NOT NULL, data TEXT,
 contraparte_documento TEXT, contraparte_nome TEXT, valor_centavos INTEGER NOT NULL,
 lote_id INTEGER, linha_origem INTEGER);
CREATE TABLE IF NOT EXISTS despesas_eleitorais(
 id TEXT PRIMARY KEY, eleicao INTEGER, candidatura_id TEXT NOT NULL, data TEXT,
 contraparte_documento TEXT, contraparte_nome TEXT, valor_centavos INTEGER NOT NULL,
 lote_id INTEGER, linha_origem INTEGER);
CREATE TABLE IF NOT EXISTS autores_emenda(
 id TEXT PRIMARY KEY, nome TEXT NOT NULL, tipo TEXT NOT NULL, pessoa_id INTEGER REFERENCES pessoas(id));
CREATE TABLE IF NOT EXISTS emendas(
 id TEXT PRIMARY KEY, ano INTEGER NOT NULL, autor_id TEXT NOT NULL REFERENCES autores_emenda(id),
 tipo TEXT NOT NULL, localidade TEXT, funcao TEXT, lote_id INTEGER, linha_origem INTEGER);
CREATE TABLE IF NOT EXISTS execucoes_emenda(
 id TEXT PRIMARY KEY, emenda_id TEXT NOT NULL REFERENCES emendas(id), ano INTEGER NOT NULL,
 empenhado_centavos INTEGER NOT NULL, liquidado_centavos INTEGER NOT NULL, pago_centavos INTEGER NOT NULL,
 restos_inscritos_centavos INTEGER NOT NULL, restos_cancelados_centavos INTEGER NOT NULL,
 restos_pagos_centavos INTEGER NOT NULL, lote_id INTEGER, linha_origem INTEGER, localidade TEXT, funcao TEXT, tipo TEXT);
CREATE TABLE IF NOT EXISTS registros_origem(
 lote_id INTEGER NOT NULL, linha INTEGER NOT NULL, conteudo TEXT NOT NULL, PRIMARY KEY(lote_id,linha));
CREATE INDEX IF NOT EXISTS idx_candidatura_eleicao ON candidaturas(eleicao,nome);
CREATE INDEX IF NOT EXISTS idx_despesa_documento ON despesas_eleitorais(contraparte_documento);
CREATE VIEW IF NOT EXISTS candidaturas_publicas AS SELECT id,eleicao,sequencial,nome,nome_urna,partido,uf,situacao,pessoa_id,lote_id,linha_origem FROM candidaturas;
CREATE VIEW IF NOT EXISTS v_identidades AS SELECT * FROM identificadores_externos;
CREATE VIEW IF NOT EXISTS v_bens AS SELECT b.*,b.valor_centavos/100.0 AS valor,c.nome,c.nome_urna,c.uf,c.partido
 FROM bens_declarados b JOIN candidaturas c ON c.id=b.candidatura_id;
CREATE VIEW IF NOT EXISTS v_receitas_eleitorais AS SELECT b.*,b.valor_centavos/100.0 AS valor,c.nome,c.nome_urna,c.uf,c.partido
 FROM receitas_eleitorais b JOIN candidaturas c ON c.id=b.candidatura_id;
CREATE VIEW IF NOT EXISTS v_despesas_eleitorais AS SELECT b.*,b.valor_centavos/100.0 AS valor,c.nome,c.nome_urna,c.uf,c.partido
 FROM despesas_eleitorais b JOIN candidaturas c ON c.id=b.candidatura_id;
CREATE VIEW IF NOT EXISTS v_emendas AS SELECT e.id,e.ano,a.nome AS autor,a.tipo AS autoria,a.pessoa_id,COALESCE(x.tipo,e.tipo) AS tipo,
 x.localidade,x.funcao,x.empenhado_centavos/100.0 AS empenhado,x.liquidado_centavos/100.0 AS liquidado,
 x.pago_centavos/100.0 AS pago,x.restos_inscritos_centavos/100.0 AS restos_inscritos,
 x.restos_cancelados_centavos/100.0 AS restos_cancelados,x.restos_pagos_centavos/100.0 AS restos_pagos,
 x.lote_id,x.linha_origem FROM emendas e JOIN autores_emenda a ON a.id=e.autor_id
 JOIN execucoes_emenda x ON x.emenda_id=e.id;
"""

TABLES = {
    "candidatos": "candidaturas",
    "bens": "bens_declarados",
    "receitas": "receitas_eleitorais",
    "despesas": "despesas_eleitorais",
}


@contextmanager
def banco(db: Path | None = None):
    conn = sqlite3.connect(db or config.SQLITE_CEAP, timeout=60)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def inicializar_catalogo(db: Path | None = None):
    destino = db or config.SQLITE_CEAP
    destino.parent.mkdir(parents=True, exist_ok=True)
    from src.identity import SCHEMA_IDENTIDADE

    with banco(destino) as conn:
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA_IDENTIDADE)
        if "assinatura" not in {r[1] for r in conn.execute("PRAGMA table_info(revisoes_identidade)")}:
            conn.execute("ALTER TABLE revisoes_identidade ADD COLUMN assinatura TEXT NOT NULL DEFAULT ''")
        colunas_cand = {r[1] for r in conn.execute("PRAGMA table_info(candidaturas)")}
        if "nascimento" not in colunas_cand:
            conn.execute("ALTER TABLE candidaturas ADD COLUMN nascimento TEXT NOT NULL DEFAULT ''")
        conn.execute("DROP VIEW IF EXISTS v_favorecidos_emendas")
        conn.executescript(SCHEMA_FAVORECIDOS)
        exec_cols = {r[1] for r in conn.execute("PRAGMA table_info(execucoes_emenda)")}
        for coluna in ("localidade", "funcao", "tipo"):
            if coluna not in exec_cols:
                conn.execute(f"ALTER TABLE execucoes_emenda ADD COLUMN {coluna} TEXT")
        conn.execute("DROP VIEW IF EXISTS v_emendas")
        conn.executescript(SCHEMA[SCHEMA.index("CREATE VIEW IF NOT EXISTS v_emendas") :])
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lotes_importacao)")}
        for coluna, tipo in [
            ("url", "TEXT NOT NULL DEFAULT ''"),
            ("linhas", "INTEGER NOT NULL DEFAULT 0"),
            ("versao_importador", "TEXT NOT NULL DEFAULT '1'"),
        ]:
            if coluna not in cols:
                conn.execute(f"ALTER TABLE lotes_importacao ADD COLUMN {coluna} {tipo}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='despesas_ceap'").fetchone():
            conn.execute(
                "CREATE VIEW IF NOT EXISTS v_despesas_ceap AS SELECT rowid AS registro_local, *, CAST(ROUND(valor*100) AS INTEGER) AS valor_centavos FROM despesas_ceap"
            )


def hash_arquivo(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as arquivo:
        for chunk in iter(lambda: arquivo.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def centavos(texto: str) -> int:
    valor = str(texto).strip()
    if not valor or valor.upper() in {"NAN", "#NULO", "#NE"}:
        raise ValueError(f"Valor financeiro ausente ou inválido: {valor!r}")
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(valor)
        if not numero.is_finite():
            raise ValueError("Valor não finito")
        return int((numero * 100).quantize(Decimal(1)))
    except InvalidOperation as exc:
        raise ValueError(f"Valor financeiro inválido: {texto}") from exc


def campo(row: dict, *nomes: str, obrigatorio=False, default="") -> str:
    for nome in nomes:
        if nome in row and str(row[nome]).strip():
            return str(row[nome]).strip()
    if obrigatorio:
        raise ValueError(f"Coluna obrigatória ausente: {nomes}")
    return default


def documento(texto: str) -> str:
    if "*" in texto:
        return ""  # CPF mascarado não é um documento completo. O original fica na proveniência.
    return re.sub(r"\D", "", texto) if texto not in {"#NULO", "#NE", "-1", "-3"} else ""


def _ler(path: Path):
    # Os recursos TSE usam Latin-1; os demais podem usar UTF-8/BOM.
    try:
        with path.open(encoding="utf-8-sig") as f:
            while f.read(1024 * 1024):
                pass
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "latin-1"
    yield from pd.read_csv(
        path, sep=";", encoding=encoding, dtype=str, keep_default_na=False, chunksize=20000
    )


def _lote(conn, fonte, path, cobertura, url):
    return conn.execute(
        "INSERT INTO lotes_importacao(fonte,arquivo,sha256,cobertura,status,url) VALUES(?,?,?,?,?,?)",
        (fonte, str(path), hash_arquivo(path), str(cobertura), "PUBLICADO", url),
    ).lastrowid


def importar_tse(path: Path, tipo: str, eleicao: int, url="") -> dict:
    if tipo not in TABLES or eleicao not in {2018, 2022, 2026}:
        raise ValueError("Tipo ou eleição não suportado")
    from src.identity import documento_posicional, nascimento_normalizado

    inicializar_catalogo()
    # Estágio em arquivo separado: nenhum commit do pandas afeta a versão publicada.
    stage = config.DATA_DIR / f".stage-{uuid.uuid4().hex}.db"
    linhas = 0
    try:
        with banco(stage) as temp:
            temp.execute("CREATE TABLE dados(linha INTEGER PRIMARY KEY, registro TEXT, original TEXT)")
            for chunk in _ler(path):
                for row in chunk.to_dict("records"):
                    linhas += 1
                    # Candidaturas/bens usam ANO_ELEICAO; contas eleitorais usam AA_ELEICAO.
                    anos_declarados = {
                        int(str(row[c]).strip())
                        for c in ("ANO_ELEICAO", "AA_ELEICAO")
                        if str(row.get(c, "")).strip()
                    }
                    if len(anos_declarados) > 1:
                        raise ValueError("Colunas de eleição conflitantes no arquivo")
                    ano = int(campo(row, "ANO_ELEICAO", "AA_ELEICAO", obrigatorio=True))
                    if ano != eleicao:
                        raise ValueError("Eleição do arquivo difere da solicitada")
                    cargo = campo(row, "CD_CARGO")
                    if tipo == "candidatos" and not cargo:
                        raise ValueError("CD_CARGO obrigatório para selecionar deputados federais")
                    if cargo and cargo != "6":
                        continue
                    seq = campo(row, "SQ_CANDIDATO", obrigatorio=True)
                    cid = f"{eleicao}:{seq}"
                    if tipo == "candidatos":
                        reg = {
                            "id": cid,
                            "eleicao": eleicao,
                            "sequencial": seq,
                            "nome": campo(row, "NM_CANDIDATO", obrigatorio=True),
                            "nome_urna": campo(row, "NM_URNA_CANDIDATO"),
                            "cpf": documento_posicional(campo(row, "NR_CPF_CANDIDATO")),
                            "nascimento": nascimento_normalizado(campo(row, "DT_NASCIMENTO")),
                            "partido": campo(row, "SG_PARTIDO"),
                            "uf": campo(row, "SG_UF"),
                            "situacao": campo(row, "DS_SIT_TOT_TURNO", "DS_SITUACAO_CANDIDATURA"),
                            "pessoa_id": None,
                        }
                    elif tipo == "bens":
                        reg = {
                            "id": f"{cid}:{campo(row, 'NR_ORDEM_BEM_CANDIDATO', 'NR_ORDEM_CANDIDATO', default=str(linhas))}",
                            "eleicao": eleicao,
                            "candidatura_id": cid,
                            "descricao": campo(row, "DS_BEM_CANDIDATO"),
                            "valor_centavos": centavos(campo(row, "VR_BEM_CANDIDATO", obrigatorio=True)),
                        }
                    else:
                        receita = tipo == "receitas"
                        chave = campo(row, "SQ_RECEITA" if receita else "SQ_DESPESA", default=str(linhas))
                        reg = {
                            # SQ_RECEITA/SQ_DESPESA podem se repetir em itens distintos.
                            # A linha da versão do arquivo identifica o fato; não descarte itens.
                            "id": f"{cid}:{chave}:{linhas}",
                            "eleicao": eleicao,
                            "candidatura_id": cid,
                            "data": campo(row, "DT_RECEITA" if receita else "DT_DESPESA"),
                            "contraparte_documento": documento(
                                campo(row, "NR_CPF_CNPJ_DOADOR" if receita else "NR_CPF_CNPJ_FORNECEDOR")
                            ),
                            "contraparte_nome": campo(row, "NM_DOADOR" if receita else "NM_FORNECEDOR"),
                            "valor_centavos": centavos(
                                campo(
                                    row,
                                    "VR_RECEITA" if receita else "VR_DESPESA_CONTRATADA",
                                    "VR_DESPESA_PAGA",
                                    obrigatorio=True,
                                )
                            ),
                        }
                    temp.execute(
                        "INSERT INTO dados VALUES(?,?,?)",
                        (linhas, json.dumps(reg, ensure_ascii=False), json.dumps(row, ensure_ascii=False)),
                    )
                if linhas >= 20000:
                    print(
                        f"TSE {tipo}/{eleicao}: {linhas:,} linhas lidas; preparando publicação...", flush=True
                    )
        with banco() as conn, sqlite3.connect(stage) as temp:
            quantidade = temp.execute("SELECT COUNT(*) FROM dados").fetchone()[0]
            if not quantidade:
                raise ValueError("Arquivo não contém registros do recorte")
            if tipo != "candidatos":
                ids = {r[0] for r in conn.execute("SELECT id FROM candidaturas WHERE eleicao=?", (eleicao,))}
                if not ids:
                    raise ValueError("Importe candidaturas antes dos dados dependentes")
            lote = _lote(conn, "TSE:" + tipo, path, eleicao, url)
            if tipo == "candidatos":
                conn.execute("DELETE FROM identidades_eleitorais WHERE eleicao=?", (eleicao,))
            conn.execute(f"DELETE FROM {TABLES[tipo]} WHERE eleicao=?", (eleicao,))
            gravados = 0
            for linha, registro, original in temp.execute("SELECT * FROM dados ORDER BY linha"):
                reg = json.loads(registro)
                if tipo != "candidatos" and reg["candidatura_id"] not in ids:
                    continue
                if tipo == "candidatos":
                    vinculo = conn.execute(
                        "SELECT pessoa_id FROM identificadores_externos WHERE fonte='TSE' AND identificador=?",
                        (reg["id"],),
                    ).fetchone()
                    if vinculo:
                        reg["pessoa_id"] = vinculo[0]
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO pendencias_identidade VALUES(?,?,?,?)",
                            (
                                "TSE",
                                reg["id"],
                                reg["nome"],
                                "Sem vínculo documental com a Câmara; não vincular por nome",
                            ),
                        )
                reg.update(lote_id=lote, linha_origem=linha)
                cols = ",".join(reg)
                conn.execute(
                    f"INSERT INTO {TABLES[tipo]}({cols}) VALUES({','.join('?' for _ in reg)})",
                    list(reg.values()),
                )
                conn.execute("INSERT INTO registros_origem VALUES(?,?,?)", (lote, linha, original))
                gravados += 1
            conn.execute("UPDATE lotes_importacao SET linhas=? WHERE id=?", (gravados, lote))
            if not gravados:
                raise ValueError("Nenhum registro vinculado a candidaturas federais")
        return {"fonte": "TSE:" + tipo, "eleicao": eleicao, "registros": gravados, "lote_id": lote}
    finally:
        stage.unlink(missing_ok=True)


def importar_emendas(path: Path, url="") -> dict:
    inicializar_catalogo()
    # Publicação única: falha em qualquer linha restaura a versão anterior.
    with banco() as conn:
        lote = _lote(conn, "CGU:emendas", path, "arquivo completo", url)
        conn.execute("DELETE FROM execucoes_emenda")
        conn.execute("DELETE FROM emendas")
        quantidade = 0
        vistos = set()
        for chunk in _ler(path):
            for row in chunk.to_dict("records"):
                assinatura = json.dumps(row, sort_keys=True)
                if assinatura in vistos:
                    raise ValueError("Linha CGU duplicada; arquivo deve ser revisado")
                vistos.add(assinatura)
                quantidade += 1
                codigo = campo(row, "Código da Emenda", "CODIGO_EMENDA", "Código Emenda", obrigatorio=True)
                ano = int(campo(row, "Ano da Emenda", "ANO_EMENDA", obrigatorio=True))
                tipo = campo(row, "Tipo de Emenda", "TIPO_EMENDA", obrigatorio=True)
                nome = campo(
                    row, "Nome do Autor da Emenda", "Nome Autor Emenda", "NOME_AUTOR", obrigatorio=True
                )
                autor = campo(
                    row, "Código do Autor da Emenda", "Código Autor Emenda", "CODIGO_AUTOR", default=nome
                )
                # A CGU usa placeholders repetidos para emendas sem identificação.
                # Preserve cada linha sem inventar que elas são a mesma emenda/autor.
                ausentes = {"s/i", "s/n", "sem informação", "sem informacao", "-1", "-3", "#nulo"}
                if codigo.casefold() in ausentes:
                    codigo = f"sem-identificacao:{ano}:{quantidade}"
                if autor.casefold() in ausentes:
                    autor = f"sem-identificacao:{ano}:{tipo}"
                autoria = "individual" if "individual" in tipo.lower() else "coletiva"
                conn.execute(
                    "INSERT INTO autores_emenda(id,nome,tipo) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET nome=excluded.nome,tipo=excluded.tipo",
                    (autor, nome, autoria),
                )
                # Uma linha de execução pode compartilhar código de emenda; não duplicar cabeçalhos.
                anterior = conn.execute(
                    "SELECT autor_id,tipo,ano FROM emendas WHERE id=?", (codigo,)
                ).fetchone()
                if anterior and (anterior[0], anterior[2]) != (autor, ano):
                    raise ValueError("Cabeçalho conflitante para a mesma emenda")
                conn.execute(
                    "INSERT OR IGNORE INTO emendas VALUES(?,?,?,?,?,?,?,?)",
                    (
                        codigo,
                        ano,
                        autor,
                        tipo,
                        campo(row, "Localidade de aplicação do recurso", "Localidade do gasto"),
                        campo(row, "Nome Função", "Função", "FUNCAO"),
                        lote,
                        quantidade,
                    ),
                )
                valores = []
                for col, alias in [
                    ("Valor Empenhado", "EMPENHADO"),
                    ("Valor Liquidado", "LIQUIDADO"),
                    ("Valor Pago", "PAGO"),
                    ("Valor Restos A Pagar Inscritos", "RESTOS_INSCRITOS"),
                    ("Valor Restos A Pagar Cancelados", "RESTOS_CANCELADOS"),
                    ("Valor Restos A Pagar Pagos", "RESTOS_PAGOS"),
                ]:
                    valores.append(centavos(campo(row, col, alias, obrigatorio=True)))
                conn.execute(
                    "INSERT INTO execucoes_emenda VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{lote}:{quantidade}",
                        codigo,
                        ano,
                        *valores,
                        lote,
                        quantidade,
                        campo(row, "Localidade de aplicação do recurso", "Localidade do gasto"),
                        campo(row, "Nome Função", "Função", "FUNCAO"),
                        tipo,
                    ),
                )
                conn.execute(
                    "INSERT INTO registros_origem VALUES(?,?,?)",
                    (lote, quantidade, json.dumps(row, ensure_ascii=False)),
                )
        if not quantidade:
            raise ValueError("Arquivo vazio; publicação cancelada")
        conn.execute("UPDATE lotes_importacao SET linhas=? WHERE id=?", (quantidade, lote))
    return {"fonte": "CGU:emendas", "registros": quantidade, "lote_id": lote}


def vincular_identidade(pessoa_id: int, fonte: str, identificador: str, evidencia: str, revisor="manual:cli"):
    if identificador.startswith("sem-identificacao:"):
        raise ValueError("Registro sem identificação oficial não permite vínculo pessoal")
    if not evidencia.strip():
        raise ValueError("Vínculo exige evidência documental")
    if fonte not in {"TSE", "CGU"}:
        raise ValueError("Fonte de identidade não suportada")
    with banco() as conn:
        tabela = "candidaturas" if fonte == "TSE" else "autores_emenda"
        if not conn.execute(f"SELECT 1 FROM {tabela} WHERE id=?", (identificador,)).fetchone():
            raise ValueError("Identificador externo não encontrado")
        if not conn.execute("SELECT 1 FROM pessoas WHERE id=?", (pessoa_id,)).fetchone():
            raise ValueError("Pessoa não encontrada")
        if fonte == "TSE":
            from src.identity import cpf_valido

            cpf = conn.execute("SELECT cpf FROM candidaturas WHERE id=?", (identificador,)).fetchone()[0]
            if cpf_valido(cpf):
                outras = conn.execute(
                    "SELECT DISTINCT pessoa_id FROM evidencias_pessoa WHERE cpf=? AND pessoa_id<>?",
                    (cpf, pessoa_id),
                ).fetchall()
                ligadas = conn.execute(
                    "SELECT DISTINCT pessoa_id FROM candidaturas WHERE cpf=? AND pessoa_id IS NOT NULL AND pessoa_id<>?",
                    (cpf, pessoa_id),
                ).fetchall()
                proprios = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT cpf FROM evidencias_pessoa WHERE pessoa_id=?", (pessoa_id,)
                    )
                    if cpf_valido(r[0])
                }
                proprios.update(
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT cpf FROM candidaturas WHERE pessoa_id=?", (pessoa_id,)
                    )
                    if cpf_valido(r[0])
                )
                if outras or ligadas or len(proprios) > 1 or (proprios and cpf not in proprios):
                    raise ValueError(
                        "CPF associado a outra pessoa; vínculo bloqueado até resolver a unicidade"
                    )
        anterior = conn.execute(
            "SELECT pessoa_id FROM identificadores_externos WHERE fonte=? AND identificador=?",
            (fonte, identificador),
        ).fetchone()
        if anterior and anterior[0] != pessoa_id:
            raise ValueError("Identificador já vinculado a outra pessoa; resolva o conflito antes")
        conn.execute(
            "INSERT INTO identificadores_externos VALUES(?,?,?,?) ON CONFLICT(fonte,identificador) DO UPDATE SET pessoa_id=excluded.pessoa_id,metodo=excluded.metodo",
            (fonte, identificador, pessoa_id, evidencia),
        )
        if fonte == "TSE":
            conn.execute("UPDATE candidaturas SET pessoa_id=? WHERE id=?", (pessoa_id, identificador))
        conn.execute(
            "DELETE FROM pendencias_identidade WHERE fonte=? AND identificador=?", (fonte, identificador)
        )
        if fonte == "CGU":
            if conn.execute("SELECT tipo FROM autores_emenda WHERE id=?", (identificador,)).fetchone() != (
                "individual",
            ):
                raise ValueError("Autoria coletiva não pode ser atribuída a uma pessoa")
            conn.execute("UPDATE autores_emenda SET pessoa_id=? WHERE id=?", (pessoa_id, identificador))

        conn.execute(
            "UPDATE revisoes_identidade SET status='CONFIRMADO',evidencia=?,revisor=?,revisado_em=CURRENT_TIMESTAMP WHERE fonte=? AND identificador=? AND pessoa_id=?",
            (evidencia, revisor, fonte, identificador, pessoa_id),
        )
        conn.execute(
            "UPDATE revisoes_identidade SET status='CONFLITO' WHERE fonte=? AND identificador=? AND pessoa_id<>? AND status='SUGERIDO'",
            (fonte, identificador, pessoa_id),
        )
        if not anterior or not revisor.startswith("automatico:"):
            conn.execute(
                "INSERT INTO historico_identidade(fonte,identificador,pessoa_id,acao,evidencia,revisor) VALUES(?,?,?,'CONFIRMADO',?,?)",
                (fonte, identificador, pessoa_id, evidencia, revisor),
            )


def cobertura() -> list[dict]:
    if not config.SQLITE_CEAP.exists():
        return []
    with banco() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='lotes_importacao'").fetchone():
            return []
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id AS lote_id,fonte,cobertura,criado_em AS atualizado_em,status,sha256,url,linhas FROM lotes_importacao WHERE id IN (SELECT MAX(id) FROM lotes_importacao GROUP BY fonte,cobertura) ORDER BY fonte,cobertura"
            )
        ]


SCHEMA_FAVORECIDOS = """
CREATE TABLE IF NOT EXISTS pagamentos_favorecidos(
 id INTEGER PRIMARY KEY, emenda_codigo TEXT NOT NULL, autor_codigo TEXT NOT NULL,
 ano INTEGER NOT NULL, mes INTEGER NOT NULL, favorecido_codigo TEXT NOT NULL,
 favorecido_nome TEXT NOT NULL, tipo_favorecido TEXT NOT NULL, documento TEXT NOT NULL,
 documento_valido INTEGER NOT NULL, natureza_juridica TEXT, uf TEXT, municipio TEXT,
 valor_centavos INTEGER NOT NULL, lote_id INTEGER NOT NULL, linha_origem INTEGER NOT NULL,
 UNIQUE(lote_id,linha_origem));
CREATE INDEX IF NOT EXISTS idx_favorecido_doc ON pagamentos_favorecidos(documento);
CREATE VIEW IF NOT EXISTS v_favorecidos_emendas AS
 SELECT f.*, f.valor_centavos/100.0 AS valor_recebido,
 CASE WHEN f.documento_valido=1 AND f.tipo_favorecido='Pessoa Jurídica' THEN substr(f.documento,1,8) ELSE NULL END AS raiz_cnpj,
 a.pessoa_id, a.nome AS autor,
 CASE WHEN e.id IS NULL THEN 0 ELSE 1 END AS emenda_identificada
 FROM pagamentos_favorecidos f LEFT JOIN emendas e ON e.id=f.emenda_codigo AND e.autor_id=f.autor_codigo
 LEFT JOIN autores_emenda a ON a.id=f.autor_codigo;
"""


def importar_favorecidos(path: Path, url="") -> dict:
    from src.identity import cnpj_valido, cpf_valido, documento_posicional

    inicializar_catalogo()
    with banco() as conn:
        lote = _lote(conn, "CGU:favorecidos", path, "arquivo completo", url)
        conn.execute("DELETE FROM pagamentos_favorecidos")
        quantidade = 0
        total = 0
        for chunk in _ler(path):
            for row in chunk.to_dict("records"):
                quantidade += 1
                codigo = campo(row, "Código da Emenda", obrigatorio=True)
                autor = campo(row, "Código do Autor da Emenda", obrigatorio=True)
                periodo = campo(row, "Ano/Mês", obrigatorio=True)
                if not re.fullmatch(r"[0-9]{6}", periodo) or not 1 <= int(periodo[4:]) <= 12:
                    raise ValueError("Período do favorecido inválido")
                cod_fav = campo(row, "Código do Favorecido", obrigatorio=True)
                doc = documento_posicional(cod_fav)
                tipo = campo(row, "Tipo Favorecido", obrigatorio=True)
                valido = (tipo == "Pessoa Jurídica" and cnpj_valido(doc)) or (
                    tipo in {"Pessoa Física", "Pessoa Fisica"} and cpf_valido(doc)
                )
                valor = centavos(campo(row, "Valor Recebido", obrigatorio=True))
                total += valor
                conn.execute(
                    "INSERT INTO pagamentos_favorecidos(emenda_codigo,autor_codigo,ano,mes,favorecido_codigo,favorecido_nome,tipo_favorecido,documento,documento_valido,natureza_juridica,uf,municipio,valor_centavos,lote_id,linha_origem) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        codigo,
                        autor,
                        int(periodo[:4]),
                        int(periodo[4:]),
                        cod_fav,
                        campo(row, "Favorecido", obrigatorio=True),
                        tipo,
                        doc,
                        int(valido),
                        campo(row, "Natureza Jurídica"),
                        campo(row, "UF Favorecido"),
                        campo(row, "Município Favorecido"),
                        valor,
                        lote,
                        quantidade,
                    ),
                )
                conn.execute(
                    "INSERT INTO registros_origem VALUES(?,?,?)",
                    (lote, quantidade, json.dumps(row, ensure_ascii=False)),
                )
            print(f"CGU favorecidos: {quantidade:,} linhas preparadas...", flush=True)
        if not quantidade:
            raise ValueError("Arquivo de favorecidos vazio")
        conferido = conn.execute("SELECT COUNT(*),SUM(valor_centavos) FROM pagamentos_favorecidos").fetchone()
        if conferido != (quantidade, total):
            raise ValueError("Conferência de quantidade e soma falhou")
        conn.execute("UPDATE lotes_importacao SET linhas=? WHERE id=?", (quantidade, lote))
    return {"fonte": "CGU:favorecidos", "registros": quantidade, "total_centavos": total, "lote_id": lote}
