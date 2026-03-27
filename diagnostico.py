"""
diagnostico.py — Ferramenta de diagnóstico de seletores CSS por plataforma.

Port do diagnostico.js (v5 Node.js) para Python/Playwright.

Navega para cada plataforma, tira screenshot, salva o HTML completo
e analisa automaticamente quais seletores CSS retornam resultados —
essencial para atualizar seletores quando o site mudar o DOM.

Uso:
    python diagnostico.py                        → Todas as plataformas ativas
    python diagnostico.py --platform ml          → Só Mercado Livre
    python diagnostico.py --platform ml amazon   → ML + Amazon
    python diagnostico.py --visible              → Abre o browser visível
    python diagnostico.py --keyword "midea 12000 btus"

Saída em ./diagnostico/:
    {plataforma}_screenshot.png   → Screenshot da página carregada
    {plataforma}_page.html        → HTML completo para inspeção manual
    {plataforma}_analysis.json    → Seletores detectados + contagens
"""

import json
import sys
import time
import argparse
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from loguru import logger
from playwright.sync_api import sync_playwright

from config import DIAGNOSTICO_DIR, USER_AGENTS

DEFAULT_KEYWORD = "ar condicionado 12000 btus inverter"

# ---------------------------------------------------------------------------
# Plataformas para diagnóstico
# ---------------------------------------------------------------------------
PLATFORMS = [
    {
        "id": "ml",
        "name": "Mercado Livre",
        "url_fn": lambda kw: f"https://lista.mercadolivre.com.br/{quote_plus(kw).replace('+','-').lower()}",
    },
    {
        "id": "amazon",
        "name": "Amazon Brasil",
        "url_fn": lambda kw: f"https://www.amazon.com.br/s?k={quote_plus(kw)}",
    },
    {
        "id": "magalu",
        "name": "Magazine Luiza",
        "url_fn": lambda kw: f"https://www.magazineluiza.com.br/busca/{quote_plus(kw)}/",
    },
    {
        "id": "shopee",
        "name": "Shopee",
        "url_fn": lambda kw: f"https://shopee.com.br/search?keyword={quote_plus(kw)}",
    },
    {
        "id": "leroy",
        "name": "Leroy Merlin",
        "url_fn": lambda kw: f"https://www.leroymerlin.com.br/busca?term={quote_plus(kw)}",
    },
]

# ---------------------------------------------------------------------------
# Seletores candidatos para teste automático
# ---------------------------------------------------------------------------
CANDIDATE_SELECTORS = [
    # Genéricos
    "article",
    ".product-card",
    ".product",
    ".search-result",
    "[data-testid*='product']",
    "[data-testid*='card']",
    # Mercado Livre — legado e Poly
    "li.ui-search-layout__item",
    ".ui-search-result",
    ".andes-card",
    ".poly-card",
    "ol.ui-search-layout li",
    # Amazon
    "div[data-component-type='s-search-result']",
    ".s-result-item",
    # Shopee
    ".shopee-search-item-result__item",
    "[data-sqe='item']",
    ".col-xs-2-4",
    # Magalu
    "[data-testid='product-card']",
    "li[data-testid]",
    "a[data-testid='product-card-container']",
    # Leroy Merlin / Fast Shop
    "[class*='ProductCard']",
    "[class*='product-card']",
    "[class*='productCard']",
]


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnóstico de seletores CSS por plataforma",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--platform", nargs="+",
        choices=[p["id"] for p in PLATFORMS],
        default=None,
        help="Plataformas a diagnosticar (padrão: todas)",
    )
    parser.add_argument(
        "--keyword", default=DEFAULT_KEYWORD,
        help=f"Keyword de teste (padrão: \"{DEFAULT_KEYWORD}\")",
    )
    parser.add_argument(
        "--visible", action="store_true", default=False,
        help="Exibir browser (útil para ver o que está sendo carregado)",
    )
    return parser.parse_args()


def _analyze_html(html: str) -> dict:
    """
    Analisa o HTML com BeautifulSoup e conta quantos elementos
    cada seletor candidato retorna.
    """
    soup = BeautifulSoup(html, "html.parser")
    container_counts = {}

    for sel in CANDIDATE_SELECTORS:
        try:
            count = len(soup.select(sel))
            if count > 0:
                container_counts[sel] = count
        except Exception:
            pass

    # Detecta elementos que contêm preço em formato brasileiro
    import re
    price_elements = []
    price_re = re.compile(r"R\$\s?[\d.]+[,.]?\d*")
    for el in soup.find_all(True):
        text = el.get_text(strip=True)
        if price_re.search(text) and len(text) < 30 and not el.find():
            classes = " ".join(el.get("class", []))[:80]
            entry = {"tag": el.name, "classes": classes, "sample": text[:30]}
            if entry not in price_elements:
                price_elements.append(entry)
        if len(price_elements) >= 10:
            break

    return {
        "container_candidates": container_counts,
        "price_elements": price_elements,
        "stats": {
            "total_elements": len(soup.find_all()),
            "links": len(soup.find_all("a")),
            "h2_count": len(soup.find_all("h2")),
            "title": soup.title.get_text() if soup.title else "",
        },
    }


def run_diagnostics(platforms: list, keyword: str, visible: bool) -> None:
    output_dir = Path(DIAGNOSTICO_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"\n{'═'*56}")
    logger.info("  DIAGNÓSTICO DE SELETORES")
    logger.info(f"{'═'*56}")
    logger.info(f"  Keyword: \"{keyword}\"")
    logger.info(f"  Browser visível: {'SIM' if visible else 'NÃO'}")
    logger.info(f"  Plataformas: {', '.join(p['name'] for p in platforms)}")
    logger.info(f"  Saída em: {output_dir.resolve()}")
    logger.info(f"{'═'*56}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not visible,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )

        for platform in platforms:
            url = platform["url_fn"](keyword)
            logger.info(f"\n▸ {platform['name']}")
            logger.info(f"  URL: {url}")

            context = browser.new_context(
                user_agent=USER_AGENTS[0],
                viewport={"width": 1366, "height": 768},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
            )
            # Stealth básico
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(4)

                # Scroll para triggerar lazy-load
                for _ in range(5):
                    page.evaluate("window.scrollBy(0, 800)")
                    time.sleep(0.5)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(2)

                # Screenshot
                screenshot_path = output_dir / f"{platform['id']}_screenshot.png"
                page.screenshot(path=str(screenshot_path), full_page=False)
                logger.info(f"  ✓ Screenshot: {screenshot_path}")

                # HTML completo
                html = page.content()
                html_path = output_dir / f"{platform['id']}_page.html"
                html_path.write_text(html, encoding="utf-8")
                logger.info(f"  ✓ HTML salvo: {html_path} ({len(html)//1024} KB)")

                # Análise de seletores
                analysis = _analyze_html(html)
                analysis_path = output_dir / f"{platform['id']}_analysis.json"
                analysis_path.write_text(
                    json.dumps(analysis, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"  ✓ Análise: {analysis_path}")

                # Exibe seletores com >= 3 elementos (candidatos a container)
                found = sorted(
                    [(s, c) for s, c in analysis["container_candidates"].items() if c >= 3],
                    key=lambda x: -x[1],
                )
                if found:
                    logger.info("  ✓ SELETORES DE CONTAINER ENCONTRADOS:")
                    for sel, count in found[:6]:
                        logger.info(f"      {sel}  →  {count} elementos")
                else:
                    logger.warning(
                        "  ✗ Nenhum seletor padrão encontrado — "
                        "inspecione o HTML manualmente"
                    )

                # Exibe elementos de preço
                if analysis["price_elements"]:
                    logger.info("  ✓ ELEMENTOS DE PREÇO:")
                    for p in analysis["price_elements"][:3]:
                        logger.info(f"      <{p['tag']} class='{p['classes']}'>  →  \"{p['sample']}\"")

                logger.info(f"  ℹ Página: {analysis['stats']['title']}")

            except Exception as exc:
                logger.error(f"  ✗ ERRO: {exc}")
                try:
                    err_path = output_dir / f"{platform['id']}_error.png"
                    page.screenshot(path=str(err_path))
                    logger.info(f"  ✓ Screenshot de erro: {err_path}")
                except Exception:
                    pass
            finally:
                context.close()
                time.sleep(3)

        browser.close()

    logger.info(f"\n{'═'*56}")
    logger.info("  DIAGNÓSTICO COMPLETO")
    logger.info(f"{'═'*56}")
    logger.info(f"  Arquivos em: {output_dir.resolve()}")
    logger.info("")
    logger.info("  PRÓXIMOS PASSOS:")
    logger.info("  1. Abra os screenshots para verificar se a página carregou")
    logger.info("  2. Veja os _analysis.json para seletores detectados")
    logger.info("  3. Abra os _page.html no browser (F12) para inspecionar")
    logger.info("  4. Atualize _SELECTORS no scraper correspondente")
    logger.info("")
    logger.info("  DICA: Use --visible para ver o browser em tempo real")
    logger.info(f"{'═'*56}\n")


if __name__ == "__main__":
    args = _build_args()

    if args.platform:
        selected = [p for p in PLATFORMS if p["id"] in args.platform]
    else:
        selected = PLATFORMS

    run_diagnostics(
        platforms=selected,
        keyword=args.keyword,
        visible=args.visible,
    )
