"""
scripts/setup_local_profile.py — Setup ÚNICO do perfil Chrome dedicado e logado.

Abre o Chrome real sobre o perfil dedicado do projeto (``data/chrome_profile/``,
o MESMO usado pela coleta) de forma VISÍVEL, para você:

  1. Fazer login na Shopee (obrigatório — sem login a API v4 responde 403).
  2. (Opcional) Navegar 1-2 min no Magalu e na Casas Bahia pra "aquecer" o
     perfil (o Akamai trata perfis com histórico como mais legítimos).

O login fica salvo NESTE diretório e PERSISTE entre execuções — diferente da
abordagem antiga (perfil copiado), aqui o Chrome não invalida a sessão porque
o diretório é estável e nunca é movido/copiado.

Por que isto substitui o setup_cdp_profile + start_chrome_cdp
-------------------------------------------------------------
  * Não copia o perfil (a cópia deslogava as contas).
  * Não usa ``--remote-debugging-port`` (o Chrome 136+ ignora essa flag no
    perfil padrão — era a causa de "liguei o CDP e não conectou").
  * A coleta abre ESTE mesmo diretório via launch_persistent_context.

USO:
    python scripts/setup_local_profile.py               # abre p/ login (Shopee)
    python scripts/setup_local_profile.py --check        # só relata status/cookies
    python scripts/setup_local_profile.py --headless-check

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
    _LAUNCH_ARGS,
    _IGNORE_DEFAULT_ARGS,
    _DEFAULT_UA,
    _import_sync_playwright,
    _resolve_profile_dir,
)

# Cookies que indicam sessão LOGADA na Shopee (any-of). csrftoken/SPC_SI saem
# até para visitante anônimo — sem estes a API de busca retorna 403.
_SHOPEE_LOGIN_COOKIES = ("SPC_EC", "SPC_ST", "SPC_U")

_SITES = [
    ("Shopee",      "https://shopee.com.br/",           True),
    ("Magalu",      "https://www.magazineluiza.com.br/", False),
    ("Casas Bahia", "https://www.casasbahia.com.br/",   False),
]


def _report_cookies(context) -> None:
    """Relata se a sessão da Shopee está logada, lendo os cookies do perfil."""
    try:
        cookies = context.cookies("https://shopee.com.br")
    except Exception as exc:
        print(f"  [aviso] não consegui ler cookies da Shopee: {exc}")
        return
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
            "e rode este script de novo (ou use --check para reconferir)."
        )


def _open_profile(headless: bool):
    """Abre o Chrome persistente sobre o perfil dedicado. Retorna (pw, context)."""
    sync_playwright, flavor = _import_sync_playwright()
    if sync_playwright is None:
        print(
            "ERRO: Playwright não instalado. Execute:\n"
            "  pip install rebrowser-playwright\n"
            "  python -m rebrowser_playwright install chromium"
        )
        sys.exit(2)
    if flavor != "rebrowser-playwright":
        print(
            "AVISO: usando Playwright STOCK. Para a coleta passar melhor pelo "
            "Akamai, instale o fork: pip install rebrowser-playwright"
        )

    profile_dir = _resolve_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Perfil: {profile_dir} ({flavor})")

    pw = sync_playwright().start()
    context = None
    for channel in ("chrome", "msedge", None):
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=channel,
                user_agent=_DEFAULT_UA,
                viewport={"width": 1366, "height": 768},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                args=_LAUNCH_ARGS,
                ignore_default_args=_IGNORE_DEFAULT_ARGS,
            )
            print(f"  Browser: {channel or 'chromium'}")
            break
        except Exception as exc:
            msg = str(exc).lower()
            if "singletonlock" in msg or "already in use" in msg:
                print(
                    "\nERRO: o perfil já está aberto por outro Chrome.\n"
                    "Feche TODAS as janelas do Chrome de coleta/setup e tente de novo."
                )
                pw.stop()
                sys.exit(3)
            continue

    if context is None:
        print("ERRO: não foi possível abrir nenhum Chrome (chrome/msedge/chromium).")
        pw.stop()
        sys.exit(2)
    return pw, context


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Setup único do perfil Chrome dedicado e logado (Shopee).",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Só abre, relata o status de login da Shopee e fecha (visível).",
    )
    parser.add_argument(
        "--headless-check", action="store_true",
        help="Como --check, porém sem abrir janela (só lê os cookies do perfil).",
    )
    args = parser.parse_args()

    headless = args.headless_check
    print("=" * 64)
    print("  Setup do perfil Chrome dedicado — RAC Position Tracker")
    print("=" * 64)

    pw, context = _open_profile(headless=headless)

    try:
        if args.check or args.headless_check:
            _report_cookies(context)
            return 0

        # Modo interativo: abre uma aba por site e espera o ENTER do usuário.
        for name, url, needs_login in _SITES:
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                pass
            flag = " (LOGIN OBRIGATÓRIO)" if needs_login else " (opcional: navegue p/ aquecer)"
            print(f"  → Aba aberta: {name}{flag}")
            time.sleep(1.0)

        print("\n" + "─" * 64)
        print("  INSTRUÇÕES:")
        print("   1. Na aba da SHOPEE, faça LOGIN com a sua conta (obrigatório).")
        print("   2. (Opcional) Navegue 1-2 min no Magalu e na Casas Bahia.")
        print("   3. Volte aqui e pressione ENTER para salvar e fechar.")
        print("─" * 64)
        try:
            input("\n  → Pressione ENTER quando terminar o login: ")
        except KeyboardInterrupt:
            print("\n  Cancelado (sem confirmar). O que você já logou fica salvo.")

        _report_cookies(context)
        print(
            "\n  Pronto. O login fica salvo no perfil. Rode a coleta com:\n"
            "    RAC_LOCAL_CHROME=1 python main.py --platforms magalu shopee casasbahia --pages 1\n"
            "    (Windows: scripts\\collect_local_authenticated.bat)"
        )
        return 0
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
