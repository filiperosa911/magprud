"""Webhook público do Z-API (callback de mensagem recebida).

Registrado no MESMO app FastAPI, mas SEM a auth por cookie — a autenticação é
própria (no service): segredo no PATH + fail-closed + validação de instância.
Responde 200 rápido e processa em BackgroundTasks (ações MAG/Z-API são lentas e
seriais pelo worker; segurar a request convidaria retry/timeout do Z-API).

NUNCA logar o segredo nem a URL completa.
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException

from .service import DashboardService, _texto_zapi

log = logging.getLogger("seguros.dashboard.webhook")


def parse_zapi_payload(payload: dict) -> dict:
    """Extrai os campos que o agente usa do ReceivedCallback do Z-API."""
    return {
        "message_id": payload.get("messageId") or payload.get("messageid"),
        "telefone": (str(payload.get("phone") or "").strip() or None),
        "sender_name": payload.get("senderName") or payload.get("chatName"),
        "texto": _texto_zapi(payload),
    }


def register_webhook(app: FastAPI, service: DashboardService) -> None:
    @app.get("/webhook/zapi/health")
    def zapi_health():  # probe — não processa nada, não exige segredo
        return {"ok": True}

    @app.post("/webhook/zapi/{secret}")
    def zapi_inbound(secret: str, payload: dict, background: BackgroundTasks):
        aceito, motivo = service.ingest_zapi_webhook(payload, secret)
        if not aceito:
            log.info("webhook recusado: %s (instance=%s, type=%s)",
                     motivo, payload.get("instanceId"), payload.get("type"))
            if motivo in {"fail_closed", "segredo_invalido"}:
                raise HTTPException(status_code=401, detail="unauthorized")
            return {"ok": True, "ignored": motivo}  # filtro normal: 200 p/ não gerar retry
        background.add_task(service.processar_inbound_async,
                            origem="webhook", **parse_zapi_payload(payload))
        return {"ok": True, "dispatched": True}


__all__ = ["register_webhook", "parse_zapi_payload"]
