"""FakeConnector — dados canned para rodar o app end-to-end SEM a MAG.

Cobre a matriz de consentimento e casos de borda (telefone fixo inválido), para
validar o dry-run, o relatório e os gates antes de tocar no Playwright.
"""

from __future__ import annotations

from ..clock import now_utc
from .base import (
    ClientNotFoundError,
    ClientStatus,
    CompetenciaStatus,
    Contact,
    Delinquent,
    PaymentLinkResult,
    SeguradoraConnector,
    Situacao,
    WorkStatus,
)

# (delinquent, contact) por CPF — a "verdade" simulada da plataforma.
_SEED: dict[str, tuple[Delinquent, Contact]] = {
    "11111111111": (
        Delinquent("11111111111", "MARIA SILVA SOUZA", "2026-04-10", 25990, "R$ 259,90",
                   "04/2026", WorkStatus.NAO_TRABALHADO),
        Contact("11111111111", "maria.silva@example.com", "(11) 99876-5432", "(11) 3322-1100",
                autoriza_whatsapp=True, autoriza_email=True),
    ),
    "22222222222": (
        Delinquent("22222222222", "JOÃO PEREIRA", "2026-05-05", 18050, "R$ 180,50",
                   "05/2026", WorkStatus.NAO_TRABALHADO),
        Contact("22222222222", "joao.pereira@example.com", "(21) 98765-4321", None,
                autoriza_whatsapp=False, autoriza_email=True),
    ),
    "33333333333": (
        Delinquent("33333333333", "PEDRO ALVES", "2026-05-20", 42000, "R$ 420,00",
                   "05/2026", WorkStatus.TRABALHADO_PARCIALMENTE),
        Contact("33333333333", None, "(31) 99999-1234", None,
                autoriza_whatsapp=True, autoriza_email=False),
    ),
    "44444444444": (
        Delinquent("44444444444", "ANA COSTA", "2026-03-15", 99900, "R$ 999,00",
                   "03/2026", WorkStatus.NAO_TRABALHADO),
        Contact("44444444444", "ana.costa@example.com", "(41) 91234-5678", None,
                autoriza_whatsapp=False, autoriza_email=False),
    ),
    "55555555555": (
        Delinquent("55555555555", "CARLOS LANDLINE", "2026-04-01", 15000, "R$ 150,00",
                   "04/2026", WorkStatus.NAO_TRABALHADO),
        # telefone fixo (não-celular) -> WhatsApp será pulado como inválido
        Contact("55555555555", "carlos@example.com", "(11) 3344-5566", "(11) 3344-5566",
                autoriza_whatsapp=True, autoriza_email=True),
    ),
}


class FakeConnector(SeguradoraConnector):
    name = "FAKE"

    def __init__(
        self,
        seed: dict[str, tuple[Delinquent, Contact]] | None = None,
        *,
        regularizados: set[str] | None = None,
    ) -> None:
        self._seed = seed if seed is not None else _SEED
        # CPFs que devem aparecer como "Regularizados" no check_status (pagaram).
        self._regularizados = regularizados or set()

    def ensure_authenticated(self, *, interactive: bool) -> None:
        return None

    def discover_delinquents(self) -> list[Delinquent]:
        return [d for cpf, (d, _) in self._seed.items() if cpf not in self._regularizados]

    def fetch_contact(self, cpf: str) -> Contact:
        if cpf not in self._seed:
            raise ClientNotFoundError(cpf)
        return self._seed[cpf][1]

    def generate_payment_link(self, cpf: str, *, live: bool) -> PaymentLinkResult:
        if not live:
            return PaymentLinkResult(cpf, link=None, dry_run=True, would_generate=True)
        link = f"https://pagamento.mag.com.br/fake/{cpf}"
        return PaymentLinkResult(cpf, link=link, dry_run=False, generated_at=now_utc())

    def check_status(self, cpf: str) -> ClientStatus:
        regularizado = cpf in self._regularizados
        situacao = Situacao.REGULARIZADA if regularizado else Situacao.EM_ABERTO
        comp = self._seed[cpf][0].competencia if cpf in self._seed else "—"
        return ClientStatus(
            cpf=cpf,
            competencias=(CompetenciaStatus(comp or "—", situacao),),
            all_regularized=regularizado,
            checked_at=now_utc(),
        )


__all__ = ["FakeConnector"]
