"""
bestsellers/sources/casas_bahia.py — Lista "Mais Vendidos" da Casas Bahia.

Site:  /ar-condicionado/b?ordenacao=maisvendidos
API:   VTEX Intelligent Search com `sort=orders_desc` (é o que a UI envia por
       trás de `ordenacao=maisvendidos`)

A coleta usa a API porque o `sellers[]` do payload VTEX expõe o vencedor da
buy box (`sellerDefault`) e a competição de ofertas diretamente — o DOM não
traz nada disso. O warm-up de cookies Akamai (session curl_cffi persistente do
`CasasBahiaScraper`) é o que mantém o endpoint acessível de IP de datacenter.

ARMADILHA PRINCIPAL — CONTAMINAÇÃO. Em 10/08/2026, 6 de 20 itens da lista eram
umidificador, depurador de ar e afins, ocupando as posições #3, #4 e #5. Os
itens continuam na base (com `tipo=FORA_ESCOPO`) para que o portão de validação
consiga medir a contaminação; o KPI os ignora. Preços "de" desta fonte são
lixo (desconto fantasma de -79%) e por isso `preco_de` não é coletado aqui.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from scrapers.casas_bahia import CasasBahiaScraper

_VTEX_IS_URL = (
    "https://www.casasbahia.com.br/_v/api/intelligent-search"
    "/product_search/pt/pt-BR/search"
)
_TERMO = "ar condicionado"
_ORDENACAO = "orders_desc"
_ITENS_POR_PAGINA = 24
_TIMEOUT = 10


class CasasBahiaBestSellers(BestSellerSource):
    """Coletor do ranking de mais vendidos da Casas Bahia (VTEX IS)."""

    key = "casasbahia"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._cb: Optional[CasasBahiaScraper] = None

    def _abrir(self) -> None:
        # Não abre browser: só precisamos da session curl_cffi aquecida e dos
        # extratores de seller/rating do payload VTEX.
        self._cb = CasasBahiaScraper(headless=self.headless)

    def _fechar(self) -> None:
        self._cb = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        sessao = self._cb._get_warmed_session()
        if sessao is None:
            raise RuntimeError(
                "curl_cffi indisponível — sem ele o Akamai bloqueia a API VTEX. "
                "Instale: pip install curl-cffi>=0.6.0"
            )

        itens: List[BestSellerItem] = []
        for pagina in range(1, max(1, paginas) + 1):
            params = {
                "query": _TERMO,
                "page": pagina,
                "count": _ITENS_POR_PAGINA,
                "sort": _ORDENACAO,
                "hideUnavailableItems": "false",
            }
            self.registrar_endpoint(f"{_VTEX_IS_URL}?{urlencode(params)}")

            produtos = self._consultar(sessao, params, pagina)
            if not produtos:
                break
            itens.extend(self._parse(produtos, offset=len(itens)))
            if len(produtos) < _ITENS_POR_PAGINA:
                break
        return itens

    def _consultar(self, sessao, params: Dict[str, Any], pagina: int) -> List[Dict]:
        try:
            resposta = sessao.get(
                _VTEX_IS_URL,
                headers=self._cb._vtex_headers(),
                params=params,
                impersonate="chrome124",
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            raise RuntimeError(f"API VTEX inacessível: {exc}") from exc

        tipo = resposta.headers.get("content-type", "")
        if resposta.status_code != 200 or "application/json" not in tipo:
            # HTML aqui = página de desafio do Akamai, não lista curta.
            raise RuntimeError(
                f"API VTEX respondeu HTTP {resposta.status_code} (CT={tipo[:40]}) "
                "— bloqueio Akamai. Sem proxy BR, re-capture a sessão: "
                "python utils/session_grabber.py --site casasbahia"
            )

        dados = resposta.json()
        produtos = (
            dados.get("products")
            or (dados.get("productSearch") or {}).get("products")
            or []
        )
        if produtos:
            logger.info(
                f"[{self.nome}] VTEX {_ORDENACAO}: {len(produtos)} produtos (p{pagina})"
            )
        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, produtos: List[Dict], offset: int) -> List[BestSellerItem]:
        itens: List[BestSellerItem] = []
        for produto in produtos:
            titulo = (
                produto.get("productName")
                or produto.get("name")
                or produto.get("productTitle")
            )
            if not titulo:
                continue

            sellers = self._cb._extract_vtex_sellers(produto)
            rating, reviews = self._cb._extract_vtex_rating(produto)

            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                # Preço da oferta que vence a buy box — não o menor do array,
                # que pode ser de um seller sem estoque.
                preco=sellers.get("price_float"),
                rating=rating,
                reviews=reviews,
                seller=sellers.get("buy_box_seller"),
                sku_plataforma=str(
                    produto.get("productId") or produto.get("productReference") or ""
                ) or None,
                url_produto=self._url(produto),
            ))
        return itens

    @staticmethod
    def _url(produto: Dict[str, Any]) -> Optional[str]:
        link = produto.get("link") or produto.get("linkText")
        if not isinstance(link, str) or not link.strip():
            return None
        link = link.strip()
        if link.startswith("http"):
            return link
        if link.startswith("/"):
            return f"https://www.casasbahia.com.br{link}"
        return f"https://www.casasbahia.com.br/{link}/p"
