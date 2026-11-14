# Entrega acadêmica do ApuraMandato

## Conteúdo

- `src/`, `app.py`, `scripts/`: implementação e reprodução.
- `notebooks/01` a `06`: fases e integração, com saídas executadas registradas.
- `tests/`, `examples/`, `fixtures/`: testes, amostras e alternativas sem credenciais.
- `data/normas/`, `indices/ato_mesa/`: textos e índice normativo existente.
- `devbox.json`, `devbox.lock`, `pyproject.toml`, `uv.lock`, `.env.example`: ambiente reproduzível.
- `docs/RELATORIO.md` e `entregaveis/igor_laltuf_ai-agents_langchain-agents.pdf`: relatório técnico.
- `entregaveis/`: evidências offline/online e resultados de testes.

## Reproduzir sem credenciais

Na pasta extraída, com Devbox instalado:

```bash
devbox run sync
devbox run demo
MODO_OFFLINE=true APURAMANDATO_DB="$PWD/data/demo/apuramandato.db" devbox run app
devbox run test
```

Para avaliar a amostra sem API real:

```bash
devbox run -- uv run python scripts/evaluate_mandato.py --demo
```

A avaliação grava seu resultado; copie evidências originais antes de repetir comandos com o mesmo arquivo de saída. A amostra é sintética, tem cobertura menor e não deve ser comparada às referências numéricas do banco real. Não precisa de chave. Instalar dependências inicialmente exige rede; depois de instalado, o modo offline não precisa dos serviços de modelo ou cadastro.

## Reprodução com fontes reais

Copie `.env.example` para `.env`, configure a chave OpenAI e modo online, sem publicar segredos. Execute `devbox run setup`, depois `devbox run app`. As bases de produção não estão no ZIP; o setup as baixa. Para reproduzir o LangSmith, configure também as credenciais e o projeto correspondentes.

```bash
devbox run validate-online
devbox run investigate-online
devbox run -- uv run python scripts/evaluate_mandato.py --online --langsmith
```

## Conferência antes da submissão

1. Consultar as evidências e o link do experimento na seção 7 do relatório.
2. Gravar o vídeo com interação real. Publicar no Drive com acesso por link e testar em janela anônima.
3. Substituir a indicação de vídeo pendente no relatório pelo endereço real. O vídeo ainda não foi fornecido.
4. Regenerar o PDF: `python scripts/export_report.py` em Python com Pycairo instalado.
5. Atualizar o ZIP se o relatório ou vídeo forem alterados depois de sua geração. Não incluir `.env`, chaves ou conversas pessoais.
6. Enviar código/pacote, PDF e link de vídeo no Moodle e conferir o recibo da submissão.

O pacote preparado é uma versão para revisão com resultados reais e indicação transparente de vídeo pendente. Não representa uma submissão já realizada. HTMLs de explicação e materiais de apresentação foram movidos para `../apuramandato-materiais-auxiliares/` e não integram a entrega.
