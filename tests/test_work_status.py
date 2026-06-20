"""Regressão do mapeamento de status MAG (o acento de 'Não' quebrava o match,
e o substring 'trabalhado' capturava 'Não Trabalhado' como TRABALHADO)."""

from seguros.connectors.base import WorkStatus
from seguros.connectors.mag.connector import _map_work_status, _parse_valor_brl


def test_parse_valor_inadimplente():
    assert _parse_valor_brl("R$ 0,00 Valor inadimplente") == 0
    assert _parse_valor_brl("R$ 240,21 Valor inadimplente") == 24021
    assert _parse_valor_brl("R$\xa0240 , 21") == 24021  # com nbsp/espacos
    assert _parse_valor_brl("R$ 2.020,96") == 202096
    assert _parse_valor_brl("sem valor") is None


def test_nao_trabalhado_nao_vira_trabalhado():
    assert _map_work_status("Não Trabalhado") is WorkStatus.NAO_TRABALHADO
    assert _map_work_status("NÃO TRABALHADO") is WorkStatus.NAO_TRABALHADO


def test_parcial():
    assert _map_work_status("Trabalhado Parcialmente") is WorkStatus.TRABALHADO_PARCIALMENTE


def test_trabalhado():
    assert _map_work_status("Trabalhado") is WorkStatus.TRABALHADO


def test_desconhecido():
    assert _map_work_status("") is WorkStatus.UNKNOWN
    assert _map_work_status("Outro") is WorkStatus.UNKNOWN
