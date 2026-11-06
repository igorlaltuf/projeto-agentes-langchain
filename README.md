# ApuraCota — Agente de apuração de gastos parlamentares (CEAP)

Projeto da disciplina **Agentes Cognitivos com LangChain** (Infnet, 2026).

Um agente conversacional para repórteres que responde perguntas sobre a Cota para o Exercício da Atividade Parlamentar (CEAP) da Câmara dos Deputados. Ele decide sozinho entre consultar a **API de Dados Abertos da Câmara**, um **banco SQLite** com as despesas do ano e um **índice FAISS** com as regras da cota (Ato da Mesa 43/2009), mantém a memória da apuração entre turnos e **verifica os próprios números antes de responder**.

## Status

| Fase | Conteúdo | Status |
|---|---|---|
| 1 | MVP: chain com entrada e saída estruturadas (JSON), Runnable, `@chain` vs LCEL | ⬜ |
| 2 | Agente ReAct, ferramentas (API da Câmara, SQL, hardcoded, semântica), roteamento, prompt PTCF, confiabilidade | ⬜ |
| 3 | Memória CoALA, chatbot multi-turno, NLU (intenção e entidades) | ⬜ |
| 4 | Text-to-query, retriever sobre índice FAISS salvo, diagnóstico de falha de recuperação | ⬜ |
| 5 | Verificação factual + NLI, agente integrador, Streamlit, avaliação no LangSmith | ⬜ |

O plano detalhado, fase a fase e rubrica a rubrica, está em [`docs/PLANO_DE_IMPLEMENTACAO.md`](docs/PLANO_DE_IMPLEMENTACAO.md). O enunciado oficial está em [`docs/ENUNCIADO_E_RUBRICAS.md`](docs/ENUNCIADO_E_RUBRICAS.md).

## Estrutura

```
projeto-agentes-langchain/
├── devbox.json / pyproject.toml / uv.lock   # ambiente reprodutível
├── app.py                        # interface Streamlit (chat + painel de verificação)
├── src/
│   ├── config.py                 # modelos, caminhos, modo offline
│   ├── data.py                   # carga do CSV da CEAP em SQLite, índice FAISS
│   ├── tools.py                  # ferramentas: API da Câmara, Brasil API, SQL, estatísticas, regras
│   ├── prompts.py                # prompt PTCF, few-shot, templates
│   ├── memory.py                 # memória CoALA: checkpointer, episódica, cache
│   ├── verification.py           # extração de claims, ground truth, NLI
│   └── agent.py                  # montagem do agente integrador
├── scripts/
│   ├── download_data.py          # baixa o CSV da CEAP e o Ato da Mesa
│   └── record_fixtures.py        # grava respostas reais das APIs para o modo offline
├── notebooks/                    # um notebook por fase (01 a 05)
├── data/                         # CSV da CEAP e SQLite (não versionados)
├── indices/                      # índice FAISS salvo (fase 2) e reaproveitado (fase 4)
├── fixtures/                     # respostas gravadas das APIs (modo offline)
└── docs/                         # plano, enunciado, relatório
```

## Como rodar

O ambiente é gerenciado por [devbox](https://www.jetify.com/devbox) (fixa Python 3.12 e `uv` via Nix) e por [uv](https://docs.astral.sh/uv/) (ambiente virtual e lockfile). Com o devbox instalado:

```bash
devbox shell                    # instala Python 3.12 + uv e roda `uv sync` (cria .venv a partir do uv.lock)
cp .env.example .env            # preencha OPENAI_API_KEY e, opcionalmente, LANGCHAIN_API_KEY

devbox run data                 # baixa o CSV da CEAP e monta o SQLite
devbox run index                # monta o índice FAISS do Ato da Mesa (precisa de data/ato_mesa.txt)
devbox run fixtures             # grava respostas reais das APIs para o modo offline
devbox run app                  # abre o Streamlit
devbox run notebook             # abre o Jupyter Lab em notebooks/
devbox run lint                 # ruff
```

Sem devbox, basta ter Python 3.12 e `uv`: `uv sync && uv run streamlit run app.py`. As dependências e versões exatas estão em `pyproject.toml` e `uv.lock`.

### Modo offline (avaliação sem chaves de API externas)

As ferramentas que chamam a API da Câmara e a Brasil API leem respostas gravadas em `fixtures/` quando `MODO_OFFLINE=true` no `.env`. Isso permite reproduzir todos os casos de teste sem depender da disponibilidade das APIs. A chave da OpenAI continua necessária para o LLM.

## Fontes de dados

| Fonte | Uso | Acesso |
|---|---|---|
| [API de Dados Abertos da Câmara](https://dadosabertos.camara.leg.br/swagger/api.html) | deputados, despesas por período, partidos | pública, sem chave |
| [CSV anual da CEAP](https://www.camara.leg.br/cota-parlamentar/) | agregações em SQL (média por partido, ranking de fornecedores) | download |
| Ato da Mesa 43/2009 e FAQ da cota | RAG: o que a cota pode pagar, limites por UF | site da Câmara |
| [Brasil API](https://brasilapi.com.br/docs#tag/CNPJ) | dados do fornecedor por CNPJ | pública, sem chave |

## Uso de IA generativa

Este projeto foi desenvolvido com apoio de assistentes de IA (Claude, da Anthropic) para planejamento, escrita de código e revisão de texto. Todo o código foi revisado e executado pelo autor. Detalhes no relatório técnico.

## Autor

Igor Laltuf — [Fiquem Sabendo](https://fiquemsabendo.com.br)
