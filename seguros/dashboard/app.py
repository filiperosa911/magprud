"""FastAPI do dashboard. Endpoints ``def`` (threadpool) — o service serializa as
ações da MAG no worker. Erros de negócio viram HTTP 400 com mensagem amigável.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .service import DashboardError, DashboardService

log = logging.getLogger("seguros.dashboard.app")
_STATIC = Path(__file__).parent / "static"


class TestNumberBody(BaseModel):
    numero: str | None = None


def create_app(config) -> FastAPI:
    app = FastAPI(title="Régua MAG — Dashboard")
    service = DashboardService(config)

    def _guard(fn):
        try:
            return fn()
        except DashboardError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001
            log.exception("erro no endpoint: %s", err)
            raise HTTPException(status_code=500, detail=str(err)) from err

    @app.get("/api/metrics")
    def metrics():
        return _guard(service.metrics)

    @app.get("/api/clientes")
    def clientes():
        return _guard(service.list_clientes)

    @app.get("/api/log")
    def log_recente(limit: int = 50):
        return _guard(lambda: service.log_recente(limit))

    @app.post("/api/discover")
    def discover():
        return _guard(service.discover)

    @app.post("/api/clientes/{cpf}/gerar-link")
    def gerar_link(cpf: str):
        return _guard(lambda: service.gerar_link(cpf))

    @app.post("/api/clientes/{cpf}/disparar")
    def disparar(cpf: str, forcar: bool = False):
        return _guard(lambda: service.disparar(cpf, forcar=forcar))

    @app.post("/api/clientes/{cpf}/optout")
    def optout(cpf: str):
        return _guard(lambda: service.add_optout(cpf))

    @app.post("/api/config/test-number")
    def test_number(body: TestNumberBody):
        return _guard(lambda: service.set_test_number(body.numero))

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.on_event("shutdown")
    def _shutdown():
        service.shutdown()

    return app


__all__ = ["create_app"]
