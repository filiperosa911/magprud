from seguros.report import ReportRow, RunReport


def _row(cpf="1", canal="whatsapp", decisao="dry_run"):
    return ReportRow(
        cpf=cpf, nome="Maria", primeiro_nome="Maria", canal=canal, dia_regua=0,
        decisao=decisao, destino="5511999998888", competencia="04/2026",
        valor_total="R$ 259,90", link_pagamento="https://pag/x",
        mensagem_renderizada="Olá, Maria...",
    )


def test_csv_tem_cabecalho_e_mensagem(tmp_path):
    rep = RunReport(live=False)
    rep.add(_row())
    path = rep.write_csv(tmp_path / "reports")
    assert path.exists()
    content = path.read_text(encoding="utf-8-sig")
    assert "mensagem_renderizada" in content
    assert "Olá, Maria..." in content


def test_console_summary_conta_por_canal():
    rep = RunReport(live=False)
    rep.add(_row(cpf="1", decisao="dry_run"))
    rep.add(_row(cpf="2", decisao="pulado_consentimento"))
    rep.add(_row(cpf="3", canal="email", decisao="dry_run"))
    s = rep.console_summary()
    assert "DRY-RUN" in s
    assert "whatsapp" in s
    assert "email" in s
