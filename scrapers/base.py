"""
scrapers/base.py — Classe base abstrata para todos os scrapers.

Define:
  - Inicialização do Playwright com stealth e rotação de User-Agent
  - Método abstrato `search()` que cada scraper deve implementar
  - Helpers compartilhados: scroll humano, delay aleatório, snapshot de HTML
  - Construção do registro de dados (linha do DataFrame) com campos fixos
"""

import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from loguru import logger
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

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
from utils.brands import extract_brand
from utils.text import get_turno, infer_keyword_category, normalize_text, now_brt
from utils.normalize_product import normalize_product_name, normalize_product_name_v2


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
        """Inicia o Playwright, o browser e o contexto com configurações stealth."""
        self._playwright = sync_playwright().start()

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
            # Para o Playwright antes de lançar a exceção — sem isso o event
            # loop interno fica aberto e o próximo scraper recebe
            # "Sync API inside asyncio loop".
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            raise RuntimeError(
                "Não foi possível iniciar nenhum browser (chrome/msedge/chromium). "
                "Execute: python -m playwright install chromium"
            )

        # Viewport maior quando capturando screenshots para evidência mais legível
        if self.screenshot_manager is not None:
            vp_w, vp_h = SCREENSHOTS_VIEWPORT
        else:
            vp_w, vp_h = 1366, 768

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
        logger.info(
            f"[{self.platform_name}] Browser iniciado ({used_channel}) | UA: {self._user_agent[:60]}..."
        )

    def _close(self) -> None:
        """Encerra browser e playwright de forma limpa."""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Erro ao fechar browser: {exc}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

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

        Preço continua coletado, porém como campo secundário.
        """
        from utils.text import parse_price

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
            "Buy Box Seller":      normalize_text(buy_box_seller) or normalize_text(seller),
            "Qtd Sellers":         qtd_sellers,
            "Tipo Seller":         normalize_text(tipo_seller),
            "Reputação Seller":    normalize_text(reputacao_seller),
            "Seller / Vendedor":   normalize_text(seller),
            "Fulfillment?":        "Sim" if is_fulfillment else "Não",
            "Avaliação":           rating,
            "Qtd Avaliações":      review_count,
            "Tag Destaque":        normalize_text(tag_destaque),
            # ── Preço: secundário a partir de Mai/2026 ──
            "Preço (R$)":          price_float,
            "URL Produto":         url_produto,
            "Screenshot Busca":    screenshot_busca,
            "Screenshot Produto":  screenshot_produto,
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
