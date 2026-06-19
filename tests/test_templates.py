from seguros.messaging.templates import (
    WHATSAPP_DIA0,
    brl_from_cents,
    primeiro_nome,
    render,
)


def test_brl_from_cents():
    assert brl_from_cents(25990) == "R$ 259,90"
    assert brl_from_cents(100000) == "R$ 1.000,00"
    assert brl_from_cents(123456789) == "R$ 1.234.567,89"
    assert brl_from_cents(0) == "R$ 0,00"
    assert brl_from_cents(None) == "R$ —"


def test_primeiro_nome_normaliza_caps():
    assert primeiro_nome("MARIA SILVA SOUZA") == "Maria"
    assert primeiro_nome("  joão  pereira ") == "João"
    assert primeiro_nome("") == ""


def test_render_substitui_variaveis():
    ctx = {
        "primeiro_nome": "Maria",
        "competencia": "04/2026",
        "valor_total": "R$ 259,90",
        "link_pagamento": "https://pag.example.com/x?a=1&b=2",
        "nome_corretor": "Kike",
        "corretora": "Aurex",
    }
    msg = render(WHATSAPP_DIA0, ctx)
    assert "Maria" in msg
    assert "R$ 259,90" in msg
    assert "https://pag.example.com/x?a=1&b=2" in msg
    assert "SAIR" in msg
    assert "${" not in msg  # nenhum placeholder sobrando


def test_render_link_com_chaves_nao_quebra():
    # str.format quebraria com {}; string.Template não.
    ctx = {"link_pagamento": "https://x/{token}", "primeiro_nome": "Ana",
           "competencia": "x", "valor_total": "y", "nome_corretor": "a", "corretora": "b"}
    msg = render(WHATSAPP_DIA0, ctx)
    assert "https://x/{token}" in msg
