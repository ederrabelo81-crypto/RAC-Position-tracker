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

Seller / Buy Box: a SERP de mais vendidos NÃO imprime "Vendido por" — o PDP é
o único lugar onde a buy box existe. Por isso a coleta abre o PDP de cada item
do ranking, TODA execução: a lista é diária e o que se quer medir é a buy box
MUDANDO de dono, então `seller` é uma observação do dia, não um atributo fixo
do produto. Não há cache entre execuções (ver `_resolve_sellers_via_pdp`).
"""

import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Tag
from loguru import logger

from bestsellers.base import BestSellerSource, PlainBrowser
from bestsellers.models import BestSellerItem
from config import PAGE_TIMEOUT
from utils.text import parse_price, parse_rating, parse_review_count
from utils.amazon_sellers import extract_seller_from_pdp

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

# Resolução de seller via PDP. LIGADA por padrão: sem ela a série de mais
# vendidos sai sem buy box, e a buy box é o motivo de a Amazon estar no painel.
_ENV_PDP = "RAC_BESTSELLERS_AMAZON_PDP"
_ENV_PDP_BUDGET = "RAC_BESTSELLERS_AMAZON_PDP_BUDGET"
# PDPs seguidos sem "Vendido por" que caracterizam falha SISTÊMICA (layout novo
# ou bloqueio) em vez de produto fora do ar. Abortar aqui evita varrer a lista
# inteira abrindo página que não vai revelar nada.
_MAX_FALHAS_SEGUIDAS = 5

# Desafio de bot da Amazon — os mesmos marcadores que `scrapers/amazon.py` usa.
_SELECTORS_CAPTCHA = (
    "form[action='/errors/validateCaptcha'], #captcha, #captcha-form, "
    "#px-captcha, #distil_r_captcha"
)


def pdp_habilitado() -> bool:
    """
    True se a coleta deve abrir os PDPs para ler a buy box (default: sim).

    Só um "0"/"false"/"no"/"off" explícito desliga — variável ausente ou com
    lixo mantém a resolução ligada, porque o modo silenciosamente-sem-seller é
    o pior default possível para esta série.
    """
    return os.getenv(_ENV_PDP, "").strip().lower() not in ("0", "false", "no", "off")


def pdp_teto() -> Optional[int]:
    """
    Teto de PDPs por execução, ou None para resolver a lista inteira.

    Sem teto por padrão: o ranking já é limitado por `paginas × 30`, então um
    teto fixo só serviria para truncar a leitura no meio da lista.
    """
    bruto = os.getenv(_ENV_PDP_BUDGET, "").strip()
    return int(bruto) if bruto.isdigit() else None


class AmazonBestSellers(BestSellerSource):
    """Coletor do ranking de mais vendidos da Amazon Brasil."""

    key = "amazon"
    ranks_explicitos = True

    # A página serve 30 itens por vez; ?pg=2 traz de #31 a #50.
    _ITENS_POR_PAGINA = 30

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._browser: Optional[PlainBrowser] = None

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

        # A buy box só existe no PDP — resolvida depois que o ranking
        # inteiro está em mãos, para o log dar a cobertura real da lista.
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
    # Seller / Buy Box via PDP
    # ------------------------------------------------------------------

    def _resolve_sellers_via_pdp(self, itens: List[BestSellerItem]) -> None:
        """
        Preenche `seller` abrindo o PDP de CADA item do ranking, toda execução.

        A SERP de mais vendidos não imprime "Vendido por" — o PDP é o único
        lugar onde a buy box existe. Como a lista é diária e o objetivo é ver
        a buy box MUDAR de dono ao longo do tempo, a leitura é refeita a cada
        execução: `seller` aqui é uma OBSERVAÇÃO DO DIA, não um atributo fixo
        do produto.

        Por isso esta fonte NÃO lê o cache persistente de
        `utils/amazon_sellers` (`data/amazon_sellers.json`), que não tem
        validade e devolveria para sempre o vendedor da primeira resolução —
        do 2º dia em diante a série registraria um vencedor de buy box que
        ninguém observou. O único cache é POR EXECUÇÃO: o mesmo ASIN repetido
        em duas páginas custa um PDP só, e as duas linhas recebem a leitura
        daquela mesma execução, que é a mesma observação.

        Ligado por padrão. `RAC_BESTSELLERS_AMAZON_PDP=0` desliga (a coleta
        segue, com `seller` vazio). `RAC_BESTSELLERS_AMAZON_PDP_BUDGET` impõe
        um teto de PDPs por execução; sem ele, a lista inteira é resolvida —
        ela já é limitada por `paginas × 30`.

        Args:
            itens: lista de BestSellerItem; alterada no lugar.

        Note:
            Item que não resolve fica com `seller` vazio — campo sem dado fica
            vazio, nunca preenchido por herança de outro dia.
        """
        if not pdp_habilitado():
            logger.info(
                f"[{self.nome}] Resolução de seller via PDP desligada "
                f"({_ENV_PDP}=0) — a série do dia sai sem buy box."
            )
            return

        pendentes = [i for i in itens if not i.seller and i.sku_plataforma]
        if not pendentes:
            return

        teto = pdp_teto()
        orcamento = len(pendentes) if teto is None else min(teto, len(pendentes))
        if teto is not None and teto < len(pendentes):
            logger.warning(
                f"[{self.nome}] Teto de {teto} PDPs abaixo dos "
                f"{len(pendentes)} itens do ranking ({_ENV_PDP_BUDGET}) — "
                "os excedentes ficam sem seller."
            )

        # Cache POR EXECUÇÃO. Guarda também o insucesso (None) para não reabrir
        # o mesmo PDP morto quando o ASIN se repete entre páginas.
        lidos: Dict[str, Optional[str]] = {}
        abertos = 0
        falhas_seguidas = 0

        for item in pendentes:
            asin = item.sku_plataforma

            if asin in lidos:
                item.seller = lidos[asin]
                continue

            if abertos >= orcamento:
                continue

            abertos += 1
            nome = self._fetch_pdp_seller(asin)
            lidos[asin] = nome
            item.seller = nome

            if nome:
                falhas_seguidas = 0
                continue

            falhas_seguidas += 1
            if falhas_seguidas >= _MAX_FALHAS_SEGUIDAS:
                logger.error(
                    f"[{self.nome}] {falhas_seguidas} PDPs seguidos sem "
                    "'Vendido por' — a Amazon mudou o layout do bloco de "
                    "compra ou está barrando. Abortando a resolução; os "
                    f"{len(pendentes) - abertos} itens restantes ficam sem "
                    "seller (campo vazio, não herdado)."
                )
                break

        resolvidos = sum(1 for i in pendentes if i.seller)
        registrar = logger.success if resolvidos else logger.warning
        registrar(
            f"[{self.nome}] Buy box: {resolvidos}/{len(pendentes)} itens com "
            f"seller ({abertos} PDPs abertos, {len(lidos)} ASINs distintos)."
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
