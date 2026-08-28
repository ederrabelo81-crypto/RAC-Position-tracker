"""
scrapers/google_shopping.py — Scraper do Google Shopping (google.com/search?tbm=shop).

Estratégia:
  - URL: https://www.google.com/search?tbm=shop&q={keyword}&gl=br&hl=pt-BR
  - Proteção: reCAPTCHA v3 / bot fingerprinting agressivo do Google.
    Com stealth e delays adequados, coletas esporádicas funcionam.
    Para volume alto (todas as keywords diariamente), use proxy residencial.
  - Paginação: parâmetro `&start={offset}` (10 resultados por página no shopping)

Manutenção de seletores — estrutura confirmada via debug HTML de 31/mar/2026:
  Container: div.rwVHAc (75 por página)
  Título:    primeiro <div> folha (sem filhos, sem classe) com 15-200 chars, sem R$
  Preço:     span.VbBaOe — texto "R$\xa02.184,05" (non-breaking space, não espaço normal)
  O Google Shopping rotaciona nomes de classe constantemente; guardamos fallbacks.
  Quando 0 itens: HTML salvo em logs/google_{causa}_p{n}_{kw}.html, onde
  {causa} é challenge|consent|sem_resultados|login|layout (ver
  _classify_zero_result) — o nome do arquivo já diz o diagnóstico.

ATUALIZAÇÃO 08/mai/2026: Múltiplas estratégias de extração de título + mais seletores CSS.
ATUALIZAÇÃO 09/mai/2026: Restaurada leaf-div como estratégia primária (COMMON_MISTAKES #2);
  aria-label rebaixado para último recurso; removido check hardcoded "Ar Condicionado".
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR, PAGE_TIMEOUT
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled
from utils.brands import extract_brand
from utils.text import parse_price, parse_rating, parse_review_count

# ---------------------------------------------------------------------------
# Seletores — confirmados em 31/mar/2026 + fallbacks legacy + NOVO 08/mai/2026
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de card de produto (confirmado: 75/página em 31/mar/2026)
    # Fallbacks para versões anteriores do layout + novos padrões CSS observados
    "item_candidates": [
        "div.Ez5pwe",                    # NOVO layout (mai/2026) ← PRIMÁRIO
        "div.rwVHAc",                    # layout anterior (31/mar/2026)
        "div.sh-dgr__gr-auto",           # layout anterior (estável)
        "div.sh-dlr__list-result",       # variação conhecida
        "[data-docid]",                  # layout muito anterior
        "div[data-item-id]",             # atributo data genérico
        "div[class*='shopping'][class*='result']",  # CSS class pattern matching
        "div.i0X6df",                    # classe observada
        "div.KZmu8e",                    # outra variação
        ".cu-container",                 # PLAs patrocinados
        ".pla-unit",                     # PLA alternativo
        "div.sh-np",                     # resultado estrutural mínimo
        "div[jsaction*='rcm']",          # padrão JavaScript action
    ],
    # Preço — confirmado: span.VbBaOe (31/mar/2026), fallbacks legacy
    "price_candidates": [
        ".lmQWe",               # NOVO layout (mai/2026) ← PRIMÁRIO
        ".VbBaOe",              # layout anterior (31/mar/2026)
        ".a8Pemb",
        ".OFFNJ",
        ".g9WsWb",
        ".kHxwFf span",
        ".P1usuSb",
        "[data-xpc='price']",
        "span[class*='price']",
        "span[class*='Price']",
    ],
    # Vendedor / loja — confirmado 01/mai/2026: div.UsGWMe (aria-label="De {seller}")
    "seller_candidates": [
        ".n7emVc",              # NOVO layout (mai/2026) ← PRIMÁRIO
        ".UsGWMe",              # layout anterior (01/mai/2026)
        ".Baoj6d",              # classe auxiliar observada junto a UsGWMe
        ".E5ocAb",
        ".aULzUe",
        ".IuHnof",
        ".NkoJne",
        ".vf0Yd",
        ".XrAfOe",
        ".LbUacb",
    ],
    # Rating
    "rating_candidates": [
        ".Rsc7Yb",
        ".yi40Hd",
        "[aria-label*='estrela']",
        "[aria-label*='star']",
        "[class*='rating']",
    ],
    # Badge de oferta
    "tag_candidates": [
        ".Ib8pOd",
        "[class*='badge']",
        "[class*='offer']",
        "[class*='tag']",
    ],
    # Contagem de avaliações (best-effort — nem sempre disponível no grid)
    "review_count_candidates": [
        "[aria-label*='avaliações']",
        "[aria-label*='reviews']",
        ".Rsc7Yb + span",
        ".QIrs8",
    ],
    # Detecção de CAPTCHA / bloqueio
    "captcha": "#captcha-form, #recaptcha, .g-recaptcha, #challenge-form",
    # Link do produto/loja dentro do card
    "url_candidates": [
        "a[href*='/shopping/product']",
        "a[href*='google.com/shopping']",
        "a[href^='http']",
        "a[href^='/url?']",
    ],
}

_RESULTS_PER_PAGE = 10

# Delay mínimo/máximo entre keywords do Google — maior que o global para
# reduzir probabilidade de reCAPTCHA em sequências rápidas.
# Aumentado de 12–22s para 25–45s (01/mai/2026) após CAPTCHA na 13ª keyword.
_MIN_DELAY_GOOGLE = 25.0
_MAX_DELAY_GOOGLE = 45.0

# Textos que indicam badge/promo, nunca nome de loja
_SELLER_BLACKLIST_RE = re.compile(
    r"desconto|frete|cupom|acima\s+de|compras|entrega|gr[áa]tis|\boff\b|^\d+\s*%|parcel",
    re.IGNORECASE,
)


class GoogleShoppingScraper(BaseScraper):
    """Scraper modular para Google Shopping Brasil."""

    platform_name = "Google Shopping"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self.captcha_hit: bool = False
        self._card_logged: bool = False
        self._cards_sem_seller: int = 0
        # Modo Chrome real local (RAC_LOCAL_CHROME) — perfil residencial logado
        # atacado via CDP. É o único caminho que o anti-bot do Google aceita em
        # volume: o launch próprio do BaseScraper sobe um Chromium sem sessão e
        # com fingerprint de automação, que leva reCAPTCHA de imediato.
        self._local_active: bool = False
        self._local_browser: Optional[Any] = None
        # Warm-up da home do Google roda uma vez por sessão (carrega consent +
        # cookies quentes antes de bater na SERP de shopping).
        self._google_warmed: bool = False

    # ------------------------------------------------------------------
    # Browser: Chrome real local via CDP (RAC_LOCAL_CHROME) ou launch próprio
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        """
        Preferência no notebook: Chrome real logado compartilhado
        (``RAC_LOCAL_CHROME``), atacado via CDP. O perfil residencial com
        conta Google e cookies de consentimento já aceitos é o antídoto ao
        reCAPTCHA — um Chromium recém-aberto pelo Playwright (o launch padrão
        do BaseScraper) é barrado logo na primeira keyword, mesmo com stealth.

        Sem o modo local, cai no launch padrão do BaseScraper (VM/Actions
        seguem no caminho antigo — sujeito a reCAPTCHA, sem regressão).
        """
        if is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None:
                page = lb.new_page()
                if page is not None:
                    self._local_browser = lb
                    self._context = lb.context
                    self._page = page
                    self._page.set_default_timeout(PAGE_TIMEOUT)
                    self._local_active = True
                    logger.info(
                        f"[{self.platform_name}] Chrome real local (perfil "
                        "compartilhado, logado) via CDP — fingerprint nativo "
                        "contra o reCAPTCHA do Google"
                    )
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome "
                "local não abriu — caindo no browser próprio (reCAPTCHA provável)"
            )

        super()._launch()

    def _close(self) -> None:
        # Modo local: fecha SÓ a aba — a janela é compartilhada e encerrada no
        # fim da coleta (close_local_browser).
        if self._local_active:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            self._page = None
            self._context = None
            self._local_browser = None
            self._local_active = False
            return
        super()._close()

    # ------------------------------------------------------------------
    # Warm-up + resolução manual de reCAPTCHA (modo Chrome real local)
    # ------------------------------------------------------------------

    def _warmup_google_home(self) -> None:
        """Aquece a home do Google uma vez antes da 1ª SERP de shopping.

        Um ``goto`` direto em ``/search?tbm=shop`` chega sem os cookies de
        sessão/consentimento que uma navegação humana normal carrega. Visitar a
        home primeiro (com um scroll leve) promove esses cookies para a rota de
        busca e reduz a chance de reCAPTCHA. Só faz sentido no Chrome real
        local — no launch próprio o perfil é descartável e a home não ajuda.
        """
        if self._google_warmed or not self._local_active:
            return
        try:
            self._page.goto(
                "https://www.google.com/?gl=br&hl=pt-BR",
                wait_until="domcontentloaded",
            )
            self._wait_for_network_idle()
            self._human_scroll(steps=3, step_px=250)
        except Exception as exc:
            # NÃO marca como aquecido: uma falha transitória aqui deve ser
            # re-tentada na próxima keyword, não silenciada para a sessão toda
            # (marcar antes do goto pulava o warm-up de todas as keywords
            # seguintes quando a 1ª navegação falhava).
            logger.debug(f"[{self.platform_name}] Warm-up da home falhou: {exc}")
            return
        self._google_warmed = True
        logger.info(f"[{self.platform_name}] Warm-up da home do Google concluído")

    @staticmethod
    def _manual_captcha_enabled() -> bool:
        """Resolução manual do reCAPTCHA (padrão: ligada no modo Chrome local).

        Desligue com ``RAC_GOOGLE_MANUAL_CAPTCHA=0`` para o comportamento antigo
        (abortar a sessão ao ver o reCAPTCHA).
        """
        return os.getenv("RAC_GOOGLE_MANUAL_CAPTCHA", "1").strip().lower() not in (
            "0", "false", "no", "nao", "off"
        )

    @staticmethod
    def _manual_captcha_timeout() -> float:
        """Segundos de espera pela resolução manual (env, padrão 180)."""
        raw = os.getenv("RAC_GOOGLE_MANUAL_CAPTCHA_TIMEOUT", "").strip()
        try:
            return float(raw) if raw else 180.0
        except ValueError:
            return 180.0

    def _await_manual_captcha_solution(self) -> bool:
        """Espera o usuário resolver o reCAPTCHA na janela do Chrome real.

        Só vale no modo Chrome local (a janela é visível e humana). Faz polling
        do seletor de captcha até ele sumir ou estourar o timeout.

        Returns:
            True se o captcha foi resolvido (página liberada), False se o tempo
            esgotou ou a resolução manual está desligada.
        """
        if not self._local_active or not self._manual_captcha_enabled():
            return False

        timeout = self._manual_captcha_timeout()
        logger.warning(
            f"[{self.platform_name}] reCAPTCHA na janela do Chrome real — "
            f"resolva-o manualmente ({int(timeout)}s de tolerância). "
            "Desligue com RAC_GOOGLE_MANUAL_CAPTCHA=0."
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3.0)
            if self._captcha_cleared():
                logger.success(
                    f"[{self.platform_name}] reCAPTCHA resolvido — retomando coleta"
                )
                return True
        logger.warning(
            f"[{self.platform_name}] reCAPTCHA não resolvido em {int(timeout)}s — "
            "abortando keywords restantes"
        )
        return False

    def _captcha_cleared(self) -> bool:
        """True quando a página de resultados foi liberada após o reCAPTCHA.

        Existência do elemento de captcha não é sinal confiável de conclusão:
        o Google às vezes mantém o widget resolvido no DOM, e checar só o
        seletor esperaria o timeout inteiro à toa. O sinal forte é a página de
        resultados ter voltado — cards presentes. Sem cards, aí sim o
        desaparecimento do formulário de captcha vale como liberação (SERP
        legítima e vazia, ou redirect de volta à busca).
        """
        try:
            html = self._page.content()
        except Exception:
            return False
        soup = BeautifulSoup(html, "html.parser")
        items, _ = self._detect_items(soup)
        if items:
            return True
        return soup.select_one(_SELECTORS["captcha"]) is None

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        offset  = (page - 1) * _RESULTS_PER_PAGE
        url = (
            f"https://www.google.com/search?tbm=shop"
            f"&q={encoded}&gl=br&hl=pt-BR"
        )
        if offset > 0:
            url += f"&start={offset}"
        return url

    # ------------------------------------------------------------------
    # Detecção de containers de produto
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        """Retorna (items, selector_usado) usando a cadeia de fallback."""
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 2:
                return items, sel
        return [], "nenhum"

    # ------------------------------------------------------------------
    # Extração de título — múltiplas estratégias (NOVO 08/mai/2026)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(item: Tag) -> Optional[str]:
        """
        Extrai o título do produto de um card Google Shopping.

        Estratégias em cascata (ordem importa — mais confiável primeiro):
        1. Leaf-div estrito: primeiro <div> folha (sem filhos, sem classe), 15-200 chars,
           sem R$/\\n/\\xa0 — estratégia documentada em COMMON_MISTAKES.md #2.
        1b. Leaf-div relaxado: <div> com classe mas sem filhos-div, mesmos filtros.
            Necessário desde mai/2026: Google encapsula título em <product-viewer-entrypoint>
            (Web Component), cujo conteúdo BeautifulSoup não navega via CSS selectors.
            Os divs de título (.gkQHve, .SsM98d) têm classe mas não têm filhos-div.
        2. h2/h3/h4 com texto longo (headings semânticos).
        3. Link <a href="/shopping"> — texto do link de produto.
        4. img[alt] — Google preenche alt apenas com nome do produto.
        5. Seletores CSS legacy de layouts anteriores.
        6. aria-label do container — ÚLTIMO RECURSO: Google concatena
           "nome + R$ preço + seller" no aria-label; _clean_title() remove artefatos
           mas pode falhar; só usar quando tudo acima falhar.
        """
        # Estratégia 1: leaf-div estrito — sem filhos, sem classe (COMMON_MISTAKES.md #2)
        for div in item.find_all("div"):
            if div.find():          # tem filhos → não é folha, pula
                continue
            if div.get("class"):    # tem classe → componente UI, pula
                continue
            text = div.get_text(strip=True)
            if (15 <= len(text) <= 200
                    and "R$" not in text
                    and "\n" not in text
                    and "\xa0" not in text):
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 1b: leaf-div relaxado — tem classe mas não tem filhos-div
        # Captura .gkQHve / .SsM98d dentro de <product-viewer-entrypoint> (mai/2026)
        for div in item.find_all("div"):
            if div.find("div"):     # tem filhos-div → container, pula
                continue
            if not div.get("class"):  # sem classe → já testado na estratégia 1
                continue
            text = div.get_text(strip=True)
            if (15 <= len(text) <= 200
                    and "R$" not in text
                    and "\n" not in text
                    and "\xa0" not in text):
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 2: h2/h3/h4 com texto longo (headings semânticos)
        for tag_name in ["h2", "h3", "h4"]:
            el = item.select_one(tag_name)
            if el:
                text = el.get_text(strip=True)
                if text and 15 <= len(text) <= 200 and "R$" not in text:
                    return GoogleShoppingScraper._clean_title(text)

        # Estratégia 3: <a> com href para /shopping/product
        link = item.select_one("a[href*='/shopping']")
        if not link:
            link = item.select_one("a[href*='product']")
        if link:
            text = link.get_text(strip=True)
            if text and 15 <= len(text) <= 200 and "R$" not in text:
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 4: img[alt] — Google preenche alt apenas com nome do produto
        img = item.select_one("img[alt]")
        if img:
            alt = img.get("alt", "").strip()
            if alt and len(alt) > 3 and "R$" not in alt:
                return GoogleShoppingScraper._clean_title(alt)

        # Estratégia 5: seletores CSS de layouts anteriores
        legacy_selectors = [
            ".gkQHve",              # confirmado 11/mai/2026 — dentro de product-viewer-entrypoint
            ".SsM98d",              # confirmado 11/mai/2026 — cópia do título no mesmo card
            ".Lq5OHe", ".tAxDx", ".rgHvZc", ".muB3Ob",
            ".sh-np__click-target", "h3.sh-np__click-target",
        ]
        for sel in legacy_selectors:
            el = item.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text and 15 <= len(text) <= 200:
                    return GoogleShoppingScraper._clean_title(text)

        # Estratégia 6: aria-label — ÚLTIMO RECURSO (ver COMMON_MISTAKES.md #2)
        # Google concatena nome+preço+seller; _clean_title() tenta remover artefatos.
        al = item.get("aria-label", "").strip()
        if al and 15 <= len(al) <= 300:
            cleaned = GoogleShoppingScraper._clean_title(al)
            if cleaned:
                return cleaned

        return None

    @staticmethod
    def _clean_title(raw: str) -> Optional[str]:
        """Remove artefatos de preço/rating que aparecem concatenados ao nome."""
        if not raw:
            return None

        # Remove "R$ ..." patterns no final
        raw = re.sub(r"\s*R\$\s+[\d.,]+.*$", "", raw, flags=re.IGNORECASE)
        # Remove "(X avaliações)" ou "(X reviews)"
        raw = re.sub(r"\s*\(\s*\d+\s*(avaliações|reviews?)\s*\)", "", raw, flags=re.IGNORECASE)
        # Remove "★ 4.5" patterns
        raw = re.sub(r"★\s+[\d.]+\s*$", "", raw)
        # Remove espaços extras
        raw = " ".join(raw.split())

        if 3 <= len(raw) <= 300:
            return raw
        return None

    # ------------------------------------------------------------------
    # Extração de preço
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(item: Tag) -> Optional[float]:
        """Extrai preço do card."""
        for price_sel in _SELECTORS["price_candidates"]:
            price_el = item.select_one(price_sel)
            if price_el:
                price_text = price_el.get_text(strip=True)
                return parse_price(price_text)
        return None

    # ------------------------------------------------------------------
    # Extração de URL do produto
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_url(item: Tag) -> Optional[str]:
        """
        Extrai a URL do card. O Google encapsula links de produto em redirects
        `/url?q=<destino>` — extraímos o destino real quando presente.
        """
        from urllib.parse import urlparse, parse_qs, unquote

        for sel in _SELECTORS["url_candidates"]:
            el = item.select_one(sel)
            href = el.get("href") if el else None
            if not href:
                continue
            href = href.strip()
            # Redirect do Google: /url?q=<url_real>&...
            if href.startswith("/url?"):
                qs = parse_qs(urlparse(href).query)
                real = (qs.get("q") or qs.get("url") or [None])[0]
                if real:
                    return unquote(real)
                continue
            if href.startswith("/"):
                href = f"https://www.google.com{href}"
            return href
        return None

    # ------------------------------------------------------------------
    # Extração de seller
    # ------------------------------------------------------------------

    _RE_NOT_SELLER = re.compile(
        r"^(de|por|a partir|em|até|novo|usado|anúncio)",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_seller(item: Tag) -> Optional[str]:
        """Extrai nome do vendedor/loja do card."""
        for seller_sel in _SELECTORS["seller_candidates"]:
            seller_el = item.select_one(seller_sel)
            if seller_el:
                seller = seller_el.get_text(strip=True)
                if (seller
                    and len(seller) > 2
                    and len(seller) < 100
                    and not GoogleShoppingScraper._RE_NOT_SELLER.search(seller)
                    and not _SELLER_BLACKLIST_RE.search(seller)):
                    logger.debug(f"[Google Shopping] seller [texto]: {seller}")
                    return seller

        return None

    @staticmethod
    def _classify_tipo_seller(
        seller: Optional[str], title: Optional[str]
    ) -> Optional[str]:
        """Classifica o merchant do card em loja oficial da marca vs varejista.

        Heurística: se o nome do merchant contém a marca do produto (extraída
        do título via lista BRANDS do config) → "Loja da Marca (1P)"; merchant
        presente sem relação com a marca → "Varejista". Sem merchant extraído
        não inventamos classificação (None).

        Args:
            seller: nome do merchant extraído do card (pode ser None).
            title:  título do produto (fonte da marca monitorada).

        Returns:
            "Loja da Marca (1P)", "Varejista" ou None quando não há merchant.
        """
        if not seller or not seller.strip():
            return None
        product_brand = extract_brand(title)
        if product_brand != "Desconhecida":
            if re.search(rf"\b{re.escape(product_brand)}\b", seller, re.IGNORECASE):
                return "Loja da Marca (1P)"
            return "Varejista"
        # Marca do produto desconhecida: merchant cujo nome é uma marca
        # monitorada (ex: "LG Brasil") ainda conta como loja da marca.
        if extract_brand(seller) != "Desconhecida":
            return "Loja da Marca (1P)"
        return "Varejista"

    _RE_MERCHANTS = re.compile(
        r"(\d+)\s*\+?\s*(?:lojas?|ofertas?|vendedores?)", re.IGNORECASE
    )

    @classmethod
    def _extract_merchants_count(cls, item: Tag) -> Optional[int]:
        """
        Nº de lojas/ofertas comparando o mesmo produto.

        Google exibe textos como "em 12 lojas", "Comparar preços de 8+ lojas"
        ou "+5 ofertas". Procura o primeiro padrão numérico no texto do card.
        """
        text = item.get_text(" ", strip=True)
        m = cls._RE_MERCHANTS.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug(
        self, html: str, page: int, keyword: str, causa: str = "debug"
    ) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"google_{causa}_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(f"[{self.platform_name}] HTML salvo para diagnóstico: {path}")
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Diagnóstico de resultado vazio
    # ------------------------------------------------------------------
    # O Google Shopping está com ZERO registros desde 23/05/2026 (79 dias em
    # 10/08). O remédio óbvio — mais seletores de fallback — já foi aplicado
    # em Mai/2026 (são 13 hoje) e não resolveu, então repetir a dose não é
    # diagnóstico. O que falta é saber QUAL das causas está em jogo: o log
    # dizia apenas "0 cards encontrados", que é compatível com todas elas.
    #
    # As causas são mutuamente excludentes e pedem ações opostas: um muro de
    # consentimento se resolve com cookie/perfil; um challenge se resolve com
    # IP residencial; um layout novo se resolve com parser. Nomear a causa no
    # log e no nome do dump é o que transforma "não funciona" em uma decisão.

    # Marcadores de ALTO SINAL — só aparecem numa página de controle, nunca
    # no rodapé de uma SERP normal. Um scan ingênuo por substring erraria aqui:
    # toda SERP do Google traz link de cookies/privacidade no rodapé, então
    # procurar "aceitar tudo" em qualquer lugar do HTML classificaria uma
    # página perfeitamente normal (o caso `layout`) como muro de consentimento
    # — a conclusão enganosa que esta classificação existe para evitar.
    _CAUSAS_ZERO = (
        ("challenge", (
            "unusual traffic", "tráfego incomum",
            "detected unusual", "our systems have detected",
        )),
        ("consent", (
            "before you continue to google", "antes de continuar no google",
        )),
        ("sem_resultados", (
            "did not match any", "não encontrou nenhum",
            "no results found", "nenhum documento",
        )),
        ("login", ("sign in to continue", "faça login para continuar")),
    )

    #: Marcadores que só valem quando aparecem no <title> ou numa URL de
    #: redirect — lá são estruturais, no corpo seriam só um link de rodapé.
    # Só marcadores específicos de cada tipo de página de controle. "erro" e
    # "entrar" já estiveram aqui e são genéricos demais: qualquer página de
    # erro comum do Google mandaria o operador atrás de proxy residencial, e
    # "entrar" aparece em botão de SERP normal. Marcador ambíguo devolve
    # `layout`, que manda olhar o HTML — barato — em vez de trocar de IP.
    _CAUSAS_POR_TITULO = (
        ("challenge", ("sorry", "unusual traffic", "tráfego incomum")),
        ("consent", ("before you continue", "antes de continuar", "consent")),
        ("login", ("sign in", "fazer login", "iniciar sessão")),
    )

    @staticmethod
    def _document_title(html: str) -> str:
        """Extrai o <title> em minúsculas, ou string vazia."""
        match = re.search(r"<title[^>]*>(.*?)</title>", html or "",
                          re.IGNORECASE | re.DOTALL)
        return " ".join(match.group(1).split()).lower() if match else ""

    def _classify_zero_result(self, html: str) -> str:
        """
        Nomeia a causa provável de uma página sem cards.

        Args:
            html: HTML bruto da resposta.

        Returns:
            Uma de: ``challenge`` (anti-bot/IP marcado), ``consent`` (muro de
            cookies), ``sem_resultados`` (busca legítima e vazia), ``login``,
            ou ``layout`` (a página veio normal — os seletores é que não
            reconhecem mais os cards).

        Note:
            ``layout`` é o único caso em que mexer no parser é o conserto certo;
            nos demais o parser está correto e o problema é de acesso. Por isso
            a classificação é conservadora: na dúvida devolve ``layout``, que
            manda olhar o HTML salvo, em vez de mandar trocar de IP à toa.
        """
        lowered = (html or "").lower()

        # 1) Título e redirect são estruturais: uma página de controle se
        #    identifica neles, uma SERP normal não.
        titulo = self._document_title(lowered)
        for causa, marcadores in self._CAUSAS_POR_TITULO:
            if titulo and any(marcador in titulo for marcador in marcadores):
                return causa
        if "/sorry/index" in lowered or "google.com/sorry" in lowered:
            return "challenge"

        # 2) Frases longas o bastante para não caberem num link de rodapé.
        for causa, marcadores in self._CAUSAS_ZERO:
            if any(marcador in lowered for marcador in marcadores):
                return causa

        return "layout"

    _ACAO_POR_CAUSA = {
        "challenge": (
            "IP marcado pelo anti-bot do Google (datacenter). Parser está OK — "
            "exige proxy residencial BR ou coleta pelo PC local."
        ),
        "consent": (
            "Muro de consentimento de cookies bloqueando a SERP. Parser está OK "
            "— exige perfil com o consentimento já aceito."
        ),
        "sem_resultados": (
            "O Google respondeu busca vazia para esta keyword — não é falha "
            "de coleta."
        ),
        "login": "Redirecionado para login do Google — exige perfil autenticado.",
        "layout": (
            "Página veio normal, mas nenhum dos 13 seletores reconheceu cards: "
            "layout do Google Shopping mudou. Aqui sim o conserto é no parser "
            "— use o HTML salvo para achar o novo container."
        ),
    }

    # ------------------------------------------------------------------
    # Parse principal
    # ------------------------------------------------------------------

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int = 1,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecta CAPTCHA — marca flag para abortar keywords restantes
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] reCAPTCHA detectado — abortando sessão. "
                "Use proxy residencial para coletas em escala."
            )
            self.captcha_hit = True
            return []  # registros já coletados anteriormente são preservados pelo caller

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} cards encontrados "
            f"(seletor: {sel_used})"
        )

        if not items:
            causa = self._classify_zero_result(html)
            logger.warning(
                f"[{self.platform_name}] 0 cards em '{keyword}' (pág {page}) — "
                f"causa provável: {causa.upper()}. "
                f"{self._ACAO_POR_CAUSA.get(causa, '')}"
            )
            self._dump_debug(html, page, keyword, causa=causa)
            return []

        # Log único do HTML do primeiro card para diagnóstico de seletores
        if not self._card_logged and items:
            self._card_logged = True
            try:
                logger.debug(
                    f"[{self.platform_name}] Primeiro card HTML (seletor: {sel_used}):\n"
                    f"{items[0].decode_contents()[:1200]}"
                )
            except Exception as _e:
                logger.debug(f"[{self.platform_name}] Erro ao logar card HTML: {_e}")

        records = []
        empty_title_count = 0
        empty_seller_count = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1

            title     = self._extract_title(item)
            price_raw = self._extract_price(item)
            seller    = self._extract_seller(item)
            if not seller:
                empty_seller_count += 1

            # Nº de lojas comparando o mesmo produto (insight de competição).
            # Google mostra "em N lojas" / "Comparar preços de N+ lojas" / "+N ofertas".
            qtd_sellers = self._extract_merchants_count(item)

            # Rating
            rating = None
            for rating_sel in _SELECTORS["rating_candidates"]:
                rel = item.select_one(rating_sel)
                if rel:
                    r = parse_rating(rel.get("aria-label") or rel.get_text())
                    if r:
                        rating = r
                        break

            # Tag de destaque
            tag = None
            for tag_sel in _SELECTORS["tag_candidates"]:
                tag_el = item.select_one(tag_sel)
                if tag_el:
                    tag = tag_el.get_text(strip=True)
                    break

            # Contagem de avaliações (best-effort)
            review_count = None
            for rev_sel in _SELECTORS["review_count_candidates"]:
                rev_el = item.select_one(rev_sel)
                if rev_el:
                    raw_rv = rev_el.get("aria-label") or rev_el.get_text(strip=True)
                    rc = parse_review_count(raw_rv)
                    if rc and rc > 5:        # descarta valores que seriam ratings (≤5)
                        review_count = rc
                        break

            if not title:
                empty_title_count += 1

            url_produto = self._extract_url(item)

            # Google Shopping: todos os resultados são PLAs (anúncios pagos).
            # Registramos como orgânicos em ordem de posição (sem posição patrocinada)
            # para manter consistência com os outros scrapers.
            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_general,
                position_sponsored=None,
                price_float=price_raw,  # price_raw já é float aqui
                seller=seller,
                buy_box_seller=seller,
                qtd_sellers=qtd_sellers,
                tipo_seller=self._classify_tipo_seller(seller, title),
                is_fulfillment=False,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
                url_produto=url_produto,
            ))

        if empty_title_count > 0:
            logger.info(
                f"[{self.platform_name}] {len(records) - empty_title_count}/"
                f"{len(records)} títulos extraídos "
                f"(seletor: {sel_used})"
            )
        if empty_title_count > len(records) // 2:
            logger.warning(
                f"[{self.platform_name}] {empty_title_count}/{len(records)} sem título — "
                "seletores possivelmente desatualizados. HTML salvo."
            )
            self._dump_debug(html, page, keyword)

        n_total = len(records)
        self._cards_sem_seller += empty_seller_count
        pct_sem = empty_seller_count * 100 // max(n_total, 1)
        log_fn = logger.warning if pct_sem > 30 else logger.info
        log_fn(
            f"[{self.platform_name}] '{keyword}' p{page} → {n_total} cards, "
            f"{empty_seller_count} sem seller ({pct_sem}%)"
            + (" — seletores podem estar desatualizados" if pct_sem > 30 else "")
        )

        return records

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=8, max=25),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword no Google Shopping por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        # Aquece a home do Google uma vez (só no Chrome real local) antes de
        # bater na SERP de shopping — carrega consent + cookies quentes.
        self._warmup_google_home()

        for page in range(1, page_limit + 1):
            if self.captcha_hit:
                break

            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                # Delay generoso — Google detecta padrões rápidos com alta precisão
                self._random_delay(min_s=_MIN_DELAY_GOOGLE, max_s=_MAX_DELAY_GOOGLE)
                self._human_scroll(steps=8, step_px=350)

                # captura screenshot da página de busca
                self._last_screenshot_busca = self.capture_screenshot(
                    identifier=f"{keyword}_p{page}", tipo="busca"
                )

                offset  = (page - 1) * _RESULTS_PER_PAGE
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page=page,
                    page_offset=offset,
                )

                # No Chrome real local a janela é visível: em vez de abortar a
                # sessão inteira ao ver o reCAPTCHA, dá ao usuário a chance de
                # resolvê-lo à mão e reparseia a mesma página.
                if self.captcha_hit and self._await_manual_captcha_solution():
                    self.captcha_hit = False
                    self._human_scroll(steps=8, step_px=350)
                    records = self._parse_results(
                        html=self._page.content(),
                        keyword=keyword,
                        keyword_category_map=keyword_category_map,
                        page=page,
                        page_offset=offset,
                    )

                all_records.extend(records)

                if not records or self.captcha_hit:
                    break

                if page < page_limit:
                    self._random_delay(min_s=_MIN_DELAY_GOOGLE, max_s=_MAX_DELAY_GOOGLE)

            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                raise

        self._log_search_result(keyword, len(all_records))
        return all_records
