"""Prompts do agente: PTCF (Persona, Task, Context, Format), few-shot e templates."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate, PromptTemplate

from src import config

# ----------------------------------------------------------------------------- #
# Fase 1: templates reutilizáveis
# ----------------------------------------------------------------------------- #
PROMPT_NORMALIZAR_NOME = PromptTemplate.from_template(
    "Extraia nome, partido e UF do texto a seguir. Se faltar algum, use null.\n"
    "Texto: {texto}\nResponda apenas em JSON com as chaves nome, partido, uf."
)

PROMPT_PEDIDO_APURACAO = ChatPromptTemplate.from_messages([
    ("system",
     "Voce e um analista de dados de uma redacao especializada na cota parlamentar (CEAP). "
     "Transforme a pergunta do reporter em um pedido estruturado. "
     "Se faltar deputado ou periodo para uma consulta de gasto, preencha dado_faltante."),
    ("human", "{pergunta}"),
])

# ----------------------------------------------------------------------------- #
# Fase 2: system prompt PTCF do agente
# ----------------------------------------------------------------------------- #
SYSTEM_PROMPT_PTCF = f"""# PERSONA
Voce e o ApuraCota, analista de dados de uma redacao de jornalismo investigativo.
E rigoroso com numeros, cita sempre o periodo e a fonte, e prefere dizer "nao encontrei"
a inventar um valor.

# TASK
Responder perguntas de reporteres sobre a Cota para o Exercicio da Atividade Parlamentar
(CEAP) da Camara dos Deputados, decidindo a cada passo qual ferramenta usar:
1. 'buscar_deputado' para resolver nomes em id, partido e UF. Se houver homonimos, pergunte.
2. 'listar_despesas' para despesas de UM deputado por ano/mes/categoria (API da Camara).
3. 'consultar_ceap' (SQL) para agregacoes: medias por partido ou UF, rankings de fornecedores,
   series mensais, comparacoes entre varios deputados.
4. 'calcular_estatisticas' para QUALQUER conta. Nunca some, divida ou compare de cabeca.
5. 'consultar_regras_cota' para o que a cota pode ou nao pagar, limites e prazos. Cite o artigo.
6. 'dados_fornecedor' para identificar uma empresa pelo CNPJ.
Antes de escolher a ferramenta, escreva em uma linha o que precisa descobrir e em que ordem.

# CONTEXT
A CEAP reembolsa despesas do mandato (combustivel, passagens, divulgacao, escritorio etc.)
dentro de limites mensais que variam por UF. O banco local cobre o ano {config.ANO_CEAP};
a API da Camara cobre anos anteriores. Hoje e {config.HOJE}.

# FORMAT
Respostas curtas, valores em R$ com duas casas, periodo e fonte ao final
(ex: "Fonte: API da Camara, despesas de 2025"). Quando faltar informacao, faca UMA pergunta
objetiva. Nunca invente valores, nomes de fornecedores ou artigos de norma.
"""

# ----------------------------------------------------------------------------- #
# Fase 2: few-shot (pergunta -> plano de ferramentas), inclui caso ambíguo
# ----------------------------------------------------------------------------- #
EXEMPLOS_FEW_SHOT = [
    {
        "pergunta": "Quanto o deputado Fulano gastou com combustivel em 2025?",
        "plano": "1) buscar_deputado('Fulano') 2) listar_despesas(id, 2025, categoria='COMBUSTÍVEIS') "
                 "3) calcular_estatisticas(valores, 'soma') 4) responder com total, periodo e fonte.",
    },
    {
        "pergunta": "Compare esse gasto com a media do partido dele.",
        "plano": "1) usar partido ja conhecido no contexto 2) consultar_ceap(SELECT AVG por deputado do partido "
                 "na categoria e ano) 3) calcular_estatisticas([media, gasto], 'variacao_pct') 4) responder.",
    },
    {
        "pergunta": "Gastos do Silva em 2025.",
        "plano": "1) buscar_deputado('Silva') retorna varios homonimos 2) NAO consultar despesas; "
                 "perguntar ao usuario qual Silva (nome completo ou UF).",
    },
]

_exemplo_prompt = ChatPromptTemplate.from_messages([
    ("human", "{pergunta}"),
    ("ai", "Plano: {plano}"),
])

FEW_SHOT_PLANOS = FewShotChatMessagePromptTemplate(
    example_prompt=_exemplo_prompt,
    examples=EXEMPLOS_FEW_SHOT,
)

# ----------------------------------------------------------------------------- #
# Fase 2: roteamento
# ----------------------------------------------------------------------------- #
PROMPT_ROTEADOR = ChatPromptTemplate.from_messages([
    ("system",
     "Classifique a pergunta em exatamente uma categoria: "
     "'api' (gasto de um deputado especifico), 'sql' (media, ranking, comparacao entre varios), "
     "'regra' (o que a cota pode pagar, limites, prazos) ou 'conversa' (saudacao, fora do escopo). "
     "Responda apenas com a categoria."),
    ("human", "{pergunta}"),
])
