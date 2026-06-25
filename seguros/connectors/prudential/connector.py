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

import base64
import logging
import pathlib

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
    scrape_boleto_urls,
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
            tel = extract_phone(r.get("telefone", ""))
            out.append(
                Delinquent(
                    cpf=key,
                    nome=(r.get("nome") or "").strip(),
                    vencimento_mais_antigo=venc_iso,
                    valor_total_cents=parse_brl_to_cents(r.get("valor")),
                    valor_texto=r.get("valor"),
                    competencia=competencia_from_iso(venc_iso),
                    telefone=tel,
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
        rows = scrape_grid(
            self.page,
            self.selectors,
            table_key="atraso.table",
            col_map=self.selectors.get("atraso.col", {}),
            key_col="apolice",
        )
        # Injeta a URL de segunda via de boleto em cada linha (captura enquanto a
        # sessão está viva — a URL carrega parâmetros do servidor).
        boleto_urls = scrape_boleto_urls(self.page)
        for row in rows:
            apolice = row.get("apolice", "")
            if apolice in boleto_urls:
                row["boleto_url"] = boleto_urls[apolice]
        return rows

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

    # --- segunda via de boleto -----------------------------------------------

    def generate_payment_link(self, cpf: str, *, live: bool) -> PaymentLinkResult:
        """Gera segunda via de boleto pelo portal da Prudential.

        Navega até PAG_DBClient_EmissaoSegundaViaBoleto.aspx, seleciona a
        parcela mais antiga em aberto, clica Imprimir e captura a URL do popup.
        Em dry-run, apenas informa se o botão existe para este cliente.
        """
        cpf = normalize_cpf(cpf)
        row = self._last_rows.get(cpf)
        if not row:
            self.discover_delinquents()
            row = self._last_rows.get(cpf)

        boleto_url = (row or {}).get("boleto_url")

        if not live:
            return PaymentLinkResult(cpf, link=None, dry_run=True,
                                     would_generate=bool(boleto_url))
        if not boleto_url:
            log.warning("sem URL de segunda via para apólice %s", cpf)
            return PaymentLinkResult(cpf, link=None, dry_run=False, would_generate=False)

        # Navega direto à página de emissão (evita lidar com popup window).
        self.session.goto(boleto_url)
        wait_ready(self.page, self.selectors)

        # Seleciona a primeira parcela (mais antiga).
        try:
            first_radio = self.page.locator('input[name*="RBT_Selecionado"]').first
            first_radio.check(timeout=5000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.warning("radio não selecionado: %s", e)

        # Clica Imprimir: ASP.NET faz postback -> abre popup que carrega o boleto
        # (ExibeRelatorio.aspx). A URL exige sessão ativa, então salvamos o PDF
        # localmente via CDP (funciona no Chrome headed) enquanto o popup está aberto.
        link: str | None = None
        try:
            with self.page.expect_popup(timeout=20000) as popup_info:
                self.page.locator('input[name="BTN_Imprimir"]').click(
                    timeout=8000, no_wait_after=True
                )
            popup = popup_info.value
            popup.wait_for_load_state("networkidle", timeout=25000)
            # Aguarda o iframe interno carregar completamente antes de salvar.
            popup.wait_for_timeout(4000)

            pdf_path = self._save_popup_pdf(popup, cpf)
            if pdf_path:
                link = str(pdf_path)
                log.info("boleto salvo: %s", pdf_path)
            else:
                # Fallback: guarda a URL (só abre com sessão ativa)
                url = popup.url
                link = url if url and url != "about:blank" else None

            popup.close()
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.warning("popup do boleto não capturado: %s", e)
            # Fallback: download direto (PDF entregue como arquivo)
            try:
                with self.page.expect_download(timeout=8000) as dl_info:
                    self.page.locator('input[name="BTN_Imprimir"]').click(timeout=5000)
                download = dl_info.value
                pdf_path = self._boleto_dir() / f"{cpf}.pdf"
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                download.save_as(str(pdf_path))
                link = str(pdf_path)
            except (PlaywrightTimeout, PlaywrightError):
                pass

        log.info("segunda via gerada: apólice=%s link=%s", cpf, link or "(sem URL)")
        return PaymentLinkResult(cpf, link=link, dry_run=False,
                                 would_generate=True, generated_at=now_utc())

    # --- helpers de PDF ------------------------------------------------------

    def _boleto_dir(self) -> pathlib.Path:
        return pathlib.Path(self.cfg.db_path).parent / "boletos"

    def _save_popup_pdf(self, popup, cpf: str) -> pathlib.Path | None:
        """Salva o boleto como PDF via CDP (Page.printToPDF — funciona em headed).

        ModalGenerica é um wrapper com overlay escuro + iframe do boleto. Não
        navegamos para fora dela (o servidor rejeita navegação direta ao iframe).
        Em vez disso, limpamos a página via JS para que o iframe ocupe tudo.
        """
        try:
            if "ModalGenerica" in (popup.url or ""):
                # Remove overlay escuro e faz o iframe preencher a página inteira.
                popup.evaluate("""
                    () => {
                        document.documentElement.style.background = 'white';
                        document.body.style.cssText =
                            'margin:0;padding:0;background:white;overflow:hidden;';
                        const iframe = document.querySelector('iframe');
                        if (iframe) {
                            iframe.style.cssText =
                                'position:fixed;top:0;left:0;' +
                                'width:100vw;height:100vh;border:none;';
                        }
                        Array.from(document.body.children).forEach(el => {
                            if (el.tagName.toLowerCase() !== 'iframe') {
                                el.style.display = 'none';
                            }
                        });
                    }
                """)

            popup.emulate_media(media="print")
            cdp = popup.context.new_cdp_session(popup)
            result = cdp.send("Page.printToPDF", {
                "printBackground": False,
                "paperWidth": 8.27,
                "paperHeight": 11.69,
                "marginTop": 0.4,
                "marginBottom": 0.4,
                "marginLeft": 0.4,
                "marginRight": 0.4,
            })
            pdf_bytes = base64.b64decode(result["data"])
            dest = self._boleto_dir() / f"{cpf}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(pdf_bytes)
            cdp.detach()
            return dest
        except Exception as e:
            log.warning("PDF via CDP falhou: %s", e)
            return None

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
