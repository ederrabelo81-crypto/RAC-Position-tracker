"""
scripts/diagnose_google_shopping.py — Diagnóstico autônomo do Google Shopping.

Abre o browser (visível por padrão), navega para o Google Shopping com uma
keyword de teste, salva o HTML completo e produz um relatório de seletores:
  - Quais seletores do _SELECTORS atual ainda funcionam
  - Quais classes CSS aparecem nos cards encontrados
  - Tipo de bloqueio detectado (CAPTCHA, consent page, unusual traffic)
  - Exemplo de título/preço/seller extraído por cada estratégia

Uso:
    python scripts/diagnose_google_shopping.py
    python scripts/diagnose_google_shopping.py --keyword "ar condicionado 9000"
    python scripts/diagnose_google_shopping.py --headless
    python scripts/diagnose_google_shopping.py --html logs/google_debug_p1_ar.html

Saída:
    logs/google_diag_<timestamp>.html   HTML bruto capturado
    logs/google_diag_<timestamp>.txt    Relatório de diagnóstico
"""

import argparse
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# Bootstrap — garante que imports do projeto funcionam de qualquer diretório
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bs4 import BeautifulSoup, Tag
from loguru import logger

# ---------------------------------------------------------------------------
# Seletores — espelho exato do google_shopping.py (manter sincronizado)
# ---------------------------------------------------------------------------
_ITEM_CANDIDATES = [
    "div.Ez5pwe",
    "div.rwVHAc",
    "div.sh-dgr__gr-auto",
    "div.sh-dlr__list-result",
    "[data-docid]",
    "div[data-item-id]",
    "div[class*='shopping'][class*='result']",
    "div.i0X6df",
    "div.KZmu8e",
    ".cu-container",
    ".pla-unit",
    "div.sh-np",
    "div[jsaction*='rcm']",
]

_PRICE_CANDIDATES = [
    ".lmQWe",
    ".VbBaOe",
    ".a8Pemb",
    ".OFFNJ",
    ".g9WsWb",
    ".kHxwFf span",
    ".P1usuSb",
    "[data-xpc='price']",
    "span[class*='price']",
    "span[class*='Price']",
]

_SELLER_CANDIDATES = [
    ".n7emVc",
    ".UsGWMe",
    ".Baoj6d",
    ".E5ocAb",
    ".aULzUe",
    ".IuHnof",
    ".NkoJne",
    ".vf0Yd",
    ".XrAfOe",
    ".LbUacb",
]

_CAPTCHA_SELECTOR = "#captcha-form, #recaptcha, .g-recaptcha, #challenge-form"

# Novos padrões de bloqueio não cobertos pelo captcha_selector original
_BLOCK_PATTERNS = [
    ("#consent-page",               "Consent / Cookie Gate"),
    ("form[action*='consent.google']", "Google Consent Form"),
    ("form[action*='sorry']",       "Google Sorry / Unusual Traffic"),
    ("#main > div > p",             "Página de erro genérica"),
]

_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    try { delete navigator.__proto__.webdriver; } catch(_) {}
    window.chrome = {
        runtime: { onConnect: {addListener: () => {}}, onMessage: {addListener: () => {}}, id: undefined },
        loadTimes: () => ({}), csi: () => ({}),
    };
    Object.defineProperty(navigator, 'plugins', {
        get: () => { const a = [1,2,3,4,5]; a.item = () => null; return a; }
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en']
    });
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : _origQuery(p);
"""

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# Funções de análise de HTML
# ---------------------------------------------------------------------------

def detect_block_type(soup: BeautifulSoup) -> Optional[str]:
    """Identifica o tipo de bloqueio presente no HTML, se houver."""
    # CAPTCHA original
    if soup.select_one(_CAPTCHA_SELECTOR):
        return "reCAPTCHA / Challenge Form"

    # Novos padrões de bloqueio
    for selector, label in _BLOCK_PATTERNS:
        if soup.select_one(selector):
            return label

    # Título da página como sinal de bloqueio
    title_el = soup.select_one("title")
    title = (title_el.get_text(strip=True) if title_el else "").lower()
    if any(kw in title for kw in ["antes de continuar", "unusual traffic", "captcha", "verify", "robot"]):
        return f"Bloqueio por título: '{title_el.get_text(strip=True)}'"

    return None


def find_items(soup: BeautifulSoup) -> tuple[list[Tag], str]:
    """Retorna (items, seletor_usado) usando a cadeia de fallback."""
    for sel in _ITEM_CANDIDATES:
        items = soup.select(sel)
        if len(items) >= 2:
            return items, sel
    return [], "nenhum"


def extract_title_leaf_div(item: Tag) -> Optional[str]:
    for div in item.find_all("div"):
        if div.find():
            continue
        if div.get("class"):
            continue
        text = div.get_text(strip=True)
        if 15 <= len(text) <= 200 and "R$" not in text and "\n" not in text and "\xa0" not in text:
            return text
    return None


def extract_price(item: Tag) -> tuple[Optional[str], Optional[str]]:
    """Retorna (texto_bruto, seletor_que_funcionou)."""
    for sel in _PRICE_CANDIDATES:
        el = item.select_one(sel)
        if el:
            return el.get_text(strip=True), sel
    return None, None


def extract_seller(item: Tag) -> tuple[Optional[str], Optional[str]]:
    """Retorna (seller, seletor_que_funcionou)."""
    for sel in _SELLER_CANDIDATES:
        el = item.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text and 2 < len(text) < 100:
                return text, sel
    return None, None


def collect_all_classes(items: list[Tag]) -> Counter:
    """Coleta todas as classes CSS que aparecem nos cards."""
    counter: Counter = Counter()
    for item in items:
        for tag in item.find_all(True):
            for cls in tag.get("class", []):
                counter[cls] += 1
    return counter


def analyse_html(html: str, keyword: str) -> dict:
    """Analisa o HTML e retorna dicionário com todos os achados."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {"keyword": keyword}

    # Título da página
    title_el = soup.select_one("title")
    result["page_title"] = title_el.get_text(strip=True) if title_el else "(sem título)"

    # Detecção de bloqueio
    result["block_type"] = detect_block_type(soup)

    # Tamanho do HTML (indicativo: < 50KB sugere bloqueio ou redirect)
    result["html_size_kb"] = round(len(html) / 1024, 1)

    # Detecção de items
    items, sel_used = find_items(soup)
    result["items_found"]  = len(items)
    result["selector_used"] = sel_used

    # Se não achou nenhum, testa TODOS os candidatos para reportar qual chegou mais perto
    result["selector_hits"] = {}
    for sel in _ITEM_CANDIDATES:
        n = len(soup.select(sel))
        if n > 0:
            result["selector_hits"][sel] = n

    # Análise de classes CSS nos cards encontrados
    if items:
        result["top_classes"] = collect_all_classes(items).most_common(30)

        # Importa a extração completa (cascata de 6 estratégias) do scraper de produção
        try:
            from scrapers.google_shopping import GoogleShoppingScraper
            _extract_title_full = GoogleShoppingScraper._extract_title
        except Exception:
            _extract_title_full = extract_title_leaf_div  # fallback se import falhar

        # Amostra dos primeiros 3 cards
        samples = []
        for i, item in enumerate(items[:3]):
            titulo_leafdiv = extract_title_leaf_div(item)
            titulo_full    = _extract_title_full(item)
            preco_txt, preco_sel = extract_price(item)
            seller_txt, seller_sel = extract_seller(item)
            aria = item.get("aria-label", "")
            samples.append({
                "idx":           i + 1,
                "titulo_leafdiv": titulo_leafdiv,
                "titulo_full":    titulo_full,
                "preco":          preco_txt,
                "preco_sel":      preco_sel,
                "seller":         seller_txt,
                "seller_sel":     seller_sel,
                "aria_label":     aria[:120] if aria else None,
                "html_snippet":   item.decode_contents()[:600],
            })
        result["samples"] = samples

        # Estatísticas de extração em todos os cards (leaf-div e cascata completa)
        titles_leafdiv = sum(1 for it in items if extract_title_leaf_div(it))
        titles_full    = sum(1 for it in items if _extract_title_full(it))
        prices_ok      = sum(1 for it in items if extract_price(it)[0])
        sellers_ok     = sum(1 for it in items if extract_seller(it)[0])
        result["extraction_stats"] = {
            "titles_leafdiv": f"{titles_leafdiv}/{len(items)} (só leaf-div)",
            "titles_full":    f"{titles_full}/{len(items)} (cascata completa — valor real do scraper)",
            "prices":         f"{prices_ok}/{len(items)}",
            "sellers":        f"{sellers_ok}/{len(items)}",
        }
    else:
        result["top_classes"] = []
        result["samples"] = []
        result["extraction_stats"] = {}

    return result


# ---------------------------------------------------------------------------
# Formatação do relatório de texto
# ---------------------------------------------------------------------------

def format_report(r: dict, html_path: Path, report_path: Path) -> str:
    sep  = "=" * 70
    sep2 = "-" * 70
    lines = [
        sep,
        f"  DIAGNÓSTICO GOOGLE SHOPPING — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        sep,
        f"  Keyword:        {r['keyword']}",
        f"  Título da pág:  {r['page_title']}",
        f"  Tamanho HTML:   {r['html_size_kb']} KB",
        f"  HTML salvo em:  {html_path}",
        f"  Relatório em:   {report_path}",
        sep2,
    ]

    if r["block_type"]:
        lines += [
            f"  ⛔  BLOQUEIO DETECTADO: {r['block_type']}",
            "",
            "  O scraper está retornando uma página de bloqueio/verificação.",
            "  Verifique o HTML salvo para ver o conteúdo exato.",
            sep2,
        ]
    else:
        lines.append("  ✅  Sem bloqueio detectado")
        lines.append(sep2)

    lines += [
        f"  Items encontrados:  {r['items_found']}",
        f"  Seletor ativo:      {r['selector_used']}",
    ]

    if r["selector_hits"]:
        lines.append("")
        lines.append("  Todos os seletores com match (≥1 elemento):")
        for sel, n in sorted(r["selector_hits"].items(), key=lambda x: -x[1]):
            marker = " ← PRIMÁRIO" if sel == r["selector_used"] else ""
            lines.append(f"    {n:3d}x  {sel}{marker}")
    else:
        lines.append("  Nenhum seletor retornou resultados.")

    lines.append(sep2)

    if r["extraction_stats"]:
        st = r["extraction_stats"]
        lines += [
            "  Taxa de extração por campo:",
            f"    Títulos (leaf-div):        {st['titles_leafdiv']}",
            f"    Títulos (cascata scraper): {st['titles_full']}",
            f"    Preços:                    {st['prices']}",
            f"    Sellers:                   {st['sellers']}",
            sep2,
        ]

    if r["top_classes"]:
        lines.append("  Classes CSS mais frequentes nos cards (top 30):")
        for cls, count in r["top_classes"]:
            marker = ""
            all_sels = _ITEM_CANDIDATES + _PRICE_CANDIDATES + _SELLER_CANDIDATES
            for s in all_sels:
                if cls in s:
                    marker = f"  ← usado em seletor atual"
                    break
            lines.append(f"    {count:3d}x  .{cls}{marker}")
        lines.append(sep2)

    if r["samples"]:
        lines.append("  Amostra dos primeiros 3 cards:")
        for s in r["samples"]:
            lines += [
                f"",
                f"  Card #{s['idx']}",
                f"    título (leaf-div):        {s['titulo_leafdiv']!r}",
                f"    título (cascata scraper): {s['titulo_full']!r}",
                f"    preço:      {s['preco']!r}  (via {s['preco_sel']})",
                f"    seller:     {s['seller']!r}  (via {s['seller_sel']})",
                f"    aria-label: {s['aria_label']!r}",
                f"    HTML (600c): {s['html_snippet'][:300]}...",
            ]
        lines.append(sep2)

    lines += [
        "  Como usar este relatório:",
        "  1. Se há bloqueio: veja o HTML salvo para identificar o tipo exato.",
        "  2. Se 'selector_used' é diferente de 'div.Ez5pwe': atualize _SELECTORS.",
        "  3. Se taxa de títulos < 70%: leaf-div falhou, verifique os HTML snippets.",
        "  4. Use as 'Classes CSS mais frequentes' para identificar novos seletores.",
        sep,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Captura via Playwright
# ---------------------------------------------------------------------------

def fetch_with_playwright(keyword: str, headless: bool) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright não instalado. Execute: pip install playwright && python -m playwright install chromium")
        return None

    url = f"https://www.google.com/search?tbm=shop&q={quote_plus(keyword)}&gl=br&hl=pt-BR"
    logger.info(f"Abrindo browser ({'headless' if headless else 'visível'}) → {url}")

    html = None
    pw = sync_playwright().start()
    browser = None

    for channel in ["chrome", "msedge", None]:
        try:
            browser = pw.chromium.launch(
                headless=headless,
                channel=channel,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                ],
            )
            logger.info(f"Browser iniciado: {channel or 'chromium'}")
            break
        except Exception as e:
            logger.debug(f"Canal {channel} falhou: {e}")
            continue

    if browser is None:
        logger.error("Nenhum browser disponível. Execute: python -m playwright install chromium")
        pw.stop()
        return None

    try:
        ua = random.choice(USER_AGENTS)
        ctx = browser.new_context(
            user_agent=ua,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        ctx.add_init_script(_STEALTH_JS)
        page = ctx.new_page()
        page.set_default_timeout(45_000)

        logger.info(f"Navegando para Google Shopping...")
        page.goto(url, wait_until="domcontentloaded")

        # Aguarda networkidle com tolerância
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        # Scroll humano suave
        for _ in range(6):
            page.mouse.wheel(0, random.randint(200, 400))
            time.sleep(random.uniform(0.3, 0.7))

        # Delay generoso para JS carregar cards
        delay = random.uniform(4.0, 7.0)
        logger.info(f"Aguardando {delay:.1f}s para JS carregar...")
        time.sleep(delay)

        html = page.content()
        logger.success(f"HTML capturado: {len(html):,} chars")

    except Exception as e:
        logger.error(f"Erro durante captura: {e}")
    finally:
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass

    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnóstico autônomo do Google Shopping — verifica seletores e tipo de bloqueio."
    )
    parser.add_argument(
        "--keyword", default="ar condicionado split inverter 12000",
        help="Keyword a buscar no Google Shopping (default: 'ar condicionado split inverter 12000')"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Rodar em modo headless (padrão: visível para diagnóstico)"
    )
    parser.add_argument(
        "--html", metavar="FILE",
        help="Usar HTML já salvo em disco em vez de abrir o browser (ex: logs/google_debug_p1_ar.html)"
    )
    args = parser.parse_args()

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path    = logs_dir / f"google_diag_{ts}.html"
    report_path  = logs_dir / f"google_diag_{ts}.txt"

    # ---- Obtém o HTML ----
    if args.html:
        src = Path(args.html)
        if not src.exists():
            logger.error(f"Arquivo não encontrado: {src}")
            sys.exit(1)
        html = src.read_text(encoding="utf-8", errors="replace")
        logger.info(f"HTML carregado do disco: {src} ({len(html):,} chars)")
        html_path = src  # aponta para o arquivo original no relatório
    else:
        html = fetch_with_playwright(keyword=args.keyword, headless=args.headless)
        if not html:
            logger.error("Falha ao capturar HTML. Abortando.")
            sys.exit(1)
        html_path.write_text(html, encoding="utf-8")
        logger.info(f"HTML salvo: {html_path}")

    # ---- Analisa ----
    logger.info("Analisando HTML...")
    result = analyse_html(html, keyword=args.keyword if not args.html else f"(arquivo: {args.html})")

    # ---- Gera relatório ----
    report = format_report(result, html_path, report_path)
    report_path.write_text(report, encoding="utf-8")

    # Imprime no terminal também
    print("\n" + report)

    logger.success(f"Diagnóstico concluído. Relatório: {report_path}")


if __name__ == "__main__":
    main()
