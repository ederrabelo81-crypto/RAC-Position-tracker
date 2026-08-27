"""
scrapers/base.py — Classe base abstrata para todos os scrapers.

Define:
  - Inicialização do Playwright com stealth e rotação de User-Agent
  - Método abstrato `search()` que cada scraper deve implementar
  - Helpers compartilhados: scroll humano, delay aleatório, snapshot de HTML
  - Construção do registro de dados (linha do DataFrame) com campos fixos
"""

import math
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from loguru import logger
from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from config import (
    ANALYST_NAME,
    ENABLE_SCREENSHOTS,
    MAX_DELAY,
    MIN_DELAY,
    NETWORK_IDLE_TIMEOUT,
    PAGE_TIMEOUT,
    PLATFORM_TYPE,
    SCREENSHOTS_BUCKET,
    SCREENSHOTS_DIR,
    SCREENSHOTS_RETENTION_DAYS,
    SCREENSHOTS_UPLOAD_SUPABASE,
    SCREENSHOTS_VIEWPORT,
    USER_AGENTS,
)
from scrapers import playwright_runtime
from utils.brands import extract_brand
from utils.text import (
    MAX_COUNT_SANE,
    get_turno,
    infer_keyword_category,
    normalize_text,
    now_brt,
    parse_review_count,
)
from utils.normalize_product import normalize_product_name, normalize_product_name_v2
from utils.seller_names import normalize_seller_name


class BaseScraper(ABC):
    """
    Classe base para scrapers de marketplace.

    Cada subclasse deve:
      1. Definir `platform_name` (str) com o nome da plataforma.
      2. Implementar `search(keyword, page_limit)` retornando lista de dicts.
      3. Implementar `_parse_results(html, keyword)` para extrair dados do HTML.
    """

    platform_name: str = "Base"

    def __init__(self, headless: bool = True) -> None:
        """
        Args:
            headless: se True, executa o browser sem interface gráfica.
                      Definir False para depuração visual.
        """
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._user_agent = random.choice(USER_AGENTS)

        # ScreenshotManager só é instanciado quando a flag está ativa —
        # garante zero overhead/import quando desligado.
        self.screenshot_manager = None
        self._last_screenshot_busca: Optional[str] = None
        if ENABLE_SCREENSHOTS:
            try:
                from utils.screenshot_manager import ScreenshotManager
                self.screenshot_manager = ScreenshotManager(
                    base_dir=SCREENSHOTS_DIR,
                    retention_days=SCREENSHOTS_RETENTION_DAYS,
                    bucket_name=SCREENSHOTS_BUCKET,
                    viewport=SCREENSHOTS_VIEWPORT,
                    upload_enabled=SCREENSHOTS_UPLOAD_SUPABASE,
                )
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] ScreenshotManager não inicializado: {exc}"
                )
                self.screenshot_manager = None

    # ------------------------------------------------------------------
    # Gerenciamento de ciclo de vida do browser
    # ------------------------------------------------------------------

    # Patch JS completo — WAF bypass com máxima stealth
    _STEALTH_JS = """
        // Remove webdriver detection (primary WAF indicator)
        Object.defineProperty(navigator, 'webdriver', {get: () => false});
        try { delete navigator.__proto__.webdriver; } catch(_) {}

        // Chrome API simulation
        window.chrome = {
            runtime: {
                onConnect: {addListener: () => {}},
                onMessage: {addListener: () => {}},
                id: undefined,
            },
            loadTimes: () => ({}),
            csi: () => ({}),
        };

        // Plugins array (Firefox has real plugins)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const a = [1,2,3,4,5];
                a.item = () => null;
                return a;
            }
        });

        // Language preference (Brazilian Portuguese)
        Object.defineProperty(navigator, 'languages', {
            get: () => ['pt-BR', 'pt', 'en-US', 'en']
        });

        // Permissions API
        const _origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : _origQuery(p);

        // Remove headless detection
        Object.defineProperty(document, 'hidden', {get: () => false});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});

        // UA string normalization
        const baseUA = navigator.userAgent;
        Object.defineProperty(navigator, 'userAgent', {
            get: () => baseUA.replace(/HeadlessChrome/, 'Chrome')
        });
    """

    def _launch(self) -> None:
        """Inicia o Playwright, o browser e o contexto com configurações stealth.

        O handle do Playwright vem do runtime compartilhado
        (``scrapers/playwright_runtime``), NUNCA de um ``sync_playwright()``
        próprio: com o Chrome local ligado (``RAC_LOCAL_CHROME``) já existe um
        handle vivo na thread, e um segundo estoura "Sync API inside the
        asyncio loop" — o que derrubava Amazon, Google Shopping, Leroy e
        Dealers inteiros na coleta de 14/08/2026.
        """
        self._playwright, _ = playwright_runtime.acquire()
        if self._playwright is None:
            raise RuntimeError(
                "Playwright indisponível. Execute: pip install playwright && "
                "python -m playwright install chromium"
            )

        # Tenta Chrome real primeiro (menos detectável que Chromium headless).
        # Chrome real tem TLS fingerprint diferente — Shopee e Akamai aceitam melhor.
        _launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ]
        self._browser = None
        used_channel = None
        for channel in ["chrome", "msedge", None]:
            try:
                self._browser = self._playwright.chromium.launch(
                    headless=self.headless,
                    channel=channel,
                    args=_launch_args,
                )
                used_channel = channel or "chromium"
                break
            except Exception:
                continue

        if self._browser is None:
            # Solta a referência antes de lançar — sem isso o handle fica
            # pendurado até o fim do processo e o próximo scraper recebe
            # "Sync API inside asyncio loop".
            self._playwright = None
            playwright_runtime.release()
            raise RuntimeError(
                "Não foi possível iniciar nenhum browser (chrome/msedge/chromium). "
                "Execute: python -m playwright install chromium"
            )

        # Viewport maior quando capturando screenshots para evidência mais legível
        if self.screenshot_manager is not None:
            vp_w, vp_h = SCREENSHOTS_VIEWPORT
        else:
            vp_w, vp_h = 1366, 768

        # Daqui pra baixo já existe browser: qualquer falha tem que devolver o
        # browser E a referência do handle. `__exit__` NÃO roda quando
        # `__enter__` levanta, então sem este cleanup um `new_context()` que
        # falhasse deixava um Chromium órfão e o handle preso até o fim do
        # processo — e o `main.py` seguiria para o próximo scraper assim.
        try:
            self._context = self._browser.new_context(
                user_agent=self._user_agent,
                viewport={"width": vp_w, "height": vp_h},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                accept_downloads=False,
            )

            self._context.add_init_script(self._STEALTH_JS)

            self._page = self._context.new_page()
            self._page.set_default_timeout(PAGE_TIMEOUT)
        except Exception:
            BaseScraper._close(self)
            raise

        logger.info(
            f"[{self.platform_name}] Browser iniciado ({used_channel}) | UA: {self._user_agent[:60]}..."
        )

    def _close(self) -> None:
        """Encerra browser e playwright de forma limpa.

        Cada nível é fechado por conta própria: encadeados num único ``try``, um
        ``page.close()`` que falhasse pulava o ``browser.close()`` e deixava um
        Chromium órfão — logo no caminho de limpeza, que só roda quando algo já
        deu errado.
        """
        had_handle = self._playwright is not None
        try:
            for nivel in (self._page, self._context, self._browser):
                if nivel is None:
                    continue
                try:
                    nivel.close()
                except Exception as exc:
                    logger.warning(
                        f"[{self.platform_name}] Erro ao fechar "
                        f"{type(nivel).__name__}: {exc}"
                    )
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            # O handle é compartilhado: só devolvemos a referência. Ele só para
            # de fato quando o último usuário (ex.: o Chrome local) soltar.
            if had_handle:
                playwright_runtime.release()

    def _rotate_browser(self) -> None:
        """
        Fecha e reinicia o browser para resetar cookies e fingerprint de
        bot-managers (ex: Radware). Sorteia um novo User-Agent também.
        """
        logger.info(f"[{self.platform_name}] Rotacionando browser (reset de fingerprint)...")
        self._close()
        self._user_agent = random.choice(USER_AGENTS)
        time.sleep(random.uniform(3.0, 7.0))
        self._launch()

    # ------------------------------------------------------------------
    # Context manager — permite uso com `with MLScraper() as s:`
    # ------------------------------------------------------------------

    def __enter__(self) -> "BaseScraper":
        self._launch()
        if self.screenshot_manager is not None:
            try:
                self.screenshot_manager.cleanup_expired()
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Cleanup de screenshots falhou: {exc}"
                )
        return self

    # ------------------------------------------------------------------
    # Hook de screenshot — no-op silencioso quando ENABLE_SCREENSHOTS=False
    # ------------------------------------------------------------------

    def capture_screenshot(
        self,
        identifier: str,
        tipo: str = "busca",
        full_page: bool = False,
    ) -> Optional[str]:
        """
        Captura a página atual via ScreenshotManager.

        Retorna o caminho remoto/local ou None se desligado/indisponível.
        Seguro de chamar mesmo com ENABLE_SCREENSHOTS=False — vira no-op.

        Se tipo="busca", armazena em self._last_screenshot_busca para uso em _build_record.
        """
        if self.screenshot_manager is None or self._page is None:
            return None

        url = self.screenshot_manager.capture(
            page=self._page,
            platform=self.platform_name,
            identifier=identifier,
            tipo=tipo,
            full_page=full_page,
        )

        # Armazena screenshot de busca para passar ao _build_record
        if tipo == "busca":
            self._last_screenshot_busca = url

        return url

    def __exit__(self, *_) -> None:
        self._close()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_search_result(self, keyword: str, count: int) -> None:
        """
        Loga o resultado final de uma keyword no nível correto.

        SUCCESS com 0 produtos mascarava bloqueios (Akamai/CAPTCHA) na leitura
        do log — keyword sem dados sai como WARNING para o grep de
        `(ERROR|WARNING)` do monitoramento capturá-la.

        Args:
            keyword: termo buscado (ou nome do dealer).
            count:   nº de registros coletados para a keyword.
        """
        if count:
            logger.success(
                f"[{self.platform_name}] '{keyword}' → {count} produtos coletados"
            )
        else:
            logger.warning(
                f"[{self.platform_name}] '{keyword}' → 0 produtos coletados"
            )

    # ------------------------------------------------------------------
    # Helpers de interação humana com a página
    # ------------------------------------------------------------------

    def _random_delay(self, min_s: float = MIN_DELAY, max_s: float = MAX_DELAY) -> None:
        """Aguarda um intervalo aleatório para simular comportamento humano."""
        delay = random.uniform(min_s, max_s)
        logger.debug(f"[{self.platform_name}] Aguardando {delay:.1f}s...")
        time.sleep(delay)

    def _human_scroll(self, steps: int = 8, step_px: int = 350) -> None:
        """
        Rola a página suavemente em múltiplos passos com pequenas pausas.
        Garante que imagens lazy-load e conteúdo JS sejam carregados.

        Args:
            steps:   número de incrementos de scroll
            step_px: pixels por incremento
        """
        for i in range(steps):
            self._page.evaluate(f"window.scrollBy(0, {step_px})")
            time.sleep(random.uniform(0.15, 0.45))
        # scroll de volta ao topo para não afetar a paginação
        time.sleep(random.uniform(0.3, 0.7))

    def _get_soup(self) -> BeautifulSoup:
        """Retorna o BeautifulSoup do HTML atual da página."""
        html = self._page.content()
        return BeautifulSoup(html, "html.parser")

    def _wait_for_network_idle(self) -> None:
        """Aguarda a rede estabilizar após navegação."""
        try:
            self._page.wait_for_load_state(
                "networkidle", timeout=NETWORK_IDLE_TIMEOUT
            )
        except Exception:
            # timeout de networkidle é tolerado — página pode ter polling
            pass

    def _wait_for_products(
        self,
        timeout: int = 10000,
        item_selectors: Optional[List[str]] = None,
    ) -> bool:
        """
        Aguarda renderização de produtos com múltiplos seletores.
        Útil para dealers com estruturas DOM variadas.

        Args:
            timeout: tempo máximo em ms
            item_selectors: lista de seletores a tentar (usa padrão se None)

        Returns:
            True se produtos encontrados, False se timeout
        """
        if item_selectors is None:
            # Seletores genéricos para cobrir VTEX, WooCommerce, custom
            item_selectors = [
                'article[class*="vtex-product-summary"]',
                'li.product-summary',
                'ul.products li.product',
                '[class*="product-card"]',
                '[data-sku]',
                '[data-product-id]',
                '.pdc_product-item',  # SAP Hybris
                '.cardprod',  # EngageEletro
            ]

        combined_selector = ", ".join(item_selectors)
        try:
            self._page.wait_for_selector(combined_selector, timeout=timeout)
            logger.debug(f"[{self.platform_name}] Produtos renderizados com sucesso")
            return True
        except Exception as e:
            logger.warning(
                f"[{self.platform_name}] Timeout aguardando produtos ({timeout}ms): {e}"
            )
            return False

    def _inject_form_value(self, selector: str, value: str) -> bool:
        """
        Injeta valor em um input/select e pressiona Enter.
        Usado para CEP injection (Frigelar), filtros, etc.

        Args:
            selector: seletor CSS do input
            value: valor a injetar

        Returns:
            True se sucesso, False se elemento não encontrado
        """
        try:
            elem = self._page.query_selector(selector)
            if not elem:
                logger.debug(f"[{self.platform_name}] Input não encontrado: {selector}")
                return False

            elem.fill(value)
            elem.press("Enter")
            logger.debug(f"[{self.platform_name}] Valor injetado: {selector} = {value}")

            # Aguardar página processar o input
            time.sleep(random.uniform(1.0, 3.0))
            return True
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Erro ao injetar valor: {e}")
            return False

    def _check_waf_block(self) -> bool:
        """
        Detecta se página foi bloqueada por WAF (403, "Access Denied", etc).

        Returns:
            True se bloqueado, False se OK
        """
        try:
            html = self._page.content()
            text = html.lower()

            # Padrões de WAF block
            waf_indicators = [
                "403",
                "access denied",
                "please wait",
                "checking your browser",
                "valide seu acesso",
                "insira um cep",
                "too many requests",
                "rate limit",
            ]

            for indicator in waf_indicators:
                if indicator in text:
                    logger.warning(
                        f"[{self.platform_name}] WAF block detectado: {indicator}"
                    )
                    return True

            return False
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao verificar WAF: {e}")
            return False

    def _dump_debug_html(self, filename_prefix: str = "debug") -> str:
        """
        Salva HTML da página para debug (útil quando 0 produtos encontrados).

        Args:
            filename_prefix: prefixo do arquivo (ex: "debug_frigelar_p1")

        Returns:
            Path do arquivo salvo
        """
        from pathlib import Path

        try:
            html = self._page.content()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{filename_prefix}_{timestamp}.html"
            filepath = Path("logs") / filename

            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(html, encoding="utf-8")

            logger.debug(f"[{self.platform_name}] Debug HTML salvo: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Erro ao salvar debug HTML: {e}")
            return ""

    # ------------------------------------------------------------------
    # Construção de registro de dado padronizado
    # ------------------------------------------------------------------

    def _coerce_count(
        self,
        value: Any,
        field: str,
        keyword: str,
    ) -> Optional[int]:
        """
        Normaliza um contador (avaliações, sellers) para int — ou None.

        As colunas "Qtd Avaliações" e "Qtd Sellers" são tipadas como ``Int64``
        na exportação. Um valor fracionário ou grande demais ali não vira uma
        célula estranha: derruba o `astype` e, com ele, o CSV inteiro (run #174).
        A conversão acontece aqui, na única camada que conhece **plataforma e
        keyword** — sem esse contexto o aviso não diz onde consertar o parser.

        Args:
            value:   valor cru vindo do parser (int, float, str ou None).
            field:   nome da coluna, só para o log (ex: "Qtd Avaliações").
            keyword: keyword em coleta, para localizar a origem do problema.

        Returns:
            Inteiro ≥ 0, ou None quando ausente/implausível.
        """
        if value is None or isinstance(value, bool):
            return None

        if isinstance(value, str):
            # Strings já sabem lidar com "1.234"/"1,2 mil" no parser dedicado.
            return parse_review_count(value)

        if isinstance(value, int):
            number: float = float(value)
            rounded = value
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                logger.debug(
                    f"[{self.platform_name}] {field} não numérico ({value!r}) "
                    f"em '{keyword}' — descartado."
                )
                return None
            if math.isnan(number) or math.isinf(number):
                return None
            rounded = int(round(number))
            if rounded != number:
                logger.warning(
                    f"[{self.platform_name}] {field} fracionário ({number!r}) "
                    f"na keyword '{keyword}' — arredondado para {rounded}. "
                    "O parser de origem deveria devolver inteiro."
                )

        if rounded < 0 or rounded > MAX_COUNT_SANE:
            logger.warning(
                f"[{self.platform_name}] {field} implausível ({rounded}) na "
                f"keyword '{keyword}' — descartado (limite: {MAX_COUNT_SANE})."
            )
            return None
        return rounded

    def _build_record(
        self,
        *,
        keyword: str,
        keyword_category_map: dict,
        title: Optional[str],
        position_general: int,
        position_organic: Optional[int],
        position_sponsored: Optional[int],
        price_raw: Optional[str] = None,
        price_float: Optional[float] = None,
        seller: Optional[str] = None,
        is_fulfillment: bool = False,
        rating: Optional[float] = None,
        review_count: Optional[int] = None,
        tag_destaque: Optional[str] = None,
        url_produto: Optional[str] = None,
        screenshot_busca: Optional[str] = None,
        screenshot_produto: Optional[str] = None,
        # ── Foco em insights (Mai/2026): buy box, sellers, competição ──
        buy_box_seller: Optional[str] = None,
        qtd_sellers: Optional[int] = None,
        tipo_seller: Optional[str] = None,
        reputacao_seller: Optional[str] = None,
        # ── Identidade da oferta (Fase 1 da auditoria — Ago/2026) ──
        marketplace_product_id: Optional[str] = None,
        marketplace_offer_id: Optional[str] = None,
        seller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monta um dicionário compatível com as colunas do DataFrame de saída.

        Aceita preço como string bruta OU float já parseado.

        Campos de insight (foco principal a partir de Mai/2026):
            buy_box_seller:   seller que vence a oferta principal (buy box) do produto.
            qtd_sellers:      nº de sellers/ofertas competindo na mesma listagem.
            tipo_seller:      classificação do seller — ex: "1P", "3P", "Loja Oficial",
                              "Shopee Mall", "Preferred+".
            reputacao_seller: nota/nível de reputação do seller quando disponível
                              (ex: "MercadoLíder Platinum", "green", "4.8").

        Identidade da oferta (Fase 1 — Ago/2026):
            marketplace_product_id: id de PRODUTO do marketplace (ASIN, MLB de
                              catálogo, id da CB…). Opcional: quando o coletor
                              não passa, é derivado da URL.
            marketplace_offer_id: id da OFERTA individual, **só quando o
                              marketplace expõe um de verdade**. Nunca é
                              sintetizado — para uma chave sempre-presente use
                              a coluna `Offer Key`.
            seller_id:        id do seller no marketplace (idLojista da CB,
                              seller_id do Magalu, shopid da Shopee…).

        Preço continua coletado, porém como campo secundário.
        """
        from utils.text import parse_price
        from utils.offer_identity import build_identity

        now = now_brt()
        title_clean = normalize_text(title)

        # screenshot da página de busca: se o caller não passar explicitamente,
        # usa o último capturado por capture_screenshot(tipo="busca").
        if screenshot_busca is None:
            screenshot_busca = self._last_screenshot_busca

        # preço: prioriza float já parseado; fallback para parse da string
        if price_float is None and price_raw:
            price_float = parse_price(price_raw)

        brand = extract_brand(title_clean)
        product_name = normalize_product_name(title_clean, brand)
        # v2 canonical (UPPERCASE, SKU-anchored). Parte descritiva apenas —
        # voltagem/SKU são anexados depois pela resolução de-para (catálogo).
        product_name_v2 = normalize_product_name_v2(title_clean, brand)

        # Identidade da oferta: ids passados pelo coletor têm precedência; a
        # URL é a rede de segurança quando ele não os tem. Sem isto não existe
        # série histórica de oferta (auditoria §2).
        identity = build_identity(
            self.platform_name,
            url_produto,
            marketplace_product_id=marketplace_product_id,
            marketplace_offer_id=marketplace_offer_id,
            seller_id=seller_id,
            title=title_clean,
        )

        return {
            "Data":                now.strftime("%Y-%m-%d"),
            "Turno":               get_turno(now),
            "Horário":             now.strftime("%H:%M"),
            "Analista":            ANALYST_NAME,
            "Plataforma":          self.platform_name,
            "Tipo Plataforma":     PLATFORM_TYPE.get(self.platform_name, "Outro"),
            "Keyword Buscada":     keyword,
            "Categoria Keyword":   infer_keyword_category(keyword, keyword_category_map),
            "Marca Monitorada":    brand,
            "Produto / SKU":       product_name,
            "Produto Normalizado": product_name_v2,
            "Posição Orgânica":    position_organic,
            "Posição Patrocinada": position_sponsored,
            "Posição Geral":       position_general,
            "Patrocinado?":        "Sim" if position_sponsored else "Não",
            # ── Insights de buy box / seller (foco principal) ──
            # `normalize_seller_name` colapsa as grafias que cada marketplace
            # impõe ao MESMO lojista (`friopecas`/`Friopeças`,
            # `continentalcenter`/`Webcontinental ES`). Sem isso o share de
            # buy box divide um dealer em várias linhas e o ranking mente
            # sobre quem lidera — ver utils/seller_names.py.
            "Buy Box Seller":      normalize_seller_name(buy_box_seller) or normalize_seller_name(seller),
            "Qtd Sellers":         self._coerce_count(
                qtd_sellers, "Qtd Sellers", keyword
            ),
            "Tipo Seller":         normalize_text(tipo_seller),
            "Reputação Seller":    normalize_text(reputacao_seller),
            "Seller / Vendedor":   normalize_seller_name(seller),
            "Fulfillment?":        "Sim" if is_fulfillment else "Não",
            "Avaliação":           rating,
            "Qtd Avaliações":      self._coerce_count(
                review_count, "Qtd Avaliações", keyword
            ),
            "Tag Destaque":        normalize_text(tag_destaque),
            # ── Preço: secundário a partir de Mai/2026 ──
            "Preço (R$)":          price_float,
            "URL Produto":         url_produto,
            "Screenshot Busca":    screenshot_busca,
            "Screenshot Produto":  screenshot_produto,
            # ── Identidade da oferta (Fase 1) ──
            "ID Produto Marketplace": identity.marketplace_product_id,
            "ID Oferta Marketplace":  identity.marketplace_offer_id,
            "ID Seller":              identity.seller_id,
            "URL Canônica":           identity.canonical_url,
            "Offer Key":              identity.offer_key,
        }

    # ------------------------------------------------------------------
    # Interface pública — deve ser implementada por cada subclasse
    # ------------------------------------------------------------------

    @abstractmethod
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Executa a busca pela keyword na plataforma e retorna lista de registros.

        Args:
            keyword:              termo de busca
            keyword_category_map: dict {categoria: [keywords]} para inferência
            page_limit:           número máximo de páginas a navegar

        Returns:
            Lista de dicts no formato de `_build_record`.
        """
        ...
