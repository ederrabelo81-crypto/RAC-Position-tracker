"""
scrapers/mercado_livre.py — Scraper do Mercado Livre (mercadolivre.com.br).

Estratégia de extração:
  - URL de busca: https://lista.mercadolivre.com.br/{keyword_encoded}
  - Paginação: `_Desde_{itens_já_vistos + 1}` — o passo vem da contagem real de
    cards, não de um tamanho de página fixo (o ML já mudou de 48 para ~60)
  - Distinção Orgânico/Patrocinado: 5 camadas — classe do container, chip
    legado/Poly, texto "Patrocinado", aria-label/title e href de ad-tracking
    (click1.mercadolivre / mclics)
  - Avaliação/reviews: seletores Poly (.poly-reviews__*) + legados + texto
    acessível "Avaliação 4,8 de 5 (1.234 avaliações)" + camada ESTRUTURAL
    (nós "4.8" e "(1.234)" adjacentes), imune a renomeação de classe
  - Loja Oficial: só com sinal explícito e escopado ao bloco do seller
  - Fulfillment (FULL): classe + atributo acessível + nó de texto exato
  - Preço: fragmentos `.andes-money-amount__fraction` + `.andes-money-amount__cents`

Notas de manutenção:
  - Se o ML alterar sua estrutura CSS, ajuste os seletores em _SELECTORS abaixo.
  - Todos os seletores estão centralizados neste dict para facilitar atualização.
  - Valide mudanças com `python scripts/diagnose_ml.py` (taxa de acerto por
    campo) — cobertura por plataforma fica na página 🩺 Data Health.
  - Cada keyword loga a cobertura por campo; campo zerado vira WARNING e grava
    `logs/ml_card_sample.html` para o conserto ser feito com o DOM na mão.
  - Histórico: avaliação/qtd_avaliações/patrocinado ficaram 0% Mar→Jun/2026
    porque os seletores Poly originais não existiam no DOM real (fix Jun/2026);
    avaliação seguiu 0% em 35 mil registros até o fix estrutural de Jul/2026.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, PAGE_TIMEOUT
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled
from utils.text import parse_rating, parse_review_count


# ---------------------------------------------------------------------------
# Seletores CSS centralizados — atualize aqui se o ML mudar o DOM
# ---------------------------------------------------------------------------
_SELECTORS = {
    # container de cada resultado (orgânico e patrocinado).
    # ML migra a classe do wrapper periodicamente (redesigns "Poly"); por isso
    # tentamos vários seletores em ordem de prioridade e usamos o PRIMEIRO que
    # retornar itens (ver _select_items). Nunca fazemos união — cada card é um
    # único nó, então parar no primeiro que casar evita contagem duplicada.
    "item_container_candidates": [
        "li.ui-search-layout__item",     # wrapper clássico (atual/legado)
        "div.ui-search-result__wrapper", # wrapper interno (variante Poly)
        "div.poly-card",                 # o próprio card Poly (fallback direto)
        "li.ui-search-layout--grid__item",  # variante grid recente
        ".ui-search-result",             # legado pré-Poly
    ],
    # mantido para retrocompat/diagnóstico — espelha o 1º candidato
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

# Não existe constante de itens por página aqui de propósito: ao contrário dos
# scrapers de API (que PEDEM o tamanho da página), quem decide a densidade da
# SERP do ML é o servidor. O antigo `_ITEMS_PER_PAGE = 48` ficou obsoleto quando
# a SERP passou a ~60 cards e fez a página 2 (`_Desde_49`) recoletar os itens
# 49..60 da página 1. O offset agora vem de `_SerpCursor.items_seen`.

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

# --- reviews por ESTRUTURA (independente de classe CSS) --------------------
# O widget de reviews do card renderiza dois nós de texto vizinhos: a nota
# ("4.8") e a contagem entre parênteses ("(1.234)"). Casamos o nó INTEIRO
# (^...$) porque só o widget de reviews produz um nó com exatamente esse
# formato: preço é "2.799" (3 decimais), parcela é "12x R$ 233", título é longo.
# Isso sobrevive à próxima renomeação de classe do Poly — que já custou 0% de
# avaliação em 100% dos registros de ML desde Mar/2026.
_RATING_NODE_RE = re.compile(r"^([0-5](?:[.,]\d))$")
_COUNT_NODE_RE  = re.compile(r"^\(\s*(\d{1,3}(?:[.,]\d{3})*|\d+)\s*\)$")

# selo de loja oficial — texto ("Loja oficial Midea") no bloco do seller
_OFFICIAL_STORE_RE = re.compile(r"loja\s+oficial|tienda\s+oficial", re.I)

# href de vitrine oficial: mercadolivre.com.br/loja/<slug> e variantes.
# Ancorado no host/raiz para não casar com "/loja/" no meio do slug de um produto.
_OFFICIAL_HREF_RE = re.compile(
    r"mercadoli[bv]re\.com(?:\.br)?/(?:loja|tienda)/"
    r"|^/(?:loja|tienda)/"
    r"|[?&]official_store_id=",
    re.I,
)

# selo FULL (Mercado Envios Full) em atributo acessível. Só vale como nó de
# texto EXATO — "Full" solto no título ("Full DC Inverter") não é fulfillment.
_FULL_ATTR_RE = re.compile(r"\bfull\b|fulfillment", re.I)
_FULL_TEXT_VALUES = {"full", "mercado envios full", "enviado pelo full"}

# id do item embutido no link de tracking de ads
# (…&pdp_filters=item_id%3AMLB6968369576)
_AD_ITEM_ID_RE = re.compile(r"item_id(?:%3A|:)MLB(\d+)", re.I)

# tags de destaque conhecidas do ML — fallback quando a classe CSS mudar
_KNOWN_TAG_RE = re.compile(
    r"\b(MAIS VENDIDO|OFERTA DO DIA|OFERTA IMPERD[ÍI]VEL|OFERTA REL[ÂA]MPAGO|"
    r"RECOMENDADO|MELHOR PRE[ÇC]O)\b",
    re.I,
)

# sinais de bloqueio/desafio — usados só quando 0 itens são encontrados, para
# distinguir "ML mudou o DOM" de "IP bloqueado / login gate / captcha".
# Padrões ancorados em frases reais de bloqueio (evita falso positivo com texto
# benigno como "robôs de cozinha" ou meta robots); aplicado ao HTML completo.
_BLOCK_SIGNALS_RE = re.compile(
    r"Para continuar, acesse sua conta"     # login gate ML
    r"|account-verification|/gz/webdevice"  # device verification ML
    r"|g-recaptcha|hcaptcha|px-captcha|captcha-delivery"  # widgets de captcha
    r"|unusual\s+traffic|access\s+denied|pardon\s+our\s+interruption"
    r"|(?:robot|bot)[^<>]{0,40}(?:check|challenge|verification)",
    re.I,
)


# ---------------------------------------------------------------------------
# Estado acumulado entre as páginas de UMA keyword
# ---------------------------------------------------------------------------

# Campos cuja ausência total NUMA KEYWORD denuncia quebra de extração.
# `sponsored` fica de fora de propósito: uma SERP de cauda longa pode não ter
# nenhum anúncio, e tratar isso como quebra geraria WARNING falso e sobrescreveria
# o card de amostra. Ads são avaliados no fim da run (ver `_log_run_summary`):
# zero em UMA keyword é normal, zero em TODAS é detecção quebrada.
_CRITICAL_FIELDS = ("rating", "review_count", "seller")


@dataclass
class _SerpCursor:
    """
    Estado que atravessa as páginas de uma mesma keyword.

    `items_seen` é a base tanto da Posição Geral quanto do offset `_Desde_` da
    próxima página — derivá-lo dos cards realmente parseados (em vez de um
    tamanho de página fixo) evita repetir itens quando o ML muda a densidade da
    SERP, e mantém Posição Orgânica/Patrocinada contínuas na keyword inteira.
    """

    items_seen: int = 0
    organic: int = 0
    sponsored: int = 0
    coverage: Dict[str, int] = field(default_factory=dict)

    def hit(self, field_name: str) -> None:
        """Contabiliza um campo extraído com sucesso (para o log de cobertura)."""
        self.coverage[field_name] = self.coverage.get(field_name, 0) + 1


class MLScraper(BaseScraper):
    """Scraper modular para o Mercado Livre."""

    platform_name = "Mercado Livre"

    # Agregados da run inteira (todas as keywords) — ver _log_run_summary.
    # Declarados na classe porque o parser também é usado offline via
    # `MLScraper.__new__` (testes e scripts/diagnose_ml.py), que pula o __init__.
    _run_keywords: int = 0
    _run_items: int = 0
    _run_sponsored: int = 0

    def __init__(self, headless: bool = True) -> None:
        # ML detecta Chromium headless como bot e exibe login gate.
        # Forçamos headless=False — no Oracle VM use xvfb para display virtual:
        #   sudo apt-get install -y xvfb
        #   Xvfb :99 -screen 0 1366x768x24 &
        #   export DISPLAY=:99
        super().__init__(headless=False)
        # Modo browser local (RAC_LOCAL_CHROME) — mesmo Chrome real/perfil
        # dedicado compartilhado com Shopee/Magalu/Casas Bahia. Um browser
        # Playwright lançado do zero (mesmo com stealth) ainda tem sinais de
        # automação que o ML cruza com IP de datacenter para mostrar o login
        # gate; o Chrome comum + CDP evita isso (ver scrapers/local_browser.py).
        self._local_active: bool = False
        self._run_keywords = 0
        self._run_items = 0
        self._run_sponsored = 0

    def _launch(self) -> None:
        """Preferência: Chrome real local (perfil dedicado) via CDP; senão, launch próprio."""
        if is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None:
                page = lb.new_page()
                if page is not None:
                    self._context = lb.context
                    self._page = page
                    self._page.set_default_timeout(PAGE_TIMEOUT)
                    self._local_active = True
                    logger.info(
                        f"[{self.platform_name}] Chrome real local (perfil "
                        "compartilhado) — fingerprint nativo, sem login gate"
                    )
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome local "
                "não abriu — caindo para launch próprio (Playwright)"
            )
        super()._launch()

    def _close(self) -> None:
        self._log_run_summary()
        # Modo browser local: fecha SÓ a aba dedicada — o Chrome é
        # compartilhado e fechado no fim da coleta (close_local_browser).
        if self._local_active:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            self._page = None
            self._context = None
            self._local_active = False
            return
        super()._close()

    def _is_login_gate(self) -> bool:
        """
        Retorna True se a página atual for o login/device-verification gate do ML.

        Robusto a uso offline/testes: se não houver página Playwright associada
        (parser chamado com HTML avulso), retorna False em vez de estourar.
        """
        page = getattr(self, "_page", None)
        if page is None:
            return False
        try:
            url = page.url
            if "account-verification" in url or "webdevice" in url:
                return True
            content = page.content()
            return "Para continuar, acesse sua conta" in content
        except Exception:
            return False

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, items_seen: int = 0) -> str:
        """
        Constrói a URL de busca do ML a partir de quantos itens já foram vistos.

        `_Desde_N` é o índice (base 1) do PRIMEIRO item da página, então a
        próxima página começa em `items_seen + 1`. Derivar isso da contagem real
        — em vez de `(page-1) * 48 + 1` — é o que impede a sobreposição: o ML
        passou a servir ~60 cards por página, e o passo fixo de 48 fazia a
        página 2 recomeçar em itens que a página 1 já havia coletado.

        Args:
            keyword:    termo buscado.
            items_seen: nº de cards já parseados nas páginas anteriores.

        Returns:
            URL absoluta da SERP.

        Example:
            >>> MLScraper._build_url("ar condicionado", 0)
            'https://lista.mercadolivre.com.br/ar-condicionado'
            >>> MLScraper._build_url("ar condicionado", 60)
            'https://lista.mercadolivre.com.br/ar-condicionado_Desde_61'
        """
        slug = quote_plus(keyword).replace("+", "-").lower()
        base = f"https://lista.mercadolivre.com.br/{slug}"
        if items_seen > 0:
            return f"{base}_Desde_{items_seen + 1}"
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
                # âncora "de 5" libera os padrões ambíguos (evita confundir
                # parcela/preço com rating); a contagem com a palavra
                # "avaliações" é inequívoca e vale mesmo em texto separado
                has_anchor = "de 5" in text
                if rating is None and has_anchor:
                    m = _RATING_OF5_RE.search(text)
                    if m:
                        rating = parse_rating(m.group(1))
                if count is None:
                    m = _COUNT_WORD_RE.search(text)
                    if m is None and has_anchor:
                        m = _COUNT_PARENS_RE.search(text)
                    if m:
                        count = parse_review_count(m.group(1))
                if rating is not None and count is not None:
                    break

        if rating is None or count is None:
            struct_rating, struct_count = MLScraper._extract_reviews_structural(item)
            if rating is None:
                rating = struct_rating
            if count is None:
                count = struct_count

        return rating, count

    @staticmethod
    def _extract_reviews_structural(item: Tag) -> Tuple[Optional[float], Optional[int]]:
        """
        Extrai (avaliação, qtd) pela ESTRUTURA do card, sem depender de classe CSS.

        O widget de reviews do ML renderiza a nota e a contagem como dois nós de
        texto vizinhos — "4.8" seguido de "(1.234)". Casamos o nó inteiro, então
        nada mais do card se confunde: preço tem 3 decimais ("2.799"), parcela
        tem "x", título é longo. Essa camada existe porque as duas anteriores são
        reféns dos nomes de classe do Poly, que já mudaram duas vezes e deixaram
        `avaliacao`/`qtd_avaliacoes` em 0% de preenchimento.

        Returns:
            Tupla (Optional[float], Optional[int]).
        """
        texts = [t.strip() for t in item.stripped_strings]
        fallback_rating: Optional[float] = None

        for idx, text in enumerate(texts):
            m = _RATING_NODE_RE.match(text)
            if not m:
                continue

            value = parse_rating(m.group(1))
            if value is None:
                continue

            # par nota + "(contagem)" — assinatura inequívoca do widget
            nxt = texts[idx + 1] if idx + 1 < len(texts) else ""
            count_match = _COUNT_NODE_RE.match(nxt)
            if count_match:
                return value, parse_review_count(count_match.group(1))

            # nota isolada (produto sem contagem visível): guarda como fallback
            if fallback_rating is None:
                fallback_rating = value

        return fallback_rating, None

    # ------------------------------------------------------------------
    # Tipo de seller: Loja Oficial vs 3P
    # ------------------------------------------------------------------

    @staticmethod
    def _seller_block(item: Tag) -> Optional[Tag]:
        """Retorna o nó que carrega a linha do seller ("Por Fulano"), se houver."""
        for sel in _SELECTORS["seller_candidates"]:
            el = item.select_one(sel)
            if el is not None:
                return el
        return None

    @staticmethod
    def _official_store_evidence(item: Tag, seller: Optional[str]) -> Optional[str]:
        """
        Retorna o nome da camada que PROVOU "Loja Oficial", ou None.

        Devolver a camada (em vez de um bool) é o que torna o campo auditável:
        `search()` loga o histograma por camada, então quando a classificação
        desandar o log já diz qual sinal passou a mentir.

        Todas as camadas são ancoradas em sinal EXPLÍCITO de loja oficial e
        escopadas ao bloco do seller. A varredura antiga de texto no card
        inteiro marcava 86% dos registros como "Loja Oficial" — inclusive
        vendedores 3P evidentes (ex: "KARZEN ELETRO", "REFRIGERAÇÃO MOTA") e
        cards sem seller nenhum.
        """
        if item.select_one(".ui-search-official-store-label"):
            return "label-legado"

        if seller and _OFFICIAL_STORE_RE.search(seller):
            return "nome-do-seller"

        if item.select_one(
            '[aria-label*="loja oficial" i], [title*="loja oficial" i], '
            '[alt*="loja oficial" i]'
        ):
            return "atributo-acessivel"

        for anchor in item.find_all("a", href=True):
            if _OFFICIAL_HREF_RE.search(anchor["href"]):
                return "href-vitrine"

        block = MLScraper._seller_block(item)
        if block is not None:
            if block.find(string=_OFFICIAL_STORE_RE):
                return "texto-no-bloco-seller"
            # escarapela (cockade) do Poly: só vale colada ao seller — solta no
            # card ela casaria com selos genéricos de outros componentes.
            if block.select_one('[class*="cockade" i]'):
                return "cockade-no-bloco-seller"

        return None

    @staticmethod
    def _detect_tipo_seller(item: Tag, seller: Optional[str]) -> str:
        """
        Classifica o seller do card como "Loja Oficial" ou "3P".

        Returns:
            "Loja Oficial" quando há sinal explícito; "3P" caso contrário.
        """
        return "Loja Oficial" if MLScraper._official_store_evidence(item, seller) else "3P"

    # ------------------------------------------------------------------
    # Extração de URL do produto
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_url(item: Tag) -> Optional[str]:
        """
        Extrai a URL do produto, preferindo o PDP ao link de tracking de ads.

        Cards patrocinados apontam para `click1.mercadolivre.com.br/mclics/...`,
        um link que expira e não serve para deduplicar nem casar com o catálogo.
        O id do item vem embutido nesse link (`pdp_filters=item_id%3AMLB…`), então
        quando só há âncora de ad reconstruímos o permalink canônico.

        Âncoras de vitrine (`/loja/<slug>`) são ignoradas: o candidato genérico
        `a[href*="mercadolivre.com"]` também casa o link da loja oficial, que no
        Poly vem DEPOIS do título. Num card patrocinado de loja oficial — a
        combinação mais comum no topo da SERP — isso devolvia a página da loja
        no lugar do produto anunciado.

        Returns:
            URL do PDP, ou None se o card não tiver âncora alguma.
        """
        ad_hrefs: List[str] = []

        for sel in _SELECTORS["url_candidates"]:
            for el in item.select(sel):
                href = (el.get("href") or "").strip()
                if not href:
                    continue
                href = href.split("#")[0]
                if _AD_HREF_RE.search(href):
                    ad_hrefs.append(href)
                    continue
                if _OFFICIAL_HREF_RE.search(href):
                    continue  # vitrine da loja, não é o produto
                return href  # âncora direta para o PDP — melhor caso

        for href in ad_hrefs:
            m = _AD_ITEM_ID_RE.search(href)
            if m:
                return f"https://produto.mercadolivre.com.br/MLB-{m.group(1)}"

        # sem id extraível: devolve o link de ad para não perder a referência
        return ad_hrefs[0] if ad_hrefs else None

    # ------------------------------------------------------------------
    # Detecção de Fulfillment (FULL)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fulfillment(item: Tag) -> bool:
        """
        Verifica se o item tem selo FULL (Mercado Envios Full).

        O selo é um ícone, não texto: por isso a checagem principal é por classe
        e por atributo acessível (aria-label/title/alt). O fallback textual casa
        o nó INTEIRO — a versão anterior aceitava "full" em qualquer posição e
        marcaria "Ar Condicionado Full DC Inverter" como fulfillment.
        """
        if item.select_one(_SELECTORS["fulfillment"]):
            return True
        if item.select_one('[class*="fulfillment" i]'):
            return True

        for el in item.find_all(True):
            for attr in ("aria-label", "title", "alt"):
                val = el.get(attr)
                if val and _FULL_ATTR_RE.search(str(val)):
                    return True

        return any(t.strip().lower() in _FULL_TEXT_VALUES for t in item.stripped_strings)

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

    @staticmethod
    def _select_items(soup: BeautifulSoup) -> tuple:
        """
        Localiza os cards de resultado testando os seletores de container em
        ordem de prioridade e retornando o PRIMEIRO que casar com itens.

        ML troca a classe do wrapper a cada redesign; percorrer candidatos torna
        o scraper resiliente a essas mudanças sem exigir patch a cada quebra.

        Returns:
            Tupla (itens, seletor_usado). Se nada casar, ([], None).
        """
        for sel in _SELECTORS["item_container_candidates"]:
            found = soup.select(sel)
            if found:
                return found, sel
        return [], None

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
        cursor: Optional[_SerpCursor] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extrai todos os produtos de uma página HTML do ML.

        Args:
            html:                 conteúdo HTML da página
            keyword:              keyword que gerou essa SERP
            keyword_category_map: mapa de categorias para _build_record
            page_offset:          itens já coletados (usado só quando `cursor`
                                  não é passado — compatibilidade)
            cursor:               estado acumulado da keyword. Quando presente,
                                  é a fonte da Posição Geral e mantém as posições
                                  Orgânica/Patrocinada contínuas entre páginas;
                                  a função o avança com os itens desta página.

        Returns:
            Lista de dicts no formato do DataFrame de saída.
        """
        cursor = cursor if cursor is not None else _SerpCursor(items_seen=page_offset)
        soup = BeautifulSoup(html, "html.parser")
        items, used_selector = self._select_items(soup)
        if used_selector and used_selector != _SELECTORS["item_container_candidates"][0]:
            # o wrapper primário sumiu — sinaliza qual fallback salvou a coleta
            logger.warning(
                f"[{self.platform_name}] Container primário ausente; usando "
                f"fallback {used_selector!r} ({len(items)} itens). "
                "Revise _SELECTORS se isto persistir (ML mudou o DOM)."
            )
        logger.info(f"[{self.platform_name}] {len(items)} itens encontrados na página")

        # Diagnóstico: se nenhum item for encontrado, salva HTML e caracteriza
        # a falha (bloqueio/desafio vs. mudança de DOM) para o próximo incidente.
        if not items:
            debug_path = f"logs/ml_debug_{cursor.items_seen}.html"
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                debug_path = "(não gravado)"
            page_title = soup.title.get_text(strip=True) if soup.title else ""
            looks_blocked = self._is_login_gate() or bool(
                _BLOCK_SIGNALS_RE.search(html)
            )
            tried = ", ".join(_SELECTORS["item_container_candidates"])
            logger.warning(
                f"[{self.platform_name}] Nenhum item encontrado "
                f"(keyword='{keyword}', title={page_title!r}, "
                f"len_html={len(html)}, possível_bloqueio={looks_blocked}). "
                f"Seletores testados: [{tried}]. HTML salvo em {debug_path}."
            )

        records = []

        for idx, item in enumerate(items):
            # Posição Geral e os contadores de Orgânica/Patrocinada vêm do cursor
            # da keyword: reiniciá-los por página fazia a página 2 repetir
            # "Posição Orgânica 1, 2, 3…" — dois produtos distintos disputando a
            # mesma posição no mesmo turno, o que corrompe share e SOV.
            pos_general = cursor.items_seen + idx + 1
            sponsored   = self._is_sponsored(item)

            if sponsored:
                cursor.sponsored += 1
                cursor.hit("sponsored")
                pos_organic    = None
                pos_sponsored  = cursor.sponsored
            else:
                cursor.organic += 1
                pos_organic    = cursor.organic
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

            # --- seller: None quando o card não informa. NÃO cair para
            # "Mercado Livre" — isso inventava um vencedor de buy box e fez o
            # ML aparecer como 2º maior "buy box seller" da categoria (13,5%).
            seller: Optional[str] = None
            for sel in _SELECTORS["seller_candidates"]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    # Poly prefixa com "Por " (ex: "Por WebContinental")
                    seller = re.sub(
                        r"^por\s+", "", el.get_text(strip=True), flags=re.I
                    ).strip() or None
                    break

            # --- URL do produto ---
            url_produto = self._extract_url(item)

            # --- tipo de seller: Loja Oficial vs 3P (sinal explícito) ---
            evidence   = self._official_store_evidence(item, seller)
            tipo_seller = "Loja Oficial" if evidence else "3P"

            # --- fulfillment ---
            fulfillment = self._is_fulfillment(item)

            # --- avaliação + qtd avaliações (CSS + texto acessível + estrutura) ---
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

            for name, value in (
                ("title", title), ("price", price), ("url", url_produto),
                ("seller", seller), ("rating", rating),
                ("review_count", review_count), ("tag", tag),
            ):
                if value is not None:
                    cursor.hit(name)
            if fulfillment:
                cursor.hit("fulfillment")
            if evidence:
                cursor.hit("oficial")
                cursor.hit(f"oficial::{evidence}")

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

        cursor.items_seen += len(items)
        return records

    # ------------------------------------------------------------------
    # Instrumentação de cobertura
    # ------------------------------------------------------------------

    def _log_coverage(self, keyword: str, cursor: _SerpCursor, sample: Optional[Tag]) -> None:
        """
        Loga a cobertura por campo da keyword e denuncia campos zerados.

        Um campo que zera não derruba a coleta — ela continua devolvendo
        registros, só que ocos. Foi assim que `avaliacao`/`qtd_avaliacoes`
        passaram meses em 0% sem ninguém notar. Aqui a regressão aparece no log
        da própria coleta, e o card de amostra vai para `logs/` para permitir o
        conserto com evidência em vez de chute.
        """
        total = cursor.items_seen
        if not total:
            return

        cov = cursor.coverage
        resumo = " ".join(
            f"{name}={cov.get(name, 0)}/{total}"
            for name in ("title", "price", "url", "seller", "rating",
                         "review_count", "fulfillment", "oficial", "sponsored", "tag")
        )
        logger.info(f"[{self.platform_name}] cobertura '{keyword}': {resumo}")

        camadas = {k.split("::", 1)[1]: v for k, v in cov.items() if k.startswith("oficial::")}
        if camadas:
            logger.debug(f"[{self.platform_name}] Loja Oficial por camada: {camadas}")

        self._run_keywords += 1
        self._run_items += total
        self._run_sponsored += cov.get("sponsored", 0)

        zerados = [f for f in _CRITICAL_FIELDS if not cov.get(f)]
        if not zerados:
            return

        dump = "(não gravado)"
        if sample is not None:
            try:
                path = "logs/ml_card_sample.html"
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(str(sample))
                dump = path
            except Exception:
                pass
        logger.warning(
            f"[{self.platform_name}] Campos ZERADOS em '{keyword}': "
            f"{', '.join(zerados)} ({total} cards lidos). O DOM do card mudou — "
            f"card de amostra em {dump}."
        )

    def _log_run_summary(self) -> None:
        """
        Fecha a run com o veredito sobre patrocinados.

        Uma keyword sem anúncio é rotina; a coleta inteira sem um único anúncio
        não é — foi assim que `patrocinado` ficou 0% de Mar a Jun/2026. O sinal
        só faz sentido agregado, por isso mora aqui e não no log por keyword.
        """
        if not self._run_keywords:
            return

        logger.info(
            f"[{self.platform_name}] run: {self._run_keywords} keywords, "
            f"{self._run_items} cards, {self._run_sponsored} patrocinados"
        )
        if self._run_items and not self._run_sponsored:
            logger.warning(
                f"[{self.platform_name}] ZERO patrocinados em "
                f"{self._run_keywords} keywords ({self._run_items} cards). "
                "A SERP do ML sempre tem ads no topo — detecção provavelmente "
                "quebrada (ou a sessão está sem ads)."
            )

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
        cursor = _SerpCursor()
        sample_card: Optional[Tag] = None

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, cursor.items_seen)
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

                # extrai os dados — o cursor carrega offset e posições entre páginas
                if sample_card is None:
                    found, _ = self._select_items(soup)
                    sample_card = found[0] if found else None

                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    cursor=cursor,
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

        self._log_coverage(keyword, cursor, sample_card)
        self._log_search_result(keyword, len(all_records))
        return all_records
