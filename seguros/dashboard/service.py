"""Camada de serviço do dashboard: métricas, pipeline e ações.

Lê/escreve o ``regua.sqlite`` (uma conexão por operação — thread-safe), serializa
ações da MAG pelo :class:`ConnectorWorker` (Playwright) e dispara WhatsApp via Z-API.

Pipeline por cliente (derivado dos campos existentes):
  descoberto -> link gerado (HOLD) -> disparado -> resolvido
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections import Counter

from ..clock import days_since, iso_utc, now_in
from ..connectors.base import NotAuthenticatedError, SessionExpiredError
from ..cpf import format_cpf, normalize_cpf
from ..db.connection import get_conn, init_db
from ..db.repository import (
    InboundRepository,
    LogRepository,
    OptOutRepository,
    RescheduleRepository,
    ReguaRepository,
    StatusCheckRepository,
)
from ..domain.models import Canal, ClienteRegua, Modo, ReguaStatus
from ..messaging.intents import (
    INTENT_JA_PAGUEI,
    INTENT_NOVO_LINK,
    INTENT_RESCHEDULE,
    INTENT_SAIR,
    INTENT_SAUDACAO,
    classificar,
)
from ..messaging.phone import canonical_brazilian_phone, is_valid_whatsapp
from ..messaging.templates import (
    NOTIFY_ADMIN_DUVIDA,
    NOTIFY_ADMIN_JA_PAGUEI_SEM_LEITURA,
    NOTIFY_ADMIN_RESCHEDULE,
    RESP_DUVIDA,
    RESP_JA_PAGUEI_CONFIRMADO,
    RESP_JA_PAGUEI_PENDENTE,
    RESP_JA_PAGUEI_VERIFICANDO,
    RESP_NOVO_LINK,
    RESP_NOVO_LINK_SEM_LINK,
    RESP_RESCHEDULE_OK,
    RESP_RESCHEDULE_SEM_DATA,
    RESP_SAIR,
    WHATSAPP_DIA0,
    WHATSAPP_FOLLOWUP,
    brl_from_cents,
    primeiro_nome,
    render,
)
from .worker import ConnectorWorker

log = logging.getLogger("seguros.dashboard.service")


def _canon(telefone: str | None) -> str | None:
    """Telefone canonical (55DDD9XXXXXXXX) ou None — p/ casar opt-out por número."""
    return canonical_brazilian_phone(telefone) if telefone else None


def _texto_zapi(payload: dict) -> str | None:
    """Extrai o texto de um ReceivedCallback do Z-API (text.message)."""
    txt = payload.get("text")
    if isinstance(txt, dict):
        msg = txt.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    # alguns formatos trazem 'message' direto
    msg = payload.get("message")
    return msg.strip() if isinstance(msg, str) and msg.strip() else None


def _fmt_data(iso: str | None) -> str:
    """ISO 'YYYY-MM-DD' -> 'DD/MM/YYYY' (ou '—' se vazio)."""
    if not iso:
        return "—"
    try:
        a, m, d = iso.split("-")
        return f"{d}/{m}/{a}"
    except ValueError:
        return iso


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
        # revisão matinal do DOM (health-check) — 1x/dia, só com a sessão de pé
        self._last_health: dict | None = None
        self._last_health_date: str | None = None
        self._health_lock = threading.Lock()
        self._health_stop = threading.Event()
        if config.healthcheck_auto:
            threading.Thread(target=self._healthcheck_loop, name="dom-healthcheck",
                             daemon=True).start()

    # --- infra ---------------------------------------------------------------

    def _conn(self):
        return get_conn(self.config.db_path)

    def _mag(self, fn, **kw):
        """Ação na MAG (via worker), traduzindo sessão expirada em msg amigável."""
        try:
            return self.worker.submit(fn, **kw)
        except (NotAuthenticatedError, SessionExpiredError) as err:
            raise DashboardError(
                "Sessão MAG expirada. Rode `python -m seguros --login` no terminal "
                "e tente de novo."
            ) from err

    def _zapi_sender(self):
        if self._zapi is None:
            from ..messaging.whatsapp import ZApiClient, ZApiSender

            if not (self.config.zapi_instance_id and self.config.zapi_token):
                raise DashboardError("Z-API não configurado (.env).")
            client = ZApiClient(self.config.zapi_instance_id, self.config.zapi_token,
                                self.config.zapi_client_token)
            # Anti-ban: pacing real entre disparos (protege o número que ENVIA).
            self._zapi = ZApiSender(client, pacing_min_s=self.config.pacing_min_s,
                                    pacing_max_s=self.config.pacing_max_s)
        return self._zapi

    def _enviados_hoje(self, conn) -> int:
        hoje = now_in(self.config.timezone).date().isoformat()
        cur = conn.execute(
            "SELECT COUNT(*) n FROM log_disparos WHERE corretor_id=? AND canal='whatsapp' "
            "AND resultado='enviado' AND substr(data,1,10)=?",
            (self.corretor_id, hoje),
        )
        return cur.fetchone()["n"]

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
            "followup_enviado": bool(c.follow_up_enviado_em),
            "followup_elegivel": (
                _stage(c) == "disparado" and not c.follow_up_enviado_em
                and c.whatsapp_enviado_em is not None
                and days_since(c.whatsapp_enviado_em) >= self.config.followup_offset_days
            ),
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
            # sucesso: disparados, convertidos (atribuídos), tempo médio, valor recuperado
            s = conn.execute(
                "SELECT COUNT(*) disp, COALESCE(SUM(conversao_atribuida),0) conv, "
                "AVG(tempo_ate_pagar_horas) tmed, "
                "COALESCE(SUM(CASE WHEN conversao_atribuida=1 THEN valor_recuperado_cents END),0) vrec "
                "FROM clientes_regua WHERE corretor_id=? AND primeiro_disparo_em IS NOT NULL",
                (self.corretor_id,),
            ).fetchone()
            disparados = s["disp"] or 0
            convertidos = s["conv"] or 0
            tempo_medio_h = round(s["tmed"], 1) if s["tmed"] is not None else None
            valor_recup_atrib = s["vrec"] or 0
            pagamentos_dia = StatusCheckRepository(conn, self.corretor_id).pagamentos_por_dia(30)
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
            "sucesso": {
                "disparados": disparados,
                "convertidos": convertidos,
                "taxa_conversao": round(100 * convertidos / disparados, 1) if disparados else 0.0,
                "tempo_medio_horas": tempo_medio_h,
                "valor_recuperado_atribuido": brl_from_cents(valor_recup_atrib),
                "pagamentos_por_dia": list(reversed(pagamentos_dia)),
            },
            "disparos_por_resultado": por_resultado,
            "test_number": self.test_number,
            "worker": self.worker.status(),
            "health": self._last_health,  # última revisão do DOM (None = ainda não rodou)
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
        self._maybe_daily_healthcheck()  # revisão matinal do DOM (1x/dia) ao começar o trabalho
        delinquents = self._mag(lambda c: c.discover_delinquents())
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

    def reconciliar(self, limite: int = 8) -> dict:
        """Detecta pagamentos lendo o "Valor inadimplente" na tela de cada cliente
        disparado (0 = pagou). Sinal confiável (não confunde 'cobrado' com 'pago').
        Processa um lote pequeno por chamada (cada cliente exige navegação)."""
        conn = self._conn()
        try:
            fila = ReguaRepository(conn, self.corretor_id).due_for_recheck(limit=limite)
        finally:
            conn.close()
        resolvidos = checados = sem_leitura = 0
        for cli in fila:
            cents = self._mag(
                lambda mag, cpf=cli.cpf: mag.check_client_inadimplente_cents(cpf)
            )
            conn = self._conn()
            try:
                repo = ReguaRepository(conn, self.corretor_id)
                checks = StatusCheckRepository(conn, self.corretor_id)
                repo.touch_check(cli.cpf)
                if cents is None:
                    sem_leitura += 1
                    continue
                checados += 1
                pago = cents == 0
                checks.record(cpf=cli.cpf, all_regularized=pago, transicao=pago, origem="reconcile")
                if pago and repo.mark_resolved(cli.cpf).get("first_time"):
                    resolvidos += 1
            finally:
                conn.close()
        return {"fila": len(fila), "checados": checados, "resolvidos": resolvidos,
                "sem_leitura": sem_leitura}

    def gerar_link(self, cpf: str) -> dict:
        cpf = normalize_cpf(cpf)

        def _action(c):
            contact = c.fetch_contact(cpf)
            link_result = c.generate_payment_link(cpf, live=True)
            return contact, link_result

        contact, link_result = self._mag(_action)
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
            if optout.is_opted_out(cpf=cpf, telefone=_canon(cliente.telefone)):
                raise DashboardError("Cliente está em opt-out.")
            if not cliente.link_pagamento:
                raise DashboardError("Sem link. Gere o link (HOLD) antes de disparar.")
            if cliente.whatsapp_enviado_em and not forcar:
                raise DashboardError("Já disparado. Use 'forçar' para reenviar.")
            destino = self._destino(cliente)
            if not is_valid_whatsapp(destino):
                raise DashboardError(f"Destino inválido: {destino!r}")
            # Anti-ban: teto diário de WhatsApp (protege o número que envia).
            if self._enviados_hoje(conn) >= self.config.max_whatsapp_por_dia:
                raise DashboardError(
                    f"Teto diário de WhatsApp atingido ({self.config.max_whatsapp_por_dia}). "
                    "Aumente MAX_WHATSAPP_POR_DIA no .env se necessário."
                )

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

    def follow_up(self, cpf: str, *, forcar: bool = False) -> dict:
        """Dia 2: 2º toque sutil. Antes de enviar, RE-CHECA o pagamento ao vivo —
        se já pagou, marca resolvido e NÃO incomoda."""
        cpf = normalize_cpf(cpf)
        # 1) valida o estado pela DB
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            cliente = repo.get(cpf)
            if cliente is None:
                raise DashboardError("Cliente não encontrado.")
            if OptOutRepository(conn, self.corretor_id).is_opted_out(cpf=cpf):
                raise DashboardError("Cliente está em opt-out.")
            if OptOutRepository(conn, self.corretor_id).is_opted_out(
                cpf=cpf, telefone=_canon(cliente.telefone)
            ):
                raise DashboardError("Cliente está em opt-out.")
            if not cliente.whatsapp_enviado_em:
                raise DashboardError("Faça o disparo (dia 0) antes do follow-up.")
            if cliente.status is ReguaStatus.RESOLVIDO:
                raise DashboardError("Cliente já está resolvido (pagou).")
            if cliente.follow_up_enviado_em and not forcar:
                raise DashboardError("Follow-up já enviado. Use 'forçar' para reenviar.")
            if not cliente.link_pagamento:
                raise DashboardError("Sem link de pagamento para reaproveitar.")
        finally:
            conn.close()

        # 2) re-checa pagamento ao vivo (não incomodar quem já pagou)
        cents = self._mag(lambda mag: mag.check_client_inadimplente_cents(cpf))

        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            logr = LogRepository(conn, self.corretor_id)
            checks = StatusCheckRepository(conn, self.corretor_id)
            if cents is not None:
                repo.touch_check(cpf)
            if cents == 0:  # pagou — marca resolvido e não envia
                checks.record(cpf=cpf, all_regularized=True, transicao=True, origem="followup")
                info = repo.mark_resolved(cpf)
                return {"ok": True, "pago": True, "enviado": False,
                        "cliente": self._cliente_dict(repo.get(cpf)),
                        "tempo_horas": info.get("tempo_horas")}
            # 3) ainda deve -> envia o follow-up sutil
            if self._enviados_hoje(conn) >= self.config.max_whatsapp_por_dia:
                raise DashboardError(
                    f"Teto diário de WhatsApp atingido ({self.config.max_whatsapp_por_dia})."
                )
            cliente = repo.get(cpf)
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
            mensagem = render(WHATSAPP_FOLLOWUP, ctx)
            result = self._zapi_sender().send(destino, mensagem)
            nota = (f"[FOLLOWUP][TESTE→{self.test_number}] cliente real: {cliente.telefone}"
                    if self.test_number else "[FOLLOWUP]")
            logr.record(cpf=cpf, canal=Canal.WHATSAPP, resultado=result.resultado, modo=Modo.LIVE,
                        link=cliente.link_pagamento,
                        payload_resumo=" ".join(x for x in (nota, result.detail or
                                                            (result.message_id or "")) if x))
            if result.sent:
                repo.mark_follow_up_sent(cpf)
                return {"ok": True, "pago": False, "enviado": True, "destino": destino,
                        "message_id": result.message_id,
                        "cliente": self._cliente_dict(repo.get(cpf))}
            raise DashboardError(f"Falha no envio: {result.detail or result.resultado.value}")
        finally:
            conn.close()

    # --- agente inbound (webhook Z-API) -------------------------------------

    def ingest_zapi_webhook(self, payload: dict, secret: str | None) -> tuple[bool, str]:
        """Autentica + filtra o callback do Z-API. FAIL-CLOSED: segredo vazio rejeita
        tudo. Retorna (aceito, motivo)."""
        cfg = self.config
        if not cfg.zapi_webhook_secret:
            return False, "fail_closed"  # segredo não configurado -> rejeita tudo
        if not secrets.compare_digest(str(secret or ""), cfg.zapi_webhook_secret):
            return False, "segredo_invalido"
        if not isinstance(payload, dict):
            return False, "payload_invalido"
        inst = str(payload.get("instanceId", "")) if payload.get("instanceId") else ""
        if cfg.zapi_instance_id and inst and inst != cfg.zapi_instance_id:
            return False, "instancia_divergente"
        if payload.get("fromMe"):
            return False, "from_me"  # anti-loop: nossa própria mensagem
        if payload.get("isGroup"):
            return False, "grupo"
        if not _texto_zapi(payload):
            return False, "sem_texto"
        return True, "ok"

    def processar_inbound_async(self, **kw) -> None:
        """Wrapper p/ BackgroundTasks: nunca propaga exceção (só loga)."""
        try:
            self.processar_inbound(**kw)
        except Exception:  # noqa: BLE001 - fronteira de background
            log.exception("erro ao processar inbound")

    def processar_inbound(self, *, texto: str, cpf: str | None = None,
                          telefone: str | None = None, message_id: str | None = None,
                          sender_name: str | None = None, origem: str = "webhook") -> dict:
        """Classifica -> resolve cliente -> persiste (gate atômico de idempotência)
        -> age. É o ponto único de entrada (webhook real e simulação)."""
        hoje = now_in(self.config.timezone).date()
        res = classificar(texto, hoje=hoje, max_dias=self.config.reschedule_max_dias)
        tel_canon = _canon(telefone)
        cpf_resolvido, motivo = self._resolver_cpf(
            cpf_hint=cpf, telefone_canonical=tel_canon, origem=origem
        )
        conn = self._conn()
        try:
            inbound_id, is_new = InboundRepository(conn, self.corretor_id).record(
                message_id=message_id, cpf=cpf_resolvido, telefone=tel_canon,
                telefone_raw=telefone, sender_name=sender_name, texto=texto,
                intent=res.intent, confianca=res.confianca, data_desejada=res.data_desejada,
                origem=origem, outcome="processando",
            )
        finally:
            conn.close()
        base = {"ok": True, "intent": res.intent, "inbound_id": inbound_id,
                "data_desejada": res.data_desejada}
        if not is_new:  # idempotência: message_id já processado
            return {**base, "outcome": "duplicado"}
        if cpf_resolvido is None:  # não age sobre cliente (sem_cpf / trava de teste / etc.)
            self._set_outcome(inbound_id, motivo)
            return {**base, "outcome": motivo}
        outcome = self._despachar_inbound(res, cpf_resolvido, texto, inbound_id)
        self._set_outcome(inbound_id, outcome, cpf=cpf_resolvido)
        return {**base, "outcome": outcome, "cpf": cpf_resolvido}

    def _resolver_cpf(self, *, cpf_hint, telefone_canonical, origem) -> tuple[str | None, str]:
        if cpf_hint:  # simulação por CPF explícito (contorna ambiguidade phone->cliente)
            return normalize_cpf(cpf_hint), "cpf_explicito"
        # must-fix #3b: com a trava de teste ativa, o webhook real NÃO age sobre
        # clientes por telefone (o inbound de teste chega do próprio número do corretor).
        if self.test_number:
            return None, "bloqueado_trava_teste"
        # must-fix #3c: nunca resolver números internos (admin/notify) como cliente.
        internos = {_canon(x) for x in (self.config.admin_whatsapp,
                                        self.config.notify_whatsapp_to) if x}
        if telefone_canonical and telefone_canonical in internos:
            return None, "numero_interno"
        conn = self._conn()
        try:
            cpf = ReguaRepository(conn, self.corretor_id).find_cpf_by_telefone(telefone_canonical)
        finally:
            conn.close()
        return (cpf, "telefone") if cpf else (None, "sem_cpf")

    def _despachar_inbound(self, res, cpf, texto, inbound_id) -> str:
        if res.intent == INTENT_SAIR:
            return self._agir_optout(cpf)
        if res.intent == INTENT_JA_PAGUEI:
            return self._agir_ja_paguei(cpf, texto)
        if res.intent == INTENT_RESCHEDULE:
            return self._agir_reschedule(cpf, texto, res.data_desejada, inbound_id)
        if res.intent == INTENT_NOVO_LINK:
            return self._agir_novo_link(cpf, texto)
        if res.intent == INTENT_SAUDACAO:
            return "saudacao"  # silêncio deliberado (anti-loop)
        return self._agir_duvida(cpf, texto)

    def _set_outcome(self, inbound_id, outcome, *, cpf=None) -> None:
        if inbound_id is None:
            return
        conn = self._conn()
        try:
            InboundRepository(conn, self.corretor_id).mark_outcome(inbound_id, outcome, cpf=cpf)
        finally:
            conn.close()

    def _agir_optout(self, cpf: str) -> str:
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            cliente = repo.get(cpf)
            tel = _canon(cliente.telefone) if cliente and cliente.telefone else None
            OptOutRepository(conn, self.corretor_id).add(
                cpf=cpf, telefone=tel, origem="sair_whatsapp"
            )
            repo.set_status(cpf, ReguaStatus.OPT_OUT)
            LogRepository(conn, self.corretor_id).record(
                cpf=cpf, canal=Canal.SISTEMA, resultado="opt_out_inbound", modo=Modo.LIVE
            )
        finally:
            conn.close()
        self._responder(cpf, RESP_SAIR, critico=True)  # confirmação de opt-out sempre sai
        return "opt_out"

    def _agir_ja_paguei(self, cpf: str, texto: str) -> str:
        cents = self._mag(lambda m: m.check_client_inadimplente_cents(cpf))  # NUNCA confia no texto
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            if cents is not None:
                repo.touch_check(cpf)
            if cents == 0:
                StatusCheckRepository(conn, self.corretor_id).record(
                    cpf=cpf, all_regularized=True, transicao=True, origem="inbound"
                )
                repo.mark_resolved(cpf)
                outcome = "pago"
            elif cents is None:
                outcome = "sem_leitura"
            else:
                outcome = "pendente"
            cliente = repo.get(cpf)
        finally:
            conn.close()
        if outcome == "pago":
            self._responder(cpf, RESP_JA_PAGUEI_CONFIRMADO, critico=True)
        elif outcome == "sem_leitura":
            self._responder(cpf, RESP_JA_PAGUEI_VERIFICANDO, critico=True)
            self._notificar_admin(NOTIFY_ADMIN_JA_PAGUEI_SEM_LEITURA, cliente, texto)
        else:
            self._responder(cpf, RESP_JA_PAGUEI_PENDENTE, critico=True)
        return outcome

    def _agir_reschedule(self, cpf, texto, data_desejada, inbound_id) -> str:
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            resched = RescheduleRepository(conn, self.corretor_id)
            cliente = repo.get(cpf)
            existente = resched.open_for_cpf(cpf)  # dedupe de pedidos repetidos
            if existente and existente["data_desejada"] == data_desejada:
                rid, novo = existente["id"], False
            else:
                rid = resched.create(cpf=cpf, data_desejada=data_desejada,
                                     texto_origem=texto, inbound_id=inbound_id)
                novo = True
        finally:
            conn.close()
        if novo:
            self._notificar_admin(NOTIFY_ADMIN_RESCHEDULE, cliente, texto,
                                  data_desejada=_fmt_data(data_desejada))
            conn = self._conn()
            try:
                RescheduleRepository(conn, self.corretor_id).mark_admin_notified(rid)
            finally:
                conn.close()
        if data_desejada:
            self._responder(cpf, RESP_RESCHEDULE_OK, extra={"data_desejada": _fmt_data(data_desejada)})
        else:
            self._responder(cpf, RESP_RESCHEDULE_SEM_DATA)
        return "reschedule"

    def _agir_novo_link(self, cpf: str, texto: str) -> str:
        # must-fix #1: NÃO re-clica Cobrar (cliente já saiu da lista) — reenvia o link salvo.
        conn = self._conn()
        try:
            cliente = ReguaRepository(conn, self.corretor_id).get(cpf)
        finally:
            conn.close()
        if cliente and cliente.link_pagamento:
            self._responder(cpf, RESP_NOVO_LINK, extra={"link_pagamento": cliente.link_pagamento})
            return "novo_link_reenviado"
        self._responder(cpf, RESP_NOVO_LINK_SEM_LINK)
        self._notificar_admin(NOTIFY_ADMIN_DUVIDA, cliente, texto)
        return "novo_link_sem_link"

    def _agir_duvida(self, cpf: str, texto: str) -> str:
        conn = self._conn()
        try:
            cliente = ReguaRepository(conn, self.corretor_id).get(cpf)
        finally:
            conn.close()
        self._responder(cpf, RESP_DUVIDA)
        self._notificar_admin(NOTIFY_ADMIN_DUVIDA, cliente, texto)
        return "duvida"

    def _ctx_cliente(self, cliente: ClienteRegua) -> dict:
        return {
            "primeiro_nome": primeiro_nome(cliente.nome),
            "nome": cliente.nome,
            "cpf_fmt": format_cpf(cliente.cpf),
            "competencia": cliente.competencia or "—",
            "valor_total": brl_from_cents(cliente.valor_inadimplente_cents),
            "link_pagamento": cliente.link_pagamento or "",
            "nome_corretor": self.config.nome_corretor,
            "corretora": self.config.nome_corretora,
        }

    def _responder(self, cpf: str, template, *, critico: bool = False, extra: dict | None = None) -> None:
        conn = self._conn()
        try:
            repo = ReguaRepository(conn, self.corretor_id)
            cliente = repo.get(cpf)
            if cliente is None:
                return
            destino = self._destino(cliente)  # respeita a trava de teste (override)
            if not is_valid_whatsapp(destino):
                return
            # must-fix #6: respostas críticas (opt-out/pagamento) furam o teto; as
            # não-críticas respeitam e, se estourar, escalam ao admin em vez de sumir.
            if not critico and self._enviados_hoje(conn) >= self.config.max_whatsapp_por_dia:
                self._notificar_admin_texto(
                    f"Teto diário atingido — resposta a {cliente.nome} "
                    f"({format_cpf(cpf)}) NÃO enviada."
                )
                return
            ctx = self._ctx_cliente(cliente)
            if extra:
                ctx.update(extra)
            mensagem = render(template, ctx)
            result = self._zapi_sender().send(destino, mensagem)
            LogRepository(conn, self.corretor_id).record(
                cpf=cpf, canal=Canal.WHATSAPP, modo=Modo.LIVE,
                resultado=("resp_enviada" if result.sent else "resp_erro"),
                payload_resumo=result.message_id or result.detail or "",
            )
        finally:
            conn.close()

    def _notificar_admin(self, template, cliente, texto: str, **extra) -> None:
        ctx = self._ctx_cliente(cliente) if cliente else {"nome": "?", "cpf_fmt": "?"}
        ctx["texto"] = texto
        ctx.update(extra)
        self._notificar_admin_texto(render(template, ctx))

    def _notificar_admin_texto(self, mensagem: str) -> None:
        destino = self.test_number or self.config.admin_whatsapp or self.config.notify_whatsapp_to
        destino = _canon(destino)
        if not is_valid_whatsapp(destino):
            log.warning("admin sem número válido; aviso não enviado")
            return
        try:
            self._zapi_sender().send(destino, mensagem)
        except Exception:  # noqa: BLE001 - aviso é best-effort
            log.warning("falha ao avisar admin", exc_info=True)

    def list_inbound(self, limit: int = 50) -> list[dict]:
        conn = self._conn()
        try:
            rows = InboundRepository(conn, self.corretor_id).recentes(limit)
        finally:
            conn.close()
        for r in rows:
            r["cpf_fmt"] = format_cpf(r["cpf"]) if r.get("cpf") else None
        return rows

    def list_reschedules(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = RescheduleRepository(conn, self.corretor_id).pendentes()
        finally:
            conn.close()
        for r in rows:
            r["cpf_fmt"] = format_cpf(r["cpf"]) if r.get("cpf") else None
        return rows

    def regenerar_link_reschedule(self, rid: int) -> dict:
        """Ação humana: reenvia ao cliente o link de pagamento salvo (must-fix #1:
        re-Cobrar não funciona p/ quem já saiu da lista) e marca o pedido."""
        conn = self._conn()
        try:
            ped = RescheduleRepository(conn, self.corretor_id).get(rid)
            cliente = ReguaRepository(conn, self.corretor_id).get(ped["cpf"]) if ped else None
        finally:
            conn.close()
        if ped is None:
            raise DashboardError("Pedido de remarcação não encontrado.")
        if cliente is None:
            raise DashboardError("Cliente não encontrado.")
        if not cliente.link_pagamento:
            raise DashboardError("Cliente sem link salvo. Gere o link (Cobrar) no pipeline antes.")
        self._responder(cliente.cpf, RESP_NOVO_LINK, extra={"link_pagamento": cliente.link_pagamento})
        conn = self._conn()
        try:
            RescheduleRepository(conn, self.corretor_id).mark_link_reenviado(rid, cliente.link_pagamento)
        finally:
            conn.close()
        return {"ok": True, "cpf": cliente.cpf, "link": cliente.link_pagamento}

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

    # Seletores que DEVEM resolver SEMPRE na tela de lista (se quebrarem, a
    # descoberta quebra). Os de detalhe/modal/login só aparecem em telas específicas.
    _CRITICOS = {"auth.logged_in_marker", "inadimplencias.table", "clientes.search_input"}
    # Par OU: a lista "renderizou" se houver LINHA (com dados) OU ESTADO-VAZIO (sem
    # dados). São mutuamente exclusivos — exigir os dois daria alarme falso diário.
    _CRITICOS_LISTA = ("inadimplencias.row", "inadimplencias.empty_state")

    def health_selectors(self) -> dict:
        """Smoke test (read-only): seletores CRÍTICOS da lista. O par linha/estado-
        vazio conta como UMA checagem (basta um resolver — a lista renderizou)."""
        from ..connectors.mag.inspect_mode import validate_selectors

        results = self._mag(lambda c: validate_selectors(c))
        passou = {k: p for k, p, _ in results}
        detalhe = {k: d for k, _, d in results}
        falhas = [{"chave": k, "detalhe": detalhe.get(k, "")}
                  for k in self._CRITICOS if not passou.get(k)]
        if not any(passou.get(k) for k in self._CRITICOS_LISTA):  # nem linha nem vazio
            falhas.append({"chave": "inadimplencias.lista",
                           "detalhe": "nem linha nem estado-vazio resolveram"})
        total = len(self._CRITICOS) + 1  # os obrigatórios + o par-OU da lista
        return {
            "ok": total - len(falhas),
            "total": total,
            "falhas": falhas,
            "contextuais": len(results) - len(self._CRITICOS) - len(self._CRITICOS_LISTA),
        }

    # --- revisão matinal automática do DOM ----------------------------------

    def _healthcheck_due(self) -> bool:
        if not self.config.healthcheck_auto:
            return False
        agora = now_in(self.config.timezone)
        if agora.time() < self.config.healthcheck_hora:  # só de manhã (após a hora)
            return False
        return self._last_health_date != agora.date().isoformat()

    def _run_daily_healthcheck(self, origem: str) -> None:
        """Roda o health-check 1x/dia e ALERTA por WhatsApp se um seletor crítico
        quebrou (a MAG mudou o layout). Só marca como feito se a sessão respondeu."""
        with self._health_lock:
            if not self._healthcheck_due():  # re-checa sob lock (evita corrida)
                return
            try:
                res = self.health_selectors()
            except DashboardError as e:
                log.info("revisão do DOM adiada (%s): %s", origem, e)  # sessão fora do ar
                return
            self._last_health = {**res, "em": iso_utc(), "origem": origem}
            self._last_health_date = now_in(self.config.timezone).date().isoformat()
            if res.get("falhas"):
                chaves = ", ".join(f["chave"] for f in res["falhas"])
                log.warning("revisão do DOM: %d seletor(es) crítico(s) quebrado(s): %s",
                            len(res["falhas"]), chaves)
                self._alertar_corretor(
                    f"⚠️ Régua MAG — revisão matinal: {len(res['falhas'])} seletor(es) "
                    f"crítico(s) quebrado(s) ({chaves}). A MAG pode ter mudado o layout — "
                    f"recalibre com `python -m seguros --inspect` antes da próxima cobrança."
                )
            else:
                log.info("revisão do DOM ok (%d/%d seletores)", res["ok"], res["total"])

    def _maybe_daily_healthcheck(self) -> None:
        if self._healthcheck_due():
            self._run_daily_healthcheck("descoberta")

    def _healthcheck_loop(self) -> None:
        """Fundo: tenta a revisão a cada 30 min — só roda se a sessão estiver de pé."""
        while not self._health_stop.is_set():
            try:
                if self._healthcheck_due() and self.worker.status().get("conector_ativo"):
                    self._run_daily_healthcheck("agendado")
            except Exception:  # noqa: BLE001 - loop nunca morre
                log.warning("loop da revisão do DOM falhou", exc_info=True)
            self._health_stop.wait(1800)  # 30 min

    def _alertar_corretor(self, texto: str) -> None:
        destino = _canon(self.test_number or self.config.notify_whatsapp_to)
        if not is_valid_whatsapp(destino):
            log.warning("sem número p/ alertar o corretor (revisão do DOM)")
            return
        try:
            self._zapi_sender().send(destino, texto)
        except Exception:  # noqa: BLE001 - alerta é best-effort
            log.warning("falha ao alertar o corretor", exc_info=True)

    def set_test_number(self, numero: str | None) -> dict:
        numero = (numero or "").strip()
        if numero and not is_valid_whatsapp(numero):
            raise DashboardError(f"Número de teste inválido: {numero!r}")
        self.test_number = canonical_brazilian_phone(numero) if numero else ""
        return {"ok": True, "test_number": self.test_number}

    def shutdown(self) -> None:
        self._health_stop.set()
        self.worker.shutdown()


__all__ = ["DashboardService", "DashboardError"]
