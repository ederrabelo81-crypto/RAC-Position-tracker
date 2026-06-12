"""
scripts/refresh_sessions_cdp.py — Renova sessões (cookies) via Chrome CDP.

Automatiza a etapa que hoje é manual no session_grabber: em vez de o usuário
abrir um browser, navegar e apertar ENTER, este script conecta no Chrome REAL
já aberto com --remote-debugging-port (o mesmo da coleta Magalu CDP — ver
docs/cdp_magalu_collection.md), navega pelos sites em abas próprias e salva
as sessões em utils/sessions/{site}.json no MESMO formato do session_grabber.

Com isso, Shopee/Casas Bahia/Mercado Livre deixam de depender de captura
manual: agende este script antes de cada coleta (Task Scheduler) e os
scrapers encontram cookies sempre frescos.

Por que funciona:
  - O Chrome CDP usa o perfil copiado do usuário (logins Shopee/ML inclusos)
    e IP residencial — o anti-bot emite cookies válidos normalmente.
  - Navegar até a BUSCA (não só a home) força o site a renovar os cookies
    anti-fraude (Shopee SPC_*, Akamai _abck/bm_sz) na sessão do browser.
  - Os scrapers (curl_cffi) então reutilizam esses cookies via load_session().

⚠️  Requer `rebrowser-playwright` (como o magalu.py): o Playwright stock liga
    o domínio Runtime do CDP, que o sensor.js do Akamai detecta — flagaria o
    Chrome inteiro e derrubaria também a coleta Magalu.

USO:
    python scripts/refresh_sessions_cdp.py                          # shopee + casasbahia
    python scripts/refresh_sessions_cdp.py --sites shopee
    python scripts/refresh_sessions_cdp.py --sites shopee casasbahia mercadolivre
    python scripts/refresh_sessions_cdp.py --cdp-url http://localhost:9222

ENV:
    RAC_CDP_URL     URL do DevTools Protocol (fallback: MAGALU_CDP_URL;
                    padrão: http://localhost:9222)

Exit codes: 0 = todas as sessões salvas | 1 = parcial/falha | 2 = CDP inacessível
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Mesmo fix de runtime do scrapers/magalu.py — precisa ser setado ANTES do import
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

SESSIONS_DIR = _REPO_ROOT / "utils" / "sessions"

# Cookies críticos por site — espelha o diagnóstico do utils/session_grabber.py
CRITICAL_COOKIES = {
    "shopee":       ["csrftoken", "SPC_SI", "SPC_SEC_SI"],
    "casasbahia":   ["AKA_A2", "ak_bmsc", "bm_sz"],
    "mercadolivre": ["c_user_id", "MELI_SESSION", "_d2id"],
}

# Cookies de LOGIN (any-of) — os críticos acima saem até pra visitante
# anônimo. Sem login, a API v4 da Shopee responde 403 em TODA busca, mesmo
# com csrftoken/SPC_SI presentes (caso visto em produção Jun/2026).
LOGIN_COOKIES = {
    "shopee": ["SPC_EC", "SPC_ST", "SPC_U"],
}

SITES = {
    "shopee": {
        "home": "https://shopee.com.br/",
        # navegar pela busca renova SPC_* e os tokens anti-fraude da API v4
        "warm_urls": ["https://shopee.com.br/search?keyword=ar%20condicionado"],
        "cookie_urls": ["https://shopee.com.br/", "https://shopee.com.br/search"],
        "login_hint": (
            "Shopee exige conta logada no perfil CDP: abra o Chrome CDP, faça "
            "login na Shopee (1x) e rode este script de novo. ATENÇÃO: copiar o "
            "perfil (setup_cdp_profile.ps1) DESLOGA contas — re-logue após cada cópia."
        ),
        # Probe same-origin: chama a API de busca DENTRO da página e reporta o
        # status — prevê se a coleta vai passar (200) ou tomar 403 antes de
        # gastar uma execução inteira.
        "api_probe": (
            "/api/v4/search/search_items?by=relevancy&keyword=ar%20condicionado"
            "&limit=10&newest=0&order=desc&page_type=search"
            "&scenario=PAGE_GLOBAL_SEARCH&version=2"
        ),
    },
    "casasbahia": {
        "home": "https://www.casasbahia.com.br/",
        # Akamai emite _abck/bm_sz na home e endurece na rota de busca
        "warm_urls": ["https://www.casasbahia.com.br/busca?q=ar+condicionado"],
        "cookie_urls": ["https://www.casasbahia.com.br/"],
        "login_hint": None,
    },
    "mercadolivre": {
        "home": "https://www.mercadolivre.com.br/",
        "warm_urls": ["https://lista.mercadolivre.com.br/ar-condicionado-split"],
        "cookie_urls": [
            "https://www.mercadolivre.com.br/",
            "https://lista.mercadolivre.com.br/",
        ],
        "login_hint": "Login no ML evita o device-verification gate.",
    },
}


def _import_sync_playwright():
    """Prefere rebrowser-playwright (oculta Runtime.enable do sensor.js)."""
    try:
        from rebrowser_playwright.sync_api import sync_playwright
        return sync_playwright, "rebrowser-playwright"
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright, "playwright"
    except ImportError:
        return None, ""


def _human_pause(a: float = 1.2, b: float = 3.0) -> None:
    time.sleep(random.uniform(a, b))


def _harvest_site(context, site: str, cfg: dict) -> bool:
    """
    Navega pelo site numa aba nova do Chrome CDP e salva a sessão.

    Retorna True se a sessão foi salva com os cookies críticos presentes.
    """
    print(f"\n  ── {site} " + "─" * (50 - len(site)))
    page = context.new_page()
    final_url = cfg["home"]
    try:
        print(f"  → home: {cfg['home']}")
        page.goto(cfg["home"], wait_until="domcontentloaded", timeout=45_000)
        _human_pause()
        # scroll suave: dispara os XHRs/sensores que renovam cookies
        try:
            page.mouse.wheel(0, random.randint(400, 900))
        except Exception:
            pass
        _human_pause()

        for url in cfg["warm_urls"]:
            print(f"  → busca: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                final_url = page.url
                _human_pause(2.0, 4.0)
                try:
                    page.mouse.wheel(0, random.randint(800, 1600))
                except Exception:
                    pass
                _human_pause()
            except Exception as exc:
                print(f"  ⚠️  warm-up falhou ({exc}) — sigo com cookies da home")

        cookies = context.cookies(cfg["cookie_urls"])
        try:
            local_storage = page.evaluate(
                "() => Object.assign({}, window.localStorage)"
            )
            session_storage = page.evaluate(
                "() => Object.assign({}, window.sessionStorage)"
            )
        except Exception:
            local_storage, session_storage = {}, {}

        # UA do Chrome real que emitiu os cookies — os scrapers replicam o
        # MESMO UA no replay (anti-bots cruzam UA × cookies de sessão).
        user_agent = ""
        try:
            user_agent = page.evaluate("() => navigator.userAgent") or ""
        except Exception:
            pass

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "site":           site,
            "saved_at":       datetime.now().isoformat(),
            "url":            final_url,
            "userAgent":      user_agent,
            "cookies":        cookies,
            "localStorage":   local_storage,
            "sessionStorage": session_storage,
        }
        out = SESSIONS_DIR / f"{site}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        names = {c.get("name") for c in cookies}
        critical = CRITICAL_COOKIES.get(site, [])
        missing = [c for c in critical if c not in names]
        print(f"  💾 {out} — {len(cookies)} cookies")
        if missing:
            print(f"  ⚠️  cookies críticos ausentes: {missing}")
            if cfg.get("login_hint"):
                print(f"     {cfg['login_hint']}")
            return False
        print(f"  ✅ cookies críticos OK: {critical}")

        # Cookies de LOGIN (any-of) — sem eles a sessão é anônima e a API
        # de busca responde 403 mesmo com os críticos presentes.
        login_any = LOGIN_COOKIES.get(site, [])
        if login_any:
            present = [n for n in login_any if n in names]
            if not present:
                print(
                    f"  ❌ sessão ANÔNIMA — nenhum cookie de login "
                    f"({'/'.join(login_any)}). A coleta vai tomar 403."
                )
                if cfg.get("login_hint"):
                    print(f"     {cfg['login_hint']}")
                return False
            print(f"  ✅ sessão logada (cookies: {present})")

        # Probe da API de busca (same-origin, com os cookies da sessão) —
        # verdict antecipado: 200 = coleta deve passar; 403 = bloqueio.
        probe_path = cfg.get("api_probe")
        if probe_path:
            try:
                status = page.evaluate(
                    "url => fetch(url, {credentials: 'include'}).then(r => r.status)",
                    cfg["home"].rstrip("/") + probe_path,
                )
                if status == 200:
                    print("  ✅ probe da API de busca: HTTP 200 — sessão válida")
                else:
                    print(
                        f"  ⚠️  probe da API de busca: HTTP {status} — "
                        "coleta provavelmente bloqueada (re-logue/aguarde e rode de novo)"
                    )
                    return False
            except Exception as exc:
                print(f"  ⚠️  probe da API falhou ({exc}) — seguindo sem verdict")

        return True

    except Exception as exc:
        print(f"  ❌ {site}: {exc}")
        return False
    finally:
        try:
            page.close()  # fecha SÓ a aba criada — nunca o Chrome do usuário
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Renova sessões de scraping via Chrome CDP (sem intervenção humana)."
    )
    parser.add_argument(
        "--sites", nargs="+", choices=list(SITES.keys()),
        default=["shopee", "casasbahia"],
        help="Sites a renovar (padrão: shopee casasbahia)",
    )
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get(
            "RAC_CDP_URL",
            os.environ.get("MAGALU_CDP_URL", "http://localhost:9222"),
        ),
        help="URL do Chrome DevTools Protocol (padrão: http://localhost:9222)",
    )
    args = parser.parse_args()

    sync_playwright, flavor = _import_sync_playwright()
    if sync_playwright is None:
        print("ERRO: playwright não instalado. Execute: pip install rebrowser-playwright")
        return 2
    if flavor != "rebrowser-playwright":
        print(
            "⚠️  Playwright STOCK detectado — o Runtime.enable do CDP é visível "
            "pro sensor.js do Akamai e pode flagar o Chrome inteiro.\n"
            "    Instale o fork: pip install rebrowser-playwright"
        )

    print(f"{'='*60}")
    print(f"  Refresh de sessões via CDP — {args.cdp_url} ({flavor})")
    print(f"  Sites: {', '.join(args.sites)}")
    print(f"{'='*60}")

    results: dict = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(args.cdp_url, timeout=10_000)
        except Exception as exc:
            print(f"\n❌ Chrome CDP inacessível em {args.cdp_url}: {exc}")
            print("   Execute primeiro: scripts\\start_chrome_cdp.bat")
            return 2

        try:
            context = (
                browser.contexts[0] if browser.contexts else browser.new_context()
            )
            for site in args.sites:
                results[site] = _harvest_site(context, site, SITES[site])
        finally:
            # connect_over_cdp: close() só desconecta — o Chrome continua aberto
            browser.close()

    print(f"\n{'='*60}")
    ok = sum(1 for v in results.values() if v)
    for site, success in results.items():
        print(f"  {'✅' if success else '⚠️ '} {site}")
    print(f"  {ok}/{len(results)} sessões completas em {SESSIONS_DIR}")
    print(f"{'='*60}\n")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
