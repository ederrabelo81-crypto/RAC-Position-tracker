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
    print(f"  URL: {cfg['check_url']}")
    print(cfg["wait_message"])

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )

        # Patch anti-detecção
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)

        page = context.new_page()

        print(f"  → Abrindo {cfg['url']}...")
        try:
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"  AVISO: Erro ao carregar home: {e}")

        print(f"  → Navegando para página de busca...")
        try:
            page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"  AVISO: Erro ao carregar busca: {e}")

        print(f"\n  ⏳ {cfg['success_hint']}")
        print("  Quando estiver pronto, volte aqui e pressione ENTER...")

        try:
            input("\n  → Pressione ENTER para salvar a sessão: ")
        except KeyboardInterrupt:
            print("\n  Cancelado pelo usuário.")
            browser.close()
            return False

        # Captura estado da sessão
        cookies = context.cookies()
        current_url = page.url
        html_sample = page.content()[:500]

        # Verifica se está na página certa
        blocked = cfg["blocked_text"] in current_url or cfg["blocked_text"] in html_sample
        if blocked:
            print(f"\n  ⚠️  AVISO: Parece que o site ainda está bloqueado.")
            print(f"  URL atual: {current_url}")
            confirm = input("  Salvar mesmo assim? (s/N): ").strip().lower()
            if confirm != "s":
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
        print(f"  Total de cookies: {len(cookies)}")
        print(f"  URL no momento do save: {current_url}")

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
