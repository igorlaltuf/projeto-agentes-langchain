# ApuraMandato — agente cognitivo de investigação de gastos parlamentares

**Aluno:** Igor Laltuf. **Disciplina:** Agentes Cognitivos com LangChain, Infnet. **Professor:** Gabriel Caldas. **Versão:** núcleo simplificado, 13/11/2026. **PDF:** `igor_laltuf_ai-agents_langchain-agents.pdf`.

## 1. Problema, domínio e escopo

O ApuraMandato ajuda cidadãos e repórteres a investigar despesas da cota parlamentar, conhecer fornecedores e consultar registros de sanções, com explicações baseadas nas normas. O percurso escolhido é gastos → fornecedores → cadastro → sanções. Esse recorte mantém uma tarefa investigativa coerente sem exigir resolver identidades entre eleições, mandatos e autores de emendas durante a conversa.

O agente único é híbrido: o LLM escolhe ferramentas em um ciclo ReAct; consultas, cálculos e verificações são determinísticos. Uma tarefa complexa é decomposta em identificar o parlamentar e o período, obter o agregado, detalhar os anos, selecionar fornecedores e consultar cadastro/sanções. A confirmação de uma etapa fornece o contexto da próxima.

Os testes do escopo anterior identificaram problemas distintos: autorização de dependências de views, cobertura temporal compartilhada indevidamente entre fontes, consulta eleitoral interrompida e falhas de intenção no modo roteirizado. TSE, emendas, favorecidos e seus importadores foram preservados como extensão administrativa. O fluxo principal e o setup não dependem deles. Nenhuma rubrica exige uma quantidade mínima de bases.

## 2. Fontes e arquitetura

- **CEAP, Câmara dos Deputados:** gastos por parlamentar, ano, categoria, partido e fornecedor. A base de produção testada contém 2018–2026; o ano corrente é parcial. Identidade por ID da Câmara.
- **Ato da Mesa nº 43/2009, na versão disponível:** fonte normativa para o RAG, sem presumir que um trecho antigo represente legislação atual sem conferir sua versão.
- **BrasilAPI / Minha Receita:** cadastro empresarial por CNPJ completo validado. É a integração HTTP externa do agente; o cache pode ser desativado.
- **CEIS e CNEP, CGU:** arquivos datados para consulta local de sanções pelo CNPJ exato, conservando categoria, datas, órgão, abrangência, fundamento e observações.

Arquivos, SHA-256, lotes e referências de origem dão rastreabilidade. As sanções são publicadas transacionalmente por cadastro. Falhas preservam a versão anterior. O chat não baixa bases. A busca de deputados é local, inclusive para anos recentes. Não há fallback para uma API de despesas quando falta cobertura.

Ausência de sanção é limitada à versão consultada e não certifica regularidade. A data final não basta para inferir vigência; a categoria e o fundamento importam. Documentos mascarados ou nomes semelhantes não confirmam identidades. Os valores de multas não são somados às despesas parlamentares.

A arquitetura está documentada em `docs/ARQUITETURA.md`. A interface principal apresenta a página inicial e o chat; a página de metodologia foi removida.

## 3. Desenvolvimento pelas cinco fases

### Fase 1 — chain executável

Os templates de normalização e de pedido de apuração usam PromptTemplate e ChatPromptTemplate. Pydantic e output parsers estruturam intenção, deputado, ano, categoria e esclarecimentos. A interface Runnable fornece invoke/batch e composição. Os notebooks comparam a forma imperativa decorada e LCEL; LCEL é adotada para composição declarativa, enquanto funções Python preservam o controle dos cálculos e validações.

### Fase 2 — agente e ferramentas

A comparação de orquestradores considera autonomia, abstração e modularidade. LangChain/LangGraph mantém ferramentas tipadas e controle do ciclo, adequado a um agente que escolhe SQL, RAG ou cadastro. O prompt PTCF define persona, tarefa, contexto e formato. O notebook 02 demonstra exemplos few-shot; esses exemplos didáticos não são automaticamente inseridos em toda chamada do chat principal. O planejamento é exposto por decisões e trajetórias, sem exigir divulgação de raciocínio interno.

As ferramentas incluem busca de deputados, despesas individuais, SQL de agregações, cadastro de CNPJ, sanções locais, cálculos, recuperação e memória. Parsers, validação de documentos, retornos estruturados de indisponibilidade e callbacks tornam as falhas observáveis. Uma falha de cadastro ou de cobertura não encerra a conversa nem fabrica dados.

### Fase 3 — memória

O checkpointer por sessão oferece memória curta, com janela limitada de tokens. Memória episódica persiste resumos solicitados pelo usuário; documentos recuperáveis formam memória semântica e prompts/ferramentas memória procedural, seguindo CoALA. A comparação de ferramentas especializadas considera persistência, recuperação e controle do ciclo antes da escolha de uma solução explícita e pequena.

“E em 2024?” preserva deputado e categoria. Ranking e detalhamento anual mantêm ID, filtro e intervalo. Listas de fornecedores exigem seleção quando houver várias empresas. Sessões não compartilham histórico de curto prazo. A recuperação de resumos não substitui nova consulta dos valores quando necessária.

### Fase 4 — SQL, API e RAG

Text-to-query lê exclusivamente despesas CEAP, valida os anos solicitados e limita linhas. Datas são extraídas dos filtros de ano, evitando interpretar números de fornecedor ou valores financeiros como anos. A extensão SQL mantém proteção de tabelas e corrige a autorização de dependências internas das views; a cobertura temporal é associada à fonte.

A BrasilAPI é consultada por function calling. CNPJ inválido, documento retornado divergente, ausência de fixture ou erro HTTP resultam em indisponibilidade. As fixtures permitem avaliação sem credenciais, mas não comprovam integração online real.

O RAG usa FAISS existente e o documento normativo. Um teste offline de hospedagem em Brasília recuperava trechos irrelevantes e perdia a exceção. A correção complementa os candidatos semânticos com termos exatos ponderados por frequência e normaliza Brasília para Distrito Federal. O teste passou a recuperar “hospedagem, exceto do parlamentar no Distrito Federal”, com referência ao Ato. A retenção de siglas curtas de UF também corrigiu a recuperação do limite mensal de SP. Isso demonstra falhas de recuperação e suas correções, sem atribuir aos embeddings falsos qualidade semântica de produção.

### Fase 5 — integração, checagem e avaliação

Um executor integra as ferramentas e a interface Streamlit. A auditoria extrai afirmações, reexecuta referências SQL e compara números com tolerância de centavos. Contexto semântico como pessoa e ano é preservado; IDs técnicos não são exigidos na prosa para confirmar uma pessoa. O detalhamento anual tem referências por ano e uma referência separada para a soma.

A checagem é de consistência com a mesma fonte, não uma auditoria independente nem certificação da legalidade do gasto. A análise de coerência usa um modelo separado quando online. Offline, o julgamento semântico é indisponível, sem nota fabricada.

O conjunto padrão de avaliação foi reduzido ao núcleo. A trajetória e o valor são avaliados deterministicamente; LLM-as-a-judge e LangSmith dependem de acesso aos serviços. O roteiro `scripts/validate_core.py` testa nove passos com referências do banco configurado e separa falha, sucesso e carga pendente. Offline usa modelo roteirizado e cadastro de teste; online executa modelo real e não faz fallback silencioso.

## 4. Rubricas e equivalências

| Rubricas | Evidência |
|---|---|
| 1.1–1.5 | Anatomia, classificação, planejamento hierárquico, templates, JSON, Runnable, composição imperativa e LCEL no notebook 01 e código de chains. |
| 2.1–2.6 | Comparação de orquestradores, ciclo ReAct, ferramentas, roteamento, PTCF, exemplos, parsers, callbacks, memória e FAISS nos notebooks 02 e seguintes. A execução online do núcleo confirma cadastro empresarial com cache desativado. |
| 3.1–3.6 | Taxonomia CoALA, comparação de memória, janela de contexto, memória curta/longa, intenção e conversas multi-turno no notebook 03 e roteiro de aceitação. As execuções online do núcleo e da investigação registram continuidade multi-turno. |
| 4.1–4.5 | Distinção entre funções determinísticas e semânticas, API empresarial, SQL, FAISS e diagnóstico da recuperação no notebook 04 e testes do núcleo. |
| 5.1–5.6 | Verificação, agente único, histórico, Streamlit, avaliação de qualidade/trajectória e LangSmith no notebook 05 e integração 06. Experimento online publicado; vídeo pessoal ainda precisa ser anexado. |

O enunciado usa APIs antigas, como initialize_agent, AgentExecutor e ConversationBufferMemory. O guia consolidado das aulas aceita equivalentes atuais: create_agent sobre LangGraph implementa o ciclo com ferramentas, o checkpointer conserva o histórico por sessão e Runnables implementam composição/roteamento. Os conceitos e justificativas permanecem evidenciados; a simplificação do domínio não remove essas capacidades.

## 5. Resultados, reprodução e pendências

A suíte final passou com 216 testes, com zero falhas e zero erros (`entregaveis/testes-finais.xml`). A avaliação offline registrou trajetória correta nos oito casos e valores corretos nos quatro casos numéricos. O roteiro de aceitação passou em nove etapas. Os resultados atuais ficam em `entregaveis/validacao-nucleo.json` e `entregaveis/avaliacao-nucleo-offline.json`; não devem ser confundidos com métricas online. No banco de produção, gastos individuais, troca curta de ano, ranking, detalhamento e fornecedores foram exercitados. O total de combustível do primeiro colocado em 2022–2026 foi R$ 454.209,18; a soma anual conferiu e a auditoria reconheceu cada parcela e o total.

`devbox run test` executa regressões. `devbox run validate` testa o fluxo offline. `devbox run validate-online` testa o modelo real e a API externa. `devbox run sancoes` prepara ou atualiza CEIS/CNEP; `devbox run setup` prepara somente o núcleo padrão. A amostra de demonstração é isolada e identificada como tal.

As execuções online mais recentes substituem as tentativas anteriores indisponíveis: o núcleo passou em nove etapas e a investigação completa em 14. A avaliação online também foi concluída e o experimento foi publicado no LangSmith. A gravação pessoal e seu link acessível continuam pendentes. O roteiro está em `docs/ROTEIRO_DEMONSTRACAO.md`.

## 6. Uso de IA e limitações

O desenvolvimento contou com OpenAI Codex/ChatGPT para assistência à implementação, revisão, documentação, testes e preparação dos entregáveis. Um artefato HTML do Claude fornecido pelo aluno foi usado como referência para um roteiro auxiliar de apresentação, mantido fora do repositório; não é evidência de execução. O próprio sistema usa modelos OpenAI para agente, embeddings e juiz, conforme configuração. A responsabilidade pelas decisões, conferência dos dados e apresentação é do aluno. Limitações incluem cobertura e atualização das fontes, documentos normativos versionados, erros cadastrais, interpretação do LLM, classificação incompleta de afirmações e diferença entre teste roteirizado e conversa online.

A próxima disciplina pode reintroduzir dados eleitorais e emendas com arquitetura própria e avaliação separada. Nesta entrega, o núcleo prioriza uma investigação curta, verificável e reproduzível.


## Correção de continuidade societária

A consulta cadastral passou a preservar o quadro de sócios/administradores. A continuação usa o CNPJ do ranking ou o recupera por nome exato e único na CEAP, sem aceitar um documento inventado pelo modelo. A indisponibilidade não é convertida em ausência de sócios, e o cadastro atual não é atribuído automaticamente a pagamentos históricos. Testes controlados usam quadros sintéticos; os roteiros online mais recentes também exercitaram cadastro real e continuidade sobre sócios.

O caso FAROL também revelou período inconsistente na conversa relatada: o total de R$ 363.000,00 soma registros de 2020–2025; no recorte 2024–2026, a soma local foi R$ 103.000,00. Agregações sem filtro de ano são agora rejeitadas e argumentos de anos incompatíveis com o SQL são bloqueados. A correção impede publicar um total histórico como se pertencesse a um intervalo menor.


## Investigação completa e situações adversariais

O roteiro de 14 etapas foi executado em modo roteirizado sobre a CEAP real, com referências SQL separadas: ranking de fornecedores de Tabata em 2024–2026, evolução, categorias, concentração, cadastro, sócios, outros deputados, participação na CEAP, sanções, limites de conclusão, regras, alteração de período e ambiguidade. Cadastro e QSA foram exercitados com resposta gravada da BrasilAPI, sem alegar consulta HTTP atual. As fontes CEIS/CNEP presentes na produção tinham referência de 13/11/2026, com 23.667 e 1.845 registros respectivamente.

O ensaio encontrou lacunas de roteamento e cálculos no fluxo anterior. Foram introduzidas ações tipadas que preservam ID, CNPJ, anos e categoria. O denominador da concentração inclui todas as despesas do deputado no recorte; o da participação contém o universo CEAP do mesmo CNPJ, sem alegar faturamento total. A reexecução confere a apresentação estruturada e distingue esse procedimento de NLI por modelo.

Os três maiores fornecedores somaram R$ 465.844,75 de R$ 1.368.536,48 nas despesas do recorte, correspondendo a 34,04%. Os anos de 2024–2026 são explicitados, com 2026 parcial. Esses valores se referem à versão carregada e não representam prova de irregularidade.

Os testes adversariais incluem empresas de mesmo nome e documentos distintos, matriz/filial, máscara de documento, mudança de período, nova pessoa ambígua, seleção entre empresas, quadro societário ausente/vazio, indisponibilidade de cadastro ou sanções, denominador zero, estornos, lista limitada, texto alterado e pedidos de conclusão sobre faturamento ou corrupção. Valores conhecidos em bases controladas evitam validar a lógica apenas contra si mesma. `testes-investigacao.xml` e `testes-investigacao-riscos.json` preservam a execução; `investigacao-completa.json` contém o fluxo sobre os dados reais. As tentativas antigas de conectividade não foram convertidas em aprovação; os arquivos online atuais registram execuções posteriores bem-sucedidas.


## 7. Avaliação online final e evidências de entrega

### Ambiente e comparação entre modos

Versões verificadas no ambiente: Python 3.12, LangChain 1.4.9 e LangGraph 1.2.20. As dependências exatas estão em `uv.lock`. Modelos padrão: `gpt-4o-mini` (agente), `gpt-4o` (juiz) e `text-embedding-3-small` (embeddings), configuráveis por ambiente.

| Execução | Resultado | Evidência |
|---|---|---|
| Aceitação do núcleo online | 9/9 etapas passaram | `entregaveis/validacao-nucleo-online.json` |
| Investigação completa online | 14/14 etapas passaram | `entregaveis/investigacao-completa-online.json` |
| Avaliação online de oito casos | Trajetória 8/8; valor 4/4 aplicáveis; juiz 8/8 | `entregaveis/avaliacao-apuramandato.json` e experimento LangSmith |
| Avaliação offline de oito casos | Trajetória 8/8; valor 4/4; juiz não executado | `entregaveis/avaliacao-nucleo-offline.json` |

A avaliação online local tem horário registrado em `2026-11-14T01:27:52.523511+00:00`, equivalente a 13/11/2026 às 22:27:52 em Brasília. O dataset é `apuramandato-v1-online-4f53cda1`; o experimento é `apuramandato-56ace0e5`.

Experimento: [resultados de qualidade e trajetória no LangSmith](https://smith.langchain.com/o/d018734c-9bf1-40b6-b0d7-2c3d5d46f8fe/datasets/f2f36265-eb15-42b0-a7c6-b68e110a7aa6/compare?selectedSessions=f2ba6b0a-15a6-434b-8fbd-fa9ca9fe8e97).

O acesso ao link pode exigir login e permissão na organização do LangSmith. Por isso, a entrega inclui o JSON local, além do link. O JSON local resume uma execução de avaliação; o envio ao LangSmith realiza outra execução dos mesmos casos. As notas publicadas no painel devem ser conferidas diretamente nele, sem presumir igualdade de textos entre execuções.

Os oito casos cobrem gasto por categoria, gasto total, média por partido via SQL, homônimos, regra normativa com RAG, cadastro via API, sanções locais e recusa de consulta eleitoral fora do escopo. `trajetoria_correta` verifica a presença e ordem relativa das ferramentas esperadas e a ausência das proibidas; não exige igualdade exata de toda a sequência. `valor_correto` verifica se o número de referência aparece na resposta com tolerância de um centavo. `juiz_llm` compara resposta e referência. Casos sem valor numérico recebem `null` nesse critério e não entram no denominador 4/4.

A aprovação desses casos não comprova precisão universal. O juiz pode aprovar texto inadequado, e a extração de afirmações da checagem interna ainda pode classificar um total de categoria sob outro rótulo, deixando um valor correto como não verificado. A avaliação externa e a verificação interna são mecanismos distintos: nota 1 do juiz não implica que todos os itens do fact-check interno tenham sido confirmados.

Nos validadores online, `USAR_CACHE_API` é desativado pelo código, e modo online não substitui HTTP por fixture silenciosamente. A aprovação do cadastro nesses roteiros evidencia o retorno da BrasilAPI nessa execução. A avaliação geral isolada pode usar cache quando habilitado. O trace de uma ferramenta, sozinho, não distingue uma requisição HTTP de uma leitura de cache.

### Vídeo e fechamento da entrega

**Vídeo de demonstração: PENDENTE — inserir o link real após gravar e publicar.** Não foi fornecido um endereço de vídeo. Publicar no Drive do aluno com acesso por link e testar em janela anônima. Mostrar decisão de ferramenta, chamada real à API com cache desativado, continuidade multi-turno, recuperação normativa e checagem. Tela e webcam seguem a orientação formal do enunciado, observadas as flexibilizações informadas pelo professor.

Links das fontes: [CEAP — arquivos oficiais da Câmara](https://www.camara.leg.br/cota-parlamentar/); [BrasilAPI — documentação](https://brasilapi.com.br/docs); [CEIS — downloads oficiais](https://portaldatransparencia.gov.br/download-de-dados/ceis); [CNEP — downloads oficiais](https://portaldatransparencia.gov.br/download-de-dados/cnep). O texto normativo utilizado está em `data/normas/ato_mesa_43_2009.txt`.

O pacote inclui código, seis notebooks com saídas registradas, lockfile, instruções, amostras/fixtures, normas, índice existente, avaliações e este PDF. Não inclui `.env`, chaves, ambiente virtual, histórico de conversas nem bancos de produção volumosos. O modo offline permite avaliação sem credenciais. Os roteiros e HTML auxiliares de apresentação ficam fora do repositório. O pacote está preparado para envio; a submissão no Moodle e o preenchimento do link do vídeo não são presumidos.
