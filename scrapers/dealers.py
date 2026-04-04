"""
scrapers/dealers.py — Scraper para varejistas/dealers especializados em ar condicionado.

Diferente dos scrapers de marketplace, navega diretamente em páginas de
categoria (sem busca por keyword). Cada dealer é identificado pelo nome e
mapeado para uma URL fixa de catálogo.

Estratégia de extração (em ordem de prioridade):
  1. Extração via window.__RUNTIME__ / window.__STATE__ (VTEX)
  2. Parse DOM com cadeia de seletores — cobre VTEX IO, WooCommerce, genérico
  3. Debug HTML dump quando 0 itens encontrados

Paginação por tipo:
  - vtex        → ?page=2  (acrescenta ao final; mantém query string existente)
  - param_zero  → page=0 → page=1 → ... (troca o param na URL, 0-indexed)
  - woocommerce → /page/2/ (insere no path antes de query string)
  - query       → ?page=2 (genérico, igual vtex)
"""

import json
import re
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import LOGS_DIR, PLATFORM_TYPE
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

# ---------------------------------------------------------------------------
# Configuração por dealer
# ---------------------------------------------------------------------------

DEALER_CONFIGS: Dict[str, Dict] = {
    "Frigelar": {
        "url":        "https://www.frigelar.com.br/split-inverter/c",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "CentralAr": {
        "url":        "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "PoloAr": {
        "url": (
            "https://www.poloar.com.br/ar-condicionado/inverter"
            "?category-1=ar-condicionado&category-2=inverter&fuzzy=0&operator=and"
            "&facets=category-1%2Ccategory-2%2Cfuzzy%2Coperator&sort=score_desc&page=0"
        ),
        "pagination": "param_zero",   # page=0 → page=1 → page=2 …
        "max_pages":  5,
    },
    "Belmicro": {
        "url":        "https://www.belmicro.com.br/climatizacao",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "GoCompras": {
        "url":        "https://www.gocompras.com.br/ar-condicionado/split-hi-wall/",
        "pagination": "query",
        "max_pages":  5,
    },
    "FrioPecas": {
        "url":        "https://www.friopecas.com.br/ar-condicionado/ar-condicionado-split-inverter",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "WebContinental": {
        "url": (
            "https://www.webcontinental.com.br/climatizacao"
            "/ar-condicionado/ar-condicionado-split-hi-wall"
        ),
        "pagination": "vtex",
        "max_pages":  5,
    },
    "Dufrio": {
        "url":        "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "Leveros": {
        "url":        "https://www.leveros.com.br/ar-condicionado/inverter",
        "pagination": "vtex",
        "max_pages":  5,
        # Lista de seletores candidatos: _detect_items tentará cada um.
        # O layout da Leveros mudou — adicionamos seletores mais restritivos
        # para evitar capturar 775 elementos do seletor genérico.
        # O sanity check de max_items (120) descarta resultados excessivos.
        "item_selector_candidates": [
            # Seletores restritivos ao container principal
            "main [class*='product-item']",
            ".products-grid [class*='product-item']",
            "[class*='product-list'] [class*='product-item']",
            "[class*='shelf'] [class*='product-item']",
            "section[class*='shelf'] > div > div",
            # Fallback: product-card dentro de container principal apenas
            "main [class*='product-card']",
            ".products-grid [class*='product-card']",
        ],
    },
    "ArCerto": {
        "url":        "https://www.arcerto.com/categoria/ar-condicionado-inverter/",
        "pagination": "woocommerce",
        "max_pages":  1,   # página 2+ dispara Cloudflare challenge — limitado a 1
    },
    "FerreiraCosta": {
        "url":             "https://www.ferreiracosta.com/Destaque/split-inverter-subcategoria",
        "pagination":      "query",
        "max_pages":       5,
        "infinite_scroll": True,   # carrega produtos via scroll; paginação tradicional ausente
    },
    "Climario": {
        "url":        "https://www.climario.com.br/ar-condicionado?order=OrderByTopSaleDESC",
        "pagination": "vtex",
        "max_pages":  5,
    },
    "EngageEletro": {
        "url":           "https://www.engageeletro.com.br/ar-e-clima/ar-condicionado/",
        "pagination":    "query",
        "max_pages":     5,
        # Plataforma customizada — usa classe "cardprod" (não VTEX/WooCommerce)
        "item_selector": ".cardprod",
        # Seletores específicos para nome e preço na plataforma customizada
        "name_selector": "a[title], .cardprod a",
        "price_selector": ".cardprod [class*='price'], .cardprod [class*='Price']",
    },
}

# ---------------------------------------------------------------------------
# Strings de UI que não são produtos — filtradas antes de registrar
# ---------------------------------------------------------------------------
_JUNK_STRINGS: frozenset = frozenset({
    "favoritar esse produto", "adicionar ao carrinho", "comparar",
    "ver detalhes", "comprar", "saiba mais", "adicionar", "ver produto",
    "ver oferta", "adicionar à lista", "lista de desejos", "wishlist",
    "favoritar", "adicionar à sacola", "ir para o produto",
    "ver mais detalhes", "selecione", "escolha",
})

# ---------------------------------------------------------------------------
# Seletores CSS — cadeia de fallback cobrindo VTEX IO, WooCommerce e genérico
# ---------------------------------------------------------------------------

_SELECTORS = {
    "item_candidates": [
        # ── VTEX IO (2020+) ──────────────────────────────────────────────
        'article[class*="vtex-product-summary-2-x-element"]',
        'section[class*="vtex-product-summary-2-x-element"]',
        'div[class*="vtex-product-summary-2-x-element"]',
        '[class*="vtex-product-summary-2-x-container"]',
        # ── VTEX legacy / styled-components ─────────────────────────────
        'li.product-summary',
        'div[class*="productSummary"]',
        '.prateleira li',
        '.shelves li',
        'li[class*="ProductCard"]',
        'div[class*="ProductCard__Wrapper"]',
        # ── WooCommerce ──────────────────────────────────────────────────
        'ul.products li.product',
        'ul.products li[class*="product"]',
        'li.product-type-simple',
        'li.type-product',
        # ── Genérico ─────────────────────────────────────────────────────
        '[class*="product-card"]:not(script)',
        '[class*="ProductCard"]:not(script)',
        'li[class*="product-item"]',
        'div[class*="product-item"]',
        '[data-product-id]',
        '[data-sku]',
        'article[class*="product"]',
        # ── EngageEletro (plataforma customizada) ────────────────────────
        '.cardprod',
        '[class*="cardprod"]',
    ],
    "title_candidates": [
        # VTEX IO
        '[class*="vtex-product-summary-2-x-productNameContainer"]',
        '[class*="productNameContainer"]',
        '[class*="ProductName__Link"]',
        '[class*="ProductName"]',
        '[class*="productName"]',
        '[class*="nameContainer"]',
        # WooCommerce
        '.woocommerce-loop-product__title',
        'h2.product-title',
        'h3.product-title',
        # Ferrreira Costa e genéricos
        'h2.product-name',
        '.product-name a',
        '[class*="product-title"]',
        '[class*="product-name"]',
        # Fallback headings
        'h2[class*="title"]', 'h3[class*="title"]', 'h4[class*="title"]',
        'h2[class*="name"]',  'h3[class*="name"]',
        'h2', 'h3',
    ],
    "price_candidates": [
        # ── VTEX IO — valor montado ──────────────────────────────────────
        '[class*="vtex-product-price-1-x-sellingPriceValue"]',
        '[class*="vtex-product-price-1-x-sellingPrice"]:not([class*="Container"])',
        '[class*="sellingPriceValue"]',
        '[class*="sellingPrice"]:not([class*="Container"])',
        '[class*="spotPriceValue"]',
        '[class*="spotPrice"]:not([class*="Container"])',
        # ── VTEX IO — partes do preço (integer + decimal) ────────────────
        # Tratadas em _extract_vtex_split_price(); listadas aqui como fallback
        '[class*="currencyContainer"]',
        '[class*="currencyInteger"]',
        # ── VTEX legacy ──────────────────────────────────────────────────
        '.price .skuBestPrice',
        '.product-summary-price .skuBestPrice',
        '.skuBestPrice',
        # ── WooCommerce ──────────────────────────────────────────────────
        '.price ins .woocommerce-Price-amount',
        '.price .woocommerce-Price-amount bdi',
        'span.woocommerce-Price-amount',
        # ── Atributos de dados / microdata ───────────────────────────────
        '[itemprop="price"]',
        '[data-price]',
        # ── Genérico ─────────────────────────────────────────────────────
        '.price strong',
        '.price > span',
        'span.price-value',
        '[class*="sale-price"]',
        '[class*="salePrice"]',
        '[class*="price-value"]',
        '[class*="PriceValue"]',
        '[class*="product-price"]',
        '[class*="ProductPrice"]',
        '[class*="BestPrice"]',
        '[class*="bestPrice"]',
        '[class*="price"]',
    ],
    "rating_candidates": [
        '[class*="vtex-product-review"]',
        '[class*="rating"]',
        '[class*="Rating"]',
        '.star-rating',
        '[class*="stars"]',
        '[class*="review-score"]',
    ],
    "review_count_candidates": [
        '[class*="review-count"]',
        '[class*="reviewCount"]',
        '[class*="ReviewCount"]',
        '[class*="ratings-count"]',
        '[class*="totalReviews"]',
    ],
    # Indicadores de "próxima página" para detecção de fim de catálogo
    "next_page_candidates": [
        'a[aria-label*="próxima" i]',
        'a[aria-label*="next" i]',
        'a[class*="pagination-next"]:not([disabled])',
        'a[class*="paginationNext"]:not([disabled])',
        '.woocommerce-pagination a.next',
        '[class*="pagination"] a[rel="next"]',
        'a[class*="next"]:not([disabled])',
    ],
}

# Número mínimo de itens para considerar a página válida
_MIN_ITEMS = 3
_MIN_TITLE_LEN = 8    # títulos menores que isso são UI/lixo
_MAX_TITLE_LEN = 300


class DealerScraper(BaseScraper):
    """
    Scraper para varejistas/dealers especializados em ar condicionado.

    Cada chamada a search() corresponde a um dealer (identificado pelo nome).
    A URL de catálogo e estratégia de paginação são definidas em DEALER_CONFIGS.
    """

    platform_name = "Dealer"   # sobrescrito dinamicamente por search()

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._current_dealer: str = ""

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(base_url: str, page: int, pagination: str) -> str:
        """
        Gera a URL para a página N do catálogo de acordo com a estratégia
        de paginação do dealer.

        Args:
            base_url:   URL da primeira página (conforme DEALER_CONFIGS)
            page:       número da página (1-indexed)
            pagination: "vtex" | "param_zero" | "woocommerce" | "query"

        Returns:
            URL da página N.
        """
        if page == 1:
            return base_url

        if pagination == "woocommerce":
            # Insere /page/N/ no path antes de qualquer query string
            parsed = urlparse(base_url)
            path = parsed.path.rstrip("/")
            new_path = f"{path}/page/{page}/"
            return urlunparse(parsed._replace(path=new_path))

        if pagination == "param_zero":
            # 0-indexed: page 1 → page=0, page 2 → page=1, …
            return re.sub(r"(page=)\d+", rf"\g<1>{page - 1}", base_url)

        # "vtex" e "query": acrescenta ?page=N ou &page=N
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}page={page}"

    # ------------------------------------------------------------------
    # Detecção de itens no DOM
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_items(
        soup: BeautifulSoup,
        item_selector: Optional[str] = None,
        item_selector_candidates: Optional[List[str]] = None,
        max_items: int = 120,
    ) -> Tuple[List[Tag], str]:
        """
        Itera pelos seletores de container até encontrar entre _MIN_ITEMS e max_items
        resultados.

        Ordem de prioridade:
          1. item_selector (string única do config)
          2. item_selector_candidates (lista do config, ex: Leveros)
          3. _SELECTORS["item_candidates"] (cadeia genérica)

        O sanity check max_items evita que seletores genéricos como
        [class*="product-card"] retornem centenas de elementos de UI (Leveros: 775 itens).
        O override de uma string única (item_selector) ignora o sanity check pois
        foi confirmado manualmente.
        """
        # 1. Override único (ex: EngageEletro ".cardprod")
        if item_selector:
            items = soup.select(item_selector)
            if len(items) >= _MIN_ITEMS:
                return items, item_selector
            logger.debug(
                f"item_selector override '{item_selector}' retornou {len(items)} — "
                "usando cadeia genérica"
            )

        # 2. Lista de candidatos específicos do dealer (ex: Leveros)
        if item_selector_candidates:
            for sel in item_selector_candidates:
                items = soup.select(sel)
                if _MIN_ITEMS <= len(items) <= max_items:
                    return items, sel
            logger.debug("item_selector_candidates não retornaram resultado válido")

        # 3. Cadeia genérica com sanity check de max_items
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if _MIN_ITEMS <= len(items) <= max_items:
                return items, sel

        return [], "nenhum"

    @staticmethod
    def _first_match(item: Tag, candidates: List[str]) -> Optional[Tag]:
        for sel in candidates:
            el = item.select_one(sel)
            if el:
                return el
        return None

    # ------------------------------------------------------------------
    # Validação de título
    # ------------------------------------------------------------------

    @staticmethod
    def _is_junk_title(text: Optional[str]) -> bool:
        """
        Retorna True para strings que claramente não são nomes de produto:
        botões de UI, textos vazios, strings muito curtas ou muito longas.
        """
        if not text:
            return True
        t = text.strip()
        if len(t) < _MIN_TITLE_LEN or len(t) > _MAX_TITLE_LEN:
            return True
        if t.lower() in _JUNK_STRINGS:
            return True
        # String só com dígitos/símbolos não é título de produto
        if re.match(r'^[\d\s.,R$%\-/]+$', t):
            return True
        return False

    # ------------------------------------------------------------------
    # Extração de preço — VTEX split e meta[itemprop]
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_vtex_split_price(item: Tag) -> Optional[str]:
        """
        VTEX IO fragmenta o preço em três elementos:
          currencyInteger + currencyDecimalSeparator + currencyDecimalDigits
        Este método os une e retorna a string "R$ NNNN,NN".
        Retorna None se o padrão não for encontrado.
        """
        int_el = item.select_one('[class*="currencyInteger"]')
        if not int_el:
            return None
        price_str = int_el.get_text(strip=True)
        sep_el = item.select_one('[class*="currencyDecimalSeparator"]')
        dec_el = item.select_one('[class*="currencyDecimalDigits"]')
        if sep_el:
            price_str += sep_el.get_text(strip=True)
        if dec_el:
            price_str += dec_el.get_text(strip=True)
        return f"R$ {price_str}" if price_str else None

    @staticmethod
    def _extract_price_el(item: Tag) -> Optional[str]:
        """
        Extrai o preço de um item tentando, em ordem:
          1. Seletores CSS da cadeia _SELECTORS["price_candidates"]
          2. VTEX split price (currencyInteger + partes)
          3. meta[itemprop="price"] (valor no atributo content)
          4. [data-price] atributo
          5. Regex R$ no texto completo do item
        
        CORREÇÃO PROBLEMA #2: Usa get_text(separator=' ') para evitar concatenação
        de strings sem espaço. Aplica sanitização robusta antes de retornar.
        """
        # 1. Seletores CSS
        for sel in _SELECTORS["price_candidates"]:
            el = item.select_one(sel)
            if el:
                # meta e elementos com atributo data-price → valor no atributo
                if el.name == "meta":
                    val = el.get("content", "").strip()
                    if val:
                        return f"R$ {val}"
                    continue
                # CORREÇÃO: usa separator=' ' para evitar "13% OFFR$ 1.994,91no pix"
                text = el.get_text(separator=' ', strip=True)
                # Evita capturar container vazio ou texto sem dígito
                if text and re.search(r'\d', text):
                    return text

        # 2. VTEX split
        split = DealerScraper._extract_vtex_split_price(item)
        if split:
            return split

        # 3. [data-price] atributo direto
        for el in item.select("[data-price]"):
            val = el.get("data-price", "").strip()
            if val and re.search(r'\d', val):
                return f"R$ {val}"

        # 4. Regex R$ no texto completo — CORREÇÃO: extrai primeiro valor válido
        item_text = item.get_text(" ", strip=True)
        m = re.search(r'R\$\s*([\d.,]+)', item_text)
        if m:
            return f"R$ {m.group(1)}"

        return None

    # ------------------------------------------------------------------
    # Aguarda carregamento de preços (JS lazy-load)
    # ------------------------------------------------------------------

    def _wait_for_prices(self) -> None:
        """
        Aguarda até que algum elemento de preço apareça na página.
        VTEX IO e outros sites carregam preços via fetch separado após o DOM.
        
        Timeout aumentado para 10s por seletor (total ~60s) para cobrir sites
        mais lentos como PoloAr e ArCerto (WooCommerce).
        """
        price_wait_selectors = [
            '[class*="sellingPrice"]',
            '[class*="currencyInteger"]',
            '[class*="skuBestPrice"]',
            '.price',
            '[data-price]',
            '[itemprop="price"]',
            # WooCommerce
            '.woocommerce-Price-amount',
            'span[class*="Price"]',
            # Fallback genérico para qualquer elemento com "price" no class
            '[class*="price"]',
        ]
        for sel in price_wait_selectors:
            try:
                self._page.wait_for_selector(sel, timeout=10_000)
                logger.debug(f"[{self.platform_name}] Preços carregados ({sel})")
                return
            except Exception:
                continue
        logger.info(f"[{self.platform_name}] Timeout aguardando preços — extraindo HTML assim mesmo")

    # ------------------------------------------------------------------
    # Infinite scroll (FerreiraCosta e similares)
    # ------------------------------------------------------------------

    def _scroll_to_load_all(self, max_scrolls: int = 15) -> None:
        """
        Rola a página até o fim repetidamente para disparar infinite scroll.
        Para quando a altura da página não cresce mais ou atinge max_scrolls.
        """
        prev_height = 0
        for _ in range(max_scrolls):
            curr_height = self._page.evaluate("document.body.scrollHeight")
            if curr_height == prev_height:
                break
            prev_height = curr_height
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(random.uniform(1.5, 2.5))
        # Volta ao topo para estabilizar o DOM
        self._page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # Deduplicação defensiva
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove duplicatas por (Plataforma, Produto/SKU normalizado).
        Chave deliberadamente SEM posição — cobre carrosséis onde o mesmo
        produto aparece em posições consecutivas diferentes.
        Quando há colisão, mantém o registro com preço preenchido.
        Reatribui posição orgânica e geral em sequência limpa após dedup.
        """
        seen: Dict[tuple, Dict[str, Any]] = {}

        for row in records:
            title = (row.get("Produto / SKU") or "").lower().strip()
            key = (row.get("Plataforma", ""), title)
            if key in seen:
                if not seen[key].get("Preço (R$)") and row.get("Preço (R$)"):
                    seen[key] = row
            else:
                seen[key] = row

        result = list(seen.values())

        # Reatribui posições em sequência limpa (1, 2, 3, …)
        for i, row in enumerate(result, start=1):
            row["Posição Orgânica"] = i
            row["Posição Geral"]    = i

        no_price = sum(1 for r in result if not r.get("Preço (R$)"))
        if no_price:
            logger.warning(
                f"{no_price}/{len(result)} registros sem preço após dedup"
            )
        return result

    # ------------------------------------------------------------------
    # Correção de título com marca concatenada (ArCerto bug)
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_brand_concat(title: str) -> str:
        """
        Corrige casos onde o texto de um elemento de marca foi concatenado
        diretamente ao nome do produto sem espaço, ex:
          "ElginAr Condicionado Split..." → "Ar Condicionado Split..."
          "MideaAr Condicionado..."      → "Ar Condicionado..."

        Isso ocorre quando o seletor captura um container que tem tanto o
        elemento de marca quanto o de título como filhos, e get_text() os une.
        """
        from config import BRANDS
        for brand in BRANDS:
            if title.startswith(brand) and len(title) > len(brand):
                next_char = title[len(brand)]
                # Concatenação sem espaço: "ElginAr" — próximo char é maiúscula ou dígito
                if next_char.isupper() or next_char.isdigit():
                    return title[len(brand):]
        return title

    # ------------------------------------------------------------------
    # Validação de produto — filtro de ruído (PROBLEMA #5)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_product_title(title: str) -> bool:
        """
        CORREÇÃO PROBLEMA #5: Valida se o título corresponde a um produto real
        de ar-condicionado, filtrando banners, promoções e elementos de UI.
        
        Exige pelo menos UM dos termos obrigatórios:
          - BTU (unidade de capacidade)
          - Split (tipo)
          - Ar Condicionado / Ar-Condicionado (produto)
          - Inverter (tecnologia)
        
        Retorna False para títulos que parecem ser ruído.
        """
        if not title:
            return False
        
        title_lower = title.lower()
        
        # Termos obrigatórios — pelo menos um deve estar presente
        required_patterns = [
            r'\bbtu\b',              # Unidade de capacidade (ex: "9000 BTU")
            r'\bsplit\b',            # Tipo (ex: "Split Hi-Wall")
            r'\bar[- ]condicionado\b',  # Produto (ex: "Ar Condicionado")
            r'\binverter\b',         # Tecnologia (ex: "Inverter Dual")
        ]
        
        for pattern in required_patterns:
            if re.search(pattern, title_lower):
                return True
        
        # Fallback: se tiver "ar" e "clima" juntos, pode ser produto relacionado
        if 'ar' in title_lower and 'clima' in title_lower:
            return True
        
        # Títulos curtos (<15 chars) sem os termos acima são provavelmente ruído
        if len(title) < 15:
            return False
        
        # Caso especial: títulos longos mas sem termos de AC → provavelmente banner
        logger.debug(f"[ProdutoInválido] Título sem termos de AC: '{title[:60]}'")
        return False

    # ------------------------------------------------------------------
    # Extração de preço via JSON-LD (schema.org/Product)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Normalização e matching JSON-LD
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Normaliza string para comparação: minúsculo, sem acentos, sem pontuação."""
        import unicodedata
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = re.sub(r"[^\w\s]", " ", text.lower())
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _jsonld_match(title: str, jsonld_prices: Dict[str, float]) -> Optional[float]:
        """
        Tenta casar um título de produto com as entradas do dict JSON-LD.

        Estratégias em ordem:
          1. Exact match (após normalização)
          2. Containment: um é substring do outro
          3. Word intersection ≥ 60% (Jaccard sobre palavras >2 chars)
        Retorna o preço do melhor match ou None.
        """
        if not jsonld_prices or not title:
            return None

        norm_title = DealerScraper._normalize_for_match(title)
        title_words = {w for w in norm_title.split() if len(w) > 2}

        best_price: Optional[float] = None
        best_score: float = 0.0

        for jname, jprice in jsonld_prices.items():
            norm_jname = DealerScraper._normalize_for_match(jname)

            # 1. Exact
            if norm_title == norm_jname:
                return jprice

            # 2. Containment (um contém o outro)
            if len(norm_title) > 15 and (norm_title in norm_jname or norm_jname in norm_title):
                return jprice

            # 3. Word intersection
            j_words = {w for w in norm_jname.split() if len(w) > 2}
            if not title_words or not j_words:
                continue
            common = len(title_words & j_words)
            score = common / min(len(title_words), len(j_words))
            if score > best_score:
                best_score = score
                best_price = jprice

        # Retorna apenas se score ≥ 60% (evita falsos positivos)
        return best_price if best_score >= 0.60 else None

    @staticmethod
    def _jsonld_match_by_index(
        title: str,
        jsonld_prices: Dict[str, float],
        jsonld_price_list: List[float],
        item_index: int,
    ) -> Optional[float]:
        """
        Fallback: tenta atribuir preço por posição quando o matching por nome falha.
        Usado quando JSON-LD e DOM têm número similar de itens mas os nomes não batem.
        """
        if not jsonld_price_list or item_index >= len(jsonld_price_list):
            return None
        # Só usa fallback por índice se tiver pelo menos 1 preço no JSON-LD
        if len(jsonld_prices) == 0:
            return None
        return jsonld_price_list[item_index]

    @staticmethod
    def _extract_jsonld_prices(html: str) -> Dict[str, float]:
        """
        Lê scripts application/ld+json e constrói um dict {nome_lower: preco}.
        Funciona em VTEX, WooCommerce e qualquer site com schema.org/Product.
        Usado como fallback quando seletores CSS não capturam o preço.
        """
        prices: Dict[str, float] = {}
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                raw = (script.string or "").strip()
                if not raw:
                    continue
                data = json.loads(raw)
                # Pode ser um único objeto ou uma lista
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    # ItemList → percorre os itemListElement
                    if entry.get("@type") == "ItemList":
                        entries.extend(entry.get("itemListElement") or [])
                        continue
                    if entry.get("@type") != "Product":
                        # Tenta "item" (BreadcrumbList ou ListItem wrapping Product)
                        inner = entry.get("item") or {}
                        if inner.get("@type") == "Product":
                            entry = inner
                        else:
                            continue
                    name = entry.get("name", "")
                    offers = entry.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price_raw = (
                        offers.get("price")
                        or offers.get("lowPrice")
                        or offers.get("highPrice")
                    )
                    if name and price_raw is not None:
                        try:
                            prices[name.lower().strip()] = float(price_raw)
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass
        return prices

    # ------------------------------------------------------------------
    # Validação de resultados — alertas de qualidade antes do CSV
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_results(dealer: str, records: List[Dict[str, Any]]) -> None:
        """
        CORREÇÃO PROBLEMA #3 e #6: Emite warnings de qualidade dos dados.
        Detecta: sem preço, sem produto, duplicatas, marca concatenada.
        
        Validação adicional para Posição Patrocinada (PROBLEMA #3):
        - Verifica se campo está sempre vazio e alerta para possível falha de detecção
        """
        total = len(records)
        if total == 0:
            logger.error(f"[{dealer}] ALERTA: nenhum produto extraído!")
            return

        sem_preco   = sum(1 for r in records if not r.get("Preço (R$)"))
        sem_produto = sum(1 for r in records if not (r.get("Produto / SKU") or "").strip())
        
        # CORREÇÃO PROBLEMA #3: Valida campos de posição patrocinada
        pos_patrocinada_vazios = sum(
            1 for r in records 
            if r.get("Posição Patrocinada") is None or r.get("Posição Patrocinada") == ""
        )
        pct_patrocinada_vazio = 100 * pos_patrocinada_vazios / total

        chaves = [(r.get("Produto / SKU", "").lower(), r.get("Posição Orgânica")) for r in records]
        dupes  = total - len(set(chaves))

        from config import BRANDS
        concat_bugs = sum(
            1 for r in records
            if any(
                (r.get("Produto / SKU") or "").startswith(b)
                and len(r.get("Produto / SKU", "")) > len(b)
                and r["Produto / SKU"][len(b)].isupper()
                for b in BRANDS
            )
        )
        
        # Validação de avaliação (PROBLEMA #3)
        sem_avaliacao = sum(1 for r in records if not r.get("Avaliação"))
        sem_qtd_avaliacoes = sum(1 for r in records if not r.get("Qtd Avaliações"))

        pct_sem_preco = 100 * sem_preco / total
        logger.info(
            f"[{dealer}] Validação: {total} registros | "
            f"sem preço: {sem_preco} ({pct_sem_preco:.0f}%) | "
            f"sem produto: {sem_produto} | pos.patrocinada vazia: {pos_patrocinada_vazios} ({pct_patrocinada_vazio:.0f}%) | "
            f"dupes: {dupes} | concat brand: {concat_bugs}"
        )
        if pct_sem_preco > 20:
            logger.warning(f"[{dealer}] {pct_sem_preco:.0f}% dos produtos sem preço ({sem_preco}/{total})")
        if sem_produto > 0:
            logger.warning(f"[{dealer}] {sem_produto} linhas com Produto/SKU vazio")
        # Alerta se TODAS as posições patrocinadas estão vazias (possível falha de detecção)
        if pct_patrocinada_vazio == 100:
            logger.warning(f"[{dealer}] 100% das Posições Patrocinadas vazias — verificar seletores de anúncio")
        if dupes > 0:
            logger.warning(f"[{dealer}] {dupes} entradas duplicadas após dedup final")
        if concat_bugs > 0:
            logger.warning(f"[{dealer}] {concat_bugs} nomes com marca concatenada detectados")
        if sem_avaliacao > total * 0.7:
            logger.debug(f"[{dealer}] {sem_avaliacao} produtos sem Avaliação ({100*sem_avaliacao/total:.0f}%) — comum em dealers")
        if sem_qtd_avaliacoes > total * 0.7:
            logger.debug(f"[{dealer}] {sem_qtd_avaliacoes} produtos sem Qtd Avaliações — comum em dealers")

    # ------------------------------------------------------------------
    # Extração via VTEX __RUNTIME__ (JS state injection)
    # ------------------------------------------------------------------

    def _try_vtex_runtime(
        self,
        dealer: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Tenta extrair produtos do objeto window.__RUNTIME__ ou window.__STATE__
        injetado pelo VTEX IO na página. Retorna lista vazia se não encontrado.
        """
        try:
            runtime = self._page.evaluate("window.__RUNTIME__")
        except Exception:
            runtime = None

        if not runtime:
            return []

        # Procura o queryData que contém os produtos
        query_data = (
            runtime.get("queryData")
            or runtime.get("initialState", {}).get("queryData")
        )
        if not query_data:
            return []

        # queryData é um dict cujas chaves são strings de query hash
        products_raw = []
        for _key, val in query_data.items():
            if not isinstance(val, dict):
                continue
            data = val.get("data") or {}
            product_search = (
                data.get("productSearch")
                or data.get("search")
                or data.get("searchResult")
            )
            if not product_search:
                continue
            products_raw = (
                product_search.get("products")
                or product_search.get("items")
                or []
            )
            if products_raw:
                break

        if not products_raw:
            return []

        records = []
        org_ctr = spo_ctr = 0
        for idx, prod in enumerate(products_raw):
            sponsored = prod.get("advertisement") is not None

            if sponsored:
                spo_ctr += 1
                pos_organic, pos_sponsored = None, spo_ctr
            else:
                org_ctr += 1
                pos_organic, pos_sponsored = org_ctr, None

            # Título
            title = prod.get("productName") or prod.get("name")

            # Preço — pega o menor preço entre sellers
            price_float = None
            items = prod.get("items") or []
            for item in items:
                for seller in item.get("sellers") or []:
                    offer = (seller.get("commertialOffer") or
                             seller.get("commercialOffer") or {})
                    best = offer.get("Price") or offer.get("ListPrice")
                    if best:
                        try:
                            v = float(best)
                            if price_float is None or v < price_float:
                                price_float = v
                        except (ValueError, TypeError):
                            pass

            # Seller (primeiro seller do primeiro item)
            seller_name = dealer
            if items:
                sellers = items[0].get("sellers") or []
                if sellers:
                    seller_name = sellers[0].get("sellerName", dealer)

            records.append(self._build_record(
                keyword=dealer,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=page_offset + idx + 1,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price_float,
                seller=seller_name,
                is_fulfillment=False,
            ))

        logger.info(
            f"[{self.platform_name}] {len(records)} itens via VTEX __RUNTIME__"
        )
        return records

    # ------------------------------------------------------------------
    # Parse DOM — cadeia de fallback
    # ------------------------------------------------------------------

    def _parse_results_dom(
        self,
        html: str,
        dealer: str,
        keyword_category_map: dict,
        page: int,
        base_position: int,
        item_selector: Optional[str] = None,
        item_selector_candidates: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        items, sel_used = self._detect_items(
            soup, item_selector, item_selector_candidates
        )

        logger.info(
            f"[{self.platform_name}] {len(items)} itens no DOM "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug_html(html, page, dealer)
            return []

        # Pré-carrega preços via JSON-LD (schema.org/Product)
        jsonld_prices = self._extract_jsonld_prices(html)
        jsonld_price_list: List[float] = list(jsonld_prices.values())  # para fallback por índice
        if jsonld_prices:
            logger.info(
                f"[{self.platform_name}] JSON-LD: {len(jsonld_prices)} preços carregados"
            )

        records = []
        org_ctr = 0
        # Dedup por título dentro desta página — evita duplicatas de carrossel/galeria
        # (ex: Leveros exibe N imagens por produto, gerando N elementos idênticos no DOM)
        seen_titles_this_page: Set[str] = set()

        for item in items:
            # ── Título ────────────────────────────────────────────────
            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            # CORREÇÃO PROBLEMA #2: usa separator=' ' para evitar concatenação
            title = title_el.get_text(separator=' ', strip=True) if title_el else None

            # Fallback: img[alt]
            if not title or self._is_junk_title(title):
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None

            # Fallback: a[title]
            if not title or self._is_junk_title(title):
                link = item.select_one("a[title]")
                if link:
                    title = link.get("title", "").strip() or None

            # Descarta itens de UI (botões, labels) sem título real
            if self._is_junk_title(title):
                continue

            # CORREÇÃO PROBLEMA #5: Validação de produto — exige termos mínimos
            # para evitar falsos positivos (banners, promoções, UI)
            if not self._is_valid_product_title(title):
                logger.debug(f"[{self.platform_name}] Produto inválido (ruído): '{title[:60]}'")
                continue

            # Corrige "MarcaNome do produto" → "Nome do produto" (ArCerto bug)
            title = self._fix_brand_concat(title)

            # Dedup de carrossel/galeria: mesmo título já visto nesta página → skip
            title_key = title.lower().strip()
            if title_key in seen_titles_this_page:
                continue
            seen_titles_this_page.add(title_key)

            org_ctr += 1
            pos_general = base_position + org_ctr

            # ── Preço: seletores CSS → JSON-LD word-match → fallback por índice ──
            price_raw = self._extract_price_el(item)

            if not price_raw and jsonld_prices:
                matched = self._jsonld_match(title, jsonld_prices)
                if matched is not None:
                    price_raw = f"R$ {matched}"

            # Fallback por índice: se o matching por nome falhou mas temos JSON-LD,
            # tenta atribuir preço pela posição (item_index) na lista de preços
            if not price_raw and jsonld_price_list:
                item_index = len(records)
                matched_by_idx = self._jsonld_match_by_index(
                    title, jsonld_prices, jsonld_price_list, item_index
                )
                if matched_by_idx is not None:
                    price_raw = f"R$ {matched_by_idx}"
                    logger.debug(
                        f"[{self.platform_name}] Preço atribuído por índice [{item_index}]: "
                        f"'{title[:40]}' → R$ {matched_by_idx}"
                    )

            if not price_raw:
                logger.debug(f"[{self.platform_name}] Sem preço CSS/JSON-LD: '{title[:50]}'")

            # ── Rating / reviews ──────────────────────────────────────
            rating_el = self._first_match(item, _SELECTORS["rating_candidates"])
            review_el = self._first_match(item, _SELECTORS["review_count_candidates"])

            records.append(self._build_record(
                keyword=dealer,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=org_ctr,
                position_sponsored=None,
                price_raw=price_raw,
                seller=dealer,
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(review_el.get_text() if review_el else None),
            ))

        # Fallback por índice (pós-loop): se os counts batem (±15%) e ainda há 
        # registros sem preço, atribui prices pela posição na lista JSON-LD.
        # Cobre casos onde nomes diferem muito (BTU "9.000" vs "9000", etc.)
        # OU quando o matching por nome falha mas a ordem dos produtos é consistente.
        no_price_idx = [i for i, r in enumerate(records) if not r.get("Preço (R$)")]
        if no_price_idx and jsonld_price_list:
            ratio = len(records) / len(jsonld_price_list)
            if 0.85 <= ratio <= 1.15:   # contagens similares (±15%)
                for i in no_price_idx:
                    if i < len(jsonld_price_list):
                        records[i]["Preço (R$)"] = jsonld_price_list[i]
                logger.info(
                    f"[{self.platform_name}] Fallback por índice (pós-loop): "
                    f"{len(no_price_idx)} preços atribuídos"
                )

        return records

    # ------------------------------------------------------------------
    # Detecção de bloqueios anti-bot
    # ------------------------------------------------------------------

    def _is_blocked_page(self) -> Optional[str]:
        """
        Verifica se a página atual é um bloqueio anti-bot.
        Retorna string com tipo de bloqueio ou None se página OK.
        """
        try:
            title = self._page.title().lower()
        except Exception:
            title = ""

        # Cloudflare challenge ("Um momento…" / "Just a moment…")
        if "um momento" in title or "just a moment" in title or "checking your browser" in title:
            return "cloudflare"

        # reCAPTCHA
        try:
            html_snippet = self._page.content()[:6000]
            if "recaptcha" in html_snippet.lower() and "grecaptcha.render" in html_snippet.lower():
                return "recaptcha"
            if "challenge-platform" in html_snippet or "cf-challenge" in html_snippet:
                return "cloudflare"
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # CORREÇÃO PROBLEMA #1: Detecção de páginas de erro (404, indisponível)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_error_page(html: str, dealer: str) -> bool:
        """
        CORREÇÃO PROBLEMA #1: Detecta se o HTML contém mensagens de erro
        ao invés de produtos válidos.
        
        Strings detectadas:
          - "Página não encontrada", "404", "Indisponível"
          - "Oops!", "Not Found", "Page not found"
          - Mensagens específicas por dealer
        
        Retorna True se for página de erro, False caso contrário.
        """
        html_lower = html.lower()
        
        # Strings de erro genéricas
        error_strings = [
            "página não encontrada",
            "pagina não encontrada",  # sem acento
            "page not found",
            "not found",
            "404",
            "indisponível",
            "indisponivel",  # sem acento
            "oops!",
            "página ausente",
            "link está incorreto",
            "produto indisponível",
            "categoria indisponível",
            "acesso negado",
            "access denied",
        ]
        
        for err in error_strings:
            if err in html_lower:
                logger.warning(
                    f"[{dealer}] Erro detectado na página: '{err}'"
                )
                return True
        
        # Heurística adicional: página muito curta (<500 chars) sem "product" ou "item"
        if len(html) < 500:
            if "product" not in html_lower and "item" not in html_lower:
                logger.warning(f"[{dealer}] Página muito curta ({len(html)} chars) — possível erro")
                return True
        
        return False

    # ------------------------------------------------------------------
    # Detecção de fim de catálogo
    # ------------------------------------------------------------------

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """
        Verifica se existe botão/link de próxima página ativo.
        Retorna True se ainda há mais páginas.
        """
        for sel in _SELECTORS["next_page_candidates"]:
            el = soup.select_one(sel)
            if el:
                return True
        return False

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug_html(self, html: str, page: int, dealer: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_name = dealer[:30].replace(" ", "_")
            path = log_dir / f"dealer_debug_{safe_name}_p{page}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo para diagnóstico: {path}"
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # search() — interface principal compatível com BaseScraper
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=8, max=30),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Scrapa o catálogo de um dealer.

        Args:
            keyword:              nome do dealer (chave de DEALER_CONFIGS)
            keyword_category_map: não usado; mantido por compatibilidade
            page_limit:           limite de páginas (sobrescrito por max_pages do dealer)

        Returns:
            Lista de registros no formato padrão _build_record.
        """
        dealer = keyword
        config = DEALER_CONFIGS.get(dealer)
        if not config:
            logger.error(
                f"[DealerScraper] Dealer '{dealer}' não encontrado em DEALER_CONFIGS"
            )
            return []

        # Atualiza platform_name dinamicamente para logs e registros
        self.platform_name = dealer
        self._current_dealer = dealer

        base_url   = config["url"]
        pagination = config.get("pagination", "query")
        max_pages  = min(config.get("max_pages", 5), page_limit)

        all_records: List[Dict[str, Any]] = []
        # keyword_category_map para _build_record
        _cat_map = {"Dealers": [dealer]}
        infinite_scroll          = config.get("infinite_scroll", False)
        item_selector            = config.get("item_selector")
        item_selector_candidates = config.get("item_selector_candidates")
        name_selector            = config.get("name_selector")
        price_selector           = config.get("price_selector")

        for page in range(1, max_pages + 1):
            url = self._build_page_url(base_url, page, pagination)
            logger.info(f"[{self.platform_name}] Página {page}/{max_pages} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                self._random_delay(min_s=3.0, max_s=7.0)

                # Detecta bloqueios anti-bot (reCAPTCHA, Cloudflare)
                block_type = self._is_blocked_page()
                if block_type:
                    logger.warning(
                        f"[{self.platform_name}] Bloqueio detectado: {block_type} "
                        f"(página {page}) — interrompendo coleta do dealer"
                    )
                    self._dump_debug_html(self._page.content(), page, dealer)
                    break

                # Infinite scroll: carrega todos os itens antes de extrair
                if infinite_scroll:
                    self._scroll_to_load_all(max_scrolls=12)
                else:
                    self._human_scroll(steps=8, step_px=300)

                # Aguarda preços carregarem (fetch separado em VTEX/SPAs)
                self._wait_for_prices()
                self._random_delay(min_s=1.0, max_s=2.5)

                # base_position = quantos itens já coletamos (posição contínua)
                base_position = len(all_records)

                # 1. Tenta VTEX __RUNTIME__
                vtex_records = self._try_vtex_runtime(
                    dealer, _cat_map, page, base_position
                )
                if vtex_records:
                    all_records.extend(vtex_records)
                else:
                    # CORREÇÃO PROBLEMA #1: Validação de HTTP/Conteúdo antes de parsear
                    html = self._page.content()
                    
                    # Detecta páginas de erro 404 ou mensagens de fallback
                    if self._is_error_page(html, dealer):
                        logger.warning(
                            f"[{self.platform_name}] Página {page} contém erro (404/indisponível) — "
                            "skipando extração"
                        )
                        break
                    
                    # 2. Parse DOM
                    dom_records = self._parse_results_dom(
                        html, dealer, _cat_map, page,
                        base_position=base_position,
                        item_selector=item_selector,
                        item_selector_candidates=item_selector_candidates,
                    )
                    all_records.extend(dom_records)

                    if not dom_records:
                        logger.warning(
                            f"[{self.platform_name}] 0 itens na página {page} — "
                            "possível bloqueio ou fim do catálogo."
                        )
                        break

                # Sites com infinite scroll expõem tudo em página única
                if infinite_scroll:
                    break

                # Verifica se há próxima página
                soup = BeautifulSoup(self._page.content(), "html.parser")
                if not self._has_next_page(soup) and page < max_pages:
                    logger.info(
                        f"[{self.platform_name}] Sem próxima página detectada — "
                        f"encerrando em página {page}"
                    )
                    break

                if page < max_pages:
                    self._random_delay(min_s=2.5, max_s=6.0)

            except Exception as exc:
                logger.error(
                    f"[{self.platform_name}] Erro na página {page}: {exc}"
                )
                raise

        # Deduplicação defensiva final — remove residuais entre páginas
        all_records = self._deduplicate(all_records)

        # Validação de qualidade — loga alertas antes de entregar ao CSV
        self._validate_results(dealer, all_records)

        logger.success(
            f"[{self.platform_name}] '{dealer}' → {len(all_records)} produtos coletados"
        )
        return all_records
