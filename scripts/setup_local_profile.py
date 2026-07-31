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
  * **Mercado Livre** — não precisa no dia a dia, MAS logar é o antídoto do
    "login gate"/device-verification quando ele começa a pegar keyword atrás de
    keyword (incidente de 31/07/2026). Sessão logada não recebe o desafio.
  * **Casas Bahia** e **Magalu** — não precisam de conta. Dependem só de IP
    residencial + Chrome real, que este modo já entrega.

USO:
    python scripts/setup_local_profile.py                      # Shopee (padrão)
    python scripts/setup_local_profile.py --site mercadolivre  # login no ML
    python scripts/setup_local_profile.py --site ambos         # Shopee + ML
    python scripts/setup_local_profile.py --check              # só relata status
    python scripts/setup_local_profile.py --no-login           # só abre o Chrome

    # atalho: preenche o formulário do ML com ML_EMAIL/ML_PASSWORD do .env
    # (2FA e verificação de dispositivo você conclui na janela do Chrome)
    python scripts/setup_local_profile.py --site mercadolivre --auto

Depois de logar, rode a coleta:
    RAC_LOCAL_CHROME=1 python main.py --platforms ml magalu shopee casasbahia --pages 1
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

# --auto lê ML_EMAIL/ML_PASSWORD do .env (nunca do repositório).
try:  # dotenv é opcional — sem ele, --auto só funciona com env já exportada
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass

from scrapers.local_browser import (  # noqa: E402
    cdp_endpoint_if_up,
    find_chrome_exe,
    spawn_chrome,
    _import_sync_playwright,
    _resolve_port,
    _resolve_profile_dir,
)

_SHOPEE_HOME = "https://shopee.com.br/"

# Abrimos a HOME, não uma rota de login hardcoded: o ML troca o caminho do
# fluxo de login (`/jms/mlb/lgz/msl/login` virou 404 "Parece que esta página no
# existe" em 31/07/2026) e uma URL morta trava o setup inteiro. A home sempre
# existe e o botão "Entre" leva ao fluxo VIGENTE.
_ML_HOME = "https://www.mercadolivre.com.br/"

# Rota de conta: quando anônimo, o ML redireciona para o login atual. É o
# caminho que o `--auto` usa antes de cair no clique do "Entre" na home.
_ML_ACCOUNT_URL = "https://myaccount.mercadolivre.com.br/"

# Sites suportados. `login_cookies` = any-of que indica sessão autenticada.
SITES = {
    "shopee": {
        "label":         "Shopee",
        "start_url":     _SHOPEE_HOME,
        "cookie_url":    "https://shopee.com.br",
        # csrftoken/SPC_SI saem até para visitante anônimo — sem ESTES a API
        # de busca responde 403.
        "login_cookies": ("SPC_EC", "SPC_ST", "SPC_U"),
        "obrigatorio":   True,
        "instrucoes": [
            "Na janela do Chrome que abriu, faça LOGIN na Shopee.",
            "Pode ser 'Continuar com o Google' (funciona neste Chrome comum)",
            "ou e-mail/telefone + senha.",
        ],
    },
    "mercadolivre": {
        "label":         "Mercado Livre",
        "start_url":     _ML_HOME,
        "cookie_url":    "https://www.mercadolivre.com.br",
        "login_cookies": ("orguseridp", "MELI_SESSION", "c_user_id"),
        # O ML coleta anônimo na maioria dos dias; logar é o antídoto quando o
        # device-verification (o "login gate") começa a pegar todas as keywords.
        "obrigatorio":   False,
        "instrucoes": [
            "Abriu a HOME do Mercado Livre: clique em 'Entre' (canto superior direito)",
            "e faça login com a SUA conta (e-mail/telefone + senha).",
            "Se pedir verificação de dispositivo, confirme no app ou no e-mail —",
            "é justamente esse desafio que derruba a coleta quando não está logado.",
            "Depois de logar, abra lista.mercadolivre.com.br/ar-condicionado na mão:",
            "se os produtos aparecerem ali, a coleta também vai passar.",
        ],
    },
}


def _wait_cdp(port: int, seconds: int = 15) -> bool:
    """Espera (até `seconds`) o Chrome expor a porta de debug. True se subiu."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cdp_endpoint_if_up(port) is not None:
            return True
        time.sleep(0.5)
    return cdp_endpoint_if_up(port) is not None


_ML_EMAIL_FIELD = "input[name='user_id'], input#user_id, input[type='email']"


def _abrir_login_ml(page) -> bool:
    """
    Leva a página até o formulário de login VIGENTE do Mercado Livre.

    Nada de rota fixa: o ML muda o caminho do fluxo (`/jms/mlb/lgz/msl/login`
    virou 404 em 31/07/2026). Tenta a rota de conta (que redireciona para o
    login atual quando anônimo) e, se não der, clica no "Entre" da home.

    Returns:
        True se o campo de e-mail está visível na página.
    """
    def tem_campo() -> bool:
        try:
            page.locator(_ML_EMAIL_FIELD).first.wait_for(state="visible", timeout=8_000)
            return True
        except Exception:
            return False

    try:
        page.goto(_ML_ACCOUNT_URL, wait_until="domcontentloaded", timeout=45_000)
    except Exception:
        pass
    if tem_campo():
        return True

    try:
        page.goto(_ML_HOME, wait_until="domcontentloaded", timeout=45_000)
        page.locator(
            "a[href*='/lgz/'], a[href*='login'], "
            "a:has-text('Entre'), a:has-text('Entrar')"
        ).first.click(timeout=8_000)
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        return False
    return tem_campo()


def _ml_auto_login(port: int) -> bool:
    """
    Preenche e-mail/senha do ML a partir do `.env` (ML_EMAIL / ML_PASSWORD).

    É um ATALHO, não o caminho padrão: quem digita é o CDP, e a tela de login do
    ML é justamente a mais vigiada do site. Se a conta tiver 2FA ou o ML pedir
    verificação de dispositivo, o script para e VOCÊ termina na janela do Chrome
    que já está aberta — o resultado (cookies no perfil) é o mesmo.

    Returns:
        True se chegou a enviar as credenciais; False se faltou dado ou o DOM
        não bateu (aí é login manual).
    """
    email = os.getenv("ML_EMAIL", "").strip()
    senha = os.getenv("ML_PASSWORD", "").strip()
    if not email or not senha:
        print(
            "\n  [--auto] ML_EMAIL / ML_PASSWORD não estão no .env — "
            "seguindo com login manual na janela do Chrome."
        )
        return False

    endpoint = cdp_endpoint_if_up(port)
    sync_playwright, _ = _import_sync_playwright()
    if endpoint is None or sync_playwright is None:
        print("  [--auto] Chrome/Playwright indisponível — login manual.")
        return False

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=15_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        if not _abrir_login_ml(page):
            print(
                "  [--auto] Não achei o formulário de login. Clique em 'Entre' "
                "na janela do Chrome e faça manualmente."
            )
            return False

        campo_email = page.locator(
            "input[name='user_id'], input#user_id, input[type='email']"
        ).first
        campo_email.click(timeout=10_000)
        campo_email.type(email, delay=110)
        page.locator("button[type='submit']").first.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2.5)

        campo_senha = page.locator(
            "input[name='password'], input#password, input[type='password']"
        ).first
        campo_senha.click(timeout=15_000)
        campo_senha.type(senha, delay=110)
        page.locator("button[type='submit']").first.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3.0)

        print(f"  [--auto] Credenciais enviadas. Página atual: {page.url[:90]}")
        print(
            "  [--auto] Se o ML pediu código/2FA ou verificação de dispositivo, "
            "conclua na janela do Chrome antes de dar ENTER."
        )
        return True
    except Exception as exc:
        print(
            f"  [--auto] Não consegui preencher o formulário ({exc}). "
            "Faça o login manualmente na janela do Chrome."
        )
        return False
    finally:
        try:
            if browser is not None:
                browser.close()   # em CDP, close() apenas DESCONECTA
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _report_login(port: int, site: str) -> bool:
    """Conecta via CDP (breve) e relata se o site está logado. True se logado."""
    cfg = SITES[site]
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
        cookies = ctx.cookies(cfg["cookie_url"])
        names = {c.get("name") for c in cookies}
        login_cookies = cfg["login_cookies"]
        logged = any(n in names for n in login_cookies)
        print(f"\n  Cookies {cfg['label']} no perfil: {len(cookies)}")
        if logged:
            present = [n for n in login_cookies if n in names]
            print(f"  ✅ {cfg['label']} LOGADA (cookies de login: {present})")
        elif cfg["obrigatorio"]:
            print(
                f"  ❌ {cfg['label']} ANÔNIMA — nenhum cookie de login "
                f"({'/'.join(login_cookies)}). Faça login na aba e rode de novo "
                "(ou use --check)."
            )
        else:
            print(
                f"  ⚠️  {cfg['label']} ANÔNIMA — funciona na maioria dos dias, "
                "mas é logado que o login gate para de pegar as keywords."
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
        description=(
            "Setup do perfil Chrome dedicado e logado "
            "(Shopee obrigatório; Mercado Livre contra o login gate)."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--site", choices=("shopee", "mercadolivre", "ambos"), default="shopee",
        help=(
            "Onde logar neste perfil:\n"
            "  shopee        (padrão) — obrigatório para a API v4\n"
            "  mercadolivre  — antídoto do login gate / device-verification\n"
            "  ambos         — um login de cada vez, na mesma janela"
        ),
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Só relata o status de login do(s) site(s) (abre o Chrome se preciso).",
    )
    parser.add_argument(
        "--no-login", action="store_true",
        help="Só abre o Chrome comum no perfil (aquecer), sem esperar login.",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help=(
            "Mercado Livre: preenche e-mail/senha do .env (ML_EMAIL/ML_PASSWORD).\n"
            "Atalho — 2FA/verificação de dispositivo você conclui na janela."
        ),
    )
    args = parser.parse_args()

    sites = ["shopee", "mercadolivre"] if args.site == "ambos" else [args.site]

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
            spawn_chrome(port, profile_dir, start_url=SITES[sites[0]]["start_url"])
            _wait_cdp(port, seconds=15)
        # Exit code reflete o login — automação pode confiar no status.
        status = [_report_login(port, s) for s in sites]
        return 0 if all(status) else 1

    # Abre o Chrome comum já no site (se já houver um aberto no perfil, a URL
    # abre nele). NENHUM cliente CDP conectado agora → login humano/Google passa.
    primeiro = SITES[sites[0]]
    if cdp_endpoint_if_up(port) is not None:
        print(
            f"\n  Chrome já está aberto neste perfil — abrindo {primeiro['label']} nele."
            "\n  (Extensões abrindo abas sozinhas? Feche ESTE Chrome e rode de novo:"
            "\n   um Chrome novo sobe com --disable-extensions. Para mantê-las,"
            "\n   exporte RAC_CHROME_KEEP_EXTENSIONS=1.)"
        )
    spawn_chrome(port, profile_dir, start_url=primeiro["start_url"])

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

    status = []
    for i, site in enumerate(sites):
        cfg = SITES[site]
        if i > 0:
            # abre o próximo site na MESMA janela (perfil já quente)
            spawn_chrome(port, profile_dir, start_url=cfg["start_url"])

        if args.auto and site == "mercadolivre":
            _wait_cdp(port, seconds=15)
            _ml_auto_login(port)

        print("\n" + "─" * 66)
        print(f"  INSTRUÇÕES — {cfg['label']}:")
        for linha in cfg["instrucoes"]:
            print(f"   • {linha}")
        print("   • Deixe o Chrome ABERTO e volte aqui.")
        print("─" * 66)
        try:
            input(f"\n  → Pressione ENTER depois de logar em {cfg['label']}: ")
        except KeyboardInterrupt:
            print("\n  Cancelado (o que você já logou fica salvo no perfil).")

        status.append(_report_login(port, site))

    logged = all(status)
    print(
        "\n  Pronto. O login fica salvo no perfil dedicado. Rode a coleta com:\n"
        "\n    scripts\\collect_local_authenticated.bat 1     (jeito recomendado)\n"
        "\n  Ou manualmente. ATENÇÃO à sua shell:\n"
        "    PowerShell:  $env:RAC_LOCAL_CHROME=\"1\"; python main.py "
        "--platforms ml magalu shopee casasbahia --pages 1\n"
        "    cmd.exe   :  set RAC_LOCAL_CHROME=1 && python main.py "
        "--platforms ml magalu shopee casasbahia --pages 1\n"
        "  (No PowerShell, `set VAR=1` NÃO exporta env — tem que ser `$env:`.)\n"
        "\n  Dica: deixe este Chrome ABERTO — a coleta reaproveita ele "
        "(perfil quente)."
    )
    return 0 if logged else 1


if __name__ == "__main__":
    sys.exit(main())
