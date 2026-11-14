# Defesa do ApuraMandato — 6 a 8 minutos

1. **Problema e arquitetura:** mostrar o fluxo gastos → fornecedores → cadastro → sanções, além das regras da cota. Explicar por que um agente com memória e ferramentas é útil.
2. **Cobertura:** mostrar anos CEAP e datas CEIS/CNEP. Identificar produção ou demonstração; não apresentar sanções sintéticas como reais.
3. **Memória curta:** perguntar “Quanto Tabata Amaral gastou com combustível em 2025?” e “E em 2024?”. Mostrar que ID e categoria foram preservados.
4. **SQL e checagem:** em nova apuração, perguntar quem mais gastou com combustível nos últimos cinco anos e depois o valor por ano. Conferir a soma e indicar o ano corrente parcial.
5. **Ferramenta externa real:** listar os fornecedores do deputado, escolher um CNPJ válido e consultar seu cadastro na BrasilAPI. Mostrar a chamada real com cache desativado. Consultar CEIS/CNEP locais e suas datas, sem concluir irregularidade.
6. **RAG:** perguntar sobre hospedagem em Brasília. Mostrar o trecho da exceção no Ato da Mesa, a versão da fonte e o complemento lexical ao FAISS.
7. **Memória longa:** salvar uma apuração explicitamente e recuperá-la. Explicar memória episódica, semântica e procedural.
8. **Avaliação:** mostrar testes, roteiro de aceitação e experimento final no LangSmith, discutindo falhas, latência e trajetória. Demonstrar uma pergunta eleitoral como limite explícito do escopo.

```bash
devbox run validate
devbox run validate-online
```

Dentro do Devbox, para o experimento e a prova HTTP:

```bash
USAR_CACHE_API=false uv run python scripts/check_online.py
uv run python scripts/evaluate_mandato.py --online --langsmith
```

A execução online exige rede e credenciais. O roteiro offline usa cadastro de teste e não comprova HTTP real. Sem cargas CEIS/CNEP, a sanção aparece como pendência. A gravação pessoal, o link acessível e as capturas do experimento devem ser produzidos após a execução real; não são fabricados pelo projeto.
