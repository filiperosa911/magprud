from seguros.domain.models import Acao, ClienteRegua, Resultado
from seguros.domain.state import evaluate_email, evaluate_whatsapp


def _cliente(**kw):
    base = dict(cpf="11111111111", nome="Maria", autoriza_whatsapp=True, autoriza_email=True)
    base.update(kw)
    return ClienteRegua(**base)


def test_whatsapp_envia_quando_tudo_ok():
    d = evaluate_whatsapp(_cliente(), opted_out=False, telefone_valido=True,
                          tem_link=True, window_open=True)
    assert d.acao is Acao.SEND


def test_optout_sobrepoe_consentimento():
    d = evaluate_whatsapp(_cliente(autoriza_whatsapp=True), opted_out=True,
                          telefone_valido=True, tem_link=True, window_open=True)
    assert d.acao is Acao.SKIP
    assert d.resultado is Resultado.PULADO_OPTOUT


def test_sem_link_pula():
    d = evaluate_whatsapp(_cliente(), opted_out=False, telefone_valido=True,
                          tem_link=False, window_open=True)
    assert d.resultado is Resultado.SEM_LINK


def test_telefone_invalido_pula():
    d = evaluate_whatsapp(_cliente(), opted_out=False, telefone_valido=False,
                          tem_link=True, window_open=True)
    assert d.resultado is Resultado.TELEFONE_INVALIDO


def test_consentimento_nao_bloqueia():
    # Consentimento foi removido como gate: WhatsApp=Não ainda envia.
    d = evaluate_whatsapp(_cliente(autoriza_whatsapp=False), opted_out=False,
                          telefone_valido=True, tem_link=True, window_open=True)
    assert d.acao is Acao.SEND


def test_idempotencia_whatsapp():
    d = evaluate_whatsapp(_cliente(whatsapp_enviado_em="2026-06-18T12:00:00Z"),
                          opted_out=False, telefone_valido=True, tem_link=True, window_open=True)
    assert d.resultado is Resultado.PULADO_IDEMPOTENTE


def test_fora_da_janela_adia():
    d = evaluate_whatsapp(_cliente(), opted_out=False, telefone_valido=True,
                          tem_link=True, window_open=False)
    assert d.acao is Acao.DEFER
    assert d.resultado is Resultado.PULADO_JANELA


def test_email_envia_sem_checar_consentimento():
    no = evaluate_email(_cliente(autoriza_email=False), opted_out=False, email_valido=True,
                        tem_link=True, window_open=True)
    assert no.acao is Acao.SEND


def test_email_invalido_usa_resultado_email():
    d = evaluate_email(_cliente(), opted_out=False, email_valido=False,
                       tem_link=True, window_open=True)
    assert d.resultado is Resultado.EMAIL_INVALIDO
