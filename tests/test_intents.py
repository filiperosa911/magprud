from datetime import date

from seguros.messaging.intents import (
    INTENT_DUVIDA,
    INTENT_JA_PAGUEI,
    INTENT_NOVO_LINK,
    INTENT_RESCHEDULE,
    INTENT_SAIR,
    INTENT_SAUDACAO,
    classificar,
    extrair_data_desejada,
)

HOJE = date(2026, 6, 18)  # quinta-feira


def c(texto):
    return classificar(texto, hoje=HOJE, max_dias=30)


def test_sair_forte():
    for t in ["sair", "SAIR", "quero sair", "não quero mais receber", "pare de enviar",
              "me tira da lista"]:
        assert c(t).intent == INTENT_SAIR, t


def test_sair_nao_falso_positivo():
    # 'sair' embutido em frase longa NÃO é opt-out (protege contra opt-out indevido)
    assert c("não consigo sair de casa pra pagar essa semana").intent != INTENT_SAIR


def test_ja_paguei():
    for t in ["já paguei", "paguei", "quitei", "fiz o pagamento", "tá pago",
              "paguei ontem no pix", "segue o comprovante"]:
        assert c(t).intent == INTENT_JA_PAGUEI, t


def test_ja_paguei_negacao():
    assert c("ainda não paguei").intent != INTENT_JA_PAGUEI
    assert c("não paguei ainda").intent != INTENT_JA_PAGUEI


def test_precedencia_sair_vence_pago():
    assert c("já paguei mas quero sair").intent == INTENT_SAIR


def test_reschedule_com_data():
    r = c("posso pagar semana que vem?")
    assert r.intent == INTENT_RESCHEDULE
    assert r.data_desejada == "2026-06-25"


def test_reschedule_pago_futuro_nao_e_japaguei():
    r = c("pago dia 30")
    assert r.intent == INTENT_RESCHEDULE  # futuro, não "já paguei"
    assert r.data_desejada == "2026-06-30"


def test_reschedule_amanha():
    assert c("amanhã eu pago").data_desejada == "2026-06-19"


def test_reschedule_sem_data():
    r = c("dá pra deixar pra outro dia?")
    assert r.intent == INTENT_RESCHEDULE
    assert r.data_desejada is None


def test_novo_link():
    for t in ["o link não abre", "manda o link de novo", "o boleto não carrega",
              "gera outro link"]:
        assert c(t).intent == INTENT_NOVO_LINK, t


def test_novo_link_vs_reschedule():
    # com marcador temporal, vira RESCHEDULE (não NOVO_LINK)
    assert c("manda o link semana que vem").intent == INTENT_RESCHEDULE


def test_saudacao():
    for t in ["oi", "bom dia", "obrigado", "ok"]:
        assert c(t).intent == INTENT_SAUDACAO, t


def test_duvida():
    for t in ["quanto é?", "isso é golpe?", "que seguro é esse?"]:
        assert c(t).intent == INTENT_DUVIDA, t


def test_extrair_data_clamp():
    # depois de amanhã / daqui a N dias
    assert extrair_data_desejada("depois de amanhã", hoje=HOJE, max_dias=30) == "2026-06-20"
    assert extrair_data_desejada("daqui a 3 dias", hoje=HOJE, max_dias=30) == "2026-06-21"
    # 'dia 1' -> próximo mês (nunca passado)
    assert extrair_data_desejada("pode ser dia 1", hoje=HOJE, max_dias=40) == "2026-07-01"
    # fora do teto -> None
    assert extrair_data_desejada("dia 18", hoje=HOJE, max_dias=5) is None
    # dd/mm
    assert extrair_data_desejada("pago 25/06", hoje=HOJE, max_dias=30) == "2026-06-25"
