"""
scripts/diagnose_ml.py — Diagnóstico standalone do Mercado Livre.

Roda sem dependências do projeto (exceto playwright e beautifulsoup4).
Abre o browser, navega para a busca do ML e reporta o que encontrou.

USO (Windows PowerShell):
    python scripts/diagnose_ml.py
    python scripts/diagnose_ml.py --keyword "ar condicionado split"
    python scripts/diagnose_ml.py --headless
    python scripts/diagnose_ml.py --html logs/ml_debug_0.html   # analisa HTML salvo
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# Verifica dependências
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERRO: Playwright não instalado.")
    print("      Execute: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERRO: BeautifulSoup não instalado.")
    print("      Execute: pip install beautifulsoup4")
    sys.exit(1)

# Extração de campos de insight (avaliação/patrocinado/seller) — usa a MESMA
# lógica do scraper de produção quando o repo está disponível, evitando drift
# entre diagnóstico e coleta. Sem o repo, degrada para título/preço apenas.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scrapers.mercado_livre import MLScraper  # noqa: E402

    HAVE_SCRAPER = True
except Exception as _exc:  # repo/deps indisponíveis → modo standalone
    HAVE_SCRAPER = False
    _SCRAPER_IMPORT_ERROR = _exc

# ---------------------------------------------------------------------------
# Seletores (espelha scrapers/mercado_livre.py)
# ---------------------------------------------------------------------------
ITEM_CONTAINER   = "li.ui-search-layout__item"
TITLE_CANDIDATES = [
    ".poly-component__title",
    "a.poly-component__title",
    "h2.poly-box",
    ".poly-component__title-wrapper",
    "h2.ui-search-item__title",
    ".ui-search-item__title",
]
PRICE_CONTAINER  = ".andes-money-amount:not(.andes-money-amount--previous)"
PRICE_FRACTION   = ".andes-money-amount__fraction"
PRICE_CENTS      = ".andes-money-amount__cents"

# ---------------------------------------------------------------------------
# Detecção de bloqueios
# ---------------------------------------------------------------------------
BLOCK_PATTERNS = {
    "login_gate":        "Para continuar, acesse sua conta",
    "account_verif":     "account-verification",
    "webdevice":         "webdevice",
    "captcha":           "captcha",
    "unusual_traffic":   "unusual traffic",
    "consent":           "consent.google",
}


def detect_block(html: str, url: str) -> str | None:
    lower_html = html.lower()
    lower_url  = url.lower()
    if "account-verification" in lower_url or "webdevice" in lower_url:
        return "login_gate (URL)"
    if "para continuar, acesse sua conta" in lower_html:
        return "login_gate (page content)"
    if "captcha" in lower_html:
        return "captcha"
    if "unusual traffic" in lower_html:
        return "unusual_traffic"
    return None


# ---------------------------------------------------------------------------
# Extração de preço
# ---------------------------------------------------------------------------
def extract_price(item) -> str:
    container = item.select_one(PRICE_CONTAINER)
    if not container:
        return "—"
    fraction = container.select_one(PRICE_FRACTION)
    cents    = container.select_one(PRICE_CENTS)
    if not fraction:
        return "—"
    int_part = re.sub(r"\D", "", fraction.get_text())
    dec_part = re.sub(r"\D", "", cents.get_text()) if cents else "00"
    return f"R$ {int_part},{dec_part.ljust(2,'0')[:2]}"


# ---------------------------------------------------------------------------
# Diagnóstico de HTML (local ou capturado)
# ---------------------------------------------------------------------------
def diagnose_html(html: str, url: str = "", label: str = "HTML") -> None:
    print(f"\n{'─'*60}")
    print(f"  Analisando: {label}")
    print(f"  URL: {url or '(local)'}")
    print(f"  Tamanho HTML: {len(html):,} bytes")

    block = detect_block(html, url)
    if block:
        print(f"\n  ⛔  BLOQUEIO DETECTADO: {block}")
        # Mostra trecho relevante do HTML
        idx = html.lower().find("para continuar")
        if idx == -1:
            idx = html.lower().find("account-verification")
        if idx >= 0:
            print(f"\n  Trecho do HTML (±200 chars):")
            print(f"  {html[max(0,idx-100):idx+200]!r}")
        return

    soup  = BeautifulSoup(html, "html.parser")
    items = soup.select(ITEM_CONTAINER)
    print(f"\n  Containers ({ITEM_CONTAINER}): {len(items)}")

    if not items:
        # Mostra as 30 classes mais comuns para diagnóstico
        all_classes = []
        for tag in soup.find_all(True):
            all_classes.extend(tag.get("class", []))
        from collections import Counter
        top = Counter(all_classes).most_common(30)
        print("\n  Top-30 classes no HTML (pode ajudar a atualizar seletores):")
        for cls, count in top:
            print(f"    .{cls:<50} ({count}×)")
        return

    # Extrai amostras
    titles_found = 0
    prices_found = 0
    print(f"\n  {'#':<4} {'Título':<45} {'Preço':<12} {'Patroc':<7} {'Aval':<6} {'Rev':<7} {'Tipo':<13} {'Seller'}")
    print(f"  {'─'*4} {'─'*45} {'─'*12} {'─'*7} {'─'*6} {'─'*7} {'─'*13} {'─'*15}")
    insight = {"sponsored": 0, "rating": 0, "reviews": 0, "official": 0, "seller": 0}
    sample = items[:20]
    for i, item in enumerate(sample):
        title = None
        for sel in TITLE_CANDIDATES:
            el = item.select_one(sel)
            if el and el.get_text(strip=True):
                title = el.get_text(strip=True)[:44]
                break
        price = extract_price(item)
        if title:
            titles_found += 1
        if price != "—":
            prices_found += 1

        spon = aval = rev = tipo = seller = "—"
        if HAVE_SCRAPER:
            is_spon = MLScraper._is_sponsored(item)
            rating, count = MLScraper._extract_reviews(item)
            seller_el = item.select_one(".poly-component__seller")
            seller_txt = seller_el.get_text(strip=True) if seller_el else None
            tipo_seller = MLScraper._detect_tipo_seller(item, seller_txt)
            if is_spon:
                insight["sponsored"] += 1
                spon = "SIM"
            if rating is not None:
                insight["rating"] += 1
                aval = f"{rating:.1f}"
            if count is not None:
                insight["reviews"] += 1
                rev = str(count)
            if tipo_seller == "Loja Oficial":
                insight["official"] += 1
            tipo = tipo_seller
            if seller_txt:
                insight["seller"] += 1
                seller = seller_txt[:15]

        title_display = title or "⚠️  sem título"
        print(f"  {i+1:<4} {title_display:<45} {price:<12} {spon:<7} {aval:<6} {rev:<7} {tipo:<13} {seller}")

    if len(items) > 20:
        print(f"  ... (+{len(items)-20} itens não exibidos)")

    n = len(sample)
    print(f"\n  RESUMO:")
    print(f"  • Itens totais:    {len(items)}")
    print(f"  • Títulos: {titles_found}/{n} nas primeiras {n} amostras")
    print(f"  • Preços:  {prices_found}/{n} nas primeiras {n} amostras")
    if HAVE_SCRAPER:
        print(f"  • Avaliação:    {insight['rating']}/{n}   (esperado >70% — itens novos não têm reviews)")
        print(f"  • Qtd reviews:  {insight['reviews']}/{n}")
        print(f"  • Seller:       {insight['seller']}/{n}")
        print(f"  • Loja Oficial: {insight['official']}/{n}")
        print(f"  • Patrocinado:  {insight['sponsored']}/{n}  (SERP típica tem 2-6 ads no top-20;")
        print(f"                  0 em TODAS as keywords = detecção quebrada ou sessão sem ads)")
    else:
        print(f"  ⚠️  Campos de insight não avaliados — import do scraper falhou:")
        print(f"      {_SCRAPER_IMPORT_ERROR}")
        print(f"      Rode a partir da raiz do repo com as dependências instaladas.")

    # Seletores de título — taxa individual
    print(f"\n  Taxa por seletor de título (primeiros {n} itens):")
    for sel in TITLE_CANDIDATES:
        hits = sum(1 for it in sample if it.select_one(sel))
        bar  = "█" * hits + "░" * (n - hits)
        print(f"    {sel:<45} {bar}  {hits}/{n}")

    if HAVE_SCRAPER:
        from scrapers.mercado_livre import _SELECTORS as _S
        print(f"\n  Taxa por seletor de insight (primeiros {n} itens):")
        field_sels = (
            [("rating", s) for s in _S["rating_candidates"]]
            + [("reviews", s) for s in _S["review_count_candidates"]]
            + [("reviews_blk", _S["reviews_block"])]
            + [("ads_chip", _S["ads_chip"])]
            + [("sponsored", _S["sponsored_label"])]
            + [("seller", s) for s in _S["seller_candidates"]]
        )
        for field, sel in field_sels:
            hits = sum(1 for it in sample if it.select_one(sel))
            bar  = "█" * hits + "░" * (n - hits)
            print(f"    [{field:<11}] {sel:<40} {bar}  {hits}/{n}")


# ---------------------------------------------------------------------------
# Captura de página via Playwright
# ---------------------------------------------------------------------------
def fetch_and_diagnose(keyword: str, headless: bool, save_html: bool) -> None:
    slug = quote_plus(keyword).replace("+", "-").lower()
    url  = f"https://lista.mercadolivre.com.br/{slug}"

    print(f"\n{'='*60}")
    print(f"  Diagnóstico Mercado Livre")
    print(f"  Keyword: {keyword!r}")
    print(f"  URL:     {url}")
    print(f"  Headless: {headless}")
    print(f"{'='*60}")

    STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        try { delete navigator.__proto__.webdriver; } catch(_) {}
        window.chrome = { runtime: { onConnect:{addListener:()=>{}}, onMessage:{addListener:()=>{}}, id:undefined },
                          loadTimes:()=>({}), csi:()=>({}) };
        Object.defineProperty(navigator, 'plugins',   {get: () => {const a=[1,2,3,4,5]; a.item=()=>null; return a;}});
        Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR','pt','en-US','en']});
    """

    with sync_playwright() as p:
        browser = None
        for channel in (["chrome", "msedge", None] if not headless else [None]):
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
                print(f"  Browser: {channel or 'chromium'}")
                break
            except Exception:
                continue

        if browser is None:
            print("ERRO: Nenhum browser disponível. Execute: python -m playwright install chromium")
            return

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()

        print(f"\n  Navegando... (aguarde até 30s)")
        t0 = time.time()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"  AVISO: Timeout no goto ({e}) — continuando com o que carregou")

        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        elapsed = time.time() - t0
        current_url = page.url
        html        = page.content()

        print(f"  Carregado em {elapsed:.1f}s")
        print(f"  URL final: {current_url}")

        if save_html:
            debug_path = Path("logs/ml_diag_debug.html")
            debug_path.parent.mkdir(exist_ok=True)
            debug_path.write_text(html, encoding="utf-8")
            print(f"  HTML salvo em: {debug_path}")

        browser.close()

    diagnose_html(html, current_url, label=keyword)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico do scraper Mercado Livre")
    parser.add_argument("--keyword", "-k", default="ar condicionado split",
                        help="Keyword para buscar (default: 'ar condicionado split')")
    parser.add_argument("--headless", action="store_true",
                        help="Rodar em modo headless (sem janela visível)")
    parser.add_argument("--html", metavar="FILE",
                        help="Analisa um arquivo HTML local (pula navegação)")
    parser.add_argument("--save-html", action="store_true",
                        help="Salva o HTML capturado em logs/ml_diag_debug.html")
    args = parser.parse_args()

    if args.html:
        path = Path(args.html)
        if not path.exists():
            print(f"ERRO: arquivo não encontrado: {args.html}")
            sys.exit(1)
        html = path.read_text(encoding="utf-8", errors="replace")
        diagnose_html(html, label=str(path))
    else:
        fetch_and_diagnose(
            keyword=args.keyword,
            headless=args.headless,
            save_html=args.save_html,
        )

    print()


if __name__ == "__main__":
    main()
