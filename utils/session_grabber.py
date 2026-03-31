"""
utils/session_grabber.py — Bypass manual de WAF/login para sites bloqueados.

USO:
    python utils/session_grabber.py --site casasbahia
    python utils/session_grabber.py --site shopee

COMO FUNCIONA:
    1. Abre um browser REAL (visível) na URL do site
    2. Aguarda você navegar, resolver CAPTCHA, fazer login se necessário
    3. Salva os cookies e headers de sessão em utils/sessions/{site}.json
    4. Os scrapers carregam essa sessão automaticamente nas próximas execuções

SITES SUPORTADOS:
    casasbahia  — bypass do WAF Akamai (Reference ID no HTML de erro)
    shopee      — salva sessão autenticada (evita redirect para /buyer/login)

RENOVAÇÃO:
    Sessões expiram em horas/dias. Rode novamente quando o scraper voltar a falhar.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERRO: Playwright não instalado. Execute: pip install playwright")
    sys.exit(1)

# Diretório para salvar sessões
SESSIONS_DIR = Path(__file__).parent / "sessions"

SITE_CONFIG = {
    "casasbahia": {
        "url": "https://www.casasbahia.com.br",
        "check_url": "https://www.casasbahia.com.br/busca?q=ar+condicionado",
        "success_hint": "Aguarde os produtos aparecerem na página de busca.",
        "blocked_text": "Ops! Algo deu errado",
        "wait_message": (
            "\n🔑 INSTRUÇÃO — CASAS BAHIA:\n"
            "  O Akamai WAF pode exibir uma página de erro.\n"
            "  Se aparecer, aguarde 10-15s até carregar normalmente.\n"
            "  Se não carregar, feche e tente em outro momento.\n"
            "  Quando os PRODUTOS aparecerem, pressione ENTER.\n"
        ),
    },
    "shopee": {
        "url": "https://shopee.com.br",
        "check_url": "https://shopee.com.br/search?keyword=ar+condicionado",
        "success_hint": "Faça login se solicitado e aguarde os produtos aparecerem.",
        "blocked_text": "buyer/login",
        "wait_message": (
            "\n🔑 INSTRUÇÃO — SHOPEE:\n"
            "  Se aparecer tela de login, faça login com sua conta.\n"
            "  Aguarde a página de busca carregar com produtos.\n"
            "  Quando ver os PRODUTOS listados, pressione ENTER.\n"
        ),
    },
}


def grab_session(site: str, headless: bool = False) -> bool:
    """
    Abre browser visível, aguarda interação humana e salva a sessão.

    Usa Google Chrome real (menos detectável que Chromium) quando disponível.
    NÃO navega automaticamente para a URL de busca — o usuário navega manualmente
    para evitar redirecionamentos inesperados do WAF (ex.: Akamai).

    Retorna True se sessão salva com sucesso.
    """
    if site not in SITE_CONFIG:
        print(f"ERRO: site '{site}' não suportado. Opções: {list(SITE_CONFIG.keys())}")
        return False

    cfg = SITE_CONFIG[site]
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_path = SESSIONS_DIR / f"{site}.json"

    print(f"\n{'='*60}")
    print(f"  Session Grabber — {site.upper()}")
    print(f"{'='*60}")
    print(cfg["wait_message"])

    # Patch JS completo anti-detecção de automação
    _STEALTH_JS = """
        // Remove navigator.webdriver (principal flag de detecção)
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        try { delete navigator.__proto__.webdriver; } catch(_) {}

        // Simula Chrome real (runtime, loadTimes, csi)
        window.chrome = {
            runtime: {
                onConnect: {addListener: () => {}},
                onMessage: {addListener: () => {}},
                id: undefined,
            },
            loadTimes: () => ({}),
            csi: () => ({}),
        };

        // Plugins não-vazios (browsers reais têm plugins)
        Object.defineProperty(navigator, 'plugins', {
            get: () => { const a = [1,2,3,4,5]; a.item = () => null; return a; }
        });

        // Idiomas brasileiros
        Object.defineProperty(navigator, 'languages', {
            get: () => ['pt-BR', 'pt', 'en-US', 'en']
        });

        // Permissions API correta para browsers reais
        const _origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : _origQuery(p);
    """

    with sync_playwright() as p:
        # Tenta Chrome real primeiro (instalado pelo usuário), fallback Chromium.
        # Chrome real tem TLS fingerprint diferente do Chromium — Shopee e Akamai
        # aceitam Chrome real mesmo com --disable-blink-features.
        browser = None
        used_channel = None
        for channel in ["chrome", "msedge", None]:
            try:
                browser = p.chromium.launch(
                    headless=headless,
                    channel=channel,
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--disable-dev-shm-usage",
                    ],
                )
                used_channel = channel or "chromium"
                break
            except Exception:
                continue

        if browser is None:
            print("  ERRO: Não foi possível iniciar nenhum browser.")
            return False

        print(f"  Browser: {used_channel}")

        context = browser.new_context(
            no_viewport=True,  # usa tamanho da janela real
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()

        # Navega apenas para a home — o usuário navega o resto manualmente.
        # Navegação automática para /busca pode causar redirecionamentos WAF.
        print(f"\n  → Abrindo: {cfg['url']}")
        try:
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"  AVISO: Timeout ao carregar home (normal): {e}")

        print(f"\n  ════════════════════════════════════════")
        print(f"  INSTRUÇÃO — navegue MANUALMENTE no browser:")
        print(f"  1. Aguarde a página carregar completamente")
        print(f"  2. Se aparecer desafio/CAPTCHA, resolva-o")
        print(f"  3. Navegue até: {cfg['check_url']}")
        print(f"  4. Verifique que produtos aparecem normalmente")
        print(f"  5. {cfg['success_hint']}")
        print(f"  ════════════════════════════════════════")

        try:
            input("\n  → Volte aqui e pressione ENTER para salvar a sessão: ")
        except KeyboardInterrupt:
            print("\n  Cancelado.")
            browser.close()
            return False

        cookies = context.cookies()
        current_url = page.url

        if not cookies:
            print("  ⚠️  Nenhum cookie capturado. O site pode não ter carregado.")
            browser.close()
            return False

        session_data = {
            "site": site,
            "saved_at": datetime.now().isoformat(),
            "url": current_url,
            "cookies": cookies,
        }

        session_path.write_text(json.dumps(session_data, indent=2, ensure_ascii=False))
        print(f"\n  ✅ Sessão salva: {session_path}")
        print(f"  Cookies: {len(cookies)}")
        print(f"  URL atual: {current_url}")
        browser.close()

    return True


def load_session(site: str) -> list:
    """
    Carrega cookies de sessão salva. Retorna [] se não existir ou expirada.
    Usado pelos scrapers quando disponível.
    """
    session_path = SESSIONS_DIR / f"{site}.json"
    if not session_path.exists():
        return []
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(data["saved_at"])
        age_hours = (datetime.now() - saved_at).total_seconds() / 3600
        if age_hours > 24:
            print(f"[session_grabber] Sessão de {site} expirada ({age_hours:.1f}h). "
                  f"Execute: python utils/session_grabber.py --site {site}")
            return []
        return data.get("cookies", [])
    except Exception as e:
        print(f"[session_grabber] Erro ao carregar sessão {site}: {e}")
        return []


def apply_session_to_context(site: str, context) -> bool:
    """
    Aplica cookies de sessão salva a um contexto Playwright existente.
    Retorna True se cookies aplicados com sucesso.
    """
    cookies = load_session(site)
    if not cookies:
        return False
    try:
        context.add_cookies(cookies)
        return True
    except Exception as e:
        print(f"[session_grabber] Erro ao aplicar cookies: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Salva sessão de browser para bypass de WAF/login",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--site", "-s",
        required=True,
        choices=list(SITE_CONFIG.keys()),
        help="Site para capturar sessão",
    )
    args = parser.parse_args()

    success = grab_session(args.site)
    if success:
        print(f"\n✅ Pronto! Execute o scraper normalmente.")
        print(f"   Os cookies serão carregados automaticamente.\n")
    else:
        print(f"\n❌ Sessão não salva. Tente novamente.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
