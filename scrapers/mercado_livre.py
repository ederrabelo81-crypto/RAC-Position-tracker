"""
scrapers/mercado_livre.py — Scraper do Mercado Livre (mercadolivre.com.br).

Estratégia de extração:
  - URL de busca: https://lista.mercadolivre.com.br/{keyword_encoded}
  - Paginação: `_Desde_{itens_já_vistos + 1}` — o passo vem da contagem real de
    cards, não de um tamanho de página fixo (o ML já mudou de 48 para ~60)
  - Distinção Orgânico/Patrocinado: 5 camadas — classe do container, chip
    legado/Poly, texto "Patrocinado", aria-label/title e href de ad-tracking
    (click1.mercadolivre / mclics)
  - Avaliação: widget compacto (.poly-component__review-compacted) + Poly com
    contagem + legados + texto acessível + camada ESTRUTURAL (nós "4.8" e
    "(1.234)" adjacentes), imune a renomeação de classe.
    ⚠️ A SERP atual NÃO traz qtd de avaliações — o widget compacto é só
    estrela + nota (DOM real em tests/fixtures/ml_card_grid_20260725.html)
  - Loja Oficial: só com sinal explícito e escopado ao bloco do seller
  - Fulfillment (FULL): classe + atributo acessível + nó de texto exato
  - Preço: fragmentos `.andes-money-amount__fraction` + `.andes-money-amount__cents`

Login gate / device-verification (Jul/2026):
  - Detecção é EVIDÊNCIA primeiro (`_gate_reason`): URL de gate manda; senão,
    card de produto na página vence qualquer string ("Para continuar, acesse
    sua conta" e `/gz/webdevice` aparecem na SERP normal). O detector antigo,
    puramente textual, zerou a coleta de 31/07 inteira.
  - As tentativas ESCALAM: goto direto → re-aquece a home → entra pela caixa de
    busca da home (navegação in-site com Referer).
  - Gate persistente grava `logs/ml_gate_<kw>_p<N>.html` e, na 3ª keyword
    seguida, imprime o roteiro de conserto.
  - Antídotos, em ordem: login no perfil dedicado
    (`scripts/setup_local_profile.py --site mercadolivre`), sessão capturada
    (`utils/session_grabber.py --site mercadolivre`, injetada por
    `_inject_saved_session`) e fallback automático pela API oficial
    (`MLAPIScraper`, desligável com `RAC_ML_API_FALLBACK=0`).

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

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, PAGE_TIMEOUT
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled
from utils.text import parse_offer_count, parse_rating, parse_review_count


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
        # widget "compacto" — é o que o ML serve hoje no grid (confirmado no DOM
        # real em 25/07/2026): estrela + nota, SEM contagem. O escopo é
        # obrigatório: `.polylabel-label` sozinho também rotula "ou", "em" e
        # trechos de preço dentro do mesmo card.
        ".poly-component__review-compacted .polylabel-label",
        ".poly-reviews__rating",            # variante com contagem
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

# Marcadores de que a SERP REALMENTE renderizou. Testado por regex no HTML cru
# (não BeautifulSoup) porque isto roda a cada checagem de gate — até 6x por
# página — e a pergunta é só "tem card na tela?", não "quais são os cards".
# Espelha `item_container_candidates` + o wrapper da lista de resultados.
_SERP_CARD_RE = re.compile(
    r"ui-search-layout__item|ui-search-result__wrapper|poly-card"
    r"|ui-search-layout--grid__item|ui-search-results",
    re.I,
)

# URLs em que o ML TIROU o visitante da SERP — gate inequívoco, vale mesmo que
# a página ainda tenha resquício de card no DOM.
_GATE_URL_RE = re.compile(
    r"account-verification|/gz/webdevice|/lgz/|(?://|\.)login\.mercadoli[bv]re\.",
    re.I,
)

# Frases do gate de login / verificação de dispositivo. Só são consultadas
# quando a página NÃO tem card nenhum (ver `_gate_reason`): a SERP normal
# carrega o CTA de login no header e o script de device fingerprint
# (`/gz/webdevice`), então casar essas strings numa SERP cheia é falso positivo.
_GATE_TEXT_RE = re.compile(
    r"Para continuar, acesse sua conta"
    r"|Para continuar, ingresa a tu cuenta"
    r"|account-verification|/gz/webdevice",
    re.I,
)

# Cookies que indicam sessão LOGADA no ML (any-of). Só informativo: serve para
# o log dizer se a coleta está anônima (gate mais provável) ou autenticada.
_ML_LOGIN_COOKIES = ("orguseridp", "MELI_SESSION", "c_user_id")

# Depois de N keywords gateadas seguidas, o log imprime o roteiro de conserto
# (login no perfil, sessão capturada, fallback de API) uma única vez.
_GATE_STREAK_ALERT = 3


# ---------------------------------------------------------------------------
# Estado acumulado entre as páginas de UMA keyword
# ---------------------------------------------------------------------------

# Campos cuja ausência total NUMA KEYWORD denuncia quebra de extração.
#
# Ficam de fora de propósito (avaliados só no fim da run, em `_log_run_summary`):
#   `sponsored`    — SERP de cauda longa pode não ter anúncio nenhum.
#   `review_count` — o card do ML hoje usa o widget COMPACTO
#                    (`poly-component__review-compacted`), que renderiza estrela
#                    + nota e NENHUMA contagem (DOM real de 25/07/2026: zero
#                    ocorrências de "avalia" no card). Cobrar esse campo por
#                    keyword geraria WARNING em todas elas, todo dia.
_CRITICAL_FIELDS = ("rating", "seller")

# Home do ML — visitada uma vez por run antes da primeira busca. Um `goto`
# direto na rota de busca chega ao antibot antes do sensor.js validar a sessão;
# aquecer a home promove os cookies para /lista. Sem isso, as primeiras
# keywords da run tomam login gate em bloco (7 seguidas em 25/07 12:28).
_ML_HOME = "https://www.mercadolivre.com.br/"

# Tentativas extras quando a SERP responde com desafio/login gate. O gate do ML
# é frequentemente transitório: o card chega a navegar DEPOIS do
# domcontentloaded (visto como "Execution context was destroyed"), e a keyword
# 'ar condicionado 12000 btus' só rendeu 60 itens na 3ª tentativa.
_GATE_RETRIES = 2

# Espera antes de re-checar o gate, para não confundir estado transitório de
# navegação com bloqueio real.
_GATE_SETTLE_MS = 2_500


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
    _run_review_counts: int = 0
    _run_qtd_sellers: int = 0
    _run_sellers: int = 0
    _run_oficial: int = 0
    _warmed: bool = False
    _last_gate_reason: Optional[str] = None
    _gate_failures: int = 0
    _gate_hint_shown: bool = False
    _identity_reset: bool = False
    _api_scraper: Any = None

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
        self._warmed = False
        self._run_keywords = 0
        self._run_items = 0
        self._run_sponsored = 0
        self._run_review_counts = 0
        self._run_qtd_sellers = 0
        self._run_sellers = 0
        self._run_oficial = 0
        # Estado do gate: motivo da última checagem, keywords perdidas na run e
        # se o roteiro de conserto já foi impresso (uma vez por run).
        self._last_gate_reason = None
        self._gate_failures = 0
        self._gate_hint_shown = False
        self._identity_reset = False
        # Fallback pela API oficial: None = ainda não tentado, False = indisponível.
        self._api_scraper = None

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
                    self._log_session_state()
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome local "
                "não abriu — caindo para launch próprio (Playwright)"
            )
        super()._launch()
        # Fora do Chrome local o contexto nasce limpo — é aqui que a sessão
        # capturada (`python utils/session_grabber.py --site mercadolivre`)
        # entra. Sem isto, a instrução que o próprio log dava ao usuário no
        # gate persistente era inócua: ninguém lia o arquivo salvo.
        self._inject_saved_session()
        self._log_session_state()

    # ------------------------------------------------------------------
    # Sessão autenticada (cookies)
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_cookies(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Converte cookies do session_grabber/CDP para o formato do Playwright.

        Descarta o que `add_cookies` rejeita (chaves extras do CDP) e normaliza
        `sameSite` — o CDP exporta "no_restriction"/"unspecified", o Playwright
        só aceita "Strict"/"Lax"/"None".
        """
        same_site_map = {
            "no_restriction": "None", "none": "None",
            "lax": "Lax", "unspecified": "Lax",
            "strict": "Strict",
        }
        out: List[Dict[str, Any]] = []
        for c in raw or []:
            name, value = c.get("name"), c.get("value")
            if not name or value is None:
                continue
            cookie: Dict[str, Any] = {"name": name, "value": value}
            if c.get("domain"):
                cookie["domain"] = c["domain"]
                cookie["path"] = c.get("path") or "/"
            elif c.get("url"):
                cookie["url"] = c["url"]
            else:
                continue
            for key in ("httpOnly", "secure"):
                if isinstance(c.get(key), bool):
                    cookie[key] = c[key]
            expires = c.get("expires", c.get("expirationDate"))
            if isinstance(expires, (int, float)) and expires > 0:
                cookie["expires"] = float(expires)
            mapped = same_site_map.get(str(c.get("sameSite", "")).lower())
            if mapped:
                cookie["sameSite"] = mapped
            out.append(cookie)
        return out

    def _inject_saved_session(self) -> int:
        """
        Injeta no contexto os cookies salvos de `utils/sessions/mercadolivre.json`.

        Returns:
            Nº de cookies injetados (0 se não houver sessão válida/salva).
        """
        if self._context is None:
            return 0
        try:
            from utils.session_grabber import load_session_meta

            meta = load_session_meta("mercadolivre") or {}
            cookies = self._sanitize_cookies(meta.get("cookies") or [])
            if not cookies:
                return 0
            self._context.add_cookies(cookies)
            logger.info(
                f"[{self.platform_name}] Sessão capturada aplicada: "
                f"{len(cookies)} cookies (salva em {meta.get('saved_at', '?')})"
            )
            return len(cookies)
        except Exception as exc:
            logger.warning(
                f"[{self.platform_name}] Não consegui aplicar a sessão salva "
                f"({exc}) — seguindo anônimo."
            )
            return 0

    def _ml_cookies(self) -> List[Dict[str, Any]]:
        """Cookies do domínio do ML no contexto atual (lista vazia se falhar)."""
        if self._context is None:
            return []
        try:
            return self._context.cookies(
                ["https://www.mercadolivre.com.br", "https://lista.mercadolivre.com.br"]
            )
        except Exception:
            return []

    def _is_logged_in(self) -> bool:
        """True quando há cookie de sessão autenticada do ML no contexto."""
        names = {c.get("name") for c in self._ml_cookies()}
        return any(n in names for n in _ML_LOGIN_COOKIES)

    def _log_session_state(self) -> None:
        """Diz no log se a coleta começa autenticada — o gate depende disso."""
        cookies = self._ml_cookies()
        if self._is_logged_in():
            logger.info(
                f"[{self.platform_name}] Sessão AUTENTICADA no ML "
                f"({len(cookies)} cookies) — login gate improvável."
            )
        else:
            logger.info(
                f"[{self.platform_name}] Sessão ANÔNIMA ({len(cookies)} cookies). "
                "Funciona na maioria dos dias; se o gate persistir, logue no "
                "perfil: python scripts/setup_local_profile.py --site mercadolivre"
            )

    def _close(self) -> None:
        self._log_run_summary()
        if self._api_scraper:
            try:
                self._api_scraper._close()
            except Exception:
                pass
            self._api_scraper = None
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

    @staticmethod
    def _has_serp_cards(html: str) -> bool:
        """True se o HTML tem card de produto — a evidência de SERP utilizável."""
        return bool(html) and bool(_SERP_CARD_RE.search(html))

    def _gate_reason(self) -> Optional[str]:
        """
        Motivo do gate na página atual, ou None se ela é utilizável.

        Ordem deliberada — **evidência antes de string**:

          1. URL de gate  → o ML tirou o visitante da SERP. É gate, ponto.
          2. Tem card?    → NÃO é gate, mesmo que a frase de login apareça no
                            HTML. A SERP normal carrega o CTA "acesse sua conta"
                            no header e o script de device fingerprint
                            (`/gz/webdevice`); a checagem antiga casava esses
                            trechos e jogava fora SERPs cheias — foi assim que
                            a coleta de 31/07 devolveu 0 produto em 100% das
                            keywords com o Chrome real, perfil dedicado e IP
                            residencial (log do incidente).
          3. Sem card + frase de login/captcha → gate de verdade.

        Robusto a uso offline/testes: sem página Playwright associada (parser
        chamado com HTML avulso), retorna None em vez de estourar.
        """
        page = getattr(self, "_page", None)
        if page is None:
            return None
        try:
            url = page.url or ""
            if _GATE_URL_RE.search(url):
                return f"URL de gate ({url[:90]})"
            content = page.content()
        except Exception:
            return None

        if self._has_serp_cards(content):
            return None
        if _GATE_TEXT_RE.search(content):
            return "login/verificação de dispositivo (nenhum card na página)"
        if _BLOCK_SIGNALS_RE.search(content):
            return "captcha/bloqueio antibot (nenhum card na página)"
        return None

    def _login_gate_signal(self) -> bool:
        """
        Sinal cru de login/device-verification na página atual.

        Wrapper booleano de `_gate_reason` — o motivo fica em
        `self._last_gate_reason` para o log do incidente.
        """
        reason = self._gate_reason()
        self._last_gate_reason = reason
        return reason is not None

    def _is_login_gate(self, confirm: bool = False) -> bool:
        """
        Retorna True se a página atual for o login/device-verification gate do ML.

        Args:
            confirm: quando True, um sinal positivo é re-checado depois de a
                     página assentar. A SERP do ML navega DEPOIS do
                     `domcontentloaded` (o erro "Execution context was
                     destroyed" durante o scroll é a mesma causa), então checar
                     uma vez só transforma um estado transitório em keyword
                     perdida.
        """
        hit = self._login_gate_signal()
        if not hit or not confirm:
            return hit

        page = getattr(self, "_page", None)
        if page is None:
            return hit
        try:
            page.wait_for_timeout(_GATE_SETTLE_MS)
        except Exception:
            return hit
        return self._login_gate_signal()

    # ------------------------------------------------------------------
    # Aquecimento da sessão
    # ------------------------------------------------------------------

    def _warm_session(self) -> None:
        """
        Visita a home do ML uma vez por run, antes da primeira busca.

        Ir direto para `/lista` com sessão fria é o que faz o ML devolver o
        login gate em série no começo da coleta. Shopee e Casas Bahia já
        aquecem; o ML nunca aqueceu. Falha no warm-up não aborta a coleta — a
        busca segue e, se o gate aparecer, o retry por página cuida.
        """
        if self._warmed:
            return
        self._warmed = True  # uma tentativa por run, mesmo se falhar

        try:
            if self._local_active:
                lb = get_local_browser()
                if lb is not None and lb.warmup(
                    self._page, _ML_HOME, host_key="mercadolivre"
                ):
                    self._dismiss_cep_popup()
                    logger.info(f"[{self.platform_name}] Sessão aquecida na home (CDP)")
                    return

            self._page.goto(_ML_HOME, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            self._wait_for_network_idle()
            self._dismiss_cep_popup()
            self._human_scroll(steps=3, step_px=250)
            logger.info(f"[{self.platform_name}] Sessão aquecida na home")
        except Exception as exc:
            logger.warning(
                f"[{self.platform_name}] Warm-up da home falhou ({exc}) — "
                "seguindo direto para a busca."
            )

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
    def _detect_tipo_seller(item: Tag, seller: Optional[str]) -> Optional[str]:
        """
        Classifica o seller do card como "Loja Oficial", "3P" ou desconhecido.

        O card só ganha a linha `.poly-component__seller` quando o ML decide
        mostrar a loja — na prática, quase sempre loja oficial (na coleta de
        11/08/2026 a cobertura de `seller` e a de `oficial` bateram exatamente
        em todas as keywords). Num card SEM nome de seller e SEM selo, não há
        evidência de nada: devolver "3P" ali carimbava metade da SERP como
        marketplace terceiro só porque o ML não imprimiu o vendedor — o mesmo
        erro que já custou o `buy_box_seller` virar "Mercado Livre" por default.

        Returns:
            "Loja Oficial" com sinal explícito; "3P" quando há seller nomeado
            sem selo; None quando o card não informa o vendedor.
        """
        if MLScraper._official_store_evidence(item, seller):
            return "Loja Oficial"
        return "3P" if seller else None

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
            # A evidência é lida aqui (o histograma de camadas depende dela) e
            # a classificação sai de `_detect_tipo_seller` — fonte única, senão
            # o parser e o helper testado divergem com o tempo.
            evidence    = self._official_store_evidence(item, seller)
            tipo_seller = self._detect_tipo_seller(item, seller)

            # --- fulfillment ---
            fulfillment = self._is_fulfillment(item)

            # --- competição de sellers ("Outras opções de compra") ---
            # Texto, não seletor: o ML muda classe com frequência, mas o rótulo
            # PT-BR é estável. None quando não há sinal (≠ seller único).
            qtd_sellers = parse_offer_count(item.get_text(" ", strip=True))

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
                # Instrumentado junto com os demais: `Qtd Sellers` é um dos
                # campos que este PR passou a preencher, e sem cobertura por
                # keyword uma regressão voltaria a aparecer só semanas depois,
                # como 0% no Supabase, em vez de na hora no log da coleta.
                ("qtd_sellers", qtd_sellers),
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
                qtd_sellers=qtd_sellers,
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
            for name in ("title", "price", "url", "seller", "qtd_sellers",
                         "rating", "review_count", "fulfillment", "oficial",
                         "sponsored", "tag")
        )
        logger.info(f"[{self.platform_name}] cobertura '{keyword}': {resumo}")

        # INFO, não DEBUG: esta é a única auditoria de COMO cada card virou
        # "Loja Oficial". A coleta agendada roda em INFO, então o histograma
        # ficava invisível justamente onde ele é necessário — quando `oficial`
        # empata com `seller` em todas as keywords é aqui que se vê se a
        # classificação veio de selo real ou de um proxy que passou a mentir.
        camadas = {k.split("::", 1)[1]: v for k, v in cov.items() if k.startswith("oficial::")}
        if camadas:
            logger.info(f"[{self.platform_name}] Loja Oficial por camada: {camadas}")

        self._run_keywords += 1
        self._run_items += total
        self._run_sponsored += cov.get("sponsored", 0)
        self._run_review_counts += cov.get("review_count", 0)
        self._run_qtd_sellers += cov.get("qtd_sellers", 0)
        self._run_sellers += cov.get("seller", 0)
        self._run_oficial += cov.get("oficial", 0)

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
            f"{self._run_items} cards, {self._run_sponsored} patrocinados, "
            f"{self._run_review_counts} com qtd de avaliações"
        )
        if self._run_items and not self._run_review_counts:
            logger.info(
                f"[{self.platform_name}] Qtd de avaliações ausente em todos os "
                f"{self._run_items} cards — esperado enquanto o ML servir o "
                "widget compacto (estrela + nota, sem contagem)."
            )
        # `Qtd Sellers` zerado na run inteira NÃO é regressão: a SERP em grid
        # do ML não imprime "outras opções de compra" (o único texto de onde
        # `parse_offer_count` tira competição). Dizer isso aqui evita que a
        # próxima leitura do log trate 0/60 como parser quebrado — a competição
        # por buy box do ML só é observável no PDP/catálogo.
        if self._run_items and not self._run_qtd_sellers:
            logger.info(
                f"[{self.platform_name}] Qtd de sellers ausente em todos os "
                f"{self._run_items} cards — a SERP em grid não expõe contagem "
                "de ofertas; competição por buy box no ML exige PDP/catálogo."
            )

        # Empate exato entre `seller` e `oficial` = o ML só nomeou quem vende
        # nos cards de loja oficial. O restante da SERP fica com buy box sem
        # dono — o número que interessa para dimensionar essa cegueira.
        if self._run_sellers and self._run_sellers == self._run_oficial:
            sem_nome = self._run_items - self._run_sellers
            logger.info(
                f"[{self.platform_name}] Seller nomeado só em cards de Loja "
                f"Oficial ({self._run_sellers}/{self._run_items}); "
                f"{sem_nome} cards ficaram sem buy box identificado "
                "(Tipo Seller vazio, não '3P')."
            )

        if self._run_items and not self._run_sponsored:
            logger.warning(
                f"[{self.platform_name}] ZERO patrocinados em "
                f"{self._run_keywords} keywords ({self._run_items} cards). "
                "A SERP do ML sempre tem ads no topo — detecção provavelmente "
                "quebrada (ou a sessão está sem ads)."
            )

    # ------------------------------------------------------------------
    # Carregamento da SERP com insistência no desafio
    # ------------------------------------------------------------------

    def _rewarm_home(self) -> None:
        """Refaz o aquecimento da home (cookies novos) antes de reincidir na SERP."""
        self._warmed = False
        self._warm_session()

    def _goto_serp_via_home(self, keyword: str) -> bool:
        """
        Chega à SERP pela CAIXA DE BUSCA da home, como um usuário de verdade.

        Um `goto` direto em `/lista` é uma navegação sem Referer, sem o estado
        de sessão que a home acabou de montar — é o formato que o antibot do ML
        mais penaliza. Digitar na busca produz navegação in-site com Referer e
        sensor já rodando.

        Returns:
            True se terminou numa SERP com cards; False para o chamador cair no
            `goto` direto.
        """
        page = self._page
        page.goto(_ML_HOME, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        self._wait_for_network_idle()
        self._dismiss_cep_popup()

        box = page.locator(
            "input[name='as_word'], input#cb1-edit, "
            "input.nav-search-input, input[type='text'][role='combobox']"
        ).first
        box.click(timeout=5_000)
        box.fill("")
        box.type(keyword, delay=90)
        page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded")
        self._wait_for_network_idle()
        return self._has_serp_cards(page.content())

    def _try_solve_verification(self) -> bool:
        """
        Tenta atravessar a tela `/gz/account-verification` clicando no CTA dela.

        Essa tela é um interstício com um botão ("Continuar"/"Verificar") e o
        destino no `?go=`. Numa sessão logada ela costuma resolver no clique;
        anônima, normalmente cai no login — daí o False, e o chamador escala.

        Returns:
            True se saiu da URL de gate (a navegação seguiu para o `go=`).
        """
        page = self._page
        if not _GATE_URL_RE.search(page.url or ""):
            return False

        for sel in (
            "button[type='submit']",
            "button.andes-button--loud",
            "a.andes-button--loud",
            "[data-testid='action-primary']",
        ):
            try:
                page.locator(sel).first.click(timeout=3_000)
                page.wait_for_load_state("domcontentloaded")
                self._wait_for_network_idle()
            except Exception:
                continue
            if not _GATE_URL_RE.search(page.url or ""):
                logger.info(
                    f"[{self.platform_name}] Verificação de dispositivo "
                    f"atravessada pelo CTA ({sel})."
                )
                return True
        return False

    def _reset_anonymous_identity(self) -> bool:
        """
        Descarta a identidade anônima queimada (cookies do ML) e reaquece.

        O `_d2id` do ML é um id de DISPOSITIVO: uma vez marcado, toda navegação
        para `/lista` cai em `/gz/account-verification`, keyword após keyword.
        Sendo a sessão anônima, não há o que preservar — cookie novo é uma
        identidade nova. Numa sessão LOGADA isto deslogaria o usuário, então
        nunca roda nesse caso. Uma vez por run.

        Returns:
            True se os cookies foram limpos (e a home, reaquecida).
        """
        if self._identity_reset or self._context is None or self._is_logged_in():
            return False
        self._identity_reset = True

        limpou = False
        for domain in (".mercadolivre.com.br", "mercadolivre.com.br",
                       ".mercadolibre.com", "lista.mercadolivre.com.br"):
            try:
                self._context.clear_cookies(domain=domain)
                limpou = True
            except TypeError:
                # Playwright antigo: clear_cookies sem filtro apagaria a sessão
                # dos OUTROS scrapers no Chrome compartilhado — melhor não.
                logger.debug(
                    f"[{self.platform_name}] clear_cookies sem filtro de domínio "
                    "nesta versão do Playwright — pulei o reset de identidade."
                )
                return False
            except Exception:
                continue

        if not limpou:
            return False

        logger.warning(
            f"[{self.platform_name}] Identidade anônima queimada (device "
            "verification em série): cookies do ML descartados, reaquecendo a "
            "home com device id novo."
        )
        self._rewarm_home()
        return True

    def _gate_page_summary(self) -> str:
        """Título + primeiro texto visível da página de gate (vai para o log)."""
        try:
            titulo = (self._page.title() or "").strip()
        except Exception:
            titulo = ""
        texto = ""
        try:
            for sel in ("h1", "h2", "[class*='title']"):
                el = self._page.locator(sel).first
                texto = (el.text_content(timeout=1_500) or "").strip()
                if texto:
                    break
        except Exception:
            pass
        return f"título={titulo!r} texto={texto[:90]!r}"

    def _dump_gate_evidence(self, keyword: str, page_num: int) -> str:
        """
        Grava o HTML do gate e devolve o caminho — evidência do próximo incidente.

        Sem isto, "login gate" no log é indistinguível de falso positivo do
        detector: foi exatamente a dúvida que travou o diagnóstico de 31/07.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")[:40] or "kw"
        path = f"logs/ml_gate_{slug}_p{page_num}.html"
        try:
            html = self._page.content()
            Path("logs").mkdir(exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            return path
        except Exception:
            return "(não gravado)"

    def _log_gate_recovery_hint(self) -> None:
        """Roteiro de conserto, uma vez por run, quando o gate vira regra."""
        if self._gate_hint_shown:
            return
        self._gate_hint_shown = True
        modo = "Chrome local (CDP)" if self._local_active else "Playwright próprio"
        logado = "SIM" if self._is_logged_in() else "NÃO"
        logger.error(
            f"[{self.platform_name}] Gate em {self._gate_failures} keywords "
            f"seguidas (modo: {modo} | sessão logada: {logado}). Ordem de "
            "conserto:\n"
            "  1) Logue o perfil dedicado (resolve o device-verification):\n"
            "     python scripts/setup_local_profile.py --site mercadolivre\n"
            "  2) Confira o HTML salvo em logs/ml_gate_*.html — se ele tiver "
            "cards de produto, o problema é de detecção, não de bloqueio.\n"
            "  3) Sem Chrome local (VM/Actions), capture uma sessão:\n"
            "     python utils/session_grabber.py --site mercadolivre\n"
            "  4) Garanta o fallback de API (roda sozinho quando configurado):\n"
            "     ML_APP_ID/ML_APP_SECRET no .env + python scripts/ml_oauth_setup.py"
        )

    def _goto_serp(self, url: str, keyword: str, page: int) -> bool:
        """
        Navega para a SERP e insiste enquanto o ML devolver desafio/login gate.

        O gate do ML é largamente transitório: na coleta de 25/07 a keyword
        'ar condicionado 12000 btus' falhou duas vezes e rendeu 60 itens na
        terceira. Antes, o primeiro sinal encerrava a keyword com 0 produtos.

        As tentativas ESCALAM em vez de repetir o mesmo `goto` (que, repetido,
        só reforça o padrão que o antibot já recusou):
          1ª  `goto` direto na SERP;
          2ª  re-aquece a home (cookies novos) e repete o `goto`;
          3ª  entra pela caixa de busca da home — navegação in-site com Referer
              (só na página 1: as demais dependem do offset `_Desde_`).

        Returns:
            True se a página carregou utilizável; False se o gate persistiu.
        """
        first_page = "_Desde_" not in url

        for attempt in range(1, _GATE_RETRIES + 2):
            landed = False
            try:
                if attempt == 2:
                    self._rewarm_home()
                elif attempt >= 3 and first_page:
                    landed = self._goto_serp_via_home(keyword)
            except Exception as exc:
                logger.debug(
                    f"[{self.platform_name}] Escalada da tentativa {attempt} "
                    f"falhou ({exc}) — usando goto direto."
                )
            if not landed:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()

            if not self._is_login_gate(confirm=True):
                if attempt > 1:
                    logger.info(
                        f"[{self.platform_name}] Desafio superado na tentativa "
                        f"{attempt} (keyword='{keyword}', pág. {page})"
                    )
                self._gate_failures = 0
                return True

            # A tela de verificação de dispositivo é um interstício com CTA —
            # atravessá-la leva de volta ao `?go=` (a própria SERP).
            try:
                if self._try_solve_verification() and not self._is_login_gate():
                    self._gate_failures = 0
                    return True
            except Exception:
                pass

            if attempt <= _GATE_RETRIES:
                logger.warning(
                    f"[{self.platform_name}] Desafio/login gate na tentativa "
                    f"{attempt}/{_GATE_RETRIES + 1} (keyword='{keyword}', "
                    f"pág. {page}) — motivo: {self._last_gate_reason} — "
                    "escalando."
                )
                self._random_delay()

        self._gate_failures += 1
        evidencia = self._dump_gate_evidence(keyword, page)
        logger.error(
            f"[{self.platform_name}] Login gate persistente após "
            f"{_GATE_RETRIES + 1} tentativas (keyword='{keyword}', pág. {page}). "
            f"Motivo: {self._last_gate_reason} [{self._gate_page_summary()}]. "
            f"HTML em {evidencia}."
        )
        # Identidade anônima marcada: joga o device id fora antes da próxima
        # keyword em vez de repetir 40x o mesmo desafio com o mesmo cookie.
        try:
            self._reset_anonymous_identity()
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] Reset de identidade falhou: {exc}")
        if self._gate_failures >= _GATE_STREAK_ALERT:
            self._log_gate_recovery_hint()
        return False

    # ------------------------------------------------------------------
    # Fallback pela API oficial (OAuth) — dado algum em vez de dado nenhum
    # ------------------------------------------------------------------

    def _api_fallback_enabled(self) -> bool:
        """Fallback ligado por padrão; `RAC_ML_API_FALLBACK=0` desliga."""
        return os.getenv("RAC_ML_API_FALLBACK", "1").strip().lower() not in (
            "0", "false", "no", "nao", "off"
        )

    def _get_api_scraper(self) -> Any:
        """
        Instância (preguiçosa) do scraper de API, ou None se indisponível.

        `False` marca "já tentei e não dá" — não repetimos a checagem a cada
        keyword gateada.
        """
        if self._api_scraper is False:
            return None
        if self._api_scraper is not None:
            return self._api_scraper

        if not self._api_fallback_enabled():
            self._api_scraper = False
            return None
        if not (os.getenv("ML_APP_ID", "").strip()
                and os.getenv("ML_APP_SECRET", "").strip()):
            logger.warning(
                f"[{self.platform_name}] Sem fallback de API "
                "(ML_APP_ID/ML_APP_SECRET ausentes no .env) — a keyword gateada "
                "fica sem dado. Configure para blindar as próximas coletas."
            )
            self._api_scraper = False
            return None

        try:
            from scrapers.mercado_livre_api import MLAPIScraper

            api = MLAPIScraper()
            api._launch()
            self._api_scraper = api
            logger.info(
                f"[{self.platform_name}] Fallback pela API oficial ativado "
                "(posição patrocinada não existe na API — virão como orgânicos)."
            )
            return api
        except Exception as exc:
            logger.warning(
                f"[{self.platform_name}] Fallback de API indisponível: {exc}"
            )
            self._api_scraper = False
            return None

    def _page_is_usable(self) -> bool:
        """True enquanto a aba de coleta existe e não foi fechada."""
        page = self._page
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def _ensure_page(self) -> bool:
        """
        Garante uma aba viva antes de cada keyword.

        O Chrome compartilhado (``RAC_LOCAL_CHROME``) é a janela do usuário e
        some no meio da coleta com facilidade — fechar a janela basta. Antes
        desta checagem, a aba morta seguia sendo usada e cada keyword estourava
        ``Target page, context or browser has been closed``.

        Ordem de recuperação: aba nova no Chrome compartilhado (o
        ``LocalBrowser`` reconecta sozinho) → browser próprio do Playwright →
        desiste (o ``search`` cai na API oficial).

        Returns:
            True se há aba utilizável.
        """
        if self._page_is_usable():
            return True

        # Qualquer caminho daqui pra baixo entrega uma aba NOVA — de aba nova a
        # sessão é fria em todos eles, inclusive quando caímos para o browser
        # próprio. Sem zerar aqui, o `search()` pulava o `_warm_session()`
        # (que só roda uma vez por run) e mandava o browser recém-aberto direto
        # para a SERP, que é exatamente o que aciona o login gate do ML.
        self._warmed = False

        if self._local_active:
            lb = get_local_browser()
            page = lb.new_page() if lb is not None else None
            if page is not None:
                self._context = lb.context
                self._page = page
                self._page.set_default_timeout(PAGE_TIMEOUT)
                logger.warning(
                    f"[{self.platform_name}] Aba fechada — nova aba no Chrome "
                    "compartilhado"
                )
                return True
            logger.warning(
                f"[{self.platform_name}] Chrome local indisponível — abrindo "
                "browser próprio (Playwright) para seguir a coleta"
            )
            self._local_active = False
            self._context = None
        elif self._context is not None:
            try:
                self._page = self._context.new_page()
                self._page.set_default_timeout(PAGE_TIMEOUT)
                logger.warning(f"[{self.platform_name}] Aba fechada — nova aba")
                return True
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Contexto morto ({exc}) — "
                    "relançando o browser"
                )
                # `BaseScraper._close` de propósito: solta browser + referência
                # do runtime sem passar pelo `_close` do ML (que fecharia o
                # scraper de API e imprimiria o resumo da run no meio dela).
                BaseScraper._close(self)

        try:
            self._launch()
        except Exception as exc:
            logger.error(
                f"[{self.platform_name}] Não foi possível reabrir o browser: {exc}"
            )
            return False
        return self._page is not None

    def _api_fallback_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int,
    ) -> List[Dict[str, Any]]:
        """Coleta a keyword pela API oficial quando o browser levou gate."""
        api = self._get_api_scraper()
        if api is None:
            return []
        try:
            records = api.search(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                page_limit=page_limit,
            )
        except Exception as exc:
            logger.warning(
                f"[{self.platform_name}] Fallback de API falhou em "
                f"'{keyword}': {exc}"
            )
            return []
        if records:
            logger.success(
                f"[{self.platform_name}] Fallback de API salvou '{keyword}': "
                f"{len(records)} registros."
            )
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
        cursor = _SerpCursor()
        sample_card: Optional[Tag] = None
        gated = False

        # Browser morto não é motivo para perder a keyword: sem aba utilizável
        # a API oficial ainda coleta (quando ML_APP_ID/ML_APP_SECRET existem).
        if not self._ensure_page():
            all_records = self._api_fallback_search(
                keyword, keyword_category_map, page_limit
            )
            self._log_search_result(keyword, len(all_records))
            return all_records

        self._warm_session()

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, cursor.items_seen)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                # --- Carrega a SERP, insistindo se vier desafio/login gate ---
                if not self._goto_serp(url, keyword, page):
                    gated = True
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

        # Gate levou a keyword inteira: em vez de devolver nada, tenta a API
        # oficial (OAuth), que não passa pelo antibot da SERP.
        if gated and not all_records:
            all_records = self._api_fallback_search(
                keyword, keyword_category_map, page_limit
            )

        self._log_search_result(keyword, len(all_records))
        return all_records
