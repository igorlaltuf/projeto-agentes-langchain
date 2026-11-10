"""Avaliadores deterministicos: parser de valores e trajetoria."""

from src.evaluation import _Example, _Run, _valores_brl, trajetoria_correta, valor_correto


def test_valores_brl_aceita_formatos_pt_br_en_us_e_puro():
    assert _valores_brl("gastou R$ 48.808,83 em 2025") == [48808.83]
    assert _valores_brl("gastou R$ 48,808.83 em 2025") == [48808.83]
    assert _valores_brl("gastou R$48808.83.") == [48808.83]
    assert _valores_brl("R$ 1.234.567,00 e R$ 9,50") == [1234567.0, 9.5]
    assert _valores_brl("sem valores") == []


def test_valor_correto_independe_do_formato():
    caso = {"pergunta": "x", "valor_esperado": 48808.83}
    assert valor_correto(_Run({"resposta": "R$ 48,808.83"}), _Example(caso))["score"] == 1
    assert valor_correto(_Run({"resposta": "R$ 48.808,83"}), _Example(caso))["score"] == 1
    assert valor_correto(_Run({"resposta": "R$ 99.000,00"}), _Example(caso))["score"] == 0


def test_trajetoria_exige_ferramenta_e_pune_atalho_sem_busca():
    caso = {
        "pergunta": "Silva",
        "ferramentas_esperadas": ["buscar_deputado"],
        "ferramentas_proibidas": ["listar_despesas"],
    }
    sem_busca = _Run({"trajetoria": []})
    com_busca = _Run({"trajetoria": [{"tipo": "decisao", "ferramenta": "buscar_deputado"}]})
    assert trajetoria_correta(sem_busca, _Example(caso))["score"] == 0
    assert trajetoria_correta(com_busca, _Example(caso))["score"] == 1
