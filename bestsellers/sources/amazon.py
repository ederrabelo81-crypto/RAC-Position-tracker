"""
bestsellers/sources/amazon.py — Lista "Mais Vendidos" da Amazon Brasil.

URL: https://www.amazon.com.br/gp/bestsellers/home/17125373011/

Mecânica: ranking de VELOCIDADE de vendas, recalculado de hora em hora. É o
melhor sinal de demanda do conjunto monitorado — e o motivo de a rotina exigir
horário fixo: comparar sábado 22h com segunda 9h é ruído, não movimento.

Extração: a página é servida com o grid renderizado no HTML (não é SPA), então
um `goto` + parse basta. Três cadeias de seletores são tentadas em ordem
porque a Amazon reescreve as classes com hash (`_cDEzb_...`) a cada deploy —
qualquer seletor único vira coleta vazia silenciosa na primeira mudança.

A posição vem do badge "#7" impresso no card. Quando ele não existe (layouts
de teste A/B), cai para a ordem do DOM, que é a mesma coisa nesta página.

Seller / Buy Box (similar ao scraper principal): a SERP de mais vendidos NÃO
mostra "Vendido por" — só o PDP. Por isso este módulo pode navegar até cada
PDP para extrair o seller, com as mesmas proteções do scraper principal:
desligado por padrão (RAC_AMAZON_PDP_BUYBOX=1), com orçamento por execução
(RAC_AMAZON_PDP_BUDGET, default 40) e cache persistente em data/amazon_sellers.json.
"""

import re
from typing import List, Optional

from bs4 import BeautifulSoup, Tag
from loguru import logger

from bestsellers.base import BestSellerSource, PlainBrowser
from bestsellers.models import BestSellerItem
from config import PAGE_TIMEOUT
from utils.text import parse_price, parse_rating, parse_review_count
from utils.amazon_sellers import (
    AmazonSellerCache,
    extract_seller_from_pdp,
    is_amazon_self,
    pdp_budget,
    resolution_enabled,
)

_ITEM_SELECTORS = (
    "div#gridItemRoot",
    "div[id^='p13n-asin-index-']",
    "div.p13n-sc-uncoverable-faceout",
    "li[id^='gridItemRoot']",
    "div[data-asin]:not([data-asin=''])",
)

_TITLE_SELECTORS = (
    "div[class*='p13n-sc-css-line-clamp']",
    "div[class*='_p13n-sc-css-line-clamp']",
    "span.zg-text-center-align + div",
    "a.a-link-normal > span > div",
    "div.p13n-sc-truncate-desktop-type2",
    "div.p13n-sc-truncated",
)

_PRICE_SELECTORS = (
    "span[class*='p13n-sc-price']",
    "span.a-color-price",
    "span.a-price span.a-offscreen",
    "span.a-price",
)

_RANK_SELECTORS = ("span.zg-bdg-text", "span[class*='zg-bdg']")
_RATING_SELECTORS = ("span.a-icon-alt", "i[class*='a-star'] span")
_REVIEWS_SELECTORS = ("span.a-size-small", "a[title] span.a-size-small")

_RE_ASIN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")

# Desafio de bot da Amazon — os mesmos marcadores que `scrapers/amazon.py` usa.
_SELECTORS_CAPTCHA = (
    "form[action='/errors/validateCaptcha'], #captcha, #captcha-form, "
    "#px-captcha, #distil_r_captcha"
)


class AmazonBestSellers(BestSellerSource):
    """Coletor do ranking de mais vendidos da Amazon Brasil."""

    key = "amazon"
    ranks_explicitos = True

    # A página serve 30 itens por vez; ?pg=2 traz de #31 a #50.
    _ITENS_POR_PAGINA = 30

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._browser: Optional[PlainBrowser] = None
        # Cache e orçamento para resolução de seller via PDP
        self._seller_cache: Optional[AmazonSellerCache] = None
        self._pdp_budget_left: int = pdp_budget()

    def _abrir(self) -> None:
        self._browser = PlainBrowser(self.spec.nome, headless=self.headless)
        self._browser._launch()

    def _fechar(self) -> None:
        if self._browser is not None:
            self._browser._close()
            self._browser = None

    def _url_pagina(self, pagina: int) -> str:
        base = self.spec.url_publica
        return base if pagina <= 1 else f"{base}?pg={pagina}"

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        """Executa a coleta da lista de mais vendidos."""
        itens: List[BestSellerItem] = []
        for pagina in range(1, max(1, paginas) + 1):
            url = self._url_pagina(pagina)
            self.registrar_endpoint(url)
            html = self._baixar(url, pagina)
            if not html:
                break

            novos = self._parse(html, offset=(pagina - 1) * self._ITENS_POR_PAGINA)
            if not novos:
                self._browser._dump_debug_html(f"bestsellers_amazon_p{pagina}")
                logger.warning(
                    f"[{self.nome}] Página {pagina} carregou mas rendeu 0 itens "
                    "— HTML salvo em logs/ para inspeção dos seletores."
                )
                break
            itens.extend(novos)
            if pagina < paginas:
                self._browser._random_delay()

        # Resolve sellers via PDP após coletar todas as páginas
        self._resolve_sellers_via_pdp(itens)
        return itens

    def _baixar(self, url: str, pagina: int) -> Optional[str]:
        """Navega até a página do ranking e devolve o HTML, ou None."""
        page = self._browser._page
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            logger.warning(f"[{self.nome}] goto p{pagina} falhou: {exc}")
            return None

        # O grid é paginado por scroll infinito parcial: sem rolar, só os
        # ~15 primeiros cards têm título renderizado.
        try:
            page.wait_for_selector(", ".join(_ITEM_SELECTORS), timeout=15_000)
            self._browser._human_scroll(steps=12, step_px=600)
        except Exception:
            logger.warning(
                f"[{self.nome}] Grid de mais vendidos não renderizou em 15s "
                f"(p{pagina}) — pode ser CAPTCHA."
            )

        html = page.content()
        if self._tem_captcha(html):
            logger.error(
                f"[{self.nome}] CAPTCHA na página de mais vendidos. Sem proxy "
                "residencial BR isto é esperado em IP de datacenter."
            )
            return None
        return html

    @staticmethod
    def _tem_captcha(html: str) -> bool:
        """
        Detecta o desafio de bot da Amazon.

        Checagem por SELETOR, não por texto: `BaseScraper._check_waf_block`
        procura a substring "403" no HTML inteiro, e num grid de preços
        ("R$ 4.031,00", código de modelo) isso vira falso positivo que
        descarta uma coleta boa.
        """
        marcadores = _SELECTORS_CAPTCHA
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one(marcadores) is not None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _primeiro(item: Tag, seletores) -> Optional[Tag]:
        for sel in seletores:
            achado = item.select_one(sel)
            if achado is not None:
                return achado
        return None

    @classmethod
    def _detectar_itens(cls, soup: BeautifulSoup) -> List[Tag]:
        for sel in _ITEM_SELECTORS:
            achados = soup.select(sel)
            if len(achados) >= 5:  # 1-2 matches é banner, não o grid
                return achados
        return []

    def _parse(self, html: str, offset: int) -> List[BestSellerItem]:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._detectar_itens(soup)
        itens: List[BestSellerItem] = []

        for idx, card in enumerate(cards):
            titulo = self._extrair_titulo(card)
            if not titulo:
                continue

            rank_tag = self._primeiro(card, _RANK_SELECTORS)
            rank = None
            if rank_tag is not None:
                achado = re.search(r"\d+", rank_tag.get_text(strip=True))
                rank = int(achado.group()) if achado else None

            preco_tag = self._primeiro(card, _PRICE_SELECTORS)
            rating_tag = self._primeiro(card, _RATING_SELECTORS)

            itens.append(BestSellerItem(
                rank=rank if rank is not None else offset + idx + 1,
                titulo=titulo,
                preco=parse_price(preco_tag.get_text(" ", strip=True)) if preco_tag else None,
                rating=parse_rating(rating_tag.get_text(" ", strip=True)) if rating_tag else None,
                reviews=self._extrair_reviews(card),
                sku_plataforma=self._extrair_asin(card),
                url_produto=self._extrair_url(card),
                # A lista de mais vendidos é orgânica por construção: a Amazon
                # não vende posição nela. `False`, não `None`.
                patrocinado=False,
            ))
        return itens

    @classmethod
    def _extrair_titulo(cls, card: Tag) -> Optional[str]:
        tag = cls._primeiro(card, _TITLE_SELECTORS)
        if tag is not None:
            texto = tag.get_text(" ", strip=True)
            if texto:
                return texto
        # Último recurso: o alt da imagem do produto carrega o título completo.
        img = card.select_one("img[alt]")
        if img is not None:
            alt = (img.get("alt") or "").strip()
            if len(alt) > 15:
                return alt
        return None

    @staticmethod
    def _extrair_reviews(card: Tag) -> Optional[int]:
        """
        Contagem de avaliações do card.

        O texto vem como "1.226" (separador de milhar) e `parse_review_count`
        já trata isso. Só aceitamos elementos ligados ao bloco de estrelas —
        `span.a-size-small` solto também embala frases de frete.
        """
        for sel in ("a[href*='#customerReviews'] span", "span.a-size-small"):
            for tag in card.select(sel):
                texto = tag.get_text(" ", strip=True)
                if not texto or "estrela" in texto.lower():
                    continue
                valor = parse_review_count(texto)
                if valor:
                    return valor
        return None

    @staticmethod
    def _extrair_asin(card: Tag) -> Optional[str]:
        asin = (card.get("data-asin") or "").strip()
        if asin:
            return asin
        for link in card.select("a[href]"):
            achado = _RE_ASIN.search(link.get("href") or "")
            if achado:
                return achado.group(1)
        return None

    @staticmethod
    def _extrair_url(card: Tag) -> Optional[str]:
        link = card.select_one("a.a-link-normal[href], a[href*='/dp/']")
        if link is None:
            return None
        href = (link.get("href") or "").strip()
        if not href:
            return None
        return href if href.startswith("http") else f"https://www.amazon.com.br{href}"

    # ------------------------------------------------------------------
    # Seller resolution via PDP (opt-in, same as scrapers/amazon.py)
    # ------------------------------------------------------------------

    def _resolve_sellers_via_pdp(self, itens: List[BestSellerItem]) -> None:
        """
        Preenche o campo `seller` navegando ao PDP dos itens sem seller.

        Desligado por padrão: só roda com RAC_AMAZON_PDP_BUYBOX=1. A lista de
        mais vendidos não expõe "Vendido por" na SERP, então este é o único
        caminho — mas cada ASIN novo custa uma requisição na plataforma mais
        agressiva em anti-bot. Por isso o teto por execução
        (RAC_AMAZON_PDP_BUDGET, default 40): batendo o limite os demais itens
        seguem sem seller, em vez de arriscar derrubar a coleta.

        Args:
            itens: lista de BestSellerItem; alterada no lugar.

        Note:
            Falhas são silenciosas por item (o campo fica None) e o ASIN entra
            em quarentena no cache para não consumir o orçamento de novo.
        """
        if not resolution_enabled():
            return

        pendentes = [i for i in itens if not i.seller and i.sku_plataforma]
        if not pendentes:
            return

        if self._seller_cache is None:
            self._seller_cache = AmazonSellerCache()
        cache = self._seller_cache

        resolvidos_cache = 0
        resolvidos_pdp = 0
        for item in pendentes:
            asin = item.sku_plataforma

            conhecido = cache.get(asin)
            if conhecido:
                item.seller = conhecido
                resolvidos_cache += 1
                continue

            if self._pdp_budget_left <= 0 or not cache.should_retry(asin):
                continue

            self._pdp_budget_left -= 1
            nome = self._fetch_pdp_seller(asin)
            if nome:
                cache.put(asin, nome)
                item.seller = nome
                resolvidos_pdp += 1
            else:
                cache.mark_failed(asin, "PDP sem 'Vendido por'")

        # Salva sempre: mark_failed também suja o cache, e é justamente no
        # caso em que NADA resolveu (Amazon bloqueando todos os PDPs) que a
        # quarentena precisa chegar ao disco.
        cache.save()
        if resolvidos_cache or resolvidos_pdp:
            logger.info(
                f"[{self.nome}] Seller resolvido: "
                f"{resolvidos_cache} do cache + {resolvidos_pdp} via PDP "
                f"({len(pendentes)} pendentes · orçamento restante "
                f"{self._pdp_budget_left})"
            )

    def _fetch_pdp_seller(self, asin: str) -> Optional[str]:
        """
        Abre o PDP do ASIN e devolve o vendedor da buy box.

        Args:
            asin: identificador do produto na Amazon.

        Returns:
            Nome do vendedor, ou None se o PDP não revelou (challenge de bot,
            produto fora do ar, ou layout desconhecido).
        """
        if self._browser is None or self._browser._page is None:
            return None
        try:
            self._browser._page.goto(
                f"https://www.amazon.com.br/dp/{asin}",
                timeout=PAGE_TIMEOUT,
                wait_until="domcontentloaded",
            )
            self._browser._random_delay(min_s=2.0, max_s=5.0)
            return extract_seller_from_pdp(self._browser._page.content())
        except Exception as exc:
            logger.debug(
                f"[{self.nome}] PDP {asin} falhou: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    def _parse(self, html: str, offset: int) -> List[BestSellerItem]:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._detectar_itens(soup)
        itens: List[BestSellerItem] = []

        for idx, card in enumerate(cards):
            titulo = self._extrair_titulo(card)
            if not titulo:
                continue

            rank_tag = self._primeiro(card, _RANK_SELECTORS)
            rank = None
            if rank_tag is not None:
                achado = re.search(r"\d+", rank_tag.get_text(strip=True))
                rank = int(achado.group()) if achado else None

            preco_tag = self._primeiro(card, _PRICE_SELECTORS)
            rating_tag = self._primeiro(card, _RATING_SELECTORS)

            itens.append(BestSellerItem(
                rank=rank if rank is not None else offset + idx + 1,
                titulo=titulo,
                preco=parse_price(preco_tag.get_text(" ", strip=True)) if preco_tag else None,
                rating=parse_rating(rating_tag.get_text(" ", strip=True)) if rating_tag else None,
                reviews=self._extrair_reviews(card),
                sku_plataforma=self._extrair_asin(card),
                url_produto=self._extrair_url(card),
                # A lista de mais vendidos é orgânica por construção: a Amazon
                # não vende posição nela. `False`, não `None`.
                patrocinado=False,
            ))
        return itens
