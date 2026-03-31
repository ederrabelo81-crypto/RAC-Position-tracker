"""
utils/discover_shopee_api.py — Descoberta manual da API da Shopee.

Execução (UMA VEZ, no Windows com browser visível):
    python utils/discover_shopee_api.py

O que faz:
    1. Abre Chrome visível (headless=False) para evitar detecção
    2. Captura TODAS as requisições e respostas de rede
    3. Filtra APIs de busca (api.shopee.com.br, /api/, search_items)
    4. Salva URLs, headers e amostras de response em logs/shopee_apis.json
    5. Imprime no terminal os endpoints mais relevantes

Após a execução:
    - Inspecione logs/shopee_apis.json
    - Procure por URLs como /api/v4/search/search_items
    - Copie headers relevantes (x-csrftoken, x-api-source, etc.)
    - Implemente _shopee_discovered_api() em scrapers/shopee.py com esses dados
"""

import json
from pathlib import Path
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERRO: Playwright não instalado. Execute: python -m pip install playwright")
    raise SystemExit(1)


_SEARCH_KEYWORD = "ar condicionado split"
_OUTPUT = Path("logs/shopee_apis.json")
_SCREENSHOT = Path("logs/shopee_discovery.png")

# Padrões de URL que indicam API de busca
_INTERESTING = [
    "api.shopee.com.br",
    "/api/v4/search",
    "/api/v2/search",
    "search_items",
    "search/search",
]


def discover() -> None:
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # VISÍVEL — evita detecção por bot fingerprint
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()

        # ── Captura de requests ──────────────────────────────────────────
        def on_request(request):
            url = request.url
            if any(p in url for p in _INTERESTING):
                entry = {
                    "type": "request",
                    "url": url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "timestamp": datetime.now().isoformat(),
                }
                captured.append(entry)
                print(f"\n[REQ] {request.method} {url[:100]}")
                # Mostrar headers Shopee-específicos
                for k, v in request.headers.items():
                    if k.lower().startswith("x-") or k.lower() in ("referer", "cookie"):
                        print(f"      {k}: {v[:80]}")

        def on_response(response):
            url = response.url
            if any(p in url for p in _INTERESTING):
                ct = response.headers.get("content-type", "")
                body_sample = ""
                if "json" in ct:
                    try:
                        body_sample = response.text()[:500]
                    except Exception:
                        body_sample = "<erro ao ler body>"
                entry = {
                    "type": "response",
                    "url": url,
                    "status": response.status,
                    "content_type": ct,
                    "body_sample": body_sample,
                    "timestamp": datetime.now().isoformat(),
                }
                captured.append(entry)
                print(f"\n[RES] HTTP {response.status} — {url[:100]}")
                if body_sample:
                    print(f"      body: {body_sample[:200]}")

        page.on("request", on_request)
        page.on("response", on_response)

        # ── Navegação ────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("  Shopee API Discovery")
        print("=" * 60)
        print(f"  Keyword: {_SEARCH_KEYWORD}")
        print("  Abrindo browser visível...")
        print("  (As APIs detectadas aparecerão abaixo em tempo real)")
        print("=" * 60 + "\n")

        url = f"https://shopee.com.br/search?keyword={_SEARCH_KEYWORD.replace(' ', '+')}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"[AVISO] Timeout/erro no goto: {e}")

        print("\n⏳ Aguardando 20s para a página carregar produtos...")
        print("   (Se aparecer CAPTCHA ou tela de login, resolva manualmente)")
        page.wait_for_timeout(20_000)

        # Screenshot para diagnóstico
        try:
            page.screenshot(path=str(_SCREENSHOT))
            print(f"\n📸 Screenshot: {_SCREENSHOT}")
        except Exception:
            pass

        # ── Relatório ────────────────────────────────────────────────────
        api_calls = [e for e in captured if e["type"] == "request"]
        api_responses = [e for e in captured if e["type"] == "response" and e.get("body_sample")]

        print(f"\n{'=' * 60}")
        print(f"  Resumo: {len(api_calls)} requests | {len(api_responses)} responses com JSON")
        print("=" * 60)

        if api_calls:
            print("\n🔗 ENDPOINTS CAPTURADOS (únicos):")
            seen = set()
            for e in api_calls:
                # Mostrar apenas parte antes de "?"
                base = e["url"].split("?")[0]
                if base not in seen:
                    seen.add(base)
                    print(f"   • {base}")
        else:
            print("\n⚠️  Nenhuma API capturada.")
            print("   Possíveis causas:")
            print("   1. Shopee bloqueou JavaScript (bot detection ativa)")
            print("   2. A busca não foi feita (produto não carregou)")
            print("   3. API usa domínio diferente dos padrões monitorados")
            print(f"\n   Verifique o screenshot: {_SCREENSHOT}")

        # Salvar tudo
        _OUTPUT.write_text(
            json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n💾 Dados salvos: {_OUTPUT} ({len(captured)} entradas)")
        print("\n📋 PRÓXIMOS PASSOS:")
        print("   1. Inspecione logs/shopee_apis.json")
        print("   2. Encontre o endpoint de busca (search_items ou similar)")
        print("   3. Copie os headers x-csrftoken, x-api-source, etc.")
        print("   4. Atualize _direct_api_search() em scrapers/shopee.py")

        input("\n✅ Pressione ENTER para fechar o browser...")
        browser.close()


if __name__ == "__main__":
    discover()
