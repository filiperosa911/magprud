import pytest

from seguros.config import ConfigError, load_config

# Caminho inexistente: evita que load_dotenv carregue o .env real do projeto.
_NOENV = "tests/_sem_env_.env"

_BASE_ENV = {
    "ZAPI_INSTANCE_ID": "i",
    "ZAPI_TOKEN": "t",
    "ZAPI_CLIENT_TOKEN": "c",
    "GMAIL_ADDRESS": "a@gmail.com",
    "GMAIL_APP_PASSWORD": "x" * 16,
    "NOME_CORRETOR": "Kike",
    "NOME_CORRETORA": "Aurex",
}


def _set(monkeypatch, **extra):
    for k in list(_BASE_ENV) + ["TIMEZONE", "PAYMENT_LINK_TTL_DAYS", "HORARIO_INICIO",
                                "HORARIO_FIM", "PACING_MIN_S", "PACING_MAX_S"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in {**_BASE_ENV, **extra}.items():
        monkeypatch.setenv(k, v)


def test_config_valida_carrega(monkeypatch):
    _set(monkeypatch)
    cfg = load_config(live=True, env_path=_NOENV)
    assert cfg.nome_corretor == "Kike"
    assert cfg.timezone == "America/Sao_Paulo"


def test_timezone_invalido_falha(monkeypatch):
    _set(monkeypatch, TIMEZONE="Marte/Olympus")
    with pytest.raises(ConfigError) as e:
        load_config(live=False, env_path=_NOENV)
    assert "TIMEZONE" in str(e.value)


def test_ttl_invalido_falha_amigavel(monkeypatch):
    _set(monkeypatch, PAYMENT_LINK_TTL_DAYS="abc")
    with pytest.raises(ConfigError) as e:
        load_config(live=False, env_path=_NOENV)
    assert "PAYMENT_LINK_TTL_DAYS" in str(e.value)


def test_live_exige_credenciais(monkeypatch):
    for k in _BASE_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    with pytest.raises(ConfigError) as e:
        load_config(live=True, env_path=_NOENV)
    assert "ZAPI_INSTANCE_ID" in str(e.value)


def test_dry_run_nao_exige_credenciais(monkeypatch):
    for k in _BASE_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    cfg = load_config(live=False, env_path=_NOENV)  # nao deve levantar
    assert cfg.live is False

