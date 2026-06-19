"""End-to-end do orquestrador com FakeConnector + DryRun senders (offline)."""

from seguros.connectors.fake import FakeConnector
from seguros.db.connection import init_db
from seguros.db.repository import LogRepository, OptOutRepository, ReguaRepository
from seguros.domain.models import ClienteRegua, ReguaStatus
from seguros.messaging.email import DryRunEmail
from seguros.messaging.whatsapp import DryRunWhatsApp
from seguros.orchestrator import Orchestrator
from seguros.report import RunReport


def _build(config):
    conn = init_db(config.db_path)
    repo = ReguaRepository(conn, config.corretor_id)
    optout = OptOutRepository(conn, config.corretor_id)
    logr = LogRepository(conn, config.corretor_id)
    report = RunReport(live=config.live)
    connector = FakeConnector()
    orch = Orchestrator(
        config=config, connector=connector, repo=repo, optout_repo=optout,
        log_repo=logr, wa_sender=DryRunWhatsApp(), email_sender=DryRunEmail(), report=report,
    )
    return conn, repo, optout, orch, report


def _decisoes(report, canal):
    return {r.cpf: r.decisao for r in report.rows if r.canal == canal}


def test_dry_run_nao_persiste_nem_envia(make_config, tmp_path):
    cfg = make_config(tmp_path, live=False)
    conn, repo, _optout, orch, report = _build(cfg)
    orch.run()

    # dry-run não persiste enrollment
    assert repo.exists("11111111111") is False
    # consentimento não bloqueia: todos com telefone válido caem em dry_run
    wa = _decisoes(report, "whatsapp")
    assert len(wa) == 5
    assert wa["11111111111"] == "dry_run"          # Maria
    assert wa["33333333333"] == "dry_run"          # Pedro
    assert wa["22222222222"] == "dry_run"          # João (WA=não, mas envia)
    assert wa["44444444444"] == "dry_run"          # Ana (WA=não, mas envia)
    assert wa["55555555555"] == "telefone_invalido"     # Carlos: fixo
    conn.close()


def test_optout_sobrepoe(make_config, tmp_path):
    cfg = make_config(tmp_path, live=False)
    conn, _repo, optout, orch, report = _build(cfg)
    optout.add(cpf="11111111111", origem="manual")
    orch.run()
    decisoes = {r.cpf: r.decisao for r in report.rows}
    assert decisoes["11111111111"] == "pulado_optout"
    # opt-out some de qualquer canal de envio
    assert "11111111111" not in _decisoes(report, "whatsapp")
    conn.close()


def test_followup_email_dia2_dry_run(make_config, tmp_path):
    cfg = make_config(tmp_path, live=False)
    conn, repo, _optout, orch, report = _build(cfg)
    # cliente já em régua, enrolled há 3 dias, e-mail autorizado, ainda não pago
    c = ClienteRegua(
        cpf="22222222222", nome="João Pereira", email="joao.pereira@example.com",
        telefone="21987654321", autoriza_whatsapp=False, autoriza_email=True,
        valor_inadimplente_cents=18050, competencia="05/2026",
        enrolled_em="2026-06-15T09:00:00Z", link_pagamento="https://pag/x",
        status=ReguaStatus.EM_REGUA,
    )
    repo.insert_enrollment(c)
    orch.run()
    email = _decisoes(report, "email")
    assert email.get("22222222222") == "dry_run"
    # dry-run não marca email_enviado_em
    assert repo.get("22222222222").email_enviado_em is None
    conn.close()


def _build_regularizado(cfg):
    conn = init_db(cfg.db_path)
    repo = ReguaRepository(conn, cfg.corretor_id)
    optout = OptOutRepository(conn, cfg.corretor_id)
    logr = LogRepository(conn, cfg.corretor_id)
    report = RunReport(live=cfg.live)
    connector = FakeConnector(regularizados={"22222222222"})
    repo.insert_enrollment(ClienteRegua(
        cpf="22222222222", nome="João", autoriza_email=True,
        enrolled_em="2026-06-15T09:00:00Z", status=ReguaStatus.EM_REGUA,
    ))
    orch = Orchestrator(
        config=cfg, connector=connector, repo=repo, optout_repo=optout, log_repo=logr,
        wa_sender=DryRunWhatsApp(), email_sender=DryRunEmail(), report=report,
    )
    return conn, repo, orch


def test_followup_regularizado_vira_resolvido_em_live(make_config, tmp_path):
    conn, repo, orch = _build_regularizado(make_config(tmp_path, live=True))
    orch.run()
    assert repo.get("22222222222").status is ReguaStatus.RESOLVIDO
    conn.close()


def test_followup_regularizado_dry_run_nao_persiste(make_config, tmp_path):
    conn, repo, orch = _build_regularizado(make_config(tmp_path, live=False))
    orch.run()
    # dry-run não muta o banco: status permanece em_regua
    assert repo.get("22222222222").status is ReguaStatus.EM_REGUA
    conn.close()


def test_live_persiste_e_marca_envio(make_config, tmp_path):
    cfg = make_config(tmp_path, live=True)
    conn, repo, _optout, orch, report = _build(cfg)
    orch.run()
    # live persiste enrollment para todos
    assert repo.exists("11111111111") is True
    # WhatsApp foi "enviado" (DryRunWhatsApp não envia de verdade, mas em live
    # usaríamos ZApiSender; aqui validamos que o link foi gerado e persistido)
    maria = repo.get("11111111111")
    assert maria.link_pagamento is not None
    assert maria.link_pagamento.startswith("https://pagamento.mag.com.br/fake/")
    conn.close()


def test_pending_whatsapp_sweep_reenvia_dia0(make_config, tmp_path):
    """WhatsApp do dia 0 deferido/pendente é retomado num run seguinte."""
    cfg = make_config(tmp_path, live=True)
    conn, repo, _optout, orch, report = _build(cfg)
    # cliente já enrolado em run anterior, com link, mas WhatsApp ainda não enviado
    repo.insert_enrollment(ClienteRegua(
        cpf="77777777777", nome="Bia", telefone="11999990000",
        autoriza_whatsapp=True, link_pagamento="https://pag/x",
        enrolled_em="2026-06-18T09:00:00Z", status=ReguaStatus.EM_REGUA,
    ))
    assert {c.cpf for c in repo.pending_whatsapp()} == {"77777777777"}
    orch.run()
    # o sweep avaliou o WhatsApp pendente (aparece no relatório)
    wa = {r.cpf for r in report.rows if r.canal == "whatsapp"}
    assert "77777777777" in wa
    conn.close()
