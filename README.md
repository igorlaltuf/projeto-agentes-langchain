# ApuraMandato

Assistente de tecnologia cívica para investigar gastos parlamentares, conhecer fornecedores, consultar sanções e entender as regras da cota. Projeto da disciplina **Agentes Cognitivos com LangChain**.

## Escopo principal

- **CEAP:** gastos de deputados, categorias, anos, comparações e fornecedores.
- **Normas da cota:** recuperação do Ato da Mesa nº 43/2009 com FAISS e complemento por termos exatos.
- **BrasilAPI:** cadastro empresarial e quadro de sócios/administradores por CNPJ completo validado, com cache de até 24 horas.
- **CEIS e CNEP:** registros de sanções importados dos arquivos oficiais da CGU, com data de referência.

O agente principal não oferece consultas eleitorais nem emendas. Os dados, importadores e vínculos já preparados permanecem disponíveis como extensão administrativa. Nenhum cruzamento deve associar pessoas apenas pelo nome ou por documento mascarado.

## Preparar e abrir

```bash
devbox run setup
devbox run app
```

Abra http://localhost:8501. O setup instala as dependências, preserva o `.env` existente, prepara os anos ausentes da CEAP entre 2018 e 2026, migra o catálogo, carrega CEIS/CNEP e constrói o índice quando há chave OpenAI e modo online. Fontes já publicadas são reaproveitadas. Logs ficam em `logs/setup/`.

Configure `OPENAI_API_KEY` e `MODO_OFFLINE=false` no `.env`. LangSmith é opcional para uso cotidiano e necessário para produzir a evidência de avaliação escolhida para a entrega. CEIS/CNEP locais não exigem chave CGU.

Para atualizar apenas as sanções:

```bash
devbox run sancoes
```

A carga real depende de acesso aos servidores oficiais. Sem uma base preparada, a ferramenta informa indisponibilidade e não conclui ausência de sanções.

## Testar

```bash
devbox run test
devbox run validate
```

O primeiro comando executa os testes automatizados. O segundo executa o roteiro de aceitação sobre o banco configurado, com modelo roteirizado e fixtures de cadastro empresarial; salva `entregaveis/validacao-nucleo.json`. Compara valores com consultas de referência do próprio banco. Carga de sanções ausente aparece como pendência, não como sucesso de consulta. A recuperação offline não mede a qualidade semântica do LLM online.

Para testar com o modelo real e a BrasilAPI, com rede e chave configuradas:

```bash
devbox run validate-online
```

Essa execução consome a API OpenAI e gera `entregaveis/validacao-nucleo-online.json`. Falha de conexão é registrada; não há substituição silenciosa pelo modo offline. A avaliação de respostas e trajetórias no LangSmith pode ser executada dentro de `devbox shell`:

```bash
uv run python scripts/evaluate_mandato.py --online --langsmith
```

### Conversa sugerida

1. “Quanto Tabata Amaral gastou com combustível em 2025?”
2. “E em 2024?”
3. Em nova apuração: “Qual deputado mais gastou com combustível nos últimos 5 anos?”
4. “Quanto ele gastou por ano?”
5. “Quais os maiores fornecedores dele?”
6. Escolha um CNPJ da resposta: “Consulte o cadastro deste CNPJ.”
7. “Quem são os sócios dessa empresa?” — usa o CNPJ selecionado; a consulta pode não informar o quadro.
8. “Essa empresa aparece no CEIS ou CNEP?”
9. Em nova apuração: “A cota pode pagar hospedagem em Brasília?”

Confira identidade, período, soma, fonte e data das sanções. A ausência de dados não equivale a gasto zero ou regularidade.

## Demonstração sem chaves

```bash
devbox run demo
```

O comando prepara `data/demo/apuramandato.db` com amostra CEAP e sanções sintéticas. Dentro do Devbox:

```bash
MODO_OFFLINE=true APURAMANDATO_DB="$PWD/data/demo/apuramandato.db" uv run streamlit run app.py
```

O modo roteirizado serve para demonstrar a execução sem credenciais, não para comprovar qualidade do LLM ou HTTP real. A aplicação identifica os dados de demonstração. O roteiro completo de vários anos usa o banco de produção; a amostra não tem a mesma cobertura.

## Arquitetura e requisitos

Um agente único integra SQL somente leitura, RAG, API empresarial, cálculos e memória. A checagem recalcula os valores no mesmo recorte; não constitui auditoria independente nem certificação legal. O histórico curto usa checkpointer por sessão; resumos salvos explicitamente formam memória episódica em SQLite.

Os notebooks 01–05 documentam as fases acadêmicas, incluindo chain estruturada, LCEL, comparação de orquestradores, ferramentas, memória, RAG e avaliação. O notebook 06 demonstra o núcleo atual. APIs antigas do enunciado são explicadas por equivalência com `create_agent` e os componentes atuais, conforme as orientações consolidadas da disciplina.

Consulte [arquitetura](docs/ARQUITETURA.md), [rubricas](docs/ENUNCIADO_E_RUBRICAS.md), [relatório](docs/RELATORIO.md) e [roteiro de demonstração](docs/ROTEIRO_DEMONSTRACAO.md). As execuções online mais recentes passaram no núcleo (9/9) e na investigação (14/14). A avaliação online e o link do experimento estão no relatório. O vídeo pessoal ainda precisa ser anexado; fixtures não substituem HTTP real.

## Administração e extensão preservada

Dentro de `devbox shell`, os importadores continuam disponíveis:

```bash
uv run python scripts/load_sources.py catalogo
uv run python scripts/load_sources.py ceis --arquivo /caminho/CEIS.csv --data 2026-11-13
uv run python scripts/load_sources.py cnep --arquivo /caminho/CNEP.csv --data 2026-11-13
uv run python scripts/setup_project.py --ampliado
```

Use a data real do snapshot. O modo ampliado prepara TSE, emendas, favorecidos e identidades. A biblioteca permite `montar_agente(escopo_ampliado=True)` para experimentos, mas a interface permanece no núcleo principal. Esses importadores não são necessários para demonstrar o fluxo acadêmico selecionado.

**Fontes:** [CEAP](https://www.camara.leg.br/cotas/), [CEIS](https://portaldatransparencia.gov.br/download-de-dados/ceis), [CNEP](https://portaldatransparencia.gov.br/download-de-dados/cnep), [BrasilAPI](https://brasilapi.com.br/docs).


## Teste da investigação completa e dos riscos

```bash
devbox run investigate-test
devbox run investigate-online
```

O ensaio completo acompanha 14 etapas: dez maiores fornecedores de Tabata em 2024–2026, evolução dos três primeiros, categorias, concentração, cadastro, sócios, outros deputados, participação na CEAP, sanções, limites sobre faturamento/corrupção, regras, mudança de período e ambiguidade. Os valores são comparados com consultas SQL separadas sobre o mesmo banco; não é auditoria independente da fonte. O modo offline usa o cadastro gravado, sem comprovar HTTP ou atualidade dos sócios. O modo online exige chave e rede e desativa o cache HTTP.

Resultados em `entregaveis/investigacao-completa.json`, `entregaveis/investigacao-completa-online.json` e `entregaveis/testes-investigacao.xml`. A investigação usa ações tipadas com ID, CNPJ e anos explícitos. A concentração inclui todas as despesas do deputado no denominador, mesmo quando o ranking é de empresas. A participação de uma empresa considera somente o universo CEAP e não representa seu faturamento. Listas limitadas são sinalizadas e não alteram os agregados globais.

## Entrega acadêmica

Consulte [instruções de entrega](docs/ENTREGA.md). O relatório registra os resultados e apresenta os links e evidências de avaliação. HTMLs e materiais auxiliares de apresentação ficam fora do repositório.
