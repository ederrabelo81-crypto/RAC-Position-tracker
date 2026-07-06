"""
scripts/setup_local_profile.py — Setup ÚNICO do perfil Chrome dedicado e logado.

Abre um Chrome COMUM (não controlado por automação) sobre o perfil dedicado do
projeto (``data/chrome_profile/`` — o MESMO usado pela coleta), já na Shopee,
para você fazer login UMA vez.

Por que um Chrome comum (e não Playwright)
------------------------------------------
No login, NENHUM cliente de automação está conectado — a página vê um browser
100% humano (sem ``navigator.webdriver``, sem flags de automação). Por isso o
login pelo **Google** funciona aqui (num browser automatizado o Google recusa
com "este navegador pode não ser seguro"). O login fica salvo no perfil
dedicado e a coleta ataca esse mesmo Chrome via CDP.

O que precisa (e o que NÃO precisa) de login
--------------------------------------------
  * **Shopee** — PRECISA de login (a API v4 responde 403 sem conta). Pode ser
    via Google (funciona neste Chrome comum) ou e-mail/telefone.
  * **Casas Bahia** e **Magalu** — NÃO precisam de conta nenhuma. Só dependem
    de IP residencial + Chrome real, que este modo já entrega.

USO:
    python scripts/setup_local_profile.py            # abre a Shopee p/ login
    python scripts/setup_local_profile.py --check     # só relata status do login
    python scripts/setup_local_profile.py --no-login  # só abre o Chrome (aquecer)

Depois de logar, rode a coleta:
    RAC_LOCAL_CHROME=1 python main.py --platforms magalu shopee casasbahia --pages 1
    (no Windows: scripts\\collect_local_authenticated.bat)
"""

import argparse
import os
import sys
import time
from pathlib import Path

# rebrowser runtime fix precisa ser setado antes do import (igual à coleta).
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scrapers.local_browser import (  # noqa: E402
    cdp_endpoint_if_up,
    find_chrome_exe,
    spawn_chrome,
    _import_sync_playwright,
    _resolve_port,
    _resolve_profile_dir,
)

_SHOPEE_HOME = "https://shopee.com.br/"

# Cookies que indicam sessão LOGADA na Shopee (any-of). csrftoken/SPC_SI saem
# até para visitante anônimo — sem estes a API de busca retorna 403.
_SHOPEE_LOGIN_COOKIES = ("SPC_EC", "SPC_ST", "SPC_U")


def _wait_cdp(port: int, seconds: int = 15) -> bool:
    """Espera (até `seconds`) o Chrome expor a porta de debug. True se subiu."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cdp_endpoint_if_up(port) is not None:
            return True
        time.sleep(0.5)
    return cdp_endpoint_if_up(port) is not None


def _report_shopee_login(port: int) -> bool:
    """Conecta via CDP (breve) e relata se a Shopee está logada. True se logada."""
    endpoint = cdp_endpoint_if_up(port)
    if endpoint is None:
        print("  [aviso] Chrome não está aberto na porta — não dá pra checar.")
        return False

    sync_playwright, flavor = _import_sync_playwright()
    if sync_playwright is None:
        print("  [aviso] Playwright indisponível — pulei a checagem de cookies.")
        return False

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=15_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        cookies = ctx.cookies("https://shopee.com.br")
        names = {c.get("name") for c in cookies}
        logged = any(n in names for n in _SHOPEE_LOGIN_COOKIES)
        print(f"\n  Cookies Shopee no perfil: {len(cookies)}")
        if logged:
            present = [n for n in _SHOPEE_LOGIN_COOKIES if n in names]
            print(f"  ✅ Shopee LOGADA (cookies de login: {present})")
        else:
            print(
                "  ❌ Shopee ANÔNIMA — nenhum cookie de login "
                f"({'/'.join(_SHOPEE_LOGIN_COOKIES)}). Faça login na aba da Shopee "
                "e rode de novo (ou use --check)."
            )
        return logged
    except Exception as exc:
        print(f"  [aviso] não consegui checar o login via CDP: {exc}")
        return False
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Setup único do perfil Chrome dedicado e logado (Shopee).",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Só relata o status de login da Shopee (abre o Chrome se preciso).",
    )
    parser.add_argument(
        "--no-login", action="store_true",
        help="Só abre o Chrome comum no perfil (aquecer), sem esperar login.",
    )
    args = parser.parse_args()

    port = _resolve_port()
    profile_dir = _resolve_profile_dir()

    print("=" * 66)
    print("  Setup do perfil Chrome dedicado — RAC Position Tracker")
    print("=" * 66)

    _, flavor = _import_sync_playwright()
    if flavor != "rebrowser-playwright":
        print(
            "\n  ⚠️  AVISO: rebrowser-playwright NÃO está instalado. A COLETA vai\n"
            "     ser detectada pelo Akamai (Magalu/Casas Bahia → 403). Instale:\n"
            "        pip install rebrowser-playwright\n"
            "        python -m rebrowser_playwright install chromium\n"
            "     (O login em si funciona mesmo sem o fork — ele importa na coleta.)\n"
        )

    if find_chrome_exe() is None:
        print(
            "\n  ERRO: Chrome não encontrado. Instale o Google Chrome ou defina a\n"
            "  variável RAC_CHROME_EXE com o caminho do chrome.exe."
        )
        return 2

    print(f"  Perfil : {profile_dir}")
    print(f"  Porta  : {port}")

    # --check: só relata (abre o Chrome se ainda não estiver aberto).
    if args.check:
        if cdp_endpoint_if_up(port) is None:
            spawn_chrome(port, profile_dir, start_url=_SHOPEE_HOME)
            _wait_cdp(port, seconds=15)
        # Exit code reflete o login — automação pode confiar no status.
        return 0 if _report_shopee_login(port) else 1

    # Abre o Chrome comum já na Shopee (se já houver um aberto no perfil, a URL
    # abre nele). NENHUM cliente CDP conectado agora → login humano/Google passa.
    if cdp_endpoint_if_up(port) is not None:
        print("\n  Chrome já está aberto neste perfil — abrindo a Shopee nele.")
    spawn_chrome(port, profile_dir, start_url=_SHOPEE_HOME)

    if args.no_login:
        # Confirma que o Chrome subiu de fato (porta de debug acessível) antes
        # de reportar sucesso — evita falso "Chrome aberto".
        if _wait_cdp(port, seconds=15):
            print("\n  ✅ Chrome aberto (modo --no-login). Feche quando quiser.")
            return 0
        print(
            "\n  ❌ O Chrome não expôs a porta de debug. Feche Chromes abertos "
            "nesse perfil (ou ajuste RAC_CDP_PORT) e tente de novo."
        )
        return 1

    print("\n" + "─" * 66)
    print("  INSTRUÇÕES (só a Shopee precisa de login):")
    print("   1. Na janela do Chrome que abriu, faça LOGIN na Shopee.")
    print("      • Pode ser 'Continuar com o Google' (funciona neste Chrome comum)")
    print("        ou e-mail/telefone + senha.")
    print("   2. Casas Bahia e Magalu NÃO precisam de login — pode ignorar.")
    print("   3. Deixe o Chrome ABERTO e volte aqui.")
    print("─" * 66)
    try:
        input("\n  → Pressione ENTER depois de logar na Shopee: ")
    except KeyboardInterrupt:
        print("\n  Cancelado (o que você já logou fica salvo no perfil).")

    logged = _report_shopee_login(port)
    print(
        "\n  Pronto. O login fica salvo no perfil dedicado. Rode a coleta com:\n"
        "\n    scripts\\collect_local_authenticated.bat 1     (jeito recomendado)\n"
        "\n  Ou manualmente. ATENÇÃO à sua shell:\n"
        "    PowerShell:  $env:RAC_LOCAL_CHROME=\"1\"; python main.py "
        "--platforms magalu shopee casasbahia --pages 1\n"
        "    cmd.exe   :  set RAC_LOCAL_CHROME=1 && python main.py "
        "--platforms magalu shopee casasbahia --pages 1\n"
        "  (No PowerShell, `set VAR=1` NÃO exporta env — tem que ser `$env:`.)\n"
        "\n  Dica: deixe este Chrome ABERTO — a coleta reaproveita ele "
        "(perfil quente)."
    )
    return 0 if logged else 1


if __name__ == "__main__":
    sys.exit(main())
