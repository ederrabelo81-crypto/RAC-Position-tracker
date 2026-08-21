"""
bestsellers/sources/vtex_generic.py — Coletor genérico para dealers VTEX.

A maioria dos dealers novos roda VTEX e expõe a MESMA API pública de catálogo:

    GET https://{host}/api/catalog_system/pub/products/search/{categoria}
        ?O={ordem}&_from={a}&_to={b}

O parâmetro `O` carrega a ordenação — `OrderByTopSaleDESC` (mais vendidos) ou
`OrderByScoreDESC` (relevância/score da loja). É ele que fica gravado no
`endpoint` e que o portão de validação inspeciona para provar a referência da
lista. Um único coletor dirigido por `config.ColetaSpec` cobre todos esses
dealers em vez de replicar um arquivo por loja.

LIMITAÇÃO conhecida: de IP de datacenter (VM Oracle / GitHub Actions) várias
lojas respondem bloqueio (Akamai/Cloudflare) e a lista sai vazia — a coleta
oficial roda do PC coletor (IP residencial). Uma loja bloqueada não derruba a
coleta das outras: ela apenas não produz linhas e é reportada como ausente.

REGRA DURA herdada: `rank` é ordinal; campo sem dado fica None. O buy box vem
do `sellerDefault` do primeiro item, não do menor preço do array (que pode ser
de um seller sem estoque).
"""

from typing import Any, Dict, List, Optional, Tuple, Type
from urllib.parse import quote, urlencode

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.config import ColetaSpec, coleta_de
from bestsellers.models import BestSellerItem

_TIMEOUT = 12
_MAX_PAGINAS = 4  # teto de segurança; a coleta padrão pede 1

# Headers de uma navegação real de catálogo VTEX. A API pública dispensa
# cookie de sessão na maioria das lojas; o UA/Accept alinhados reduzem o
# 403 preventivo de quem inspeciona header.
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _session():
    """Sessão HTTP com fingerprint TLS de Chrome (curl_cffi) quando houver.

    curl_cffi imita o handshake TLS real do Chrome — é o que passa nos anti-bot
    que barram o `requests` puro pelo JA3. Sem a lib, cai no `requests`, que
    funciona nas lojas sem essa checagem.

    Returns:
        (sessão, usa_impersonate). `usa_impersonate` diz se o `get` aceita o
        kwarg `impersonate` (só o cliente curl_cffi aceita).
    """
    try:
        from curl_cffi import requests as cffi_requests

        return cffi_requests.Session(), True
    except Exception:
        import requests

        return requests.Session(), False


class VtexBestSellers(BestSellerSource):
    """Coletor genérico da API de catálogo VTEX, dirigido por `ColetaSpec`."""

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._coleta: Optional[ColetaSpec] = coleta_de(self.key)
        self._sessao = None
        self._impersonate = False

    def _abrir(self) -> None:
        if self._coleta is None:
            raise RuntimeError(
                f"[{self.key}] sem ColetaSpec em bestsellers/config.COLETA — "
                "impossível montar a chamada VTEX."
            )
        self._sessao, self._impersonate = _session()

    def _fechar(self) -> None:
        if self._sessao is not None:
            try:
                self._sessao.close()
            except Exception:
                pass
            self._sessao = None

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    def _url(self, de: int, ate: int) -> str:
        """Monta a URL da API de catálogo com a janela de paginação."""
        coleta = self._coleta
        # O caminho da categoria vai cru (barras preservadas); só os segmentos
        # são escapados, para uma categoria com acento não quebrar a URL.
        caminho = "/".join(quote(seg) for seg in coleta.categoria.split("/") if seg)
        base = (
            f"https://{coleta.host}/api/catalog_system/pub/products/search/"
            f"{caminho}"
        )
        params = {"_from": de, "_to": ate}
        if coleta.ordem:
            params["O"] = coleta.ordem
        return f"{base}?{urlencode(params)}"

    # ------------------------------------------------------------------
    # Coleta
    # ------------------------------------------------------------------

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        por_pagina = max(1, self._coleta.paginacao)
        itens: List[BestSellerItem] = []

        for pagina in range(min(max(1, paginas), _MAX_PAGINAS)):
            de = pagina * por_pagina
            ate = de + por_pagina - 1
            url = self._url(de, ate)
            # O endpoint da PRIMEIRA página é a prova de ordenação — carrega o
            # parâmetro `O`. `registrar_endpoint` ignora as páginas seguintes.
            self.registrar_endpoint(url)

            produtos = self._consultar(url, pagina + 1)
            if not produtos:
                break
            itens.extend(self._parse(produtos, offset=len(itens)))
            if len(produtos) < por_pagina:
                break
        return itens

    def _consultar(self, url: str, pagina: int) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {"headers": _HEADERS, "timeout": _TIMEOUT}
        if self._impersonate:
            kwargs["impersonate"] = "chrome124"
        try:
            resposta = self._sessao.get(url, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"catálogo VTEX inacessível: {exc}") from exc

        # A busca de catálogo devolve 200 ou 206 (Partial Content da janela
        # `_from/_to`). Qualquer outra coisa é bloqueio ou categoria errada.
        if resposta.status_code not in (200, 206):
            raise RuntimeError(
                f"catálogo VTEX HTTP {resposta.status_code} — provável bloqueio "
                f"(IP de datacenter?) ou categoria inexistente: {self._coleta.categoria}"
            )
        tipo = resposta.headers.get("content-type", "")
        if "json" not in tipo.lower():
            raise RuntimeError(
                f"catálogo VTEX respondeu {tipo[:40]!r} em vez de JSON — página "
                "de desafio anti-bot."
            )
        try:
            dados = resposta.json()
        except Exception as exc:
            raise RuntimeError(f"JSON inválido do catálogo VTEX: {exc}") from exc

        produtos = dados if isinstance(dados, list) else dados.get("products", [])
        if produtos:
            logger.info(
                f"[{self.nome}] VTEX {self._coleta.ordem or 'padrão'}: "
                f"{len(produtos)} produtos (p{pagina})"
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
                or produto.get("productTitle")
                or produto.get("name")
            )
            if not titulo:
                continue
            preco, preco_de, seller = self._oferta(produto)
            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                preco=preco,
                preco_de=preco_de,
                seller=seller,
                sku_plataforma=str(
                    produto.get("productId")
                    or produto.get("productReference")
                    or ""
                ) or None,
                url_produto=self._url_produto(produto),
            ))
        return itens

    @staticmethod
    def _oferta(produto: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """Preço/preço-de/seller da oferta que vence a buy box (sellerDefault).

        Buy box VTEX = `sellerDefault` do primeiro SKU. O menor preço do array
        pode ser de um seller sem estoque — por isso não se pega o mínimo.
        """
        itens_sku = produto.get("items") or []
        for sku in itens_sku:
            sellers = sku.get("sellers") or []
            if not sellers:
                continue
            escolhido = next(
                (s for s in sellers if s.get("sellerDefault")), sellers[0]
            )
            oferta = escolhido.get("commertialOffer") or {}
            preco = oferta.get("Price")
            preco_de = oferta.get("ListPrice")
            seller = escolhido.get("sellerName") or escolhido.get("sellerId")
            preco = float(preco) if isinstance(preco, (int, float)) and preco > 0 else None
            # ListPrice só serve como "de" quando é de fato maior que o "por".
            preco_de = (
                float(preco_de)
                if isinstance(preco_de, (int, float)) and preco and preco_de > preco
                else None
            )
            return preco, preco_de, (str(seller) if seller else None)
        return None, None, None

    @staticmethod
    def _url_produto(produto: Dict[str, Any]) -> Optional[str]:
        link = produto.get("link") or produto.get("linkText")
        if not isinstance(link, str) or not link.strip():
            return None
        return link.strip()


def make_vtex_source(key: str) -> Type[VtexBestSellers]:
    """Cria a subclasse VTEX amarrada a uma chave de plataforma.

    O orquestrador instancia `SOURCE_CLASSES[chave](headless=...)` sem passar a
    chave, e a base lê `self.key` do atributo de classe — então cada dealer
    precisa de uma classe própria carregando sua `key`.
    """
    return type(f"Vtex_{key}", (VtexBestSellers,), {"key": key})
