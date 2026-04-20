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
    MAX_DELAY,
    MIN_DELAY,
    NETWORK_IDLE_TIMEOUT,
    PAGE_TIMEOUT,
    PLATFORM_TYPE,
    USER_AGENTS,
)
from utils.brands import extract_brand
from utils.text import get_turno, infer_keyword_category, normalize_text
from utils.normalize_product import normalize_product_name


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

    # ------------------------------------------------------------------
    # Gerenciamento de ciclo de vida do browser
    # ------------------------------------------------------------------

    # Patch JS completo — mesma versão do session_grabber para consistência
    _STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        try { delete navigator.__proto__.webdriver; } catch(_) {}

        window.chrome = {
            runtime: {
                onConnect: {addListener: () => {}},
                onMessage: {addListener: () => {}},
                id: undefined,
            },
            loadTimes: () => ({}),
            csi: () => ({}),
        };

        Object.defineProperty(navigator, 'plugins', {
            get: () => { const a = [1,2,3,4,5]; a.item = () => null; return a; }
        });

        Object.defineProperty(navigator, 'languages', {
            get: () => ['pt-BR', 'pt', 'en-US', 'en']
        });

        const _origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : _origQuery(p);
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

        self._context = self._browser.new_context(
            user_agent=self._user_agent,
            viewport={"width": 1366, "height": 768},
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
        return self

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
    ) -> Dict[str, Any]:
        """
        Monta um dicionário compatível com as colunas do DataFrame de saída.

        Aceita preço como string bruta OU float já parseado.
        """
        from utils.text import parse_price

        now = datetime.now()
        title_clean = normalize_text(title)

        # preço: prioriza float já parseado; fallback para parse da string
        if price_float is None and price_raw:
            price_float = parse_price(price_raw)

        brand = extract_brand(title_clean)
        product_name = normalize_product_name(title_clean, brand)

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
            "Posição Orgânica":    position_organic,
            "Posição Patrocinada": position_sponsored,
            "Posição Geral":       position_general,
            "Preço (R$)":          price_float,
            "Seller / Vendedor":   normalize_text(seller),
            "Fulfillment?":        "Sim" if is_fulfillment else "Não",
            "Avaliação":           rating,
            "Qtd Avaliações":      review_count,
            "Tag Destaque":        normalize_text(tag_destaque),
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
