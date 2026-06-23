"""Login humano + captura de sessão + dump do DOM para uma seguradora NOVA.

Uso (exploração da Prudential ou qualquer outra):
    python tools/insurer_login.py <URL_DE_LOGIN> [nome_curto]

Abre um Chrome COMUM (onde captcha/OTP funcionam), espera o humano logar,
captura os cookies de sessão para `.{nome}_session/session_cookies.json` e
despeja URL/título/HTML/acessibilidade em `artifacts/{nome}/` para a gente
planejar o conector. Reaproveita o padrão comprovado do `login_browser.py` da MAG.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Reaproveita os helpers já testados do login da MAG.
from seguros.connectors.mag.login_browser import (
    _free_port,
    _terminate,
    _wait_cdp_ready,
    find_chrome,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: python tools/insurer_login.py <URL_DE_LOGIN> [nome_curto]")
        return 2
    login_url = sys.argv[1].strip()
    nome = (sys.argv[2].strip() if len(sys.argv) > 2 else "prudential").lower()

    chrome = find_chrome()
    if not chrome:
        print("Não encontrei o chrome.exe. Instale o Google Chrome.")
        return 1

    session_dir = Path(f".{nome}_session").resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path("artifacts") / nome
    out_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()

    import subprocess

    proc = subprocess.Popen([
        chrome, f"--user-data-dir={session_dir}", f"--remote-debugging-port={port}",
        "--no-first-run", "--no-default-browser-check", login_url,
    ])
    if not _wait_cdp_ready(port):
        print("O Chrome não respondeu a tempo.")
        _terminate(proc)
        return 1

    print(
        f"\n=============== LOGIN {nome.upper()} (Chrome normal) ===============\n"
        "Abri uma janela do Chrome COMUM — captcha e OTP funcionam aqui.\n"
        "  1) Faça login: usuário, senha e o OTP que seu contato te passar.\n"
        "  2) Espere CAIR na plataforma (página interna, logado).\n"
        "  3) NÃO feche o Chrome — volte aqui e pressione ENTER.\n"
        "====================================================================\n"
    )
    input("Pressione ENTER depois de logar e CAIR na plataforma... ")

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else None
            if ctx is None:
                print("Não consegui acessar o contexto do Chrome.")
                return 1
            cookies = ctx.cookies()
            (session_dir / "session_cookies.json").write_text(
                json.dumps(cookies), encoding="utf-8"
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            url = page.url
            title = page.title()
            (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
            try:
                snap = page.accessibility.snapshot()
                (out_dir / "a11y.json").write_text(
                    json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:  # noqa: BLE001
                print(f"(a11y snapshot falhou: {e})")
            page.screenshot(path=str(out_dir / "screenshot.png"), full_page=True)
            browser.close()
            print(f"\n✅ Sessão {nome} capturada ({len(cookies)} cookies).")
            print(f"   URL atual : {url}")
            print(f"   Título    : {title}")
            print(f"   Host      : {urlparse(url).netloc}")
            print(f"   Dumps em  : {out_dir}\\ (page.html, a11y.json, screenshot.png)")
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"Erro ao capturar a sessão: {e}")
        return 1
    finally:
        _terminate(proc)


if __name__ == "__main__":
    raise SystemExit(main())
