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

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from scrapers.casas_bahia import CasasBahiaScraper
from scrapers.local_browser import is_local_chrome_enabled

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
        # True quando um Chrome real (local logado ou CDP) subiu — é o
        # fingerprint que o Akamai aceita e a via que destrava a coleta.
        self._browser_ativo: bool = False
        # True quando `CasasBahiaScraper.__enter__` chegou a rodar — ou seja, um
        # browser (real OU próprio) pode ter sido lançado. O cleanup é gated por
        # ISTO, não por `_browser_ativo`: o `_launch` cai num Chromium headless
        # próprio quando o Chrome real não sobe, e esse browser precisa ser
        # fechado mesmo com `_browser_ativo=False`, senão vaza o handle
        # compartilhado do Playwright pelo resto da coleta (as 6 fontes).
        self._cb_entered: bool = False

    def _abrir(self) -> None:
        self._cb = CasasBahiaScraper(headless=self.headless)
        # Sobe o browser SÓ quando há um Chrome real disponível (perfil local
        # logado via RAC_LOCAL_CHROME, ou CDP). É o caminho que passa pelo
        # Akamai: o fetch same-origin carrega o _abck validado que o replay
        # curl_cffi de IP datacenter não tem — sem isso a API VTEX responde
        # text/html (desafio Akamai) e a plataforma sai ausente. Sem Chrome
        # real, NÃO lançamos um browser próprio (headless): mantém-se o caminho
        # leve curl_cffi, que é o comportamento herdado.
        tem_chrome_real = bool(
            is_local_chrome_enabled()
            or os.getenv("RAC_CDP_URL", "").strip()
            or os.getenv("MAGALU_CDP_URL", "").strip()
        )
        if not tem_chrome_real:
            return  # curl_cffi puro — nenhum browser é lançado.

        try:
            self._cb.__enter__()
            self._cb_entered = True
            self._browser_ativo = self._cb._real_browser_active
        except Exception as exc:
            logger.warning(
                f"[{self.nome}] Chrome real não subiu ({exc}) — seguindo "
                "pela API VTEX (curl_cffi)."
            )
            # `__enter__` pode ter lançado um browser antes de estourar; garante
            # o fechamento em vez de vazá-lo.
            self._fechar_cb()
            return

        # `__enter__` pode ter caído no `super()._launch()` (Chromium headless
        # próprio) porque o Chrome real não subiu. Na coleta de mais vendidos
        # esse browser não serve (o Akamai bloqueia o headless de IP datacenter)
        # e a promessa é seguir por curl_cffi — então fecha já, para não arrastar
        # um browser inútil e o handle do Playwright pelo resto da execução.
        if not self._browser_ativo:
            logger.warning(
                f"[{self.nome}] Chrome real indisponível — fechando o browser "
                "próprio e seguindo pela API VTEX (curl_cffi)."
            )
            self._fechar_cb()

    def _fechar_cb(self) -> None:
        """Fecha o scraper filho quando algum `__enter__` chegou a rodar.

        Fecha SÓ a aba dedicada no modo Chrome real (a janela é compartilhada e
        encerrada no fim do processo via ``close_local_browser``/atexit); no
        fallback headless, o ``_close`` do BaseScraper encerra o browser e
        devolve o handle do Playwright. É um no-op seguro se nada foi lançado.
        """
        if self._cb is None or not self._cb_entered:
            return
        try:
            self._cb.__exit__()
        except Exception as exc:
            logger.debug(f"[{self.nome}] Erro ao fechar o browser: {exc}")
        self._cb_entered = False
        self._browser_ativo = False

    def _fechar(self) -> None:
        self._fechar_cb()
        self._cb = None
        self._browser_ativo = False

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        # O endpoint carrega o parâmetro de ordenação — é ele que o portão de
        # validação inspeciona para provar que a lista é de vendas, não de
        # relevância. Registrado igual nos dois caminhos (browser e curl_cffi).
        params = {
            "query": _TERMO,
            "page": 1,
            "count": _ITENS_POR_PAGINA,
            "sort": _ORDENACAO,
            "hideUnavailableItems": "false",
        }
        self.registrar_endpoint(f"{_VTEX_IS_URL}?{urlencode(params)}")

        # Caminho preferido: Chrome real logado. O warm-up valida o _abck na
        # home e o fetch same-origin da IS (sort=orders_desc) passa pelo Akamai.
        if self._browser_ativo and self._cb._real_browser_active:
            itens = self._coletar_via_browser(paginas)
            if itens:
                return itens
            logger.warning(
                f"[{self.nome}] Chrome real não retornou itens — tentando a "
                "API VTEX (curl_cffi) como último recurso."
            )

        return self._coletar_via_api(paginas)

    def _coletar_via_browser(self, paginas: int) -> List[BestSellerItem]:
        """Coleta a lista de mais vendidos DENTRO do Chrome real logado.

        Aquece a home (o sensor.js do Akamai valida o ``_abck``) e faz o fetch
        same-origin da VTEX intelligent-search com ``sort=orders_desc`` — a
        MESMA ordenação que ``ordenacao=maisvendidos`` dispara na UI.
        """
        cb = self._cb
        # Home + emulação humana + espera do _abck (idempotente: 1x por execução).
        cb._warmup_cdp_session()

        itens: List[BestSellerItem] = []
        for pagina in range(1, max(1, paginas) + 1):
            produtos = cb.vtex_is_in_page(_TERMO, pagina, sort=_ORDENACAO)
            if not produtos:
                break
            itens.extend(self._parse(produtos, offset=len(itens)))
            if len(produtos) < _ITENS_POR_PAGINA:
                break
        return itens

    def _coletar_via_api(self, paginas: int) -> List[BestSellerItem]:
        """Replay da API VTEX via curl_cffi + warm-up de cookies Akamai."""
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
