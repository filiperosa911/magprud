"""Regressão do mapeamento de status MAG (o acento de 'Não' quebrava o match,
e o substring 'trabalhado' capturava 'Não Trabalhado' como TRABALHADO)."""

from seguros.connectors.base import WorkStatus
from seguros.connectors.mag.connector import _map_work_status


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
