"""Regressão da detecção de login por URL (calibração-independente).

Pega o bug do falso-positivo: deslogado, o SPA carrega no host da plataforma
antes de redirecionar ao login — não pode ser interpretado como autenticado só
por estar (momentaneamente) no host da plataforma.
"""

from seguros.connectors.mag.selectors import SelectorConfig
from seguros.connectors.mag.session import MagSession

PLAT = "https://plataformadosprodutores.mag.com.br/s/inadimplencias?orderBy=x"
LOGIN = "https://identidade.mag.com.br/Account/Login?ReturnUrl=%2Fconnect"


def _session(make_config, tmp_path):
    cfg = make_config(tmp_path, mag_inadimplencias_url=PLAT)
    return MagSession(cfg, SelectorConfig())


def test_platform_host_extraido(make_config, tmp_path):
    s = _session(make_config, tmp_path)
    assert s._platform_host == "plataformadosprodutores.mag.com.br"


def test_host_de_login_detectado(make_config, tmp_path):
    s = _session(make_config, tmp_path)
    assert s._host_is_login(LOGIN) is True
    assert s._host_is_login(PLAT) is False


def test_authenticated_by_url(make_config, tmp_path):
    s = _session(make_config, tmp_path)
    # na plataforma (sem ser host de login) => autenticado
    assert s._authenticated_by_url("https://plataformadosprodutores.mag.com.br/s/home") is True
    # no host de identidade => NÃO autenticado (mesmo que a navegação tenha vindo da plataforma)
    assert s._authenticated_by_url(LOGIN) is False
