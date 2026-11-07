# ApuraCota: agente cognitivo para apuração de gastos parlamentares (CEAP)

**Disciplina:** Agentes Cognitivos com LangChain (Infnet, 2026) · **Professor:** Gabriel Caldas
**Aluno:** Igor Laltuf · **Repositório:** https://github.com/igorlaltuf/projeto-agentes-langchain
**Arquivo final:** `igor_laltuf_ai-agents_langchain-agents.pdf`

> Esqueleto do relatório. Os blocos marcados com `[PENDENTE: ...]` dependem da execução com `OPENAI_API_KEY`/`LANGCHAIN_API_KEY` (saídas online dos notebooks, índice FAISS real, prints do LangSmith e do Streamlit). Todo o resto já reflete o código versionado.

## 1. Definição do problema

Repórteres que cobrem o Congresso precisam responder perguntas factuais sobre a Cota para o Exercício da Atividade Parlamentar (CEAP) com rapidez e fonte: quanto um deputado gastou em uma categoria, se o valor está acima da média do partido, quem são os fornecedores, e se a despesa é permitida pelas regras da cota. Hoje isso exige cruzar três fontes distintas (API da Câmara, CSV anual de 200 mil linhas e o Ato da Mesa 43/2009). O ApuraCota é um agente conversacional que faz esse cruzamento sob demanda, decide sozinho a fonte adequada, mantém o contexto da apuração e verifica os próprios números antes de responder.

## 2. Caracterização do domínio

- **CEAP:** reembolso mensal de despesas do mandato, com limites por UF (de R$ 36.582,46 no DF a R$ 51.406,33 em RR) e categorias fixas (combustíveis, passagens, divulgação, escritório etc.).
- **Fontes:** API de Dados Abertos da Câmara (ao vivo, exige `idLegislatura` junto do ano, paginada em 100 itens); CSV anual oficial (2025: 209.081 despesas, 562 deputados, R$ 240,3 milhões; BOM UTF-8, CNPJ em formato não padrão); Ato da Mesa 43/2009 consolidado até o Ato 246/2026 (21 artigos + anexo de limites); Brasil API para CNPJ.
- **Particularidades que o agente precisa tratar:** homônimos (seis deputados "Silva"), divergência entre API e CSV para o mesmo deputado/ano (Tabata Amaral 2025: R$ 530.231,55 vs R$ 504.994,08), vocabulário leigo vs jurídico nas regras ("hotel em Brasília" vs "hospedagem no Distrito Federal").

## 3. Decisões técnicas por fase

### Fase 1: MVP (notebook 01)
- `PedidoApuracao` (Pydantic) como contrato entre pergunta livre e sistema; `with_structured_output` como caminho principal e `PydanticOutputParser` para o JSON explícito.
- LCEL como padrão (batch, stream, tracing); `@chain` só onde há lógica condicional (pedir esclarecimento quando falta o deputado).

### Fase 2: agente ReAct (notebook 02)
- Ferramentas com `@tool` + `args_schema`; APIs reais via `httpx` com cache em fixtures.
- `create_agent` + `MemorySaver`; roteamento com chain classificadora + `RunnableLambda`; `RunnableParallel` para API e SQL simultâneos.
- Prompt PTCF, few-shot com caso ambíguo, chain-of-thought em uma linha.
- Confiabilidade: parser com chain de reparo, `trim_messages`, FAISS salvo com `save_local`, `LogCallbackHandler`.

### Fase 3: memória (notebook 03)
- Taxonomia CoALA aplicada (tabela na seção 3.1 do notebook). Memória episódica com filtro por repórter; sumarização grava na episódica; busca vetorial recupera.
- Decisão: memória custom em vez de Mem0/Letta, por governança dos dados de apuração.
- NLU com `MessagesPlaceholder` e campo `herdado_do_historico`; conversa de cinco turnos sem repetir entidades.

### Fase 4: ferramentas e RAG (notebook 04)
- Text-to-SQL com guardrails (só SELECT, LIMIT forçado, auditoria impressa).
- Índice FAISS carregado com `load_local` e exposto por `create_retriever_tool`.
- Diagnóstico open-box (chunk_id, score L2) e query transformation. `[PENDENTE: scores reais antes/depois com embeddings da OpenAI]`

### Fase 5: integrador, verificação, Streamlit, avaliação (notebook 05)
- Agente único com cinco categorias de ferramenta.
- Verificação em três etapas dentro do turno: extração de claims → fact-check determinístico contra o SQLite (consultas derivadas da trajetória) → NLI.
- Streamlit com trajetória e painel de verificação. `[PENDENTE: print da interface]`
- Avaliação com três avaliadores (trajetória, valor, juiz LLM), local e no LangSmith. `[PENDENTE: prints e números do LangSmith]`

## 4. Mapa das 29 rubricas

| Rubrica | Onde está | Nota |
|---|---|---|
| 1.1–1.6 | notebook 01, `src/chains.py`, `src/prompts.py` | |
| 2.1 | notebook 02 (tabela de orquestradores) | |
| 2.2 | `src/agent.py`, notebook 02 | **Equivalência:** `initialize_agent`/`ZERO_SHOT_REACT_DESCRIPTION` descontinuados → `create_agent` (mesmo ciclo ReAct sobre LangGraph) |
| 2.3 | `src/chains.py` (roteador, sequencial, paralela) | **Equivalência:** `RouterChain`/`SequentialChain` legados → classificadora + `RunnableLambda`, LCEL e `RunnableParallel` |
| 2.4 | `src/tools.py` (Câmara, Brasil API) | chamadas reais; fixtures como mock |
| 2.5 | `src/prompts.py` | PTCF, few-shot, CoT |
| 2.6 | `src/chains.py`, `src/memory.py`, `src/data.py` | parser com reparo, `trim_messages`, FAISS, callbacks |
| 3.1–3.3 | notebook 03 | CoALA, janela de contexto, comparação Mem0/Letta/LangMem |
| 3.4 | `src/memory.py`, notebook 03 | **Equivalência:** `ConversationBufferMemory` (legado) → `MemorySaver` + `thread_id` |
| 3.5–3.6 | `src/chains.py` (`AnaliseNLU`), notebook 03 | conversa de cinco turnos |
| 4.1–4.2 | notebook 04, `src/tools.py` | tabela por tipo; APIs classificadas |
| 4.3 | `src/data.py` (`consultar_ceap`) | guardrails |
| 4.4 | `src/data.py` (`carregar_indice`, `ferramenta_regras_cota`) | índice existente |
| 4.5 | `src/rag.py`, notebook 04 | open-box + query transformation |
| 5.1 | `src/verification.py`, `app.py` | dentro do turno |
| 5.2 | `src/agent.py` | RAG + SQL + API + hardcoded + memória |
| 5.3 | `src/agent.py` | **Equivalência:** `create_openai_tools_agent` + `AgentExecutor` → `create_agent` |
| 5.4 | `src/agent.py`, `app.py` | **Equivalência:** `RunnableWithMessageHistory` → checkpointer por sessão |
| 5.5 | `app.py` | opcional segundo o professor; implementado |
| 5.6 | `src/evaluation.py`, notebook 05 | local + LangSmith `[PENDENTE]` |

## 5. Resultados de avaliação

Avaliação local offline (modelo dummy + fixtures), seis casos: trajetória correta em 6/6; valor correto em 2/4 casos numéricos (as falhas são do mock: resultado SQL bruto e embeddings fake), juiz LLM não executado.

`[PENDENTE: tabela da avaliação online com gpt-4o-mini no agente e gpt-4o como juiz; link e prints do experimento no LangSmith; discussão de onde o juiz discorda dos avaliadores determinísticos]`

## 6. Análise comparativa

`[PENDENTE: comparar as duas rodadas (dummy vs OpenAI; opcionalmente gpt-4o-mini vs gpt-4o no agente) por trajetória, valor e custo/latência via LangSmith]`

## 7. Limitações e melhorias

- Memória episódica em RAM; persistir em FAISS/SQLite.
- Índice só com o Ato da Mesa; incluir FAQ da cota e metadados por artigo.
- Divergência API × CSV: criar ferramenta de reconciliação.
- Próxima disciplina (LangGraph): planejador, executor e verificador como nós, human-in-the-loop para homônimos.

## 8. Evidências de execução

Notebooks executados em `notebooks/` (saídas gravadas), testes (`devbox run test`, 31 passando), logs de callback (notebook 02), conversa multi-turno (notebook 03). `[PENDENTE: prints do Streamlit e do LangSmith; link do vídeo]`

## 9. Uso de IA generativa

Planejamento, escrita de código, testes e revisão de texto com Claude (Anthropic) via Claude Code, a partir dos resumos das aulas e do enunciado. Todo o código foi executado e revisado pelo autor; as decisões de domínio (fontes, casos de teste, interpretação das regras da cota) são do autor.

## Roteiro do vídeo (5 a 8 min)

1. Abrir o Streamlit, mostrar o aviso de modo (online) e a thread nova.
2. "Quanto a deputada Tabata Amaral gastou com combustível em 2025?" → mostrar na barra lateral a decisão `buscar_deputado` → `listar_despesas` (API real) e o painel de verificação com `CONFIRMADO`.
3. "Compare com a média do partido dela." → memória entre turnos (deputado e categoria herdados) e `consultar_ceap` com a SQL auditada.
4. "A cota pode pagar hospedagem em Brasília?" → RAG com citação do Ato da Mesa.
5. "Gastos do Silva em 2025" → homônimos: o agente pergunta em vez de chutar.
6. Encerrar mostrando o experimento no LangSmith com as métricas de trajetória.
