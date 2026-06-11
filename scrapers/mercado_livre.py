"""
scrapers/mercado_livre.py — Scraper do Mercado Livre (mercadolivre.com.br).

Estratégia de extração:
  - URL de busca: https://lista.mercadolivre.com.br/{keyword_encoded}
  - Paginação: parâmetro `_Desde_{offset}` na URL (48 itens por página)
  - Distinção Orgânico/Patrocinado: 5 camadas — classe do container, chip
    legado/Poly, texto "Patrocinado", aria-label/title e href de ad-tracking
    (click1.mercadolivre / mclics)
  - Avaliação/reviews: seletores Poly (.poly-reviews__*) + legados + fallback
    via texto acessível "Avaliação 4,8 de 5 (1.234 avaliações)"
  - Loja Oficial: label legado + texto "Loja oficial" + selo de verificação
  - Fulfillment (FULL): badge com aria-label ou texto "Full" / "Enviado pelo ML"
  - Preço: fragmentos `.andes-money-amount__fraction` + `.andes-money-amount__cents`

Notas de manutenção:
  - Se o ML alterar sua estrutura CSS, ajuste os seletores em _SELECTORS abaixo.
  - Todos os seletores estão centralizados neste dict para facilitar atualização.
  - Valide mudanças com `python scripts/diagnose_ml.py` (taxa de acerto por
    campo) — cobertura por plataforma fica na página 🩺 Data Health.
  - Histórico: avaliação/qtd_avaliações/patrocinado ficaram 0% Mar→Jun/2026
    porque os seletores Poly originais não existiam no DOM real (fix Jun/2026).
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

    # nota de avaliação — Poly 2025+ renderiza em .poly-reviews__rating;
    # os nomes "poly-component__reviews-*" nunca bateram em produção
    # (cobertura 0% Mar→Jun/2026 — ver docs/DIAGNOSTICO_COLETA_JUN2026.md)
    "rating_candidates": [
        ".poly-reviews__rating",            # Poly atual
        ".poly-component__reviews-rating",  # variante antiga (mantida por segurança)
        ".ui-search-reviews__rating-number", # legado
    ],

    # quantidade de avaliações — ex: "(1.234)"
    "review_count_candidates": [
        ".poly-reviews__total",            # Poly atual
        ".poly-component__reviews-count",  # variante antiga
        ".ui-search-reviews__amount",      # legado
    ],

    # bloco completo de reviews — fallback via texto acessível
    # ("Avaliação 4,8 de 5 (1.234 avaliações)" em span visually-hidden)
    "reviews_block": ".poly-component__reviews",

    # tag de destaque (ex: "MAIS VENDIDO", "OFERTA DO DIA")
    "tag_candidates": [
        ".poly-component__highlight",      # Poly
        ".ui-search-item__highlight-label",# legado
    ],

    # indicador de patrocinado — testa múltiplas abordagens
    "sponsored_label": ".ui-search-item__promoted-label",

    # chip "Patrocinado" do sistema Poly (âncora p/ click-tracking de ads)
    "ads_chip": ".poly-component__ads-promotions",

    # link do produto — âncora que leva ao PDP
    "url_candidates": [
        "a.poly-component__title",          # sistema Poly (atual)
        "a.ui-search-link",                 # legado
        "a.ui-search-item__group__element", # legado alternativo
        'a[href*="mercadolivre.com"]',      # fallback genérico
        'a[href*="/MLB"]',                  # fallback por padrão de SKU ML
    ],
}

# ML pagina de 48 em 48 itens; o offset começa em 1
_ITEMS_PER_PAGE = 48

# ---------------------------------------------------------------------------
# Padrões de texto/atributo — robustos a mudança de classes CSS
# ---------------------------------------------------------------------------

# rótulo de anúncio em texto, aria-label ou title
_SPONSORED_TEXT_RE = re.compile(r"patrocinad|publicidad|sponsor", re.I)

# âncoras de click-tracking de Product Ads: presentes no card patrocinado
# mesmo quando o rótulo "Patrocinado" é renderizado via CSS/ícone
_AD_HREF_RE = re.compile(
    r"click1\.mercadoli[bv]re|/mclics?/|[?&#](?:is_advertising|ad_domain)=", re.I
)

# texto acessível do bloco de reviews: "Avaliação 4,8 de 5 (1.234 avaliações)"
_RATING_OF5_RE  = re.compile(r"(\d(?:[.,]\d+)?)\s*de\s*5")
_COUNT_PARENS_RE = re.compile(r"\(\s*([\d.,]+)\s*\)")
_COUNT_WORD_RE   = re.compile(r"([\d.,]+)\s*avalia", re.I)

# selo de loja oficial — texto ("Loja oficial Midea") em qualquer nó do card
_OFFICIAL_STORE_RE = re.compile(r"loja\s+oficial|tienda\s+oficial", re.I)

# tags de destaque conhecidas do ML — fallback quando a classe CSS mudar
_KNOWN_TAG_RE = re.compile(
    r"\b(MAIS VENDIDO|OFERTA DO DIA|OFERTA IMPERD[ÍI]VEL|OFERTA REL[ÂA]MPAGO|"
    r"RECOMENDADO|MELHOR PRE[ÇC]O)\b",
    re.I,
)


class MLScraper(BaseScraper):
    """Scraper modular para o Mercado Livre."""

    platform_name = "Mercado Livre"

    def __init__(self, headless: bool = True) -> None:
        # ML detecta Chromium headless como bot e exibe login gate.
        # Forçamos headless=False — no Oracle VM use xvfb para display virtual:
        #   sudo apt-get install -y xvfb
        #   Xvfb :99 -screen 0 1366x768x24 &
        #   export DISPLAY=:99
        super().__init__(headless=False)

    def _is_login_gate(self) -> bool:
        """Retorna True se a página atual for o login/device-verification gate do ML."""
        url = self._page.url
        if "account-verification" in url or "webdevice" in url:
            return True
        try:
            content = self._page.content()
            return "Para continuar, acesse sua conta" in content
        except Exception:
            return False

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
        Retorna True se o item for um anúncio patrocinado (Product Ads).

        Estratégia em camadas (para robustez contra mudanças do ML):
          1. Classe CSS com "promoted"/"advertising" no container
          2. Label/chip filho (legado .ui-search-item__promoted-label
             ou Poly .poly-component__ads-promotions)
          3. Texto "Patrocinado"/"Publicidade" em qualquer nó
          4. Atributos acessíveis (aria-label/title/alt) com o rótulo —
             o ML às vezes só expõe "Patrocinado" para leitores de tela
          5. Âncora de click-tracking de ads (click1.mercadolivre / mclics /
             is_advertising=true) — sobrevive a redesigns do rótulo visível
        """
        # camada 1: classes do container
        item_classes = " ".join(item.get("class", []))
        if re.search(r"promot|advertis|publicidad|sponsor", item_classes, re.I):
            return True

        # camada 2: chip/label filho (legado + Poly)
        if item.select_one(_SELECTORS["sponsored_label"]):
            return True
        if item.select_one(_SELECTORS["ads_chip"]):
            return True

        # camada 3: texto visível
        if item.find(string=_SPONSORED_TEXT_RE):
            return True

        # camada 4: atributos acessíveis
        for el in item.find_all(True):
            for attr in ("aria-label", "title", "alt"):
                val = el.get(attr)
                if val and _SPONSORED_TEXT_RE.search(str(val)):
                    return True

        # camada 5: href de ad-tracking
        for anchor in item.find_all("a", href=True):
            if _AD_HREF_RE.search(anchor["href"]):
                return True

        return False

    # ------------------------------------------------------------------
    # Avaliação e nº de reviews (Poly + legado + texto acessível)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_reviews(item: Tag) -> tuple:
        """
        Extrai (avaliação, qtd_avaliações) de um card da SERP.

        Ordem de extração:
          1. Seletores CSS dedicados (.poly-reviews__rating / __total + legados)
          2. Texto acessível do bloco de reviews ou de spans
             .andes-visually-hidden: "Avaliação 4,8 de 5 (1.234 avaliações)"

        Returns:
            Tupla (Optional[float], Optional[int]).
        """
        rating: Optional[float] = None
        for sel in _SELECTORS["rating_candidates"]:
            el = item.select_one(sel)
            if el:
                rating = parse_rating(el.get_text())
                if rating is not None:
                    break

        count: Optional[int] = None
        for sel in _SELECTORS["review_count_candidates"]:
            el = item.select_one(sel)
            if el:
                count = parse_review_count(el.get_text())
                if count is not None:
                    break

        if rating is None or count is None:
            texts = []
            block = item.select_one(_SELECTORS["reviews_block"])
            if block:
                texts.append(block.get_text(" ", strip=True))
            texts.extend(
                el.get_text(" ", strip=True)
                for el in item.select(".andes-visually-hidden")
            )
            for text in texts:
                # âncora "de 5" evita confundir com preço/parcela
                if "de 5" not in text:
                    continue
                if rating is None:
                    m = _RATING_OF5_RE.search(text)
                    if m:
                        rating = parse_rating(m.group(1))
                if count is None:
                    m = _COUNT_PARENS_RE.search(text) or _COUNT_WORD_RE.search(text)
                    if m:
                        count = parse_review_count(m.group(1))
                if rating is not None and count is not None:
                    break

        return rating, count

    # ------------------------------------------------------------------
    # Tipo de seller: Loja Oficial vs 3P
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_tipo_seller(item: Tag, seller: Optional[str]) -> str:
        """
        Classifica o seller do card como "Loja Oficial" ou "3P".

        Camadas (a flag legada .ui-search-official-store-label nunca disparou
        no sistema Poly — 0 registros "Loja Oficial" no banco até Jun/2026):
          1. Label legado de loja oficial
          2. Texto "Loja oficial" no nome do seller extraído
          3. Texto "Loja oficial" em qualquer nó do card
          4. Selo de verificação (cockade) junto ao seller no Poly
        """
        if item.select_one(".ui-search-official-store-label"):
            return "Loja Oficial"
        if seller and _OFFICIAL_STORE_RE.search(seller):
            return "Loja Oficial"
        if item.find(string=_OFFICIAL_STORE_RE):
            return "Loja Oficial"
        if item.select_one('[class*="cockade" i], [class*="verified" i]'):
            return "Loja Oficial"
        return "3P"

    # ------------------------------------------------------------------
    # Extração de URL do produto
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_url(item: Tag) -> Optional[str]:
        """Extrai a URL do PDP do produto a partir das âncoras do card."""
        for sel in _SELECTORS["url_candidates"]:
            el = item.select_one(sel)
            href = el.get("href") if el else None
            if href:
                # remove query string de tracking, mantém o path canônico
                return href.split("#")[0].strip()
        return None

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
    # Tratamento de popup de CEP (validado em produção — Mar/2026)
    # ------------------------------------------------------------------

    def _dismiss_cep_popup(self) -> None:
        """
        Fecha o modal de seleção de CEP/localização que o ML exibe
        para usuários sem cookie de localização.

        Tenta clicar no botão de fechar (×) ou no overlay; se não
        encontrar em 2s, segue em frente (popup pode não aparecer).
        """
        try:
            # Botão "×" do modal de localização
            close_btn = self._page.locator(
                "button[aria-label='Fechar'], "
                ".modal-dialog__close, "
                ".ui-pdp-buybox__cep .ui-pdp-action-modal__close, "
                "[data-testid='modal-close-btn']"
            )
            close_btn.first.click(timeout=2000)
            logger.debug(f"[{self.platform_name}] Popup de CEP fechado.")
        except Exception:
            pass  # popup não apareceu — normal em sessões com cookie

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

            # --- URL do produto ---
            url_produto = self._extract_url(item)

            # --- seller: mesma lógica de fallback ---
            seller = "Mercado Livre"
            for sel in _SELECTORS["seller_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    # Poly prefixa com "Por " (ex: "Por WebContinental")
                    seller = re.sub(
                        r"^por\s+", "", el.get_text(strip=True), flags=re.I
                    ).strip() or "Mercado Livre"
                    break

            # --- tipo de seller: Loja Oficial vs 3P (multi-camada) ---
            tipo_seller = self._detect_tipo_seller(item, seller)

            # --- fulfillment ---
            fulfillment = self._is_fulfillment(item)

            # --- avaliação + qtd avaliações (CSS + texto acessível) ---
            rating, review_count = self._extract_reviews(item)

            # --- tag de destaque ---
            tag = None
            for sel in _SELECTORS["tag_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    tag = el.get_text(strip=True)
                    break
            if tag is None:
                # fallback por texto: tags conhecidas sobrevivem a redesign CSS
                hit = item.find(string=_KNOWN_TAG_RE)
                if hit:
                    m = _KNOWN_TAG_RE.search(str(hit))
                    tag = m.group(1).upper() if m else None

            record = self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price,
                seller=seller,
                buy_box_seller=seller,
                tipo_seller=tipo_seller,
                is_fulfillment=fulfillment,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
                url_produto=url_produto,
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

                # --- Detecta login gate (/gz/webdevice/account-verification) ---
                if self._is_login_gate():
                    logger.error(
                        f"[{self.platform_name}] Login gate detectado. "
                        "Capture uma sessão e tente novamente: "
                        "python utils/session_grabber.py --site mercadolivre"
                    )
                    break

                # --- Trata popup de seleção de CEP (confirmado em produção) ---
                self._dismiss_cep_popup()

                # scroll humano para carregar lazy-load
                self._human_scroll(steps=10, step_px=300)

                # captura screenshot da página de busca
                self._last_screenshot_busca = self.capture_screenshot(identifier=f"{keyword}_p{page}", tipo="busca")

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
