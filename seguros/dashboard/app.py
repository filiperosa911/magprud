"""FastAPI do dashboard. Endpoints ``def`` (threadpool) — o service serializa as
ações da MAG no worker. Erros de negócio viram HTTP 400 com mensagem amigável.

Auth PROVISÓRIA: cookie de sessão em memória. Senha opcional (DASHBOARD_PASSWORD);
vazia = provisório (qualquer senha entra). Tokens zeram a cada restart do servidor.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .service import DashboardError, DashboardService
from .webhook import register_webhook

log = logging.getLogger("seguros.dashboard.app")
_STATIC = Path(__file__).parent / "static"
_COOKIE = "mag_session"


class TestNumberBody(BaseModel):
    numero: str | None = None


class LoginBody(BaseModel):
    senha: str | None = None


class SimularInboundBody(BaseModel):
    cpf: str
    texto: str
    message_id: str | None = None


def create_app(config) -> FastAPI:
    app = FastAPI(title="Régua MAG — Dashboard")
    service = DashboardService(config)
    sessions: set[str] = set()

    def _authed(token: str | None) -> bool:
        return bool(token) and token in sessions

    def _require(token: str | None):
        if not _authed(token):
            raise HTTPException(status_code=401, detail="não autenticado")

    def _guard(fn):
        try:
            return fn()
        except DashboardError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001
            log.exception("erro no endpoint: %s", err)
            raise HTTPException(status_code=500, detail=str(err)) from err

    # --- auth ---------------------------------------------------------------

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        senha_ok = (not config.dashboard_password) or (body.senha == config.dashboard_password)
        if not senha_ok:
            raise HTTPException(status_code=401, detail="Senha incorreta.")
        token = secrets.token_urlsafe(24)
        sessions.add(token)
        response.set_cookie(_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response, mag_session: str | None = Cookie(default=None)):
        sessions.discard(mag_session or "")
        response.delete_cookie(_COOKIE)
        return {"ok": True}

    # --- dados / ações (protegidos) -----------------------------------------

    @app.get("/api/metrics")
    def metrics(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(service.metrics)

    @app.get("/api/clientes")
    def clientes(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(service.list_clientes)

    @app.get("/api/log")
    def log_recente(limit: int = 50, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.log_recente(limit))

    @app.post("/api/discover")
    def discover(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(service.discover)

    @app.get("/api/health/selectors")
    def health_selectors(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(service.health_selectors)

    @app.post("/api/reconciliar")
    def reconciliar(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(service.reconciliar)

    # --- agente inbound ------------------------------------------------------

    @app.post("/api/inbound/simular")
    def inbound_simular(body: SimularInboundBody, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.processar_inbound(
            cpf=body.cpf, texto=body.texto, message_id=body.message_id,
            telefone=None, origem="simulacao",
        ))

    @app.get("/api/inbound")
    def inbound_list(limit: int = 50, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: {"mensagens": service.list_inbound(limit)})

    @app.get("/api/reschedules")
    def reschedules_list(mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: {"pedidos": service.list_reschedules()})

    @app.post("/api/reschedules/{rid}/regenerar-link")
    def reschedule_regenerar(rid: int, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.regenerar_link_reschedule(rid))

    @app.post("/api/clientes/{cpf}/gerar-link")
    def gerar_link(cpf: str, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.gerar_link(cpf))

    @app.post("/api/clientes/{cpf}/disparar")
    def disparar(cpf: str, forcar: bool = False, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.disparar(cpf, forcar=forcar))

    @app.post("/api/clientes/{cpf}/follow-up")
    def follow_up(cpf: str, forcar: bool = False, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.follow_up(cpf, forcar=forcar))

    @app.post("/api/clientes/{cpf}/optout")
    def optout(cpf: str, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.add_optout(cpf))

    @app.post("/api/config/test-number")
    def test_number(body: TestNumberBody, mag_session: str | None = Cookie(default=None)):
        _require(mag_session)
        return _guard(lambda: service.set_test_number(body.numero))

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

    # Webhook público (auth própria por segredo, sem cookie) — registrado por último.
    register_webhook(app, service)

    @app.on_event("shutdown")
    def _shutdown():
        service.shutdown()

    return app


__all__ = ["create_app"]
