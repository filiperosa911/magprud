import pytest

from seguros.clock import iso_utc
from seguros.db.connection import init_db
from seguros.db.repository import LogRepository, OptOutRepository, ReguaRepository
from seguros.domain.models import Canal, ClienteRegua, Modo, ReguaStatus, Resultado


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.sqlite")
    yield c
    c.close()


def _cliente(cpf="11111111111"):
    return ClienteRegua(cpf=cpf, nome="Maria", enrolled_em=iso_utc(),
                        autoriza_whatsapp=True, autoriza_email=True,
                        valor_inadimplente_cents=25990, competencia="04/2026")


def test_insert_e_get(conn):
    repo = ReguaRepository(conn, "local")
    assert repo.exists("11111111111") is False
    repo.insert_enrollment(_cliente())
    assert repo.exists("11111111111") is True
    c = repo.get("11111111111")
    assert c.nome == "Maria"
    assert c.autoriza_whatsapp is True
    assert c.status is ReguaStatus.EM_REGUA


def test_mark_idempotencia_timestamps(conn):
    repo = ReguaRepository(conn, "local")
    repo.insert_enrollment(_cliente())
    assert repo.get("11111111111").whatsapp_enviado_em is None
    repo.mark_whatsapp_sent("11111111111")
    assert repo.get("11111111111").whatsapp_enviado_em is not None


def test_due_for_followup_respeita_offset(conn):
    repo = ReguaRepository(conn, "local")
    # enrolled há 3 dias -> elegível com offset 2
    antigo = _cliente("22222222222")
    antigo.enrolled_em = "2026-06-15T09:00:00Z"
    repo.insert_enrollment(antigo)
    # enrolled hoje -> não elegível
    repo.insert_enrollment(_cliente("33333333333"))

    import datetime as dt
    ref = dt.datetime(2026, 6, 18, 12, 0, tzinfo=dt.timezone.utc)
    elegiveis = {c.cpf for c in repo.due_for_followup(2, reference=ref)}
    assert "22222222222" in elegiveis
    assert "33333333333" not in elegiveis


def test_optout(conn):
    o = OptOutRepository(conn, "local")
    assert o.is_opted_out(cpf="99999999999") is False
    o.add(cpf="99999999999", origem="manual")
    assert o.is_opted_out(cpf="99999999999") is True


def test_log_record(conn):
    log = LogRepository(conn, "local")
    log.record(cpf="11111111111", canal=Canal.WHATSAPP, resultado=Resultado.DRY_RUN,
               modo=Modo.DRY_RUN, link="http://x", payload_resumo="ok")
    cur = conn.execute("SELECT COUNT(*) AS n FROM log_disparos")
    assert cur.fetchone()["n"] == 1


def test_mark_resolved_atribuido(conn):
    repo = ReguaRepository(conn, "local")
    repo.insert_enrollment(_cliente())  # valor 25990
    repo.mark_whatsapp_sent("11111111111")  # seta primeiro_disparo_em
    r = repo.mark_resolved("11111111111")
    assert r["first_time"] is True
    assert r["atribuida"] is True
    assert r["tempo_horas"] is not None and r["tempo_horas"] >= 0
    c = repo.get("11111111111")
    assert c.status is ReguaStatus.RESOLVIDO
    assert c.resolvido_em is not None
    assert c.conversao_atribuida is True
    assert c.valor_recuperado_cents == 25990
    # idempotente
    assert repo.mark_resolved("11111111111")["first_time"] is False


def test_mark_resolved_sem_disparo_nao_atribui(conn):
    repo = ReguaRepository(conn, "local")
    repo.insert_enrollment(_cliente("22222222222"))
    r = repo.mark_resolved("22222222222")  # sem disparo
    assert r["first_time"] is True
    assert r["atribuida"] is False
    assert r["tempo_horas"] is None


def test_mark_follow_up_sent(conn):
    repo = ReguaRepository(conn, "local")
    repo.insert_enrollment(_cliente("44444444444"))
    assert repo.get("44444444444").follow_up_enviado_em is None
    repo.mark_follow_up_sent("44444444444")
    assert repo.get("44444444444").follow_up_enviado_em is not None


def test_due_for_recheck(conn):
    repo = ReguaRepository(conn, "local")
    repo.insert_enrollment(_cliente("33333333333"))
    # sem disparo -> não entra na fila
    assert repo.due_for_recheck() == []
    repo.mark_whatsapp_sent("33333333333")
    fila = {c.cpf for c in repo.due_for_recheck()}
    assert "33333333333" in fila
    repo.touch_check("33333333333")
    # após o check, sai da fila (dentro de min_hours)
    assert "33333333333" not in {c.cpf for c in repo.due_for_recheck()}


def test_corretor_id_isola(conn):
    a = ReguaRepository(conn, "corretorA")
    b = ReguaRepository(conn, "corretorB")
    ca = _cliente("44444444444")
    ca.corretor_id = "corretorA"
    a.insert_enrollment(ca)
    assert a.exists("44444444444") is True
    assert b.exists("44444444444") is False
