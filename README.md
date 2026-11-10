# ApuraCota — Agente de apuração de gastos parlamentares (CEAP)

Projeto da disciplina **Agentes Cognitivos com LangChain** (Infnet, 2026).

Um agente conversacional para repórteres que responde perguntas sobre a Cota para o Exercício da Atividade Parlamentar (CEAP) da Câmara dos Deputados. Ele decide sozinho entre consultar a **API de Dados Abertos da Câmara**, um **banco SQLite** com as despesas do ano e um **índice FAISS** com as regras da cota (Ato da Mesa 43/2009), mantém a memória da apuração entre turnos e **verifica os próprios números antes de responder**.

## Status

| Fase | Conteúdo | Status |
|---|---|---|
| 1 | MVP: chain com entrada e saída estruturadas (JSON), Runnable, `@chain` vs LCEL | ✅ online |
| 2 | Agente ReAct, ferramentas (API da Câmara, SQL, hardcoded, semântica), roteamento, prompt PTCF, confiabilidade | ✅ online |
| 3 | Memória CoALA, chatbot multi-turno, NLU (intenção e entidades) | ✅ online |
| 4 | Text-to-query, retriever sobre índice FAISS salvo, diagnóstico de falha de recuperação | ✅ online |
| 5 | Verificação factual + NLI, agente integrador, Streamlit, avaliação local e no LangSmith | ✅ online |
| — | Relatório PDF, vídeo, entrega no Moodle (13/11/2026) | ⬜ |

"✅ online" significa que o código está implementado, testado (`devbox run test`, 37 testes, modo offline) e os notebooks foram executados com `gpt-4o-mini` no agente, `gpt-4o` como juiz e índice FAISS real (`text-embedding-3-small`). Avaliação local online: 6/6 em trajetória, valor e juiz. No LangSmith: dataset `apuracota-casos` com seis experimentos (gpt-4o-mini, dummy offline e gpt-4o, duas rodadas cada), comparados por `comparar_experimentos()` no notebook 05.

O plano detalhado, fase a fase e rubrica a rubrica, está em [`docs/PLANO_DE_IMPLEMENTACAO.md`](docs/PLANO_DE_IMPLEMENTACAO.md). O enunciado oficial está em [`docs/ENUNCIADO_E_RUBRICAS.md`](docs/ENUNCIADO_E_RUBRICAS.md).

## Estrutura

```
projeto-agentes-langchain/
├── devbox.json / pyproject.toml / uv.lock   # ambiente reprodutível
├── app.py                        # interface Streamlit (chat + painel de verificação)
├── src/
│   ├── config.py                 # modelos, caminhos, modo offline
│   ├── llm.py                    # OpenAI quando há chave; modelo dummy roteirizado e embeddings fake sem chave
│   ├── data.py                   # carga do CSV da CEAP em SQLite, ferramenta SQL com guardrails, índice FAISS
│   ├── tools.py                  # ferramentas: API da Câmara (paginada), Brasil API, estatísticas; cache em fixtures
│   ├── prompts.py                # prompt PTCF, few-shot, roteador, templates
│   ├── chains.py                 # fases 1-3: LCEL, @chain, roteamento, sequencial/paralela, parser com reparo, callbacks, NLU
│   ├── memory.py                 # memória CoALA: checkpointer, poda, memória episódica
│   ├── rag.py                    # retriever open-box (scores), query transformation, diagnóstico
│   ├── verification.py           # claims (Pydantic), fonte autoritativa a partir da trajetória, fact-check, NLI
│   ├── evaluation.py             # casos de teste, avaliadores de trajetória/valor/juiz, execução local e LangSmith
│   └── agent.py                  # montagem do agente integrador (create_agent + MemorySaver)
├── scripts/
│   ├── download_data.py          # baixa o CSV anual da CEAP e monta o SQLite
│   └── record_fixtures.py        # grava respostas reais das APIs para o modo offline
├── notebooks/                    # um notebook executado por fase (01 a 05)
├── tests/                        # pytest: ferramentas, SQL, agente multi-turno, memória, verificação (roda offline)
├── data/normas/                  # Ato da Mesa 43/2009 consolidado (versionado); CSV e SQLite ficam fora do git
├── indices/                      # índice FAISS salvo (fase 2) e reaproveitado (fase 4)
├── fixtures/                     # respostas gravadas das APIs (modo offline)
└── docs/                         # plano, enunciado, dataset de avaliação, relatório
```

## Como rodar

O ambiente é gerenciado por [devbox](https://www.jetify.com/devbox) (fixa Python 3.12 e `uv` via Nix) e por [uv](https://docs.astral.sh/uv/) (ambiente virtual e lockfile). Com o devbox instalado:

```bash
devbox shell                    # instala Python 3.12 + uv e roda `uv sync` (cria .venv a partir do uv.lock)
cp .env.example .env            # preencha OPENAI_API_KEY e, opcionalmente, LANGCHAIN_API_KEY

devbox run data                 # baixa o CSV da CEAP (≈75 MB) e monta o SQLite (209 mil despesas de 2025)
devbox run index                # monta o índice FAISS do Ato da Mesa (data/normas/ato_mesa_43_2009.txt)
devbox run fixtures             # grava respostas reais das APIs para o modo offline
devbox run app                  # abre o Streamlit
devbox run notebook             # abre o Jupyter Lab em notebooks/
devbox run test                 # pytest em modo offline
devbox run lint                 # ruff
```

Para reexecutar os notebooks do zero com as saídas gravadas:

```bash
for n in notebooks/0*.ipynb; do uv run jupyter nbconvert --to notebook --execute --inplace "$n"; done
```

Sem devbox, basta ter Python 3.12 e `uv`: `uv sync && uv run streamlit run app.py`. As dependências e versões exatas estão em `pyproject.toml` e `uv.lock`.

### Modo offline (avaliação sem nenhuma chave)

Sem `OPENAI_API_KEY`, ou com `MODO_OFFLINE=true`, o projeto roda inteiro com alternativas dummy, como pede o enunciado:

- **LLM:** `ChatRoteirizado` (`src/llm.py`), um modelo determinístico que escolhe ferramentas por regras e formata as observações. Serve para demonstrar o ciclo ReAct, a memória por thread, o Streamlit e os testes.
- **Embeddings:** `DeterministicFakeEmbedding` (o índice FAISS é construído, mas a busca não é semântica).
- **APIs:** as ferramentas leem as respostas gravadas em `fixtures/` (chave = hash de URL + parâmetros).

O banco SQLite é real nos dois modos (`devbox run data`). Com a chave da OpenAI, basta remover `MODO_OFFLINE` do `.env`: o código é o mesmo.

### Casos de teste

Tabata Amaral (PSB-SP, id 204534) e Nikolas Ferreira (PL-MG, id 209787) em 2025, o homônimo "Silva", a média do PSB com combustível, o limite da cota em SP e fornecedores por CNPJ. O dataset com ferramentas esperadas e valores de referência está em `docs/dataset_avaliacao.json`.

Divergência conhecida: a API da Câmara (ao vivo) e o CSV anual (baixado em 07/11/2026) não batem para o mesmo deputado e ano (Tabata Amaral 2025: R$ 530.231,55 na API, R$ 504.994,08 no CSV). O agente sempre declara a fonte e não mistura as duas numa mesma conta.

## Fontes de dados

| Fonte | Uso | Acesso |
|---|---|---|
| [API de Dados Abertos da Câmara](https://dadosabertos.camara.leg.br/swagger/api.html) | deputados, despesas por período, partidos | pública, sem chave |
| [CSV anual da CEAP](https://www.camara.leg.br/cota-parlamentar/) | agregações em SQL (média por partido, ranking de fornecedores) | download |
| [Ato da Mesa 43/2009](https://www2.camara.leg.br/legin/int/atomes/2009/atodamesa-43-21-maio-2009-588364-normaatualizada-cd-mesa.html) (texto consolidado) | RAG: o que a cota pode pagar, limites por UF, prazos | site da Câmara, versionado em `data/normas/` |
| [Brasil API](https://brasilapi.com.br/docs#tag/CNPJ) | dados do fornecedor por CNPJ | pública, sem chave |

## Uso de IA generativa

Este projeto foi desenvolvido com apoio de assistentes de IA (Claude, da Anthropic) para planejamento, escrita de código e revisão de texto. Todo o código foi revisado e executado pelo autor. Detalhes no relatório técnico.

## Autor

Igor Laltuf — [Fiquem Sabendo](https://fiquemsabendo.com.br)
