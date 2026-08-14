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
from scrapers.casas_bahia import CasasBahiaScraper
from scrapers.mercado_livre import MLScraper
from scrapers.shopee import ShopeeScraper


class _PaginaMorta:
    """Aba fechada — é o que sobra depois de o usuário fechar a janela."""

    def is_closed(self) -> bool:
        return True


class _PaginaViva:
    def is_closed(self) -> bool:
        return False

    def set_default_timeout(self, ms) -> None:
        pass


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

    def test_aba_morta_e_reaberta_pelo_chrome_compartilhado(self):
        s = self._scraper()

        class _LB:
            def new_page(self_inner):
                return _PaginaViva()

        s._local_browser = _LB()
        s._setup_xhr_intercept = lambda: None
        assert s._ensure_browser_page() is True
        assert s._local_active is True
        assert isinstance(s._page, _PaginaViva)

    def test_aba_viva_nao_reabre(self):
        s = self._scraper()
        viva = _PaginaViva()
        s._page = viva
        assert s._ensure_browser_page() is True
        assert s._page is viva


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

        class _LB:
            context = object()

            def new_page(self_inner):
                return _PaginaViva()

        monkeypatch.setattr(
            "scrapers.mercado_livre.get_local_browser", lambda: _LB()
        )
        assert s._ensure_page() is True
        assert s._warmed is False, "aba nova precisa reaquecer a sessão"

    def test_sem_chrome_local_abre_browser_proprio(self, monkeypatch):
        s = self._scraper()
        monkeypatch.setattr(
            "scrapers.mercado_livre.get_local_browser", lambda: None
        )

        def _launch(self_inner):
            self_inner._page = _PaginaViva()

        monkeypatch.setattr(MLScraper, "_launch", _launch)
        assert s._ensure_page() is True
        assert s._local_active is False

    def test_sem_browser_algum_a_keyword_vai_para_a_api(self, monkeypatch):
        s = self._scraper()
        s._log_search_result = lambda *a, **k: None
        monkeypatch.setattr(
            "scrapers.mercado_livre.get_local_browser", lambda: None
        )

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
