from datetime import time

import pytest

from seguros.clock import iso_utc, now_in
from seguros.dashboard.service import DashboardService
from seguros.db.connection import init_db
from seguros.db.repository import (
    InboundRepository,
    OptOutRepository,
    RescheduleRepository,
    ReguaRepository,
)
from seguros.domain.models import ClienteRegua, Resultado, ReguaStatus


# --- fakes (sem rede) --------------------------------------------------------


class FakeResult:
    def __init__(self, sent=True):
        self.sent = sent
        self.message_id = "FAKEID"
        self.detail = None
        self.resultado = Resultado.ENVIADO if sent else Resultado.ERRO


class FakeSender:
    def __init__(self):
        self.enviados = []

    def send(self, phone, message):
        self.enviados.append((phone, message))
        return FakeResult(True)


# --- repositórios ------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.sqlite")
    yield c
    c.close()


def test_inbound_idempotencia(conn):
    repo = InboundRepository(conn, "local")
    kw = dict(cpf=None, telefone=None, telefone_raw=None, sender_name=None, texto="oi",
              intent="DUVIDA", confianca=0.3, data_desejada=None, origem="webhook",
              outcome="processando")
    id1, new1 = repo.record(message_id="M1", **kw)
    id2, new2 = repo.record(message_id="M1", **kw)
    assert new1 is True and new2 is False
    assert id1 == id2
    # message_id None sempre insere (simulação)
    _, n3 = repo.record(message_id=None, **kw)
    assert n3 is True


def test_find_cpf_by_telefone(conn):
    repo = ReguaRepository(conn, "local")
    # cliente salvo SEM o 9º dígito (12 dígitos); inbound chega COM o 9º (13)
    repo.insert_enrollment(ClienteRegua(cpf="11111111111", nome="A",
                                        telefone="555184317808", enrolled_em=iso_utc()))
    assert repo.find_cpf_by_telefone("5551984317808") == "11111111111"
    assert repo.find_cpf_by_telefone("5551000000000") is None
    # ambiguidade: 2 clientes mesmo telefone -> None (nunca age no errado)
    repo.insert_enrollment(ClienteRegua(cpf="22222222222", nome="B",
                                        telefone="5551984317808", enrolled_em=iso_utc()))
    assert repo.find_cpf_by_telefone("5551984317808") is None


def test_optout_dedupe_telefone(conn):
    opt = OptOutRepository(conn, "local")
    opt.add(telefone="5551999990000", origem="sair_whatsapp")
    opt.add(telefone="5551999990000", origem="sair_whatsapp")  # idempotente
    n = conn.execute("SELECT COUNT(*) n FROM opt_out WHERE telefone='5551999990000'").fetchone()["n"]
    assert n == 1
    assert opt.is_opted_out(telefone="5551999990000") is True


def test_reschedule_repo(conn):
    r = RescheduleRepository(conn, "local")
    rid = r.create(cpf="11111111111", data_desejada="2026-06-25", texto_origem="semana que vem",
                   inbound_id=None)
    assert len(r.pendentes()) == 1
    r.mark_admin_notified(rid)
    assert r.get(rid)["status"] == "admin_avisado"
    r.mark_link_reenviado(rid, "https://magpag/x")
    assert r.get(rid)["status"] == "link_reenviado"
    assert r.pendentes() == []  # saiu da fila


# --- fluxo do service --------------------------------------------------------


@pytest.fixture
def svc(make_config, tmp_path, monkeypatch):
    cfg = make_config(tmp_path, zapi_instance_id="INST", zapi_token="T",
                      zapi_webhook_secret="seg", admin_whatsapp="5551988887777",
                      whatsapp_override_to="")
    s = DashboardService(cfg)
    s._zapi = FakeSender()
    s._mag_value = None
    monkeypatch.setattr(s, "_mag", lambda fn: s._mag_value)
    return s


def _enroll(s, cpf="11111111111", telefone="5551999990000", link="https://magpag/x"):
    conn = s._conn()
    try:
        ReguaRepository(conn, s.corretor_id).insert_enrollment(ClienteRegua(
            cpf=cpf, nome="Maria Silva", telefone=telefone, link_pagamento=link,
            whatsapp_enviado_em=iso_utc(), primeiro_disparo_em=iso_utc(),
            valor_inadimplente_cents=25990, competencia="04/2026", enrolled_em=iso_utc()))
    finally:
        conn.close()


def _status(s, cpf="11111111111"):
    conn = s._conn()
    try:
        return ReguaRepository(conn, s.corretor_id).get(cpf).status
    finally:
        conn.close()


def test_sair_inbound(svc):
    _enroll(svc)
    r = svc.processar_inbound(cpf="11111111111", texto="quero sair", origem="simulacao")
    assert r["outcome"] == "opt_out"
    assert _status(svc) is ReguaStatus.OPT_OUT
    conn = svc._conn()
    try:
        opt = OptOutRepository(conn, svc.corretor_id)
        assert opt.is_opted_out(cpf="11111111111") is True
        assert opt.is_opted_out(telefone="5551999990000") is True  # opt-out por número também
    finally:
        conn.close()
    assert svc._zapi.enviados  # confirmação enviada


def test_ja_paguei_confirmado(svc):
    _enroll(svc)
    svc._mag_value = 0  # MAG diz: regularizado
    r = svc.processar_inbound(cpf="11111111111", texto="já paguei", origem="simulacao")
    assert r["outcome"] == "pago"
    assert _status(svc) is ReguaStatus.RESOLVIDO


def test_ja_paguei_pendente_nao_confia_no_texto(svc):
    _enroll(svc)
    svc._mag_value = 5000  # MAG diz: ainda deve
    r = svc.processar_inbound(cpf="11111111111", texto="paguei ontem", origem="simulacao")
    assert r["outcome"] == "pendente"
    assert _status(svc) is ReguaStatus.EM_REGUA  # NÃO resolveu no grito


def test_ja_paguei_sem_leitura_escala_admin(svc):
    _enroll(svc)
    svc._mag_value = None  # não conseguiu ler
    r = svc.processar_inbound(cpf="11111111111", texto="quitei", origem="simulacao")
    assert r["outcome"] == "sem_leitura"
    # 2 envios: resposta ao cliente + aviso ao admin
    assert len(svc._zapi.enviados) == 2


def test_reschedule_registra_e_avisa(svc):
    _enroll(svc)
    r = svc.processar_inbound(cpf="11111111111", texto="pago semana que vem", origem="simulacao")
    assert r["outcome"] == "reschedule"
    assert r["data_desejada"] == "2026-06-25" or r["data_desejada"]  # data extraída
    assert len(svc.list_reschedules()) == 1


def test_novo_link_reenvia_existente(svc):
    _enroll(svc, link="https://magpag/abc")
    r = svc.processar_inbound(cpf="11111111111", texto="manda o link de novo", origem="simulacao")
    assert r["outcome"] == "novo_link_reenviado"
    assert any("magpag/abc" in m for _, m in svc._zapi.enviados)


def test_idempotencia_fim_a_fim(svc):
    _enroll(svc)
    a = svc.processar_inbound(cpf="11111111111", texto="oi", message_id="M9", origem="simulacao")
    b = svc.processar_inbound(cpf="11111111111", texto="oi", message_id="M9", origem="simulacao")
    assert a["outcome"] == "saudacao"
    assert b["outcome"] == "duplicado"


def test_trava_teste_bloqueia_webhook_por_telefone(make_config, tmp_path, monkeypatch):
    cfg = make_config(tmp_path, zapi_instance_id="INST", zapi_token="T",
                      zapi_webhook_secret="seg", whatsapp_override_to="5551984317808")
    s = DashboardService(cfg)
    s._zapi = FakeSender()
    monkeypatch.setattr(s, "_mag", lambda fn: 0)
    _enroll(s, telefone="5551999990000")
    # webhook real durante a trava NÃO age sobre cliente por telefone
    r = s.processar_inbound(telefone="5551999990000", texto="já paguei", origem="webhook")
    assert r["outcome"] == "bloqueado_trava_teste"
    assert _status(s) is ReguaStatus.EM_REGUA  # nada mudou


def test_healthcheck_due(make_config, tmp_path):
    cfg = make_config(tmp_path, healthcheck_auto=True, healthcheck_hora=time(0, 0))
    s = DashboardService(cfg)
    assert s._healthcheck_due() is True  # 00:00 já passou e nunca rodou hoje
    s._last_health_date = now_in(cfg.timezone).date().isoformat()
    assert s._healthcheck_due() is False  # já rodou hoje
    s._last_health_date = None
    cfg2 = make_config(tmp_path / "off", healthcheck_auto=False, healthcheck_hora=time(0, 0))
    assert DashboardService(cfg2)._healthcheck_due() is False  # auto desligado


def test_webhook_auth(svc, make_config, tmp_path):
    ok_payload = {"instanceId": "INST", "text": {"message": "oi"}}
    assert svc.ingest_zapi_webhook(ok_payload, "seg") == (True, "ok")
    assert svc.ingest_zapi_webhook(ok_payload, "errado")[0] is False
    assert svc.ingest_zapi_webhook({"text": {"message": "oi"}, "fromMe": True}, "seg")[0] is False
    assert svc.ingest_zapi_webhook({"text": {"message": "oi"}, "isGroup": True}, "seg")[0] is False
    assert svc.ingest_zapi_webhook({"instanceId": "INST"}, "seg")[0] is False  # sem texto
    # fail-closed: sem segredo configurado, rejeita tudo
    cfg2 = make_config(tmp_path / "b", zapi_webhook_secret="")
    s2 = DashboardService(cfg2)
    assert s2.ingest_zapi_webhook(ok_payload, "qualquer") == (False, "fail_closed")
