from seguros.messaging.phone import (
    canonical_brazilian_phone,
    is_plausible_email,
    is_valid_whatsapp,
)


def test_celular_completo_com_mascara():
    assert canonical_brazilian_phone("(11) 99876-5432") == "5511998765432"


def test_celular_com_mais_e_espacos():
    assert canonical_brazilian_phone("+55 19 99276-6760") == "5519992766760"


def test_reinsere_nono_digito_quando_omitido():
    # 8 dígitos locais começando em 9 -> reinsere o 9
    assert canonical_brazilian_phone("1188765432") == "5511988765432"
    # já tem 9 -> mantém
    assert canonical_brazilian_phone("11988765432") == "5511988765432"


def test_local_8_digitos_comeca_em_8_vira_celular():
    assert canonical_brazilian_phone("1185554444") == "5511985554444"


def test_fixo_eh_invalido_para_whatsapp():
    # 8 dígitos começando em 3 (fixo) -> inválido
    assert canonical_brazilian_phone("(11) 3344-5566") is None


def test_ddd_invalido():
    assert canonical_brazilian_phone("(00) 99999-9999") is None


def test_remove_prefixo_internacional_00():
    assert canonical_brazilian_phone("005511998765432") == "5511998765432"


def test_remove_prefixo_tronco_0():
    # 0 + DDD + celular de 9 dígitos
    assert canonical_brazilian_phone("011999998888") == "5511999998888"
    # 0 + DDD + celular de 8 dígitos (reinsere o 9)
    assert canonical_brazilian_phone("01188887777") == "5511988887777"


def test_vazio_e_lixo():
    assert canonical_brazilian_phone("") is None
    assert canonical_brazilian_phone("abc") is None


def test_is_valid_whatsapp():
    assert is_valid_whatsapp("11999998888") is True
    assert is_valid_whatsapp("1133334444") is False


def test_email_plausivel():
    assert is_plausible_email("maria@example.com") is True
    assert is_plausible_email("sem-arroba") is False
    assert is_plausible_email("") is False
    assert is_plausible_email(None) is False
