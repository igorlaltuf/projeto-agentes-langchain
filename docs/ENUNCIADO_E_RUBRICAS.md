# Projeto da Disciplina — Enunciado e Rubricas Oficiais (Moodle)

**Disciplina:** Agentes Cognitivos com LangChain
**Fonte:** página da atividade no Moodle (`lms.infnet.edu.br/moodle/mod/assign/view.php?id=475711`), copiada em 06/11/2026
**Data de entrega:** sexta, 13/11/2026, 23:59
**Tentativas:** 2 permitidas

> Este arquivo reproduz o enunciado oficial. As observações do professor em aula sobre quais itens são defasados e como ele vai avaliar estão em `Resumos/Projeto da Disciplina - Guia Consolidado.md`.

---

## Uso de IAs: Sinal Verde 🟢

Neste trabalho, os alunos são incentivados a explorar o uso de ferramentas baseadas em IA para concluir as tarefas. Todas as fontes, incluindo ferramentas de IA, devem ser devidamente citadas. O uso de IA sem a devida citação será considerado má conduta acadêmica e estará sujeito à aplicação do código disciplinar. Observe que os resultados da IA podem ser tendenciosos e imprecisos. É sua responsabilidade garantir que as informações que você usa da IA sejam precisas. Aprender como usar ferramentas baseadas em IA de maneira cuidadosa e estratégica contribui para o desenvolvimento das habilidades, refinamento de seu trabalho e prepara o aluno para sua futura carreira.

---

## Introdução

Um LLM isolado responde ao que é perguntado, mas não age: não sabe o que aconteceu antes na conversa, não consulta sistemas externos e não tem como verificar se o que gerou está correto. Uma chain fixa já resolve parte disso ao encadear etapas de processamento com entrada e saída previsíveis, mas ainda decide tudo de antemão, no código. Um agente vai além: adiciona um ciclo de raciocínio que decide, a cada passo, se responde diretamente ou se recorre a uma ferramenta, e mantém memória para que essa decisão leve em conta o que já aconteceu na interação.

Um praticante recorre a um agente quando o problema exige mais do que transformar texto em texto: quando é preciso consultar um dado que muda com o tempo, executar uma ação em um sistema externo ou sustentar uma conversa em que o contexto de turnos anteriores importa para a resposta atual. Nem todo problema precisa de um agente completo. Parte do trabalho de quem projeta esses sistemas é reconhecer o ponto em que uma chain simples deixa de ser suficiente e a complexidade de um ciclo de raciocínio e ação se justifica.

Neste projeto, seu papel é o de engenheiro de IA responsável por levar um agente do zero até um estado avaliável. Você escolhe o domínio de aplicação (um assistente de pesquisa, um atendente que consulta dados de um sistema, um agente que executa tarefas em nome de um usuário), mas o tipo de solução é definido pela disciplina: um agente que raciocina sobre tarefas, usa ferramentas reais (incluindo pelo menos uma API externa) e mantém memória de curto e de longo prazo ao longo de uma conversa.

O agente que você constrói aqui não termina nesta disciplina. Na próxima disciplina do bloco, você vai usar exatamente este agente como ponto de partida para projetar a arquitetura que vai compor outros agentes ao redor dele. Por isso, pense desde já na sua solução de ponta a ponta, documentando bem as ferramentas, os padrões de memória e as decisões técnicas que tomar ao longo do caminho: elas vão sustentar o que vem depois.

---

## Fase 1. Da Ideia ao MVP: uma Chain Executável com Entrada e Saída Estruturadas

Antes de qualquer ferramenta ou memória, você precisa de uma base que funcione de ponta a ponta. Esta primeira fase é o MVP do seu projeto: escolha o domínio da sua aplicação e construa a menor chain possível que já produz valor real, com entrada e saída previsíveis o suficiente para que o restante do sistema possa confiar nelas.

Antes de escrever código, situe o que está construindo. Descreva a anatomia do agente que vai desenvolver ao longo do projeto em termos dos seus próprios componentes (qual LLM, qual system message, o que vai contar como memória, quais ferramentas ele vai precisar, se há uma base de conhecimento envolvida) e classifique o padrão arquitetural que faz sentido para o seu domínio, reativo, deliberativo ou híbrido, situando-o no espectro de maturidade agêntica em que ele se encontra hoje e em que precisa chegar até o fim do projeto. Nesse mesmo momento, esboce em nível de projeto como o agente vai planejar tarefas mais complexas, decompondo-as hierarquicamente em etapas menores, e o que vai contar como memória de curto prazo e o que vai contar como memória de longo prazo no seu domínio. Essa é uma definição de arquitetura, não a implementação completa da memória, que virá em uma fase posterior.

Com a arquitetura esboçada, comece a construir. Monte os primeiros templates de prompt reutilizáveis do seu domínio, com `PromptTemplate` para chamadas de texto simples e `ChatPromptTemplate` para o formato de mensagens de um modelo de chat, e force a chain a devolver uma saída estruturada em JSON usando um output parser, em vez de depender de texto livre que o resto do sistema teria que interpretar por conta própria. Aplique a interface Runnable de forma consistente a esses componentes (`invoke` para uma chamada única, `batch` para lotes, `stream` quando fizer sentido expor a resposta token a token) e experimente as duas formas de compor uma chain no LangChain: a composição imperativa, escrevendo uma função Python decorada com `@chain` que combina prompt e modelo com controle total de lógica, e a composição declarativa via LCEL, encadeando os mesmos componentes com o operador pipe. Decida qual das duas vai ser o padrão de composição do seu agente daqui para a frente e tenha uma justificativa técnica para essa escolha, não apenas uma preferência de estilo.

**O que deve ser evidenciado:** um notebook com a chain funcionando de ponta a ponta (prompt estruturado, modelo, output parser em JSON), a anatomia e a classificação arquitetural do agente descritas em termos do seu próprio domínio, e a chain implementada nos dois estilos de composição com a justificativa escrita de qual foi adotado.

---

## Fase 2. Do Protótipo ao Agente: Ferramentas, Raciocínio ReAct e Prompts Avançados

Com uma chain funcional em mãos, é hora de transformá-la em um agente de verdade: um sistema que decide o que fazer, não apenas que transforma uma entrada em uma saída. Esta fase é onde o seu MVP aprende a agir sobre o mundo fora do próprio prompt.

Comece pela escolha do orquestrador. Compare pelo menos dois orquestradores de agentes disponíveis no mercado por critérios objetivos de autonomia, abstração e modularidade, e justifique por que o LangChain, com seus próprios construtos de agente e executor, é a escolha certa para continuar o seu MVP nesta etapa do projeto. Em seguida, equipe o agente com ferramentas e memória básica e implemente o padrão ReAct clássico com `initialize_agent` e o tipo `ZERO_SHOT_REACT_DESCRIPTION`, de forma que o agente alterne, passo a passo, entre raciocinar sobre a tarefa e agir usando uma ferramenta disponível.

À medida que a lógica de decisão cresce, ela para de caber em um fluxo único. Componha chains sequenciais e paralelas para processar uma tarefa em múltiplas etapas do seu domínio, e implemente roteamento dinâmico com `RouterChain` para os casos em que o agente precisa escolher, em tempo de execução, qual caminho seguir. Integre pelo menos uma API externa real como ferramenta do agente via function calling: essa é a ferramenta que conecta o seu agente a um sistema fora do próprio LLM, e o projeto exige que ela funcione de fato, não apenas que esteja configurada.

Um agente que raciocina mal produz decisões erráticas mesmo com as ferramentas certas disponíveis. Estruture o prompt de sistema do agente com o framework PTCF (Persona, Task, Context, Format) e aplique few-shot learning e chain-of-thought para tornar o raciocínio explícito e depurável diante dos casos ambíguos do seu domínio. Por fim, feche esta fase deixando o agente mais confiável em produção: implemente output parsers estruturados com tratamento de erros para as respostas que ele produz, configure a memória da chain (buffer, resumo ou limite de tokens, conforme o volume de conversa esperado), integre um vector store FAISS como ferramenta de busca semântica quando o domínio exigir consulta a conhecimento externo, e aplique callbacks para logging e depuração das execuções do agente. Justifique as escolhas técnicas feitas nesse conjunto de componentes de confiabilidade, não apenas o fato de que funcionam.

**O que deve ser evidenciado:** o agente ReAct funcional com pelo menos uma ferramenta real em uso (incluindo a API externa), o prompt PTCF do agente, e os componentes de confiabilidade (parser com tratamento de erro, memória configurada, callbacks) demonstrados com a justificativa escrita das escolhas.

---

## Fase 3. Memória de Curto e Longo Prazo: o Agente Vira Chatbot

Um agente que raciocina e age, mas esquece tudo a cada mensagem, não sustenta uma conversa real com um usuário. Esta fase adiciona ao agente construído até aqui a capacidade de reter contexto entre turnos e sessões, o requisito que separa um protótipo de demonstração de um assistente que alguém voltaria a usar.

Antes de implementar, estabeleça o vocabulário e a estratégia de memória do seu agente. Diferencie, para o seu domínio, o que deve ser tratado como memória de curto prazo e o que deve ser tratado como memória de longo prazo, apoiando-se na taxonomia CoALA de memória semântica, episódica e procedural. Defina como o agente vai gerenciar a janela de contexto quando a conversa crescer, combinando sliding window ou sumarização com busca vetorial para recuperar e atualizar memórias relevantes, e compare pelo menos duas ferramentas especializadas de memória (como LangMem, Mem0 ou LeTTA/MemGPT) por arquitetura e caso de uso antes de decidir se alguma delas entra no seu agente.

Com a estratégia definida, implemente. Construa um chatbot conversacional de fato, com `ConversationBufferMemory` mantendo o contexto de curto prazo entre as mensagens de uma sessão, e estruture os fluxos de conversação do seu domínio reconhecendo a intenção do usuário e extraindo entidades relevantes do que ele diz. Teste o agente com consultas complexas de múltiplos turnos, verificando que ele decompõe a solicitação corretamente e mantém o contexto da conversa ao longo das trocas, e não apenas na primeira mensagem.

**O que deve ser evidenciado:** o chatbot funcionando com memória de curto prazo demonstrada em uma conversa de múltiplos turnos, a estratégia de memória de curto e longo prazo documentada para o seu domínio, e um exemplo de reconhecimento de intenção e extração de entidade a partir de uma interação real.

---

## Fase 4. Ferramentas Externas e RAG Existente: Ampliando o Alcance do Agente

O agente já raciocina, usa ferramentas e mantém memória. Esta fase amplia o que ele consegue alcançar fora do próprio prompt, conectando-o a sistemas e a uma base de conhecimento já existentes de forma mais robusta do que a integração pontual feita na segunda fase.

Estruture as ferramentas do agente com rigor: cada uma precisa de nome, descrição e lógica central claros, e você precisa diferenciar quais delas são funções hardcoded, com lógica fixa, e quais são funções semânticas, cujo comportamento depende de interpretação do modelo. Integre o agente a APIs internas ou externas de acordo com o tipo de uso que cada uma exige (web APIs, APIs internas ou de microsserviço, funções serverless) e implemente o padrão text-to-query para os casos em que o agente precisa consultar dados estruturados, traduzindo uma pergunta em linguagem natural para uma consulta executável.

Depois, conecte o agente a um índice de busca já construído: monte um agente de recuperação que usa um índice FAISS existente como ferramenta, sem reconstruir o pipeline de indexação, já coberto em outro bloco do curso. Teste esse agente de recuperação contra casos difíceis e diagnostique falhas reais de recuperação analisando os documentos-fonte que ele efetivamente retornou. Quando o problema for descompasso de vocabulário entre a pergunta do usuário e o texto indexado, aplique uma técnica de transformação de consulta (reescrita da pergunta, múltiplas consultas ou geração de documento hipotético) para corrigir a recuperação, em vez de apenas registrar que ela falhou.

**O que deve ser evidenciado:** as ferramentas do agente documentadas por tipo (hardcoded, semântica, API, text-to-query), o agente de recuperação funcionando sobre o índice existente, e pelo menos uma falha de recuperação real diagnosticada a partir dos documentos-fonte retornados, com a correção aplicada e justificada.

---

## Fase 5. O Agente Integrador: Verificação de Respostas e Avaliação com LangSmith

Esta é a fase final do projeto: tudo o que você construiu nas etapas anteriores converge em um único agente completo, e esse agente precisa ser avaliado com evidência, não apenas observado funcionando em alguns casos favoráveis.

Antes de consolidar, adicione uma camada de verificação. Construa um mecanismo que checa a consistência factual das respostas do agente, usando um modelo de inferência de linguagem natural para identificar contradição, sustentação ou neutralidade entre a resposta e a evidência disponível, e avalie também a coerência lógica do que o agente produziu. Essa verificação vira parte do próprio agente, não um relatório à parte.

Consolide então o agente único do seu projeto, integrando sob um mesmo executor as ferramentas de RAG, SQL ou API pertinentes ao seu domínio que você construiu ao longo das fases anteriores. Construa esse executor com `create_openai_tools_agent` e `AgentExecutor`, e integre o histórico de conversa ao agente com `RunnableWithMessageHistory`, garantindo que ele mantenha a memória construída na terceira fase ao longo de uma sessão completa de uso, não apenas dentro de uma única chamada.

Por fim, avalie o agente como um sistema, não como um conjunto de funcionalidades isoladas. Use o LangSmith para avaliar a qualidade das respostas e a trajetória de chamadas de ferramentas que o agente executou, comparando o que ele fez com o que seria esperado em cada caso de teste. Se optar por expor o agente em uma interface Streamlit, essa é a etapa para integrá-la, aproveitando o executor e a memória já construídos.

**O que deve ser evidenciado:** o agente único integrador funcionando com pelo menos duas categorias de ferramenta combinadas, o mecanismo de verificação de respostas em ação, e o relatório de avaliação de qualidade e trajetória gerado no LangSmith.

---

## Entregáveis

### 1. Código / Notebook completo

Funcional, organizado, reprodutível; todos os notebooks ou scripts necessários para reproduzir cada resultado do projeto, da chain inicial ao agente integrador final; sem dependências ocultas. Se o projeto envolve APIs ou credenciais, forneça alternativas dummy funcionais para avaliação do professor.

### 2. Relatório técnico (PDF)

Documento estruturado cobrindo:

- Definição do problema e caracterização do domínio escolhido
- Decisões técnicas e sua justificativa, fase a fase, da chain inicial ao agente final
- Resultados da avaliação (verificação de respostas, avaliação de qualidade e trajetória no LangSmith) e análise comparativa
- Limitações e possíveis melhorias
- Evidência de execução (screenshots, logs, saídas do LangSmith)

Nomeie o arquivo seguindo o padrão: `nome_sobrenome_ai-agents_langchain-agents.pdf`

### 3. Registro de Avaliação no LangSmith

O relatório de avaliação de qualidade e de trajetória de chamadas de ferramentas gerado no LangSmith para o agente integrador final, cobrindo pelo menos os casos de teste usados para validar o mecanismo de verificação de respostas e o funcionamento das ferramentas de RAG, SQL ou API integradas.

### 4. Vídeo de Demonstração do Agente

Uma gravação em vídeo mostrando você interagindo com o agente em execução, pela interface de linha de comando ou pela interface Streamlit, à sua escolha. O vídeo precisa mostrar uma interação real, não roteirizada em texto:

- pelo menos uma pergunta que exige o agente raciocinar sobre a tarefa e decidir usar uma ferramenta;
- pelo menos uma chamada de ferramenta real acontecendo durante a conversa (incluindo a API externa integrada);
- pelo menos uma evidência de que o agente manteve memória entre turnos, respondendo com base em algo dito anteriormente na mesma conversa.

Código funcional, isoladamente, não é suficiente. A avaliação considera a qualidade da análise, a coerência das decisões técnicas e a capacidade de justificar escolhas.

### Orientações para Defesa do Trabalho em Vídeo

- **Utilize uma ferramenta adequada:** é preciso que apareça a tela com seu trabalho e a imagem da sua webcam. Sugestão gratuita: Loom. Alternativa: OBS, entre outras.
- **Escolha um local adequado:** grave em ambiente calmo, sem ruídos ou distrações.
- **Webcam ligada:** ativada, com boa iluminação, enquadrando você de forma clara e centralizada.
- **Revise o vídeo:** assista à gravação para verificar vídeo e áudio.
- **Salve o vídeo no Google Drive:** faça upload para o seu Google Drive de aluno Infnet.
- **Altere as permissões de acesso:** compartilhamento para "qualquer pessoa com o link" poder visualizar. Copie o link e envie conforme as instruções da atividade.

---

## Status da entrega (em 06/11/2026)

| Campo | Valor |
|---|---|
| Número da tentativa | Tentativa 1 (2 permitidas) |
| Status da entrega | Nenhuma tentativa |
| Status da avaliação | Não avaliado |
| Data de entrega | sexta, 13 nov 2026, 23:59 |

---

## Rubrica

Template de Rubrica para ser utilizado com a extensão Rubricator. Cada item é avaliado como **"Não demonstrou"** ou **"Demonstrou o item de rubrica"**.

### 1. Construir uma chain executável com entrada e saída estruturadas

| # | Item |
|---|---|
| 1.1 | O aluno estruturou a anatomia e a arquitetura cognitiva do agente, classificando seu tipo e nível de maturidade agêntica? |
| 1.2 | O aluno projetou uma arquitetura de planejamento e memória aumentada para o agente? |
| 1.3 | O aluno construiu templates de prompt reutilizáveis com `PromptTemplate` ou `ChatPromptTemplate`? |
| 1.4 | O aluno implementou um output parser para gerar saída estruturada em JSON a partir da chain? |
| 1.5 | O aluno aplicou a interface Runnable (`invoke`, `batch`, `stream`) e implementou composição imperativa com o decorador `@chain`? |
| 1.6 | O aluno implementou a composição declarativa da chain com LCEL e justificou a escolha entre composição imperativa e declarativa? |

### 2. Construir agentes com ferramentas, chains, ReAct e prompts avançados no LangChain

| # | Item |
|---|---|
| 2.1 | O aluno comparou orquestradores de agentes autônomos e justificou a escolha do LangChain para o problema? |
| 2.2 | O aluno construiu um agente com ferramentas e memória e implementou o padrão ReAct com `initialize_agent`? |
| 2.3 | O aluno construiu chains sequenciais ou paralelas e implementou roteamento dinâmico com `RouterChain`? |
| 2.4 | O aluno integrou pelo menos uma API externa como ferramenta do agente via function calling? |
| 2.5 | O aluno estruturou o prompt do agente com o framework PTCF e aplicou few-shot learning e chain-of-thought? |
| 2.6 | O aluno implementou os componentes de confiabilidade do agente (output parsers com tratamento de erros, memória configurada na chain, ferramenta de busca semântica com FAISS e callbacks de logging), justificando as escolhas técnicas? |

### 3. Construir chatbots com memória de curto e longo prazo

| # | Item |
|---|---|
| 3.1 | O aluno diferenciou memória de curto e longo prazo para o agente com base na taxonomia CoALA? |
| 3.2 | O aluno gerenciou janelas de contexto combinando sliding window ou sumarização com busca vetorial? |
| 3.3 | O aluno comparou ferramentas especializadas de memória por arquitetura e caso de uso? |
| 3.4 | O aluno construiu um chatbot conversacional com `ConversationBufferMemory`? |
| 3.5 | O aluno estruturou fluxos de conversação com reconhecimento de intenção e extração de entidades? |
| 3.6 | O aluno validou, com evidência de uma conversa real, que o agente manteve contexto e decompôs consultas complexas em diálogos multi-turno? |

### 4. Integrar ferramentas externas e RAG existente a agentes

| # | Item |
|---|---|
| 4.1 | O aluno estruturou ferramentas do agente diferenciando funções hardcoded de funções semânticas? |
| 4.2 | O aluno integrou o agente a APIs internas ou externas conforme o tipo de uso de cada uma? |
| 4.3 | O aluno implementou o padrão text-to-query para consultar dados estruturados? |
| 4.4 | O aluno construiu um agente de recuperação usando um índice FAISS existente? |
| 4.5 | O aluno diagnosticou uma falha real de recuperação a partir dos documentos-fonte retornados e justificou a técnica de correção aplicada? |

### 5. Construir um agente integrador com verificação de respostas, avaliando via LangSmith

| # | Item |
|---|---|
| 5.1 | O aluno construiu um mecanismo de verificação de consistência factual e coerência lógica das respostas do agente? |
| 5.2 | O aluno consolidou um agente único integrando ferramentas de RAG, SQL ou API sob um mesmo executor? |
| 5.3 | O aluno construiu o executor do agente com `create_openai_tools_agent` e `AgentExecutor`? |
| 5.4 | O aluno integrou o histórico de conversa ao agente com `RunnableWithMessageHistory`? |
| 5.5 | O aluno integrou o agente completo a uma interface Streamlit? |
| 5.6 | O aluno avaliou a qualidade das respostas e a trajetória de chamadas de ferramentas do agente com o LangSmith, justificando as conclusões a partir das métricas obtidas? |

**Total: 29 itens de rubrica.**
