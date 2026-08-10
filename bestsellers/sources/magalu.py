"""
bestsellers/sources/magalu.py — Lista "Mais Vendidos" do Magazine Luiza.

URL: /busca/ar+condicionado/?sortType=soldQuantity&sortOrientation=desc

Mecânica: quantidade ACUMULADA vendida no anúncio, ordem decrescente. Não é
velocidade — tem viés estrutural a favor de anúncio velho, e um SKU novo
excelente demora a subir. Além disso é BUSCA, não categoria: mudança no
algoritmo de busca move a lista sem nada ter mudado na demanda.

Reaproveita o `MagaluScraper` inteiro (browser persistente + curl_cffi com
impersonation, sessão Akamai validada e cacheada) e os TRÊS parsers que ele já
mantém sobre o mesmo HTML — `__NEXT_DATA__` → RSC do App Router → cards do
DOM. É o que impede que a próxima troca de layout da Magalu vire "0 produtos"
indistinguível de bloqueio.
"""

import random
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from scrapers.magalu import MagaluScraper

_KEYWORD = "ar condicionado"
_SORT_PARAMS = {"sortType": "soldQuantity", "sortOrientation": "desc"}
_TIMEOUT = 20


class MagaluBestSellers(BestSellerSource):
    """Coletor do ranking por quantidade vendida do Magazine Luiza."""

    key = "magazineluiza"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._mag: Optional[MagaluScraper] = None

    def _abrir(self) -> None:
        self._mag = MagaluScraper(headless=self.headless)
        self._mag.__enter__()

    def _fechar(self) -> None:
        if self._mag is not None:
            self._mag.__exit__()
            self._mag = None

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    def _url(self, pagina: int) -> str:
        base = "https://www.magazineluiza.com.br/busca"
        slug = quote_plus(_KEYWORD)
        params = [f"page={pagina}"] + [f"{k}={v}" for k, v in _SORT_PARAMS.items()]
        return f"{base}/{slug}/?{'&'.join(params)}"

    # ------------------------------------------------------------------
    # Coleta
    # ------------------------------------------------------------------

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        itens: List[BestSellerItem] = []
        rank = 0

        for pagina in range(1, max(1, paginas) + 1):
            url = self._url(pagina)
            self.registrar_endpoint(url)

            html = self._baixar(url, pagina)
            if not html:
                break

            produtos = self._extrair_produtos(html, pagina)
            if not produtos:
                self._mag._diagnose_empty_html(html, f"mais-vendidos p{pagina}", pagina)
                break

            for produto in produtos:
                titulo = self._titulo(produto)
                if not titulo:
                    continue
                rank += 1
                itens.append(BestSellerItem(
                    rank=rank,
                    titulo=titulo,
                    preco=self._mag._extract_price(produto),
                    rating=self._rating(produto),
                    reviews=self._reviews(produto),
                    seller=self._mag._extract_seller(produto),
                    sku_plataforma=self._sku(produto),
                    url_produto=self._mag._extract_url(produto),
                ))
        return itens

    def _baixar(self, url: str, pagina: int) -> Optional[str]:
        """
        HTML da página ordenada por quantidade vendida.

        Ordem: browser persistente (o que de fato passa pelo Akamai) →
        curl_cffi com a sessão validada. A ordem inversa gastaria ~45s por
        tentativa bloqueada antes de chegar ao caminho que funciona.
        """
        html = self._baixar_via_browser(url, pagina)
        if html:
            return html
        return self._baixar_via_cffi(url, pagina)

    def _baixar_via_browser(self, url: str, pagina: int) -> Optional[str]:
        mag = self._mag
        if not mag._browser_mode or not mag._revive_page():
            return None
        try:
            mag._pw_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=40_000,
                referer="https://www.magazineluiza.com.br/",
            )
        except Exception as exc:
            logger.warning(f"[{self.nome}] goto p{pagina} falhou: {exc}")
            return None

        # Espera a marcação de produto ANTES de rolar. A listagem hidrata do
        # lado do cliente; ler o HTML após só os ~2s do scroll transformava
        # hidratação lenta em "ranking vazio" — indistinguível de bloqueio.
        self._esperar_produtos(mag._pw_page)
        self._rolar(mag._pw_page)
        html = mag._safe_content()
        if not html:
            return None
        if mag._looks_like_login_wall(html, url):
            mag._flag_login_required(f"mais-vendidos p{pagina}")
            return None
        if not mag._has_product_markup(html):
            logger.warning(
                f"[{self.nome}] Página {pagina} sem marcação de produto "
                "(Akamai ou layout novo)."
            )
            return None
        return html

    @staticmethod
    def _esperar_produtos(page: Any) -> None:
        """
        Aguarda o primeiro card renderizar — mesmos seletores que
        `MagaluScraper._search_via_browser` usa.

        Timeout aqui não é erro: a página pode ter mudado de layout e ainda
        assim conter produtos que um dos três parsers reconhece.
        """
        try:
            page.wait_for_selector(
                'a[href*="/p/"], [data-testid="product-card"]', timeout=12_000
            )
        except Exception:
            logger.debug("[Magalu] Cards não apareceram em 12s — segue para o parse.")

    @staticmethod
    def _rolar(page: Any) -> None:
        """
        Rola a listagem para forçar o lazy-load dos cards.

        `BaseScraper._human_scroll` opera sobre `self._page`; a Magalu coleta
        na aba do browser persistente (`_pw_page`), então o scroll é feito
        aqui em vez de reaproveitado.
        """
        try:
            for _ in range(8):
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(random.randint(150, 400))
        except Exception as exc:
            logger.debug(f"[Magalu] Scroll da listagem falhou: {exc}")

    def _baixar_via_cffi(self, url: str, pagina: int) -> Optional[str]:
        mag = self._mag
        if mag._cffi_session is None or mag._cffi_disabled:
            return None
        if not mag._ensure_validated_session():
            return None
        try:
            resp = mag._cffi_session.get(
                url,
                headers=mag._mobile_headers(referer=mag._home_used),
                impersonate=mag._impersonate,
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(f"[{self.nome}] curl_cffi p{pagina} falhou: {exc}")
            return None
        if mag._is_blocked(resp.status_code, resp.text):
            logger.warning(
                f"[{self.nome}] curl_cffi p{pagina} bloqueado "
                f"(HTTP {resp.status_code}, {len(resp.text)} bytes)"
            )
            return None
        return resp.text

    def _extrair_produtos(self, html: str, pagina: int) -> List[Dict]:
        """Os 3 parsers do MagaluScraper, na ordem de riqueza do payload."""
        parsers = (
            ("__NEXT_DATA__", self._mag._products_from_next_data),
            ("RSC/App Router", self._mag._find_products_in_rsc),
            ("DOM", self._mag._parse_dom_cards),
        )
        for nome, parser in parsers:
            try:
                produtos = parser(html)
            except Exception as exc:
                logger.warning(f"[{self.nome}] Parser {nome} falhou (p{pagina}): {exc}")
                continue
            if produtos:
                logger.info(f"[{self.nome}] {len(produtos)} produtos via {nome} (p{pagina})")
                return produtos
        return []

    # ------------------------------------------------------------------
    # Campos do payload
    # ------------------------------------------------------------------

    @staticmethod
    def _titulo(produto: Dict[str, Any]) -> Optional[str]:
        for chave in ("title", "name", "fullTitle", "productTitle", "description"):
            valor = produto.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        return None

    @staticmethod
    def _sku(produto: Dict[str, Any]) -> Optional[str]:
        for chave in ("id", "sku", "productId", "variationId", "idSku"):
            valor = produto.get(chave)
            if isinstance(valor, (str, int)) and str(valor).strip():
                return str(valor).strip()
        return None

    @staticmethod
    def _rating(produto: Dict[str, Any]) -> Optional[float]:
        """
        Nota média. A Magalu entrega `rating` como número ou como
        `{value, count}` — e o card do DOM traz "4.7 (303)" num campo só,
        já separado pelo parser de cards.
        """
        for chave in ("rating", "ratingValue", "reviewScore", "averageRating"):
            valor = produto.get(chave)
            if isinstance(valor, dict):
                valor = valor.get("value") or valor.get("average") or valor.get("score")
            if isinstance(valor, (int, float)) and 0 < float(valor) <= 5:
                return round(float(valor), 2)
            if isinstance(valor, str):
                try:
                    numero = float(valor.replace(",", "."))
                except ValueError:
                    continue
                if 0 < numero <= 5:
                    return round(numero, 2)
        return None

    @staticmethod
    def _reviews(produto: Dict[str, Any]) -> Optional[int]:
        for chave in ("reviewCount", "ratingCount", "totalReviews", "reviewsCount"):
            valor = produto.get(chave)
            if isinstance(valor, bool):
                continue
            if isinstance(valor, int) and valor >= 0:
                return valor
            if isinstance(valor, str) and valor.strip().isdigit():
                return int(valor.strip())
        rating = produto.get("rating")
        if isinstance(rating, dict):
            for chave in ("count", "total", "reviewCount"):
                valor = rating.get(chave)
                if isinstance(valor, int):
                    return valor
        return None
