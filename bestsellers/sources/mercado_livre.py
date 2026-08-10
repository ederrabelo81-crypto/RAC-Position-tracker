"""
bestsellers/sources/mercado_livre.py — Lista "Mais Vendidos" do Mercado Livre.

URL: https://www.mercadolivre.com.br/mais-vendidos/MLB1646

Mecânica: ranking orgânico da categoria, 20 posições. Traz o seller — é a
única lista do conjunto que expõe a leitura de Buy Box diretamente.

ARMADILHA CENTRAL: o "+5mil vendidos" do card é ACUMULADO VITALÍCIO do
anúncio, não velocidade. Um anúncio de 2019 com +50mil pode estar na posição 9
vendendo pouco hoje. Por isso `base_vendidos` sai como 'acumulado' e o motor de
métricas se recusa a somá-lo com o campo mensal da Shopee.

Reaproveita `MLScraper` inteiro para entrar na página: injeção de sessão
salva, aquecimento da home, popup de CEP e — principalmente — a detecção de
login gate, que é o modo de falha nº 1 do ML em IP de datacenter. Sem isso a
página do gate seria parseada como "lista com 0 itens".
"""

import re
from typing import List, Optional

from bs4 import BeautifulSoup, Tag
from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.config import BASE_VENDIDOS_ACUMULADO
from bestsellers.models import BestSellerItem
from scrapers.mercado_livre import MLScraper
from utils.text import parse_rating, parse_review_count

# A página de mais vendidos reusa o card "Poly" da SERP, mas com wrappers
# próprios (`.poly-card` solto, sem `li.ui-search-layout__item`).
_ITEM_SELECTORS = (
    "div.poly-card",
    "li.ui-search-layout__item",
    "div.ui-search-result__wrapper",
    "li[class*='best-seller']",
    "ol li.andes-card",
)

_TITLE_SELECTORS = (
    ".poly-component__title",
    "a.poly-component__title",
    "h2.poly-box",
    "h2.ui-search-item__title",
    ".ui-search-item__title",
)

_SELLER_SELECTORS = (
    ".poly-component__seller",
    ".ui-search-official-store-label",
    ".ui-search-item__seller-description",
)

# "+5mil vendidos", "1234 vendidos", "+50 vendidos"
_RE_VENDIDOS = re.compile(r"\+?\s*([\d.,]+)\s*(mil|k)?\s*vendid", re.I)
_RE_MLB = re.compile(r"/(MLB-?\d+)")


class MercadoLivreBestSellers(BestSellerSource):
    """Coletor do ranking de mais vendidos do Mercado Livre."""

    key = "mercadolivre"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._ml: Optional[MLScraper] = None

    def _abrir(self) -> None:
        self._ml = MLScraper(headless=self.headless)
        self._ml._launch()
        # Aquece a home antes do goto: um acesso direto à rota chega ao antibot
        # antes do sensor.js validar a sessão e cai em gate com frequência.
        try:
            self._ml._warm_session()
        except Exception as exc:
            logger.debug(f"[{self.nome}] Warm-up da home falhou: {exc}")

    def _fechar(self) -> None:
        if self._ml is not None:
            self._ml._close()
            self._ml = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        url = self.spec.url_publica
        self.registrar_endpoint(url)
        page = self._ml._page

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise RuntimeError(f"goto da lista falhou: {exc}") from exc

        self._ml._dismiss_cep_popup()
        try:
            page.wait_for_selector(", ".join(_ITEM_SELECTORS), timeout=15_000)
        except Exception:
            logger.debug(f"[{self.nome}] Cards não apareceram em 15s — segue para o parse.")

        # Uma navegação transitória do ML no meio do scroll destrói o contexto
        # de execução e o `evaluate` estoura. Deixar isso subir marcaria a
        # plataforma como AUSENTE sem nem chegar à detecção de gate nem ao
        # parse — o estado atual da página costuma bastar.
        try:
            self._ml._human_scroll(steps=10, step_px=500)
        except Exception as exc:
            logger.warning(
                f"[{self.nome}] Scroll interrompido ({exc}) — seguindo com o "
                "estado atual da página."
            )

        html = page.content()

        # Gate confirmado = página do ML pedindo login/verificação. É uma
        # população diferente (nenhuma), não uma lista curta.
        if self._ml._is_login_gate(confirm=True):
            caminho = self._ml._dump_gate_evidence("mais-vendidos", 1)
            self._ml._log_gate_recovery_hint()
            raise RuntimeError(
                f"login gate do Mercado Livre na lista de mais vendidos "
                f"(evidência: {caminho or 'não salva'})"
            )

        itens = self._parse(html)
        if not itens:
            self._ml._dump_debug_html("bestsellers_ml")
        return itens

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _primeiro(card: Tag, seletores) -> Optional[Tag]:
        for sel in seletores:
            achado = card.select_one(sel)
            if achado is not None:
                return achado
        return None

    @classmethod
    def _detectar_cards(cls, soup: BeautifulSoup) -> List[Tag]:
        for sel in _ITEM_SELECTORS:
            achados = soup.select(sel)
            if len(achados) >= 5:
                return achados
        return []

    def _parse(self, html: str) -> List[BestSellerItem]:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._detectar_cards(soup)
        itens: List[BestSellerItem] = []

        for idx, card in enumerate(cards, start=1):
            titulo_tag = self._primeiro(card, _TITLE_SELECTORS)
            titulo = titulo_tag.get_text(" ", strip=True) if titulo_tag else None
            if not titulo:
                continue

            seller_tag = self._primeiro(card, _SELLER_SELECTORS)
            seller = seller_tag.get_text(" ", strip=True) if seller_tag else None
            if seller:
                # "Por Webcontinental" → "Webcontinental"
                seller = re.sub(r"^(por|by)\s+", "", seller, flags=re.I).strip()

            rating, reviews = self._extrair_reviews(card)
            vendidos = self._extrair_vendidos(card)

            itens.append(BestSellerItem(
                rank=idx,
                titulo=titulo,
                preco=MLScraper._extract_price(card),
                preco_de=self._extrair_preco_anterior(card),
                rating=rating,
                reviews=reviews,
                vendidos=vendidos,
                base_vendidos=BASE_VENDIDOS_ACUMULADO if vendidos is not None else None,
                seller=seller,
                sku_plataforma=self._extrair_mlb(card),
                url_produto=MLScraper._extract_url(card),
                patrocinado=MLScraper._is_sponsored(card),
            ))
        return itens

    @staticmethod
    def _extrair_preco_anterior(card: Tag) -> Optional[float]:
        """Preço riscado (`--previous`), quando o card mostra desconto."""
        bloco = card.select_one(".andes-money-amount--previous")
        if bloco is None:
            return None
        fracao = bloco.select_one(".andes-money-amount__fraction")
        if fracao is None:
            return None
        centavos = bloco.select_one(".andes-money-amount__cents")
        inteiro = re.sub(r"\D", "", fracao.get_text())
        decimal = re.sub(r"\D", "", centavos.get_text()) if centavos else "00"
        try:
            return float(f"{inteiro}.{decimal.ljust(2, '0')[:2]}")
        except ValueError:
            return None

    @staticmethod
    def _extrair_reviews(card: Tag) -> tuple:
        """
        (nota, contagem) do widget de avaliações.

        Delega ao `MLScraper`, que já carrega as três gerações de markup do
        card e o fallback estrutural (nó "4.8" seguido de nó "(1.234)").
        """
        try:
            rating, count = MLScraper._extract_reviews(card)
        except Exception:
            return None, None
        if rating is None:
            bloco = card.select_one(".poly-reviews__rating, .poly-component__review-compacted")
            if bloco is not None:
                rating = parse_rating(bloco.get_text(" ", strip=True))
        if count is None:
            total = card.select_one(".poly-reviews__total")
            if total is not None:
                count = parse_review_count(total.get_text(" ", strip=True))
        return rating, count

    @staticmethod
    def _extrair_vendidos(card: Tag) -> Optional[float]:
        """
        Volume declarado no card ("+5mil vendidos") como número.

        ACUMULADO vitalício — ver o aviso no topo do módulo. "+5mil" vira
        5000.0: é um piso declarado, o valor exato o ML não publica.
        """
        achado = _RE_VENDIDOS.search(card.get_text(" ", strip=True))
        if not achado:
            return None
        bruto = achado.group(1).replace(".", "").replace(",", ".")
        try:
            valor = float(bruto)
        except ValueError:
            return None
        if achado.group(2):  # "mil" / "k"
            valor *= 1000
        return valor

    @staticmethod
    def _extrair_mlb(card: Tag) -> Optional[str]:
        """ID MLB do anúncio — chave de pareamento entre dias."""
        for link in card.select("a[href]"):
            achado = _RE_MLB.search(link.get("href") or "")
            if achado:
                return achado.group(1).replace("-", "")
        return None
