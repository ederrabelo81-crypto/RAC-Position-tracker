"""
tests/test_browser_degradation.py — o que cada scraper faz quando o Chrome
compartilhado morre no meio da coleta.

Log de 14/08/2026: a janela do Chrome sumiu durante a Casas Bahia e, a partir
dali, **toda** keyword restante devolveu 0 produto — em 4 plataformas — mesmo
com as APIs VTEX e a API oficial do ML disponíveis. Browser morto não é
bloqueio anti-bot: é acidente recuperável, e a coleta tem que degradar em vez
de zerar.

Rode: pytest tests/test_browser_degradation.py
"""
import pytest

from scrapers.casas_bahia import CasasBahiaScraper
from scrapers.mercado_livre import MLScraper
from scrapers.shopee import ShopeeScraper


@pytest.fixture(autouse=True)
def sem_chrome_de_verdade(monkeypatch):
    """
    Nenhum teste aqui pode abrir Chrome — nem na máquina do coletor.

    `get_local_browser()` respeita `RAC_LOCAL_CHROME`, e a variável está ligada
    no PC que coleta. Sem este stub, rodar a suíte lá lançaria um Chrome real
    no meio dos testes e inverteria o resultado dos casos de "browser morto".
    Quem precisa de um browser injeta o seu com `_usa_local_browser`.
    """
    monkeypatch.setenv("RAC_LOCAL_CHROME", "0")
    for modulo in ("casas_bahia", "shopee", "mercado_livre"):
        monkeypatch.setattr(
            f"scrapers.{modulo}.get_local_browser", lambda: None
        )


def _usa_local_browser(monkeypatch, modulo: str, browser) -> None:
    """Injeta um LocalBrowser de mentira no scraper indicado."""
    monkeypatch.setattr(f"scrapers.{modulo}.get_local_browser", lambda: browser)


class _PaginaMorta:
    """Aba fechada — é o que sobra depois de o usuário fechar a janela."""

    def is_closed(self) -> bool:
        return True


class _PaginaViva:
    def is_closed(self) -> bool:
        return False

    def set_default_timeout(self, ms) -> None:
        pass


class _LocalBrowserFalso:
    """Dublê do Chrome compartilhado — conta as abas que foi obrigado a abrir."""

    def __init__(self, vivo: bool = True) -> None:
        self.vivo = vivo
        self.abas = 0
        self.context = object()

    def is_alive(self) -> bool:
        return self.vivo

    def new_page(self):
        self.abas += 1
        return _PaginaViva()


# ---------------------------------------------------------------------------
# Casas Bahia — degrada para as APIs VTEX (curl_cffi)
# ---------------------------------------------------------------------------


class TestCasasBahiaDegrada:
    def _scraper(self):
        s = CasasBahiaScraper.__new__(CasasBahiaScraper)
        s.platform_name = "Casas Bahia"
        s._local_active = True
        s._cdp_active = False
        s._local_browser = None
        s._page = None
        s._context = None
        s._xhr_page = None
        s._cdp_warmed = True
        s._browser_lost = False
        s._akamai_blocked = False
        s._blocked_keyword_streak = 0
        s.collection_aborted = False
        s._captured_products = []
        s._setup_xhr_intercept = lambda: None
        s._random_delay = lambda *a, **k: None
        s._log_search_result = lambda *a, **k: None
        return s

    def test_keyword_e_salva_pelas_apis_quando_o_browser_morre(self):
        s = self._scraper()
        registro = {"Produto / SKU": "Ar Condicionado 12000"}

        def _browser_page(*_a, **_k):
            s._browser_lost = True          # como o `_revive_page` faz
            return None

        s._browser_search_page = _browser_page
        s._warmup_cdp_session = lambda: None
        s._vtex_cffi_search = lambda *a, **k: [registro]
        s._vtex_api_search = lambda *a, **k: []

        assert s.search("ar condicionado", {}, page_limit=1) == [registro]
        assert s._real_browser_active is False, "modo browser não foi desligado"

    def test_bloqueio_continua_encerrando_a_keyword(self):
        """Só o browser MORTO degrada — bloqueio Akamai ainda para a keyword."""
        s = self._scraper()
        chamadas = []
        s._browser_search_page = lambda *a, **k: None   # sem _browser_lost
        s._warmup_cdp_session = lambda: None
        s._vtex_cffi_search = lambda *a, **k: chamadas.append(1) or []
        s._vtex_api_search = lambda *a, **k: []

        assert s.search("ar condicionado", {}, page_limit=2) == []
        assert chamadas == [], "degradou por bloqueio, não por browser morto"
        assert s._real_browser_active is True

    def test_degrade_zera_aba_e_contexto(self):
        s = self._scraper()
        s._page = _PaginaViva()
        s._context = object()
        s._degrade_to_http()
        assert s._has_browser() is False
        assert s._real_browser_active is False

    def test_revive_falha_quando_nao_ha_browser(self):
        """Sem LocalBrowser nem contexto, revive marca a perda (não explode)."""
        s = self._scraper()
        assert s._revive_page() is False
        assert s._browser_lost is True

    def test_revive_nao_reusa_instancia_descartada(self, monkeypatch):
        """
        Singleton derrubado = fim da linha, mesmo com um handle antigo guardado.

        Relançar a instância descartada abriria Chrome por fora do teto de
        tentativas e com um handle do Playwright que o `close_local_browser()`
        já não conhece.
        """
        s = self._scraper()
        descartado = _LocalBrowserFalso()
        s._local_browser = descartado          # handle de antes da queda
        # a fixture já faz get_local_browser() devolver None (singleton morto)
        assert s._revive_page() is False
        assert descartado.abas == 0
        assert s._browser_lost is True

    def test_revive_usa_o_singleton_atual(self, monkeypatch):
        s = self._scraper()
        antigo, novo = _LocalBrowserFalso(), _LocalBrowserFalso()
        s._local_browser = antigo
        _usa_local_browser(monkeypatch, "casas_bahia", novo)
        s._warmup_cdp_session = lambda: None

        assert s._revive_page() is True
        assert s._local_browser is novo
        assert antigo.abas == 0 and novo.abas == 1


# ---------------------------------------------------------------------------
# Shopee — degrada para curl_cffi
# ---------------------------------------------------------------------------


class TestShopeeDegrada:
    def _scraper(self):
        s = ShopeeScraper.__new__(ShopeeScraper)
        s.platform_name = "Shopee"
        s._local_active = True
        s._local_browser = None
        s._page = _PaginaMorta()
        s._xhr_page = None
        s._session = None
        s._hard_blocked = False
        s._blocked_keyword_streak = 0
        s.collection_aborted = False
        s._captured_search = []
        s._log_search_result = lambda *a, **k: None
        return s

    def test_sem_aba_e_sem_chrome_cai_para_http(self):
        s = self._scraper()
        chamou_http = []
        s._start_http_session = lambda: chamou_http.append(1)
        s._fetch_page = lambda *a, **k: None

        s.search("ar condicionado", {}, page_limit=1)

        assert s._local_active is False
        assert chamou_http == [1], "não abriu a sessão HTTP ao degradar"

    def test_aba_morta_e_reaberta_pelo_chrome_compartilhado(self, monkeypatch):
        s = self._scraper()
        lb = _LocalBrowserFalso()
        _usa_local_browser(monkeypatch, "shopee", lb)
        s._setup_xhr_intercept = lambda: None

        assert s._ensure_browser_page() is True
        assert s._local_active is True
        assert isinstance(s._page, _PaginaViva)
        assert s._local_browser is lb

    def test_aba_viva_nao_reabre(self, monkeypatch):
        s = self._scraper()
        lb = _LocalBrowserFalso()
        _usa_local_browser(monkeypatch, "shopee", lb)
        viva = _PaginaViva()
        s._page = viva
        s._local_browser = lb

        assert s._ensure_browser_page() is True
        assert s._page is viva
        assert lb.abas == 0, "abriu aba nova com o browser saudável"

    def test_aba_aberta_mas_browser_desconectado_reabre(self, monkeypatch):
        """`is_closed()` pode mentir se o CDP caiu — a liveness decide."""
        s = self._scraper()
        lb = _LocalBrowserFalso(vivo=False)
        _usa_local_browser(monkeypatch, "shopee", lb)
        s._page = _PaginaViva()
        s._local_browser = lb
        s._setup_xhr_intercept = lambda: None

        assert s._ensure_browser_page() is True
        assert lb.abas == 1

    def test_morte_durante_a_espera_nao_vira_bloqueio(self, monkeypatch):
        """
        Chrome fechado durante a espera pela API ≠ CAPTCHA.

        `_await_captured` e o scroll engoliam o erro de target fechado, então a
        keyword terminava em "nenhuma resposta capturada" → `_hard_blocked` →
        circuit breaker abortava a Shopee inteira culpando o anti-fraude.
        """
        s = self._scraper()
        s._page = _PaginaMorta()
        s._captured_search = []

        def _wait(_ms):
            raise RuntimeError(
                "Page.wait_for_timeout: Target page, context or browser has "
                "been closed"
            )

        s._page.wait_for_timeout = _wait
        assert s._await_captured(timeout_s=5.0) == []
        assert s._browser_lost is True
        assert s._hard_blocked is False, "morte do browser contada como bloqueio"

    def test_chrome_morto_no_meio_da_keyword_refaz_pelo_http(self, monkeypatch):
        """Meia coleta não é resultado final: degrada e repete a keyword."""
        s = self._scraper()
        lb = _LocalBrowserFalso()
        _usa_local_browser(monkeypatch, "shopee", lb)
        s._setup_xhr_intercept = lambda: None
        s._start_http_session = lambda: None

        def _busca_no_browser(*_a, **_k):
            s._browser_lost = True
            return [{"Produto / SKU": "parcial"}]

        s._search_via_browser = _busca_no_browser
        registro_http = {"Produto / SKU": "completo"}
        s._fetch_page = lambda *a, **k: {"items": [{}]}
        s._parse_items = lambda *a, **k: [registro_http]
        s._maybe_dump_hollow_parse = lambda *a, **k: None

        assert s.search("ar condicionado", {}, page_limit=1) == [registro_http]
        assert s._local_active is False


# ---------------------------------------------------------------------------
# Mercado Livre — reabre a aba, cai para browser próprio, e só então para a API
# ---------------------------------------------------------------------------


class TestMercadoLivreDegrada:
    def _scraper(self):
        s = MLScraper.__new__(MLScraper)
        s.platform_name = "Mercado Livre"
        s._local_active = True
        s._page = _PaginaMorta()
        s._context = None
        s._warmed = True
        return s

    def test_aba_morta_vira_aba_nova_no_chrome_compartilhado(self, monkeypatch):
        s = self._scraper()
        _usa_local_browser(monkeypatch, "mercado_livre", _LocalBrowserFalso())
        assert s._ensure_page() is True
        assert s._warmed is False, "aba nova precisa reaquecer a sessão"

    def test_sem_chrome_local_abre_browser_proprio(self, monkeypatch):
        """
        E o browser próprio também nasce com a sessão FRIA.

        Com `_warmed=True` sobrando do modo local, o `search()` pularia o
        `_warm_session()` (que roda 1x por run) e mandaria o browser recém-
        aberto direto para a SERP — o gatilho conhecido do login gate do ML.
        """
        s = self._scraper()

        def _launch(self_inner):
            self_inner._page = _PaginaViva()

        monkeypatch.setattr(MLScraper, "_launch", _launch)
        assert s._ensure_page() is True
        assert s._local_active is False
        assert s._warmed is False

    def test_sem_browser_algum_a_keyword_vai_para_a_api(self, monkeypatch):
        s = self._scraper()
        s._log_search_result = lambda *a, **k: None

        def _launch(self_inner):
            raise RuntimeError("Playwright indisponível")

        monkeypatch.setattr(MLScraper, "_launch", _launch)
        registro = {"Produto / SKU": "Ar Condicionado 9000"}
        s._api_fallback_search = lambda *a, **k: [registro]

        assert s.search("ar condicionado", {}, page_limit=1) == [registro]

    def test_pagina_viva_nao_mexe_em_nada(self):
        s = self._scraper()
        viva = _PaginaViva()
        s._page = viva
        assert s._ensure_page() is True
        assert s._page is viva
        assert s._warmed is True
