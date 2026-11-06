# Plano de Implementação — Agente de Apuração de Gastos Parlamentares (CEAP)

**Projeto da disciplina:** Agentes Cognitivos com LangChain
**Domínio:** jornalismo de dados; cota parlamentar (CEAP) da Câmara dos Deputados
**Prazo:** 13/11/2026, 23:59
**Referências:** `Projeto - Enunciado e Rubricas (Moodle).md` e `Resumos/Projeto da Disciplina - Guia Consolidado.md`

---

## 1. O problema e o agente

**Problema.** Um repórter que apura gastos da cota parlamentar precisa cruzar três coisas: os dados de despesas (quem gastou, com o quê, com qual fornecedor), as regras do que a cota pode pagar e o contexto (média do partido, da bancada do estado, do mês anterior). Hoje isso exige baixar CSV, filtrar em planilha e ler o Ato da Mesa. Erros de soma, de período e de interpretação da regra são comuns.

**Agente.** "ApuraCota": um assistente conversacional que responde perguntas jornalísticas sobre a CEAP, decide sozinho se consulta a API da Câmara, o banco local de despesas ou a base normativa, mantém o fio da apuração entre turnos e **verifica os próprios números antes de responder**.

**Usuário.** Repórter da redação. Conversa típica:

1. "Quanto o deputado Fulano gastou com combustível em 2025?"
2. "Compare com a média do partido dele."
3. "Qual fornecedor recebeu mais dele? Esse fornecedor atende outros deputados?"
4. "A cota pode pagar isso? Cite a regra."
5. "Resume o que achamos até agora para eu colocar na pauta."

**Fontes de dados.**

| Fonte | Tipo | Uso no agente | Acesso |
|---|---|---|---|
| API de Dados Abertos da Câmara (`dadosabertos.camara.leg.br/api/v2`) | API externa real | `/deputados` (busca por nome, partido, UF), `/deputados/{id}/despesas` (despesas por ano/mês), `/partidos` | Aberta, sem chave |
| CSV anual da CEAP (`camara.leg.br/cota-parlamentar`) | Dados estruturados | Carregado em SQLite para agregações (média por partido, ranking de fornecedores, séries mensais) | Download único |
| Ato da Mesa 43/2009 e alterações, FAQ da cota, tabela de limites por UF | Documentos | RAG: o que a cota pode e não pode pagar, limites, prazos de prestação de contas | PDF/HTML do site da Câmara |
| Brasil API (`brasilapi.com.br/api/cnpj/v1/{cnpj}`) | Segunda API externa | Enriquecer o fornecedor (razão social, CNAE, situação cadastral) | Aberta, sem chave |

**Mocks para o professor.** Gravar em JSON as respostas reais da API da Câmara e da Brasil API para os casos de teste (`fixtures/`), e ter um `MODO_OFFLINE = True` que faz as ferramentas lerem os fixtures. Isso atende "alternativas dummy funcionais" do enunciado e protege o vídeo se a API cair.

---

## 2. Fase 1 — MVP: chain executável com entrada e saída estruturadas

**Objetivo da fase:** a menor chain que já produz valor: recebe uma pergunta jornalística em texto livre e devolve um JSON com o que foi pedido (intenção, deputado, período, categoria, tipo de comparação). Esse JSON é o contrato que o resto do sistema vai consumir.

### 2.1 Anatomia e arquitetura (rubricas 1.1 e 1.2)

Texto no notebook e no relatório, em termos do domínio:

- **LLM:** GPT-4o-mini (custo) para a chain e o agente; GPT-4o para o juiz da avaliação. Justificar com benchmark de tool calling.
- **System message:** persona de analista de dados da redação, regras de roteamento e de não inventar valores.
- **Memória:** curto prazo = turnos da apuração em andamento; longo prazo = regras da cota (semântica), apurações anteriores com data (episódica), prompt e docstrings (procedural).
- **Ferramentas:** busca de deputado, despesas por período, consulta SQL ao CEAP, estatísticas (hardcoded), regras da cota (RAG), dados de fornecedor (Brasil API).
- **Base de conhecimento:** Ato da Mesa e FAQ em FAISS.
- **Padrão arquitetural:** **híbrido**. Reativo para perguntas diretas ("quanto gastou X"), deliberativo para comparações que exigem planejar várias chamadas ("compare com a média do partido e diga se a regra permite").
- **Maturidade agêntica:** hoje, agente de busca/RAG (tipo 1 da aula 1); meta ao fim do projeto, agente de tarefa com verificação de resposta (tipo 2 com camada de V&V).
- **Planejamento hierárquico (esboço):** "compare com a média do partido" se decompõe em: identificar deputado → obter partido → somar despesas do deputado no período → somar despesas de todos do partido no período e dividir → comparar → verificar números → responder com fonte. Desenhar isso como diagrama simples.

### 2.2 Chain (rubricas 1.3 a 1.6)

- `PromptTemplate` para uma chamada de texto simples: normalizar o nome do deputado ("Fulano de Tal (PT-SP)" → `{"nome": ..., "partido": ..., "uf": ...}`).
- `ChatPromptTemplate` com system + human para a chain principal.
- **Output parser JSON:** classe Pydantic `PedidoApuracao` com `intencao: Literal["consultar_gasto","comparar","ranking_fornecedor","regra_cota","resumo"]`, `deputado`, `partido`, `uf`, `ano`, `mes`, `categoria`, `dado_faltante`. Usar `with_structured_output` e também `PydanticOutputParser` com `get_format_instructions()` para mostrar o JSON explícito.
- **Runnable:** `invoke` em uma pergunta; `batch` em uma lista de 5 perguntas de teste; `stream` na resposta em linguagem natural do resumo.
- **Imperativa com `@chain`:** função que monta o prompt, chama o modelo, faz o parse e, se `dado_faltante` estiver preenchido, devolve uma pergunta de esclarecimento em vez do JSON.
- **Declarativa com LCEL:** `prompt | llm | parser`.
- **Decisão e justificativa:** LCEL como padrão porque dá `batch`, `stream` e rastreamento no LangSmith de graça e compõe com `RunnableParallel` na fase 2; a forma imperativa fica reservada aos pontos com lógica condicional (pedir esclarecimento), onde o controle de fluxo em Python é mais legível.

**Evidência:** notebook com a chain rodando nas 5 perguntas, o JSON de saída, os dois estilos lado a lado e o texto de justificativa.

---

## 3. Fase 2 — Do protótipo ao agente: ferramentas, ReAct e prompts

### 3.1 Orquestradores (rubrica 2.1)

Tabela comparando LangChain/LangGraph, LlamaIndex, AutoGen e Semantic Kernel por autonomia (quem decide o próximo passo), abstração (quanto o framework esconde) e modularidade (troca de LLM, ferramentas e memória). Conclusão: LangChain pela interface Runnable unificada, pelo `create_agent` com checkpointer nativo, pela integração com LangSmith e porque a próxima disciplina continua em LangGraph.

### 3.2 Ferramentas e ReAct (rubricas 2.2 e 2.4)

| Ferramenta | Tipo | O que faz |
|---|---|---|
| `buscar_deputado(nome, uf=None)` | API externa (Câmara) | Resolve nome para `id`, partido, UF. Trata homônimos devolvendo lista. |
| `listar_despesas(id_deputado, ano, mes=None, categoria=None)` | API externa (Câmara) | Despesas do deputado; devolve lista com valor, fornecedor, CNPJ, data, tipo. |
| `consultar_ceap(query_sql)` | Text-to-query (fase 4) | SELECT somente leitura no SQLite da CEAP. |
| `calcular_estatisticas(valores, operacao)` | Hardcoded | Soma, média, mediana, variação percentual. A LLM nunca faz conta de cabeça. |
| `consultar_regras_cota(pergunta)` | Semântica + RAG | Busca no FAISS do Ato da Mesa e responde com trecho e artigo. |
| `dados_fornecedor(cnpj)` | API externa (Brasil API) | Razão social, CNAE, situação, data de abertura. |

- ReAct com `create_agent(llm, tools, checkpointer=MemorySaver(), system_prompt=...)`. **Nota de equivalência no relatório:** `initialize_agent` com `ZERO_SHOT_REACT_DESCRIPTION` foi descontinuado; `create_agent` implementa o mesmo ciclo raciocínio → ação → observação sobre LangGraph. Mostrar o ciclo nos logs (`tool_calls` e `ToolMessage`).
- Function calling: as ferramentas da Câmara usam `@tool` com `args_schema` Pydantic; a chamada à API é real (`httpx`), com fallback para fixtures.

### 3.3 Chains sequenciais, paralelas e roteamento (rubrica 2.3)

- **Sequencial (LCEL):** extrair pedido (chain da fase 1) → resolver deputado → buscar despesas → formatar resposta.
- **Paralela:** `RunnableParallel` buscando ao mesmo tempo as despesas do deputado e a lista de deputados do partido (necessárias para a média).
- **Roteamento dinâmico:** chain classificadora com `temperature=0` que devolve `"api" | "sql" | "regra" | "conversa"`, e um `RunnableLambda` que despacha para a chain certa. **Nota de equivalência:** cumpre o papel do `RouterChain` legado com os construtos atuais.

### 3.4 Prompt PTCF, few-shot e chain-of-thought (rubrica 2.5)

System prompt em quatro blocos explícitos:

- **Persona:** analista de dados da redação, rigoroso com números, cita fonte e período.
- **Task:** responder perguntas sobre a CEAP decidindo entre API, SQL e regras; nunca inventar valores; usar `calcular_estatisticas` para qualquer conta.
- **Context:** o que é a CEAP, categorias de despesa, que o CSV local cobre 2025 e a API cobre anos anteriores, data de hoje.
- **Format:** resposta curta, valores em R$ com duas casas, período e fonte ao final; quando faltar dado, perguntar.

Few-shot: 3 exemplos de pergunta → plano de ferramentas (`FewShotChatMessagePromptTemplate`), incluindo um caso ambíguo ("gastos do Silva" com dois homônimos). Chain-of-thought: instrução "antes de escolher a ferramenta, liste em uma linha o que precisa descobrir e em que ordem"; mostrar no log um caso em que o raciocínio explícito evitou a ferramenta errada.

### 3.5 Componentes de confiabilidade (rubrica 2.6)

- **Parser com tratamento de erro:** `PydanticOutputParser` + chain de reparo capturando `OutputParserException` (aula 4). Testar com uma saída propositalmente quebrada.
- **Memória configurada:** `trim_messages(max_tokens=..., strategy="last", include_system=True)` sobre o histórico, justificado pelo volume (apurações longas com muitas listas de despesas).
- **FAISS:** índice do Ato da Mesa + FAQ, `chunk_size=500, chunk_overlap=100`, metadados `fonte`, `artigo`. Salvar com `save_local` (será o "índice existente" da fase 4).
- **Callbacks:** `BaseCallbackHandler` com `on_llm_start`, `on_tool_start`, `on_tool_end`, `on_llm_end`, registrando horário, ferramenta, argumentos e duração em uma lista de log (JSON) que vai para o relatório.

**Evidência:** agente respondendo "quanto Fulano gastou com combustível em 2025?" com chamadas reais à API visíveis no log; prompt PTCF; os quatro componentes com justificativa.

---

## 4. Fase 3 — Memória: o agente vira chatbot

### 4.1 Estratégia (rubricas 3.1 a 3.3)

Taxonomia CoALA aplicada ao domínio:

| Camada | No ApuraCota | Implementação |
|---|---|---|
| STM / working memory | Turnos da apuração atual | `MemorySaver` com `thread_id` por apuração |
| Cache semântico | Últimas respostas da API (despesas já buscadas) | Dicionário por (deputado, ano, mês); evita refazer chamadas |
| LTM semântica | Regras da cota | FAISS do Ato da Mesa |
| LTM episódica | Apurações anteriores: pergunta, resultado, data | `InMemoryVectorStore` (ou FAISS) com metadados `reporter`, `data`, `deputado`; ferramenta `lembrar_apuracao` |
| LTM procedural | Como agir | System prompt + docstrings |

Gestão de janela: avaliador de relevância por LLM (marca mensagens de "small talk" como baixa) + `trim_messages` + sumarização quando passar de N turnos; a sumarização gera um resumo que é gravado na memória episódica. Busca vetorial recupera apurações passadas quando o repórter diz "aquele fornecedor que vimos semana passada".

Comparação: custom LangChain/LangGraph × Mem0 × Letta (tabela da aula 5). Decisão: custom, porque o volume é pequeno, queremos controle do custo e das regras de retenção (dados de apuração não devem ir para serviço terceiro).

### 4.2 Chatbot, NLU e teste multi-turno (rubricas 3.4 a 3.6)

- Chatbot sobre o agente da fase 2 com `MemorySaver`. **Nota de equivalência:** `ConversationBufferMemory` pertence ao módulo `langchain.memory`, legado; o checkpointer cumpre a função de buffer de curto prazo.
- NLU: classe `AnaliseNLU` com `intencao`, `deputado`, `partido`, `uf`, `periodo`, `categoria`, `fornecedor`, `dado_faltante`, alimentada pelo histórico via `MessagesPlaceholder`. Mostrar um turno real em que a entidade "período" foi herdada do turno anterior.
- Teste multi-turno (gravar a conversa inteira no relatório):
  1. "Quanto o deputado Fulano gastou com combustível em 2025?"
  2. "E com passagens aéreas?" (herda deputado e ano)
  3. "Compare os dois com a média do PT." (decompõe em várias chamadas)
  4. "Qual fornecedor de combustível recebeu mais dele?"
  5. "Esse fornecedor aparece para outros deputados de SP?" (herda fornecedor e usa SQL)

---

## 5. Fase 4 — Ferramentas externas e RAG existente

### 5.1 Ferramentas por tipo (rubricas 4.1 e 4.2)

Tabela no relatório com nome, descrição, lógica e tipo: hardcoded (`calcular_estatisticas`), semântica (`consultar_regras_cota`, `resumir_apuracao`), API web externa (Câmara, Brasil API), text-to-query (`consultar_ceap`). Classificar a Câmara como web API pública REST e a Brasil API como API de enriquecimento; uma função interna de normalização de categorias de despesa como exemplo de "função interna/serverless".

### 5.2 Text-to-query (rubrica 4.3)

SQLite com a tabela `despesas_ceap_2025` (colunas do CSV: `txNomeParlamentar`, `sgPartido`, `sgUF`, `txtDescricao`, `txtFornecedor`, `txtCNPJCPF`, `vlrLiquido`, `numMes`, `numAno`). Ferramenta com guardrail: só `SELECT`, lista de palavras proibidas, `LIMIT` forçado, log da query gerada. Schema no system prompt. Few-shot com 3 queries de exemplo (ranking por fornecedor, média por partido, série mensal) para padronizar o SQL.

### 5.3 Agente de recuperação sobre índice existente (rubrica 4.4)

`FAISS.load_local("indices/ato_mesa", embeddings, allow_dangerous_deserialization=True)` + `create_retriever_tool(retriever, "regras_cota", descricao)`. Não reconstruir o índice nesta fase.

### 5.4 Diagnóstico de falha de recuperação (rubrica 4.5)

Caso planejado: o repórter pergunta "a cota paga aluguel de carro?". O Ato da Mesa fala em "locação de veículos automotores". Mostrar a busca com `similarity_search_with_score`, os chunks retornados com score alto (ruim) e o trecho errado no topo. Aplicar **query transformation**: a ferramenta pede ao agente uma reescrita técnica ("locação de veículos automotores, cota parlamentar, limite") e, como segunda técnica, multi-query (três reformulações, união dos resultados). Mostrar os scores depois e a resposta correta com artigo citado. Justificar a escolha (vocabulary mismatch → reescrita; cobertura → multi-query).

---

## 6. Fase 5 — Agente integrador, verificação e LangSmith

### 6.1 Verificação de respostas (rubrica 5.1)

Camada downstream que roda **dentro do agente** (como último passo antes da resposta final):

1. **Extração:** `ClaimExtractionList` com chaves canônicas (`gasto_total`, `gasto_categoria`, `media_partido`, `valor_fornecedor`, `limite_cota_uf`, `artigo_citado`, `unmapped`).
2. **Fonte de verdade sem LLM:** recalcular no SQLite / fixtures os valores das chaves, com tolerância de R$ 0,01 para somas e 0,5 ponto percentual para variações.
3. **NLI por LLM:** `LogicalCoherenceReport` (apoia / refuta / neutra, falácias, justificativa). Casos a cobrir: erro de soma, período trocado, "maior que a média" quando é menor, regra citada que não existe.

Se o veredito for "refuta", o agente reescreve a resposta com os valores corrigidos e marca "verificado" ou "não foi possível verificar". Testar com uma resposta forjada, como na aula 7.

### 6.2 Agente único (rubricas 5.2 a 5.4)

`create_agent` com o toolkit completo (API + SQL + RAG + hardcoded + semântica + verificação), `MemorySaver` e o prompt PTCF. **Notas de equivalência:** `create_openai_tools_agent` + `AgentExecutor` → `create_agent`; `RunnableWithMessageHistory` → checkpointer com `thread_id` (ou histórico passado no `invoke`). Demonstrar a memória durando uma sessão completa de 5 turnos.

### 6.3 Streamlit (rubrica 5.5)

Adaptar o `app.py` da aula 8: `session_state` com `messages`, `StreamlitCallbackHandler` mostrando as ferramentas, e um painel lateral com o relatório de verificação da última resposta. Rodar localmente para o vídeo.

### 6.4 LangSmith (rubrica 5.6)

- Dataset de 10 casos: 4 de API (gasto por deputado/categoria), 2 de SQL (ranking, média), 2 de regra (RAG), 1 de conversa sem ferramenta, 1 de pergunta fora do escopo (deve recusar). Cada caso com `reference` e `expected_steps`.
- Avaliadores: (a) exatidão de trajetória (lista de ferramentas igual à esperada); (b) corretude semântica por LLM-juiz, com prompt rigoroso e comparação `== "CORRETO"` (corrigindo o bug da aula 8); (c) exatidão numérica (valores da resposta contra o gabarito, com tolerância).
- `evaluate(...)` com `experiment_prefix="apuracota_v1"`. Rodar duas vezes (antes e depois de ajustar o prompt) e comparar no relatório, explicando as métricas, inclusive casos em que o juiz errou.
- Exportar prints da tabela do experimento e de um trace detalhado para o PDF.

---

## 7. Entregáveis e organização

```
apuracota/
├── 01_mvp_chain.ipynb
├── 02_agente_react.ipynb
├── 03_memoria_chatbot.ipynb
├── 04_ferramentas_rag.ipynb
├── 05_integrador_verificacao_langsmith.ipynb
├── app.py                      # Streamlit
├── src/
│   ├── tools.py                # ferramentas (API, SQL, hardcoded, semânticas)
│   ├── prompts.py              # PTCF, few-shot, templates
│   ├── memory.py               # CoALA: checkpointer, episódica, cache
│   ├── verification.py         # claims, ground truth, NLI
│   └── data.py                 # carga do CSV, SQLite, fixtures
├── data/ceap_2025.csv
├── indices/ato_mesa/           # FAISS salvo
├── fixtures/*.json             # respostas gravadas das APIs (modo offline)
├── .env.example
├── requirements.txt            # versões fixadas (langchain, langgraph, langsmith...)
└── README.md                   # como rodar, modo offline, citação de IAs usadas
```

Um notebook por fase, ou um só com seções, como preferir; o professor aceita os dois. Relatório em PDF com o nome `nome_sobrenome_ai-agents_langchain-agents.pdf`, seções fase a fase, tabela de rubricas × onde está atendido, limitações e melhorias, prints do LangSmith.

**Vídeo (tela + webcam, Drive de aluno e, opcionalmente, YouTube):** mostrar os três itens exigidos em uma conversa real no Streamlit:

1. pergunta que exige decidir ferramenta ("quanto Fulano gastou com combustível em 2025?");
2. chamada real da API da Câmara aparecendo no `StreamlitCallbackHandler`;
3. memória entre turnos ("e com passagens?" herdando deputado e ano).

E, como bônus, o painel de verificação marcando a resposta como "apoia".

---

## 8. Cronograma (06/11 a 13/11)

| Dia | Entrega |
|---|---|
| 06/11 (qui) | Baixar CSV da CEAP, carregar SQLite, gravar fixtures da API, montar FAISS do Ato da Mesa. Fase 1 completa. |
| 07/11 (sex) | Fase 2: ferramentas, ReAct, roteamento, PTCF, confiabilidade. |
| 08/11 (sáb) | Fase 3: memória CoALA, NLU, conversa multi-turno gravada. |
| 09/11 (dom) | Fase 4: text-to-query, retriever sobre índice salvo, diagnóstico de falha. |
| 10/11 (seg) | Fase 5: verificação, agente integrador, Streamlit. |
| 11/11 (ter) | LangSmith: dataset, avaliadores, duas rodadas. Começar relatório. |
| 12/11 (qua) | Relatório em PDF, README, revisão dos mocks, gravação do vídeo. |
| 13/11 (qui) | Folga para imprevistos e envio no Moodle (prazo 23:59). |

---

## 9. Riscos e como tratar

| Risco | Mitigação |
|---|---|
| API da Câmara lenta ou fora do ar durante o vídeo | Fixtures gravados e `MODO_OFFLINE`; gravar o vídeo com a API real, mas com fallback pronto. |
| CSV da CEAP grande (centenas de milhares de linhas) | Filtrar para 2025 e, se preciso, para uma UF; documentar o recorte. |
| Homônimos de deputados | `buscar_deputado` devolve lista e o agente pergunta; caso coberto no few-shot. |
| LLM fazendo conta errada | Toda aritmética em `calcular_estatisticas` (hardcoded); verificação numérica pega o resto. |
| Juiz LLM aprovando resposta errada | Avaliador numérico determinístico ao lado do juiz; discutir o caso no relatório. |
| Rubricas com métodos defasados | Implementar com `create_agent` e deixar notas de equivalência (itens 2.2, 2.3, 3.4, 5.3, 5.4). |
| Custo de tokens | GPT-4o-mini no agente, cache semântico das chamadas de API, `trim_messages`; relatar o custo pelo LangSmith. |
