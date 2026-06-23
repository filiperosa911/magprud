"""FastAPI do dashboard. Endpoints ``def`` (threadpool) — o service serializa as
ações do portal no worker. Erros de negócio viram HTTP 400 com mensagem amigável.

Multi-seguradora: um único processo hospeda um :class:`DashboardService` por
seguradora (MAG / Prudential), criado sob demanda. A pessoa escolhe a seguradora
na tela de login; o token de sessão guarda qual seguradora ela acessou e cada
requisição é roteada para o service correspondente. A MAG mantém o escopo de
dados atual (``corretor_id``); as demais ficam isoladas em ``corretor_id:insurer``
no mesmo banco — sem migração de schema.

Auth PROVISÓRIA: cookie de sessão em memória. Senha opcional (DASHBOARD_PASSWORD);
vazia = provisório (qualquer senha entra). Tokens zeram a cada restart do servidor.
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import config_for_insurer
from ..connectors.base import ConnectorError
from ..connectors.factory import INSURER_LABELS, SUPPORTED_INSURERS
from .service import DashboardError, DashboardService
from .webhook import register_webhook

log = logging.getLogger("seguros.dashboard.app")
_STATIC = Path(__file__).parent / "static"
_COOKIE = "mag_session"


class TestNumberBody(BaseModel):
    numero: str | None = None


class LoginBody(BaseModel):
    senha: str | None = None
    insurer: str | None = None


class SimularInboundBody(BaseModel):
    cpf: str
    texto: str
    message_id: str | None = None


def create_app(config) -> FastAPI:
    app = FastAPI(title="Régua de Cobrança — Dashboard")

    # Um DashboardService por seguradora (lazy). MAG usa a config original; as
    # demais ganham escopo de dados e pasta de sessão próprios.
    services: dict[str, DashboardService] = {}
    services_lock = threading.Lock()
    sessions: dict[str, str] = {}  # token -> insurer

    def _get_service(insurer: str) -> DashboardService:
        with services_lock:
            svc = services.get(insurer)
            if svc is None:
                svc = DashboardService(config_for_insurer(config, insurer))
                services[insurer] = svc
            return svc

    def _authed(token: str | None) -> bool:
        return bool(token) and token in sessions

    def _require(token: str | None):
        if not _authed(token):
            raise HTTPException(status_code=401, detail="não autenticado")

    def _svc(token: str | None) -> DashboardService:
        return _get_service(sessions[token])

    def _guard(fn):
        try:
            return fn()
        except DashboardError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except ConnectorError as err:  # ex.: Prudential em calibração / sessão caiu
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001
            log.exception("erro no endpoint: %s", err)
            raise HTTPException(status_code=500, detail=str(err)) from err

    # --- auth ---------------------------------------------------------------

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        insurer = (body.insurer or "mag").strip().lower() or "mag"
        if insurer not in SUPPORTED_INSURERS:
            raise HTTPException(status_code=400, detail="Seguradora inválida.")
        senha_ok = (not config.dashboard_password) or (body.senha == config.dashboard_password)
        if not senha_ok:
            raise HTTPException(status_code=401, detail="Senha incorreta.")
        _get_service(insurer)  # cria/aquece o service da seguradora escolhida
        token = secrets.token_urlsafe(24)
        sessions[token] = insurer
        response.set_cookie(_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
        return {"ok": True, "insurer": insurer}

    @app.post("/api/logout")
    def logout(response: Response, mag_session: str | None = Cookie(default=None)):
        sessions.pop(mag_session or "", None)
        response.delete_cookie(_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        insurer = sessions[mag_session]
        return {
            "insurer": insurer,
            "label": INSURER_LABELS.get(insurer, insurer.upper()),
            "nome_corretor": config.nome_corretor,
            "nome_corretora": config.nome_corretora,
        }

    # --- dados / ações (protegidos) -----------------------------------------

    @app.get("/api/metrics")
    def metrics(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(_svc(mag_session).metrics)

    @app.get("/api/clientes")
    def clientes(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(_svc(mag_session).list_clientes)

    @app.get("/api/log")
    def log_recente(limit: int = 50, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).log_recente(limit))

    @app.post("/api/discover")
    def discover(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(_svc(mag_session).discover)

    @app.get("/api/health/selectors")
    def health_selectors(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(_svc(mag_session).health_selectors)

    @app.post("/api/reconciliar")
    def reconciliar(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(_svc(mag_session).reconciliar)

    # --- agente inbound ------------------------------------------------------

    @app.post("/api/inbound/simular")
    def inbound_simular(body: SimularInboundBody, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).processar_inbound(
            cpf=body.cpf, texto=body.texto, message_id=body.message_id,
            telefone=None, origem="simulacao",
        ))

    @app.get("/api/inbound")
    def inbound_list(limit: int = 50, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: {"mensagens": _svc(mag_session).list_inbound(limit)})

    @app.get("/api/reschedules")
    def reschedules_list(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: {"pedidos": _svc(mag_session).list_reschedules()})

    @app.post("/api/reschedules/{rid}/regenerar-link")
    def reschedule_regenerar(rid: int, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).regenerar_link_reschedule(rid))

    @app.post("/api/clientes/{cpf}/gerar-link")
    def gerar_link(cpf: str, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).gerar_link(cpf))

    @app.post("/api/clientes/{cpf}/disparar")
    def disparar(cpf: str, forcar: bool = False, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).disparar(cpf, forcar=forcar))

    @app.post("/api/clientes/{cpf}/follow-up")
    def follow_up(cpf: str, forcar: bool = False, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).follow_up(cpf, forcar=forcar))

    @app.post("/api/clientes/{cpf}/optout")
    def optout(cpf: str, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).add_optout(cpf))

    @app.post("/api/config/test-number")
    def test_number(body: TestNumberBody, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: _svc(mag_session).set_test_number(body.numero))

    # --- páginas ------------------------------------------------------------

    @app.get("/login")
    def login_page():
        return FileResponse(_STATIC / "login.html")

    @app.get("/")
    def index(mag_session: str | None = Cookie(default=None)):
        if not _authed(mag_session):
            return RedirectResponse("/login")
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    # Webhook público (auth própria por segredo, sem cookie) — o inbound do Z-API
    # é da MAG (única seguradora viva), então registra-se com o service da MAG.
    register_webhook(app, _get_service("mag"))

    @app.on_event("shutdown")
    def _shutdown():
        for svc in list(services.values()):
            svc.shutdown()

    return app


__all__ = ["create_app"]
