"""
scrapers/mercado_livre.py — Scraper do Mercado Livre (mercadolivre.com.br).

Estratégia de extração:
  - URL de busca: https://lista.mercadolivre.com.br/{keyword_encoded}
  - Paginação: parâmetro `_Desde_{offset}` na URL (48 itens por página)
  - Distinção Orgânico/Patrocinado: presença do atributo data-label="publicity"
    ou classe css com "promoted" / "advertising" no container do item
  - Fulfillment (FULL): badge com aria-label ou texto "Full" / "Enviado pelo ML"
  - Preço: fragmentos `.andes-money-amount__fraction` + `.andes-money-amount__cents`

Notas de manutenção:
  - Se o ML alterar sua estrutura CSS, ajuste os seletores em _SELECTORS abaixo.
  - Todos os seletores estão centralizados neste dict para facilitar atualização.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper
from utils.text import parse_rating, parse_review_count


# ---------------------------------------------------------------------------
# Seletores CSS centralizados — atualize aqui se o ML mudar o DOM
# ---------------------------------------------------------------------------
_SELECTORS = {
    # container de cada resultado (orgânico e patrocinado)
    "item_container":  "li.ui-search-layout__item",

    # título do produto — o ML migrou para o sistema "Poly" em 2024/2025.
    # Seletores em ordem de prioridade (o primeiro que existir é usado).
    "title_candidates": [
        ".poly-component__title",          # sistema Poly (atual)
        "a.poly-component__title",         # variante Poly com âncora
        "h2.poly-box",                     # variante Poly h2
        ".poly-component__title-wrapper",  # wrapper Poly
        "h2.ui-search-item__title",        # sistema legado (fallback)
        ".ui-search-item__title",          # legado sem tag h2
    ],

    # fração inteira do preço (ex: "2.799")
    "price_fraction":  ".andes-money-amount__fraction",

    # centavos do preço (ex: "90")
    "price_cents":     ".andes-money-amount__cents",

    # nome do seller / loja oficial — também com fallbacks Poly
    "seller_candidates": [
        ".poly-component__seller",         # Poly
        ".ui-search-official-store-label", # legado
        ".ui-search-item__seller-description",
    ],

    # badge de fulfillment (FULL)
    "fulfillment":     ".poly-component__fulfillment, "
                       ".ui-search-item__group__element.ui-search-item__fulfillment",

    # nota de avaliação
    "rating_candidates": [
        ".poly-component__reviews-rating",  # Poly
        ".ui-search-reviews__rating-number", # legado
    ],

    # quantidade de avaliações
    "review_count_candidates": [
        ".poly-component__reviews-count",  # Poly
        ".ui-search-reviews__amount",      # legado
    ],

    # tag de destaque (ex: "MAIS VENDIDO", "OFERTA DO DIA")
    "tag_candidates": [
        ".poly-component__highlight",      # Poly
        ".ui-search-item__highlight-label",# legado
    ],

    # indicador de patrocinado — testa múltiplas abordagens
    "sponsored_label": ".ui-search-item__promoted-label",
}

# ML pagina de 48 em 48 itens; o offset começa em 1
_ITEMS_PER_PAGE = 48


class MLScraper(BaseScraper):
    """Scraper modular para o Mercado Livre."""

    platform_name = "Mercado Livre"

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        """
        Constrói a URL de busca paginada do Mercado Livre.

        Página 1: https://lista.mercadolivre.com.br/ar-condicionado-split-9000-btus
        Página 2: .../_Desde_49
        Página 3: .../_Desde_97
        """
        slug = quote_plus(keyword).replace("+", "-").lower()
        base = f"https://lista.mercadolivre.com.br/{slug}"
        if page > 1:
            offset = (page - 1) * _ITEMS_PER_PAGE + 1
            return f"{base}_Desde_{offset}"
        return base

    # ------------------------------------------------------------------
    # Extração de preço (dois fragmentos somados)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(item: Tag) -> Optional[float]:
        """
        Combina fração inteira + centavos para obter o preço como float.

        O ML renderiza preço em dois <span> separados:
          <span class="andes-money-amount__fraction">2.799</span>
          <span class="andes-money-amount__cents">90</span>

        Preços "riscados" (preço original antes de desconto) ficam em
        .andes-money-amount--previous; ignoramos esse container.
        """
        # Pega apenas o primeiro bloco de preço (não o preço original)
        price_container = item.select_one(
            ".andes-money-amount:not(.andes-money-amount--previous)"
        )
        if not price_container:
            return None

        fraction = price_container.select_one(_SELECTORS["price_fraction"])
        cents    = price_container.select_one(_SELECTORS["price_cents"])

        if not fraction:
            return None

        # Remove separadores de milhar
        int_part = re.sub(r"\D", "", fraction.get_text())
        dec_part = re.sub(r"\D", "", cents.get_text()) if cents else "00"
        dec_part = dec_part.ljust(2, "0")[:2]  # garante 2 casas decimais

        try:
            return float(f"{int_part}.{dec_part}")
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Detecção de anúncio patrocinado
    # ------------------------------------------------------------------

    @staticmethod
    def _is_sponsored(item: Tag) -> bool:
        """
        Retorna True se o item for um anúncio patrocinado.

        Estratégia em camadas (para robustez contra mudanças do ML):
          1. Classe CSS com "promoted" ou "advertising" no container
          2. Elemento filho com label de patrocinado
          3. Atributo data-* indicando publicidade
        """
        # camada 1: classes do container
        item_classes = " ".join(item.get("class", []))
        if re.search(r"promot|advertis|publicidad|sponsor", item_classes, re.I):
            return True

        # camada 2: label filho
        if item.select_one(_SELECTORS["sponsored_label"]):
            return True

        # camada 3: qualquer elemento com texto "Patrocinado" ou "Publicidade"
        for el in item.find_all(string=re.compile(r"patrocinado|publicidade", re.I)):
            return True

        return False

    # ------------------------------------------------------------------
    # Detecção de Fulfillment (FULL)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fulfillment(item: Tag) -> bool:
        """Verifica se o item tem badge FULL (enviado e entregue pelo ML)."""
        badge = item.select_one(_SELECTORS["fulfillment"])
        if badge:
            return True
        # fallback: qualquer texto "full" ou "mercado envios full"
        for el in item.find_all(string=re.compile(r"\bfull\b", re.I)):
            return True
        return False

    # ------------------------------------------------------------------
    # Parse de todos os itens de uma página HTML
    # ------------------------------------------------------------------

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Extrai todos os produtos de uma página HTML do ML.

        Args:
            html:                 conteúdo HTML da página
            keyword:              keyword que gerou essa SERP
            keyword_category_map: mapa de categorias para _build_record
            page_offset:          número de itens já coletados em páginas anteriores
                                  (usado para calcular Posição Geral absoluta)

        Returns:
            Lista de dicts no formato do DataFrame de saída.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(_SELECTORS["item_container"])
        logger.info(f"[{self.platform_name}] {len(items)} itens encontrados na página")

        # Diagnóstico: se nenhum item for encontrado, salva HTML para inspeção
        if not items:
            debug_path = f"logs/ml_debug_{page_offset}.html"
            except Exception:
                pass
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
                logger.warning(
                    f"[{self.platform_name}] Nenhum item encontrado. "
                    f"HTML salvo em {debug_path} para diagnóstico."
                )
            except Exception:
                pass

        records = []
        organic_counter  = 0
        sponsored_counter = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1
            sponsored   = self._is_sponsored(item)

            if sponsored:
                sponsored_counter += 1
                pos_organic    = None
                pos_sponsored  = sponsored_counter
            else:
                organic_counter += 1
                pos_organic    = organic_counter
                pos_sponsored  = None

            # --- título: tenta cada seletor até encontrar um que retorne texto ---
            title = None
            for sel in _SELECTORS["title_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

            # --- preço ---
            price = self._extract_price(item)

            # --- seller: mesma lógica de fallback ---
            seller = "Mercado Livre"
            for sel in _SELECTORS["seller_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    seller = el.get_text(strip=True)
                    break

            # --- fulfillment ---
            fulfillment = self._is_fulfillment(item)

            # --- avaliação ---
            rating = None
            for sel in _SELECTORS["rating_candidates"]:
                el = item.select_one(sel)
                if el:
                    rating = parse_rating(el.get_text())
                    break

            # --- qtd avaliações ---
            review_count = None
            for sel in _SELECTORS["review_count_candidates"]:
                el = item.select_one(sel)
                if el:
                    review_count = parse_review_count(el.get_text())
                    break

            # --- tag de destaque ---
            tag = None
            for sel in _SELECTORS["tag_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    tag = el.get_text(strip=True)
                    break

            record = self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price,
                seller=seller,
                is_fulfillment=fulfillment,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
            )
            records.append(record)

        return records

    # ------------------------------------------------------------------
    # Método público — ponto de entrada
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=15),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """
        Busca uma keyword no Mercado Livre por até `page_limit` páginas.

        O decorador @retry reexecuta automaticamente em caso de erro de rede
        ou timeout, com back-off exponencial.

        Returns:
            Lista agregada de todos os registros coletados.
        """
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                # navega para a URL de busca
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()

                # scroll humano para carregar lazy-load
                self._human_scroll(steps=10, step_px=300)

                # verifica se chegamos a uma página sem resultados
                soup = self._get_soup()
                if soup.select_one(".ui-search-rescue"):  # página de "sem resultados"
                    logger.warning(
                        f"[{self.platform_name}] Página {page} sem resultados. Encerrando."
                    )
                    break

                # extrai os dados
                offset = (page - 1) * _ITEMS_PER_PAGE
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page_offset=offset,
                )
                all_records.extend(records)

                if not records:
                    logger.warning(
                        f"[{self.platform_name}] Nenhum item parseado na página {page}."
                    )
                    break

                # delay humano entre páginas
                if page < page_limit:
                    self._random_delay()

            except Exception as exc:
                logger.error(
                    f"[{self.platform_name}] Erro na página {page} "
                    f"(keyword='{keyword}'): {exc}"
                )
                raise  # propaga para o @retry

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
