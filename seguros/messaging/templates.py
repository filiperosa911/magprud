"""Templates das mensagens + renderização segura + formatação BR.

Usamos ``string.Template`` (``${var}``) com ``safe_substitute``: é à prova de
chaves literais em URLs/nomes (que quebrariam ``str.format``) e, se faltar uma
variável, deixa o placeholder visível em vez de estourar — o dry-run pega isso.
"""

from __future__ import annotations

import html
from decimal import Decimal
from string import Template

# --- textos (exatamente os da especificação §10, em estilo ${var}) -----------

WHATSAPP_DIA0 = Template(
    """Olá, ${primeiro_nome}, tudo bem?
Aqui é ${nome_corretor}, da ${corretora} — sua corretora parceira MAG Seguros.
Identifiquei uma pendência no seu seguro referente a ${competencia}, no valor de ${valor_total}.
Para regularizar de forma rápida e segura, é só acessar o link de pagamento abaixo:
${link_pagamento}
Se já tiver pago, pode desconsiderar. Qualquer dúvida, estou à disposição por aqui.
Caso não queira mais receber estes lembretes por WhatsApp, responda SAIR."""
)

EMAIL_DIA2_ASSUNTO = Template("Pendência no seu seguro MAG — ${competencia}")

EMAIL_DIA2_TEXTO = Template(
    """Olá, ${primeiro_nome},

Identifiquei uma pendência no seu seguro MAG referente a ${competencia}, no valor de ${valor_total}, ainda em aberto.

Você pode regularizar de forma rápida e segura por este link:
${link_pagamento}

Se o pagamento já tiver sido feito, por favor desconsidere este e-mail.
Fico à disposição para qualquer dúvida.

Atenciosamente,
${nome_corretor} — ${corretora}"""
)

# HTML leve: sem imagens/pixels (boa entregabilidade), link clicável.
EMAIL_DIA2_HTML = Template(
    """<!DOCTYPE html>
<html lang="pt-BR">
<body style="font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #222; line-height: 1.5;">
  <p>Olá, ${primeiro_nome},</p>
  <p>Identifiquei uma pendência no seu seguro MAG referente a <strong>${competencia}</strong>,
     no valor de <strong>${valor_total}</strong>, ainda em aberto.</p>
  <p>Você pode regularizar de forma rápida e segura por este link:<br>
     <a href="${link_pagamento}" style="color: #0b5fff;">${link_pagamento}</a></p>
  <p>Se o pagamento já tiver sido feito, por favor desconsidere este e-mail.<br>
     Fico à disposição para qualquer dúvida.</p>
  <p>Atenciosamente,<br>${nome_corretor} — ${corretora}</p>
</body>
</html>"""
)


# --- formatação --------------------------------------------------------------


def brl_from_cents(cents: int | None) -> str:
    """Formata centavos em ``R$ 1.234,56`` (sem depender de ``locale``)."""
    if cents is None:
        return "R$ —"
    return _brl(Decimal(cents) / 100)


def _brl(value: Decimal) -> str:
    s = f"{value:,.2f}"  # 1,234.56  (padrão en_US)
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")  # -> 1.234,56
    return f"R$ {s}"


def primeiro_nome(nome: str | None) -> str:
    partes = (nome or "").strip().split()
    return partes[0].capitalize() if partes else ""


# --- renderização ------------------------------------------------------------


def render(template: Template, ctx: dict, *, escape_html: bool = False) -> str:
    if escape_html:
        ctx = {k: html.escape(str(v)) for k, v in ctx.items()}
    return template.safe_substitute(ctx)


__all__ = [
    "WHATSAPP_DIA0",
    "EMAIL_DIA2_ASSUNTO",
    "EMAIL_DIA2_TEXTO",
    "EMAIL_DIA2_HTML",
    "brl_from_cents",
    "primeiro_nome",
    "render",
]
