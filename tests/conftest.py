from datetime import time
from pathlib import Path

import pytest

from seguros.config import Config


@pytest.fixture
def make_config():
    def _make(tmp_path: Path, *, live: bool = False, **overrides) -> Config:
        base = dict(
            live=live,
            corretor_id="local",
            nome_corretor="Kike",
            nome_corretora="Aurex Seguros",
            db_path=tmp_path / "regua.sqlite",
            user_data_dir=tmp_path / ".mag_session",
            mag_login_url="https://x/login",
            mag_inadimplencias_url="https://x/inadimplencias",
            mag_clientes_url="https://x/clientes",
            zapi_instance_id="",
            zapi_token="",
            zapi_client_token="",
            notify_whatsapp_to="",
            whatsapp_override_to="",
            gmail_address="",
            gmail_app_password="",
            timezone="America/Sao_Paulo",
            horario_inicio=time(0, 0),
            horario_fim=time(23, 59),
            dias_uteis_apenas=False,  # janela sempre aberta nos testes
            followup_offset_days=2,
            max_whatsapp_por_dia=70,
            pacing_min_s=0,
            pacing_max_s=0,
            max_sends_per_run=200,
            max_falhas_consecutivas=5,
            payment_link_ttl_days=None,
        )
        base.update(overrides)
        return Config(**base)

    return _make
