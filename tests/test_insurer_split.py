"""Split de seguradora: factory, escopo por seguradora e conector Prudential.

Tudo OFFLINE: o que depende de navegador (login/inspect/discover ao vivo) é
validado ao vivo, como na MAG. Aqui cobrimos o roteamento, a trava de calibração
e o comportamento determinístico (link-como-lembrete, escopo de dados).
"""

import pytest

from seguros.config import config_for_insurer
from seguros.connectors.base import SeguradoraConnector
from seguros.connectors.factory import (
    INSURER_LABELS,
    SUPPORTED_INSURERS,
    build_connector,
    insurer_has_payment_link,
)
from seguros.connectors.prudential.connector import PrudentialConnector
from seguros.connectors.prudential.scraping import extract_phone, longest_digit_run
from seguros.connectors.prudential.selectors import load_selectors
from seguros.domain.models import Acao, Canal
from seguros.domain.state import evaluate
from seguros.messaging.templates import WHATSAPP_DIA0_LEMBRETE, render


def test_config_insurer_default_mag(make_config, tmp_path):
    assert make_config(tmp_path).insurer == "mag"


def test_supported_insurers_cobre_mag_e_prudential():
    assert "mag" in SUPPORTED_INSURERS
    assert "prudential" in SUPPORTED_INSURERS
    assert set(INSURER_LABELS) >= set(SUPPORTED_INSURERS)


def test_factory_roteia_prudential(make_config, tmp_path):
    conn = build_connector(make_config(tmp_path, insurer="prudential"))
    assert isinstance(conn, PrudentialConnector)
    assert isinstance(conn, SeguradoraConnector)
    assert conn.name == "prudential"


def test_factory_seguradora_desconhecida_levanta(make_config, tmp_path):
    with pytest.raises(ValueError):
        build_connector(make_config(tmp_path, insurer="metlife"))


def test_prudential_link_e_lembrete(make_config, tmp_path):
    """A Prudential não tem link de pagamento: régua atua como lembrete (sem link)."""
    conn = build_connector(make_config(tmp_path, insurer="prudential"))
    dry = conn.generate_payment_link("12345678909", live=False)
    assert dry.link is None and dry.would_generate is False and dry.dry_run is True
    live = conn.generate_payment_link("12345678909", live=True)
    assert live.link is None and live.dry_run is False


def test_prudential_selectors_yaml_carrega():
    sel = load_selectors()
    col = sel.get("atraso.col", {})
    assert "apolice" in col and "telefone" in col  # chaveado por Apólice, tel na grade
    assert sel.get("atraso.form.filtrar_button") is not None


def test_config_for_insurer_escopa_prudential(make_config, tmp_path):
    base = make_config(tmp_path)  # corretor_id="local"
    p = config_for_insurer(base, "prudential")
    assert p.insurer == "prudential"
    assert p.corretor_id == "local:prudential"  # isolado da MAG no mesmo banco
    assert p.user_data_dir.name == ".prudential_session"
    assert p.healthcheck_auto is False
    # MAG permanece intacta (mesmo corretor_id, mesma sessão)
    m = config_for_insurer(base, "mag")
    assert m.corretor_id == "local" and m.insurer == "mag"


# --- modo LEMBRETE (seguradora sem link de pagamento) ------------------------


def test_capability_payment_link():
    assert insurer_has_payment_link("mag") is True
    assert insurer_has_payment_link("prudential") is False
    assert insurer_has_payment_link("desconhecida") is True  # default seguro


def test_gate_lembrete_envia_sem_link():
    """Prudential (requer_link=False): sem link, mas ENVIA o lembrete."""
    d = evaluate(Canal.WHATSAPP, opted_out=False, tem_link=False, destino_valido=True,
                 ja_enviado=False, window_open=True, requer_link=False)
    assert d.acao is Acao.SEND
    # MAG (default requer_link=True): sem link -> PULA (SEM_LINK)
    d2 = evaluate(Canal.WHATSAPP, opted_out=False, tem_link=False, destino_valido=True,
                  ja_enviado=False, window_open=True)
    assert d2.acao is Acao.SKIP


def test_longest_digit_run_extrai_apolice():
    """Chave da Prudential = dígitos da Apólice (célula '74\\n000887908\\nAtiva')."""
    assert longest_digit_run("74\n000887908\nAtiva") == "000887908"
    assert longest_digit_run("sem digito") == ""


def test_extract_phone_da_coluna_contatos():
    assert extract_phone("Cel.: (11) 95430-0078") == "(11) 95430-0078"
    assert extract_phone("Tel.: (21) 3322-1100") == "(21) 3322-1100"
    assert extract_phone("sem telefone") is None


def test_template_lembrete_neutro_e_sem_link():
    msg = render(WHATSAPP_DIA0_LEMBRETE, {
        "primeiro_nome": "Ana", "competencia": "04/2026", "valor_total": "R$ 100,00",
        "nome_corretor": "Kike", "corretora": "Aurex", "link_pagamento": "",
    })
    assert "${" not in msg  # sem placeholder solto
    assert "MAG" not in msg  # neutro de seguradora
    assert "http" not in msg.lower()  # sem link
