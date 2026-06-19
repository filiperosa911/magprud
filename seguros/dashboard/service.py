"""Camada de serviço do dashboard: métricas, pipeline e ações.

Lê/escreve o ``regua.sqlite`` (uma conexão por operação — thread-safe), serializa
ações da MAG pelo :class:`ConnectorWorker` (Playwright) e dispara WhatsApp via Z-API.

Pipeline por cliente (derivado dos campos existentes):
  descoberto -> link gerado (HOLD) -> disparado -> resolvido
"""

from __future__ import annotations

import logging
from collections import Counter

from ..clock import iso_utc, now_in
from ..cpf import format_cpf, normalize_cpf
from ..db.connection import get_conn, init_db
from ..db.repository import LogRepository, OptOutRepository, ReguaRepository
from ..domain.models import Canal, ClienteRegua, Modo, ReguaStatus
from ..messaging.phone import canonical_brazilian_phone, is_valid_whatsapp
from ..messaging.templates import WHATSAPP_DIA0, brl_from_cents, primeiro_nome, render
from .worker import ConnectorWorker

log = logging.getLogger("seguros.dashboard.service")


def _stage(c: ClienteRegua) -> str:
    if c.status is ReguaStatus.RESOLVIDO:
        return "resolvido"
    if c.status is ReguaStatus.OPT_OUT:
        return "opt_out"
    if c.whatsapp_enviado_em:
        return "disparado"
    if c.link_pagamento:
        return "hold"
    # Já trabalhado na MAG (link gerado por fora) — populado, sem ação pendente.
    if (c.work_status or "").lower() == "trabalhado":
        return "trabalhado_mag"
    return "descoberto"


class DashboardError(Exception):
    pass


class DashboardService:
    def __init__(self, config) -> None:
        self.config = config
        self.corretor_id = config.corretor_id
        init_db(config.db_path)
        self.worker = ConnectorWorker(config)
        self.test_number = config.whatsapp_override_to  # número de teste (mutável)
        self._zapi = None

    # --- infra ---------------------------------------------------------------

    def _conn(self):
        return get_conn(self.config.db_path)

    def _zapi_sender(self):
        if self._zapi is None:
            from ..messaging.whatsapp import ZApiClient, ZApiSender

            if not (self.config.zapi_instance_id and self.config.zapi_token):
                raise DashboardError("Z-API não configurado (.env).")
            client = ZApiClient(self.config.zapi_instance_id, self.config.zapi_token,
                                self.config.zapi_client_token)
            self._zapi = ZApiSender(client, pacing_min_s=0, pacing_max_s=0)
        return self._zapi

    def _destino(self, cliente: ClienteRegua) -> str | None:
        return (self.test_number or cliente.telefone) or None

    def _cliente_dict(self, c: ClienteRegua) -> dict:
        destino = self._destino(c)
        return {
            "cpf": c.cpf,
            "cpf_fmt": format_cpf(c.cpf),
            "nome": c.nome,
            "telefone": c.telefone,
            "destino": destino,
            "destino_teste": bool(self.test_number),
            "destino_valido": is_valid_whatsapp(destino),
            "email": c.email,
            "competencia": c.competencia,
            "valor": brl_from_cents(c.valor_inadimplente_cents),
            "valor_cents": c.valor_inadimplente_cents or 0,
            "work_status": c.work_status,
            "autoriza_whatsapp": c.autoriza_whatsapp,
            "autoriza_email": c.autoriza_email,
            "link_pagamento": c.link_pagamento,
            "whatsapp_enviado_em": c.whatsapp_enviado_em,
            "status": c.status.value,
            "stage": _stage(c),
            "enrolled_em": c.enrolled_em,
        }

    # --- leituras ------------------------------------------------------------

    def list_clientes(self) -> list[dict]:
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            return [self._cliente_dict(c) for c in repo.all_clientes()]
        finally:
            conn.close()

    def metrics(self) -> dict:
        clientes = self.list_clientes()
        stages = Counter(c["stage"] for c in clientes)
        valor_por_stage: Counter = Counter()
        for c in clientes:
            valor_por_stage[c["stage"]] += c["valor_cents"]
        ws_counts = Counter((c["work_status"] or "desconhecido") for c in clientes)
        total_cents = sum(c["valor_cents"] for c in clientes)
        recuperado = valor_por_stage.get("resolvido", 0)

        conn = self._conn()
        try:
            hoje = now_in(self.config.timezone).date().isoformat()
            cur = conn.execute(
                "SELECT resultado, COUNT(*) n FROM log_disparos WHERE corretor_id=? "
                "AND canal='whatsapp' GROUP BY resultado",
                (self.corretor_id,),
            )
            por_resultado = {r["resultado"]: r["n"] for r in cur.fetchall()}
            cur = conn.execute(
                "SELECT COUNT(*) n FROM log_disparos WHERE corretor_id=? AND canal='whatsapp' "
                "AND resultado='enviado' AND substr(data,1,10)=?",
                (self.corretor_id, hoje),
            )
            enviados_hoje = cur.fetchone()["n"]
        finally:
            conn.close()

        return {
            "total_clientes": len(clientes),
            "valor_total": brl_from_cents(total_cents),
            "valor_total_cents": total_cents,
            "valor_recuperado": brl_from_cents(recuperado),
            "valor_recuperado_cents": recuperado,
            "taxa_resolucao": round(100 * stages.get("resolvido", 0) / max(1, len(clientes)), 1),
            "enviados_hoje": enviados_hoje,
            "stages": {k: stages.get(k, 0) for k in
                       ("descoberto", "trabalhado_mag", "hold", "disparado", "resolvido", "opt_out")},
            "valor_por_stage": {k: valor_por_stage.get(k, 0) for k in
                                ("descoberto", "trabalhado_mag", "hold", "disparado", "resolvido")},
            "work_status": {
                "naoTrabalhado": ws_counts.get("naoTrabalhado", 0),
                "trabalhadoParcialmente": ws_counts.get("trabalhadoParcialmente", 0),
                "trabalhado": ws_counts.get("trabalhado", 0),
                "outro": ws_counts.get("desconhecido", 0) + ws_counts.get("unknown", 0),
            },
            "disparos_por_resultado": por_resultado,
            "test_number": self.test_number,
            "worker": self.worker.status(),
        }

    def log_recente(self, limit: int = 50) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT cpf, canal, resultado, link, payload_resumo, modo, data "
                "FROM log_disparos WHERE corretor_id=? ORDER BY id DESC LIMIT ?",
                (self.corretor_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # --- ações MAG (via worker) ---------------------------------------------

    def discover(self) -> dict:
        delinquents = self.worker.submit(lambda c: c.discover_delinquents())
        conn = self._conn()
        novos = 0
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            optout = OptOutRepository(conn, self.corretor_id)
            for d in delinquents:
                cpf = normalize_cpf(d.cpf)
                if len(cpf) != 11 or optout.is_opted_out(cpf=cpf):
                    continue
                ws = d.status.value if d.status else None
                if repo.exists(cpf):
                    repo.update_work_status(cpf, ws)  # mantém a base correta
                    continue
                repo.insert_enrollment(ClienteRegua(
                    cpf=cpf, corretor_id=self.corretor_id, nome=d.nome,
                    valor_inadimplente_cents=d.valor_total_cents, valor_texto=d.valor_texto,
                    vencimento_mais_antigo=d.vencimento_mais_antigo, competencia=d.competencia,
                    work_status=ws, enrolled_em=iso_utc(), status=ReguaStatus.EM_REGUA,
                ))
                novos += 1
        finally:
            conn.close()
        return {"descobertos": len(delinquents), "novos": novos}

    def gerar_link(self, cpf: str) -> dict:
        cpf = normalize_cpf(cpf)

        def _action(c):
            contact = c.fetch_contact(cpf)
            link_result = c.generate_payment_link(cpf, live=True)
            return contact, link_result

        contact, link_result = self.worker.submit(_action)
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            repo.update_contact(
                cpf, telefone=(contact.celular or contact.telefone), email=contact.email,
                autoriza_whatsapp=contact.autoriza_whatsapp, autoriza_email=contact.autoriza_email,
            )
            if link_result.link:
                repo.update_link(cpf, link_result.link)
            cliente = repo.get(cpf)
        finally:
            conn.close()
        if not (link_result and link_result.link):
            raise DashboardError("Cobrança feita, mas não consegui capturar o link.")
        return {"ok": True, "cliente": self._cliente_dict(cliente)}

    # --- disparo (Z-API) -----------------------------------------------------

    def disparar(self, cpf: str, *, forcar: bool = False) -> dict:
        cpf = normalize_cpf(cpf)
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            optout = OptOutRepository(conn, self.corretor_id)
            logr = LogRepository(conn, self.corretor_id)
            cliente = repo.get(cpf)
            if cliente is None:
                raise DashboardError("Cliente não encontrado.")
            if optout.is_opted_out(cpf=cpf):
                raise DashboardError("Cliente está em opt-out.")
            if not cliente.link_pagamento:
                raise DashboardError("Sem link. Gere o link (HOLD) antes de disparar.")
            if cliente.whatsapp_enviado_em and not forcar:
                raise DashboardError("Já disparado. Use 'forçar' para reenviar.")
            destino = self._destino(cliente)
            if not is_valid_whatsapp(destino):
                raise DashboardError(f"Destino inválido: {destino!r}")

            ctx = {
                "primeiro_nome": primeiro_nome(cliente.nome),
                "competencia": cliente.competencia or "—",
                "valor_total": brl_from_cents(cliente.valor_inadimplente_cents),
                "link_pagamento": cliente.link_pagamento,
                "nome_corretor": self.config.nome_corretor,
                "corretora": self.config.nome_corretora,
            }
            mensagem = render(WHATSAPP_DIA0, ctx)
            result = self._zapi_sender().send(destino, mensagem)
            nota = (f"[TESTE→{self.test_number}] cliente real: {cliente.telefone}"
                    if self.test_number else "")
            logr.record(cpf=cpf, canal=Canal.WHATSAPP, resultado=result.resultado, modo=Modo.LIVE,
                        link=cliente.link_pagamento,
                        payload_resumo=" ".join(x for x in (nota, result.detail or
                                                            (result.message_id or "")) if x))
            if result.sent:
                repo.mark_whatsapp_sent(cpf)
                cliente = repo.get(cpf)
                return {"ok": True, "destino": destino, "message_id": result.message_id,
                        "cliente": self._cliente_dict(cliente)}
            raise DashboardError(f"Falha no envio: {result.detail or result.resultado.value}")
        finally:
            conn.close()

    # --- opt-out / config ----------------------------------------------------

    def add_optout(self, cpf: str) -> dict:
        cpf = normalize_cpf(cpf)
        conn = self._conn()
        try:
            OptOutRepository(conn, self.corretor_id).add(cpf=cpf, origem="manual")
            ReguaRepository(conn, self.corretor_id).set_status(cpf, ReguaStatus.OPT_OUT)
        finally:
            conn.close()
        return {"ok": True, "cpf": cpf}

    def set_test_number(self, numero: str | None) -> dict:
        numero = (numero or "").strip()
        if numero and not is_valid_whatsapp(numero):
            raise DashboardError(f"Número de teste inválido: {numero!r}")
        self.test_number = canonical_brazilian_phone(numero) if numero else ""
        return {"ok": True, "test_number": self.test_number}

    def shutdown(self) -> None:
        self.worker.shutdown()


__all__ = ["DashboardService", "DashboardError"]
