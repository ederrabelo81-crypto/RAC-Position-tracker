"""
bestsellers/sources/html_generic.py — Coletor genérico de dealers não-VTEX.

Para as lojas que NÃO expõem uma API pública de catálogo (Ar Certo, Central Ar,
Ferreira Costa, Engage), a única via é navegar a URL já ordenada — a mesma que
um humano abre — e parsear a página. A URL pública de cada uma (registrada em
`config.SOURCES[...].url_publica`) já carrega o parâmetro de ordenação, então
não é preciso clicar no seletor de ordenação: o `goto` reproduz a lista.

Estratégia de extração, em ordem de confiabilidade:

  1. **JSON-LD `ItemList`** — o padrão estruturado que a maioria das categorias
     brasileiras emite. Traz nome, URL e, muitas vezes, preço por item, na
     ordem da lista. É a via preferida por ser estável a redesign de CSS.
  2. **JSON-LD `Product`** solto — quando a página lista Products sem envelope
     `ItemList`, a ordem de aparição é o ranking.

Sem nenhum dos dois, a coleta devolve vazio e a loja é reportada como ausente —
melhor uma lacuna explícita do que um ranking inventado de um parser de CSS
frágil. Estas fontes são de referência RELEVÂNCIA/DESTAQUE (proxy), nunca
medida de venda; sem sellers no HTML, o buy box fica vazio.
"""

import json
from typing import Any, Dict, List, Optional, Type

from bs4 import BeautifulSoup
from loguru import logger

from bestsellers.base import BestSellerSource, PlainBrowser
from bestsellers.models import BestSellerItem
from utils.text import parse_price


class HtmlBestSellers(BestSellerSource):
    """Coletor genérico por DOM/JSON-LD, para dealers sem API de catálogo."""

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._browser: Optional[PlainBrowser] = None

    def _abrir(self) -> None:
        self._browser = PlainBrowser(platform_name=self.nome, headless=self.headless)
        self._browser.__enter__()

    def _fechar(self) -> None:
        if self._browser is not None:
            try:
                self._browser.__exit__()
            except Exception:
                pass
            self._browser = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        url = self.url_coleta()
        self.registrar_endpoint(url)

        page = self._browser._page
        if page is None:
            raise RuntimeError("browser não abriu página")
        page.goto(url, wait_until="domcontentloaded")
        # Categoria costuma renderizar por scroll (lazy-load): rola algumas
        # vezes para materializar os cards antes de ler o HTML.
        try:
            self._browser._human_scroll(steps=6, step_px=800)
        except Exception:
            pass
        html = page.content()

        itens = self._parse_jsonld(html)
        if not itens:
            logger.warning(
                f"[{self.nome}] Nenhum JSON-LD de lista/produto encontrado — "
                "layout sem dado estruturado. Coleta vazia (loja reportada como "
                "ausente)."
            )
        return itens

    # ------------------------------------------------------------------
    # JSON-LD
    # ------------------------------------------------------------------

    def _parse_jsonld(self, html: str) -> List[BestSellerItem]:
        sopa = BeautifulSoup(html, "html.parser")
        produtos: List[Dict[str, Any]] = []

        for script in sopa.find_all("script", attrs={"type": "application/ld+json"}):
            bloco = self._carregar(script.string or script.get_text() or "")
            for no in self._nos(bloco):
                tipo = self._tipo(no)
                if tipo == "itemlist":
                    produtos.extend(self._itens_da_lista(no))
                elif tipo == "product":
                    produtos.append(no)

        itens: List[BestSellerItem] = []
        vistos = set()
        for produto in produtos:
            titulo = self._texto(produto.get("name"))
            if not titulo:
                continue
            # Dedup por título dentro do JSON-LD: o mesmo Product às vezes vem
            # no ItemList E solto na página, e contá-lo duas vezes inflaria o
            # ranking antes mesmo do dedup por SKU da base.
            chave = titulo.strip().lower()
            if chave in vistos:
                continue
            vistos.add(chave)
            itens.append(BestSellerItem(
                rank=len(itens) + 1,
                titulo=titulo,
                preco=self._preco(produto),
                sku_plataforma=self._sku(produto),
                url_produto=self._texto(produto.get("url")) or self._texto(produto.get("@id")),
            ))
        return itens

    @staticmethod
    def _carregar(bruto: str) -> Any:
        try:
            return json.loads(bruto)
        except Exception:
            return None

    @staticmethod
    def _nos(bloco: Any) -> List[Dict[str, Any]]:
        """Achata o JSON-LD em nós dict, cobrindo `@graph` e listas no topo."""
        if bloco is None:
            return []
        if isinstance(bloco, list):
            saida: List[Dict[str, Any]] = []
            for item in bloco:
                saida.extend(HtmlBestSellers._nos(item))
            return saida
        if isinstance(bloco, dict):
            if "@graph" in bloco and isinstance(bloco["@graph"], list):
                return HtmlBestSellers._nos(bloco["@graph"])
            return [bloco]
        return []

    @staticmethod
    def _tipo(no: Dict[str, Any]) -> str:
        tipo = no.get("@type") or ""
        if isinstance(tipo, list):
            tipo = tipo[0] if tipo else ""
        return str(tipo).strip().lower()

    @staticmethod
    def _itens_da_lista(no: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai os Products de um ItemList, na ordem de `position`."""
        elementos = no.get("itemListElement") or []
        if not isinstance(elementos, list):
            return []

        def _posicao(el: Dict[str, Any]) -> float:
            try:
                return float(el.get("position"))
            except (TypeError, ValueError):
                return float("inf")

        ordenados = sorted(
            (e for e in elementos if isinstance(e, dict)), key=_posicao
        )
        produtos: List[Dict[str, Any]] = []
        for elemento in ordenados:
            item = elemento.get("item")
            if isinstance(item, dict):
                produtos.append(item)
            elif elemento.get("name"):
                # Alguns emissores colocam name/url direto no ListItem.
                produtos.append(elemento)
        return produtos

    # ------------------------------------------------------------------
    # Campos
    # ------------------------------------------------------------------

    @staticmethod
    def _texto(valor: Any) -> Optional[str]:
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
        return None

    @staticmethod
    def _sku(produto: Dict[str, Any]) -> Optional[str]:
        for chave in ("sku", "mpn", "productID", "gtin13", "gtin"):
            valor = produto.get(chave)
            if valor:
                return str(valor)
        return None

    @classmethod
    def _preco(cls, produto: Dict[str, Any]) -> Optional[float]:
        """Preço do Product a partir de `offers` (dict, lista ou AggregateOffer)."""
        offers = produto.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if not isinstance(offers, dict):
            return None
        # AggregateOffer usa lowPrice; Offer usa price.
        for chave in ("price", "lowPrice"):
            if chave in offers and offers[chave] not in (None, ""):
                bruto = offers[chave]
                if isinstance(bruto, (int, float)):
                    return float(bruto) if bruto > 0 else None
                return parse_price(str(bruto))
        return None


def make_html_source(key: str) -> Type[HtmlBestSellers]:
    """Cria a subclasse HTML amarrada a uma chave de plataforma (ver VTEX)."""
    return type(f"Html_{key}", (HtmlBestSellers,), {"key": key})
