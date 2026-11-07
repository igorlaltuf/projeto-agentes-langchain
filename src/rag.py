"""RAG sobre indice FAISS existente (fase 4): ferramenta open-box, diagnostico e query transformation."""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src import llm
from src.data import carregar_indice

# Sinonimos leigos -> termos do Ato da Mesa (usados no modo offline e como few-shot no modo online)
GLOSSARIO = {
    "brasília": "Distrito Federal",
    "brasilia": "Distrito Federal",
    "hotel": "hospedagem",
    "gasolina": "combustíveis e lubrificantes",
    "carro alugado": "locação de veículos automotores",
    "propaganda": "divulgação da atividade parlamentar",
    "avião": "passagens aéreas",
    "aviao": "passagens aéreas",
    "teto": "limite mensal",
    "quanto pode gastar": "limite mensal da cota",
    "comprovante": "documento fiscal",
    "nota fiscal": "documento fiscal",
    "prestar contas": "prazo para apresentação dos documentos",
}

PROMPT_REESCRITA = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Reescreva a pergunta do usuario nos termos tecnicos usados no Ato da Mesa 43/2009 da Camara "
            "(ex: 'Brasilia' -> 'Distrito Federal', 'hotel' -> 'hospedagem', 'teto' -> 'limite mensal'). "
            "Responda apenas com a pergunta reescrita.",
        ),
        ("human", "{pergunta}"),
    ]
)


def buscar_com_scores(pergunta: str, k: int = 3) -> list[dict]:
    """Ferramenta open-box: devolve chunk_id, score L2 (menor = mais proximo) e trecho."""
    indice = carregar_indice()
    saida = []
    for doc, score in indice.similarity_search_with_score(pergunta, k=k):
        saida.append(
            {
                "chunk_id": doc.metadata.get("chunk_id"),
                "score_l2": round(float(score), 4),
                "trecho": doc.page_content[:220].replace("\n", " "),
            }
        )
    return saida


def transformar_consulta(pergunta: str) -> str:
    """Query transformation: reescrita em termos tecnicos antes de buscar no indice."""
    if llm.offline():
        t = pergunta
        for leigo, tecnico in GLOSSARIO.items():
            if leigo in t.lower():
                t = t.lower().replace(leigo, tecnico)
        return t
    return (PROMPT_REESCRITA | llm.chat_model() | StrOutputParser()).invoke({"pergunta": pergunta}).strip()


def diagnosticar(pergunta: str, k: int = 3, termo_esperado: str | None = None) -> dict:
    """Compara a busca com a pergunta original e com a pergunta transformada."""
    antes = buscar_com_scores(pergunta, k)
    reescrita = transformar_consulta(pergunta)
    depois = buscar_com_scores(reescrita, k)

    def acertou(resultados: list[dict]) -> bool | None:
        if not termo_esperado:
            return None
        return any(termo_esperado.lower() in r["trecho"].lower() for r in resultados)

    return {
        "pergunta": pergunta,
        "reescrita": reescrita,
        "antes": antes,
        "depois": depois,
        "acertou_antes": acertou(antes),
        "acertou_depois": acertou(depois),
    }
