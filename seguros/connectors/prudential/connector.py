"""``PrudentialConnector`` — implementação Prudential da fronteira
``SeguradoraConnector`` (Life Planner AEM + relatório ASPX de atraso).

Esteira (igual à MAG, adaptada ao portal ASPX):
  discover  -> abre o "Relatório de Atraso", filtra por Dias Atraso >= mínimo,
               lê a grade de resultados.
  contact   -> lê telefone/e-mail da própria linha do relatório (se a grade
               trouxer); senão, found=False (fonte de contato a mapear ao vivo).
  link      -> a Prudential NÃO tem link de pagamento conhecido (provável débito
               automático / 2ª via fora do portal). Por ora é LEMBRETE: retorna
               sem link, sem mutar nada. (A definir ao vivo — ver README.)
  status    -> sumiu do Relatório de Atraso = regularizou (pagou).

AUTO-CALIBRAÇÃO: a grade é achada pelo conteúdo (CPF) e a coluna de CPF é
detectada sozinha (``scraping.find_results_table`` / ``detect_cpf_column``), então
não há selector de grade a editar. Como na MAG, o acesso exige login humano (OTP),
feito 1x no ``--login``; sem sessão válida, ``ensure_authenticated`` orienta a logar.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from ...clock import now_utc
from ...cpf import normalize_cpf
from ..base import (
    ClientStatus,
    CompetenciaStatus,
    ConnectorError,
    Contact,
    Delinquent,
    PaymentLinkResult,
    SeguradoraConnector,
    Situacao,
)
from .scraping import (
    competencia_from_iso,
    extract_phone,
    parse_brl_to_cents,
    parse_date_to_iso,
    scrape_grid,
    wait_ready,
)
from .selectors import load_selectors
from .session import PrudentialSession

log = logging.getLogger("seguros.prudential.connector")


class PrudentialConnector(SeguradoraConnector):
    name = "prudential"

    def __init__(self, config, *, notifier=None, selectors=None) -> None:
        self.cfg = config
        self.selectors = selectors or load_selectors()
        self.session = PrudentialSession(config, self.selectors, notifier=notifier)
        # Cache da última descoberta (CPF -> linha bruta) p/ o fetch_contact.
        self._last_rows: dict[str, dict] = {}

    # --- ciclo de vida -------------------------------------------------------

    def start(self) -> None:
        self.session.start()

    def close(self) -> None:
        self.session.close()

    @property
    def page(self):
        return self.session.page

    def ensure_authenticated(self, *, interactive: bool) -> None:
        self.session.ensure_authenticated(interactive=interactive)

    # --- descoberta ----------------------------------------------------------

    def discover_delinquents(self) -> list[Delinquent]:
        rows = self._run_atraso_report()
        out: list[Delinquent] = []
        self._last_rows = {}
        for r in rows:
            # chave = dígitos da Apólice (normalizados p/ casar o resto do app,
            # que é CPF-cêntrico; aqui a Apólice ocupa o campo-chave).
            key = normalize_cpf(r.get("apolice", ""))
            if not key:
                continue
            self._last_rows[key] = r
            venc_iso = parse_date_to_iso(r.get("vencimento"))
            out.append(
                Delinquent(
                    cpf=key,
                    nome=(r.get("nome") or "").strip(),
                    vencimento_mais_antigo=venc_iso,
                    valor_total_cents=parse_brl_to_cents(r.get("valor")),
                    valor_texto=r.get("valor"),
                    competencia=competencia_from_iso(venc_iso),
                    raw=r,
                )
            )
        return out

    def _run_atraso_report(self) -> list[dict]:
        """Abre o relatório, filtra por Dias Atraso >= mínimo e lê a grade."""
        self.session.goto(self.cfg.prudential_atraso_url)
        wait_ready(self.page, self.selectors)
        self._fill_and_filter()
        wait_ready(self.page, self.selectors)
        return scrape_grid(
            self.page,
            self.selectors,
            table_key="atraso.table",
            col_map=self.selectors.get("atraso.col", {}),
            key_col="apolice",
        )

    def _fill_and_filter(self) -> None:
        minimo = str(self.cfg.prudential_dias_atraso_min)
        try:
            campo = self.selectors.locator(self.page, "atraso.form.dias_atraso_de").first
            campo.fill(minimo, timeout=8000)
        except (PlaywrightTimeout, PlaywrightError) as err:
            log.debug("campo 'Dias Atraso' não preenchido (calibrar): %s", err)
        try:
            self.selectors.locator(self.page, "atraso.form.filtrar_button").first.click(
                timeout=8000
            )
        except (PlaywrightTimeout, PlaywrightError) as err:
            log.debug("botão 'Filtrar' não clicado (calibrar): %s", err)

    # --- contato -------------------------------------------------------------

    def fetch_contact(self, cpf: str) -> Contact:
        cpf = normalize_cpf(cpf)
        row = self._last_rows.get(cpf)
        if row is None:
            # ainda não descoberto neste run: roda a descoberta uma vez.
            self.discover_delinquents()
            row = self._last_rows.get(cpf)
        if not row:
            return Contact(cpf=cpf, found=False)
        # O telefone vem na PRÓPRIA grade (coluna Contatos: "Cel.: (11) 9...").
        tel = extract_phone(row.get("telefone", ""))
        return Contact(cpf=cpf, celular=tel, telefone=tel, found=bool(tel))

    # --- link de pagamento (LEMBRETE: a Prudential não tem link conhecido) ----

    def generate_payment_link(self, cpf: str, *, live: bool) -> PaymentLinkResult:
        # A forma de pagamento da Prudential ainda não foi mapeada (provável
        # débito automático / 2ª via fora do portal). Não há link a gerar nem
        # nada a mutar: a régua atua como LEMBRETE. NÃO abre o navegador.
        cpf = normalize_cpf(cpf)
        return PaymentLinkResult(cpf, link=None, dry_run=not live, would_generate=False)

    # --- re-check de status --------------------------------------------------

    def check_status(self, cpf: str) -> ClientStatus:
        cpf = normalize_cpf(cpf)
        cents = self.check_client_inadimplente_cents(cpf)
        all_reg = cents == 0
        comp = CompetenciaStatus(
            competencia="resumo",
            situacao=Situacao.REGULARIZADA if all_reg else Situacao.EM_ABERTO,
            valor_cents=cents if cents else None,
        )
        return ClientStatus(cpf, competencias=(comp,), all_regularized=all_reg,
                            checked_at=now_utc())

    def check_client_inadimplente_cents(self, cpf: str) -> int | None:
        """Sinal de pagamento: sumiu do Relatório de Atraso => 0 (pagou).

        Espelha o método homônimo da MAG (usado pelo agente inbound 'já paguei',
        que NUNCA confia no texto do cliente).
        """
        cpf = normalize_cpf(cpf)
        try:
            self.discover_delinquents()
        except ConnectorError:
            return None
        row = self._last_rows.get(cpf)
        if row is None:
            return 0  # não está mais em atraso => regularizou
        return parse_brl_to_cents(row.get("valor"))


__all__ = ["PrudentialConnector"]
