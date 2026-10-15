"""
Resiliência do Mercado Livre ao gate antibot (incidente de 09–14/10/2026).

O ML caiu de ~5.000 linhas/turno para 0–136 por cinco dias: 0–3 das 40
keywords passavam, a página 2 nunca, e o livro-razão só dizia
"Mercado Livre=48" — sem motivo, sem alarme. Estes testes cobrem as defesas:

* Referer na navegação (home → pág. 1, SERP anterior → pág. 2);
* nada de apagar cookies do perfil PERSISTENTE do Chrome local;
* freio depois de N páginas gateadas seguidas + adiamento para a repescagem;
* API oficial que responde 401/403 desliga de vez, sem retry;
* diagnóstico de uma linha que vai para o livro-razão;
* `main._run_scraper` junta adiadas + não tentadas para a repescagem.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
import requests

import main
import scrapers.mercado_livre as ml_mod
from scrapers.mercado_livre import MLScraper, _GATE_ABORT_STREAK, _ML_HOME
from scrapers.mercado_livre_api import MLAPIScraper, _is_retryable

_SERP_HTML = '<html><ol><li class="ui-search-layout__item">x</li></ol></html>'
_GATE_HTML = "<html><body>Para continuar, acesse sua conta</body></html>"


class _FakePage:
    """Página Playwright mínima: grava cada goto e devolve o HTML configurado."""

    def __init__(self, html: str = _SERP_HTML, url: str = "about:blank") -> None:
        self.html = html
        self.url = url
        self.gotos: List[Dict[str, Any]] = []

    def goto(self, url: str, **kwargs: Any) -> None:
        self.gotos.append({"url": url, **kwargs})
        self.url = url

    def content(self) -> str:
        return self.html

    def is_closed(self) -> bool:
        return False

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def wait_for_load_state(self, *_a: Any, **_k: Any) -> None:
        return None

    def title(self) -> str:
        return ""

    def locator(self, _sel: str) -> Any:
        loc = MagicMock()
        loc.first.text_content.return_value = ""
        loc.first.click.side_effect = RuntimeError("sem CTA")
        return loc


@pytest.fixture
def scraper(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> MLScraper:
    monkeypatch.chdir(tmp_path)  # _dump_gate_evidence grava em logs/
    # `search` registra cada keyword no SQLite do sistema adaptativo
    # (data/adaptive_scraper.db, versionado) — teste não pode sujá-lo.
    from utils.adaptive_scraper import AdaptiveScraperConfig
    monkeypatch.setattr(AdaptiveScraperConfig, "record_result", lambda *a, **k: None)
    monkeypatch.setattr(AdaptiveScraperConfig, "record_waf_block", lambda *a, **k: None)
    s = MLScraper()
    s._page = _FakePage()
    s._context = MagicMock()
    s._context.cookies.return_value = []
    s._warmed = True
    monkeypatch.setattr(s, "_random_delay", lambda *a, **k: None)
    monkeypatch.setattr(s, "_wait_for_network_idle", lambda: None)
    monkeypatch.setattr(s, "_rewarm_home", lambda: None)
    monkeypatch.setattr(s, "_goto_serp_via_home", lambda kw: False)
    return s


class TestReferer:
    def test_pagina_1_vem_da_home(self, scraper: MLScraper) -> None:
        url = MLScraper._build_url("ar condicionado", 0)
        assert scraper._goto_serp(url, "ar condicionado", 1) is True
        assert scraper._page.gotos[-1]["referer"] == _ML_HOME

    def test_pagina_2_vem_da_serp_anterior(self, scraper: MLScraper) -> None:
        p1 = MLScraper._build_url("ar condicionado", 0)
        scraper._page.url = p1
        p2 = MLScraper._build_url("ar condicionado", 60)
        assert scraper._goto_serp(p2, "ar condicionado", 2) is True
        assert scraper._page.gotos[-1] == {
            "url": p2, "wait_until": "domcontentloaded", "referer": p1,
        }

    def test_sem_serp_anterior_vai_sem_referer(self, scraper: MLScraper) -> None:
        scraper._page.url = "about:blank"
        p2 = MLScraper._build_url("ar condicionado", 60)
        scraper._goto_serp(p2, "ar condicionado", 2)
        assert "referer" not in scraper._page.gotos[-1]


class TestIdentidadePersistente:
    def test_chrome_local_nao_limpa_cookies(self, scraper: MLScraper) -> None:
        scraper._local_active = True
        assert scraper._reset_anonymous_identity() is False
        scraper._context.clear_cookies.assert_not_called()

    def test_playwright_proprio_ainda_reseta(self, scraper: MLScraper) -> None:
        scraper._local_active = False
        assert scraper._reset_anonymous_identity() is True
        assert scraper._context.clear_cookies.called

    def test_cookie_de_login_novo_e_reconhecido(self, scraper: MLScraper) -> None:
        scraper._context.cookies.return_value = [{"name": "orgnickp", "value": "x"}]
        assert scraper._is_logged_in() is True


class TestFreio:
    def _gate_all(self, scraper: MLScraper) -> None:
        scraper._page.html = _GATE_HTML

    def test_gate_persistente_conta_motivo(self, scraper: MLScraper) -> None:
        self._gate_all(scraper)
        url = MLScraper._build_url("kw", 0)
        assert scraper._goto_serp(url, "kw", 1) is False
        assert scraper._gate_reason_counts == {
            "login/verificação de dispositivo": 1
        }

    def test_freio_aciona_e_adia(self, scraper: MLScraper, monkeypatch) -> None:
        self._gate_all(scraper)
        monkeypatch.setattr(scraper, "_api_fallback_search", lambda *a, **k: [])
        kws = [f"kw{i}" for i in range(_GATE_ABORT_STREAK + 2)]
        for kw in kws:
            scraper.search.__wrapped__(scraper, kw, {}, page_limit=1)
        assert scraper.collection_aborted is True
        # para de navegar no freio: as keywords seguintes nem tentam
        assert scraper.deferred_keywords == kws[:_GATE_ABORT_STREAK]
        assert scraper._kw_gated == _GATE_ABORT_STREAK

    def test_repescagem_nao_adia_de_novo(self, scraper: MLScraper, monkeypatch) -> None:
        self._gate_all(scraper)
        scraper.retry_pass = True
        monkeypatch.setattr(scraper, "_api_fallback_search", lambda *a, **k: [])
        scraper.search.__wrapped__(scraper, "kw", {}, page_limit=1)
        assert scraper.deferred_keywords == []
        assert scraper._kw_gated == 1

    def test_api_que_salva_nao_adia(self, scraper: MLScraper, monkeypatch) -> None:
        self._gate_all(scraper)
        monkeypatch.setattr(
            scraper, "_api_fallback_search", lambda *a, **k: [{"Plataforma": "x"}]
        )
        scraper.search.__wrapped__(scraper, "kw", {}, page_limit=1)
        assert scraper.deferred_keywords == []
        assert scraper._kw_api_saved == 1


class TestDiagnostico:
    def test_linha_resume_a_run(self, scraper: MLScraper) -> None:
        scraper._kw_ok = 2
        scraper._kw_gated = 38
        scraper._count_gate_reason("login/verificação de dispositivo (nenhum card na página)")
        scraper._count_gate_reason("URL de gate (https://www.mercadolivre.com.br/gz/account-verification?go=x)")
        scraper._api_status = "sem credenciais"
        scraper._local_active = True
        linha = scraper.run_diagnostics()
        assert linha.startswith("ML: 2/40 kw ok, 38 gate [")
        assert "login/verificação de dispositivo ×1" in linha
        assert "URL account-verification ×1" in linha
        assert "API: sem credenciais" in linha
        assert "sessão: anônima" in linha
        assert "modo: Chrome local" in linha

    def test_freio_aparece(self, scraper: MLScraper) -> None:
        scraper.collection_aborted = True
        scraper.deferred_keywords = ["a", "b"]
        assert "FREIO (2 adiadas)" in scraper.run_diagnostics()

    def test_sem_init_nao_estoura(self) -> None:
        # parser/diagnóstico também rodam via __new__ (scripts/diagnose_ml.py)
        assert MLScraper.__new__(MLScraper).run_diagnostics().startswith("ML: 0/0")


class TestApiRecusada:
    @staticmethod
    def _http_error(status: int) -> requests.HTTPError:
        resp = requests.Response()
        resp.status_code = status
        resp._content = b'{"message":"forbidden"}'
        return requests.HTTPError(response=resp)

    def test_403_nao_e_repetido(self) -> None:
        assert _is_retryable(self._http_error(403)) is False
        assert _is_retryable(self._http_error(401)) is False
        assert _is_retryable(self._http_error(500)) is True
        assert _is_retryable(requests.ConnectionError()) is True

    def test_403_marca_api_indisponivel(self, monkeypatch) -> None:
        api = MLAPIScraper()
        calls = []

        def _get(url: str, params: Optional[dict] = None, timeout: int = 0):
            calls.append(url)
            resp = requests.Response()
            resp.status_code = 403
            resp._content = b'{"message":"forbidden"}'
            return resp

        monkeypatch.setattr(api._session, "get", _get)
        assert api.search("ar condicionado", {}, page_limit=2) == []
        assert api.unavailable_reason == "HTTP 403 em /sites/MLB/search"
        assert len(calls) == 1  # sem retry e sem página 2
        # recusa vale para a run: a próxima keyword nem chama a API...
        assert api.search("ar condicionado lg", {}, page_limit=1) == []
        assert len(calls) == 1
        # ...e o `_run_scraper` (--platforms ml_api) para o laço
        assert api.collection_aborted is True

    def test_api_salva_keyword_com_browser_morto(self, scraper: MLScraper, monkeypatch) -> None:
        monkeypatch.setattr(scraper, "_ensure_page", lambda: False)
        monkeypatch.setattr(
            scraper, "_api_fallback_search", lambda *a, **k: [{"Plataforma": "x"}]
        )
        scraper.search.__wrapped__(scraper, "kw", {}, page_limit=1)
        assert scraper._kw_api_saved == 1
        assert scraper.run_diagnostics().startswith("ML: 0/1 kw ok")

    def test_fallback_desliga_na_recusa(self, scraper: MLScraper) -> None:
        api = MagicMock()
        api.search.return_value = []
        api.unavailable_reason = "HTTP 403 em /sites/MLB/search"
        scraper._api_scraper = api
        assert scraper._api_fallback_search("kw", {}, 1) == []
        assert scraper._api_scraper is False
        assert scraper._api_status == "recusada (HTTP 403 em /sites/MLB/search)"
        assert scraper._get_api_scraper() is None


class _FakeScraper:
    """Scraper de mentira para `_run_scraper`: gateia tudo e freia na 2ª."""

    platform_name = "Fake"

    def __init__(self, headless: bool = True) -> None:
        self.collection_aborted = False
        self.deferred_keywords: List[str] = []
        self.retry_pass = False
        self.searched: List[str] = []

    def __enter__(self) -> "_FakeScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def search(self, keyword: str, keyword_category_map: dict, page_limit: int):
        self.searched.append(keyword)
        if not self.retry_pass:
            self.deferred_keywords.append(keyword)
        if len(self.searched) >= 2:
            self.collection_aborted = True
        return []

    def run_diagnostics(self) -> str:
        return f"fake: {len(self.searched)} tentadas"


class TestRepescagemNoMain:
    @pytest.fixture(autouse=True)
    def _limpa(self, monkeypatch):
        monkeypatch.setattr(main, "_PENDING_RETRY", {})
        monkeypatch.setattr(main, "_RUN_DIAGNOSTICS", [])

    _MAP = {"A": ["k1", "k2", "k3"], "B": ["k4"]}

    def test_adiadas_e_nao_tentadas_vao_para_repescagem(self) -> None:
        main._run_scraper(_FakeScraper, self._MAP, 1, True, priority_filter=None)
        assert main._PENDING_RETRY[_FakeScraper] == ["k1", "k2", "k3", "k4"]
        assert main._RUN_DIAGNOSTICS == ["fake: 2 tentadas"]

    def test_repescagem_nao_reagenda(self) -> None:
        main._run_scraper(
            _FakeScraper, self._MAP, 1, True, priority_filter=None, retry_pass=True
        )
        assert main._PENDING_RETRY == {}

    def test_recorte_do_mapa_preserva_categoria_e_ordem(self) -> None:
        assert main._keywords_map_subset(self._MAP, ["k4", "k1"]) == {
            "A": ["k1"], "B": ["k4"],
        }
