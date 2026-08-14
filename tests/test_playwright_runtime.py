"""
tests/test_playwright_runtime.py — handle único do Playwright por thread.

O incidente que originou o módulo (14/08/2026): com o Chrome local ligado
(``RAC_LOCAL_CHROME``) havia um ``sync_playwright()`` vivo pela coleta inteira,
e todo scraper de browser próprio (Amazon, Google Shopping, Leroy, Dealers, os
fallbacks de ML/Magalu/Casas Bahia) morria no ``_launch()`` com::

    It looks like you are using Playwright Sync API inside the asyncio loop.

Estes testes travam o contrato que evita a volta disso: **um handle por
thread**, compartilhado por contagem de referências.

Rode: pytest tests/test_playwright_runtime.py
"""
import threading

import pytest

from scrapers import playwright_runtime as pr


class _FakeHandle:
    """Handle mínimo: só registra se foi parado."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def runtime_limpo():
    """Cada teste começa e termina sem handle pendurado nesta thread."""
    pr.shutdown()
    yield
    pr.shutdown()


@pytest.fixture
def fake_playwright(monkeypatch):
    """Substitui o import real por uma fábrica que conta as inicializações."""
    criados = []

    def _factory(prefer_rebrowser: bool):
        flavor = pr.FLAVOR_REBROWSER if prefer_rebrowser else pr.FLAVOR_STOCK

        def _sync_playwright():
            class _Ctx:
                def start(self):
                    handle = _FakeHandle()
                    criados.append(handle)
                    return handle
            return _Ctx()

        return _sync_playwright, flavor

    monkeypatch.setattr(pr, "_import_sync_playwright", _factory)
    return criados


class TestHandleCompartilhado:
    def test_dois_acquires_devolvem_o_mesmo_handle(self, fake_playwright):
        """O caso do bug: LocalBrowser + scraper de browser próprio na mesma run."""
        primeiro, _ = pr.acquire(prefer_rebrowser=True)   # LocalBrowser
        segundo, _ = pr.acquire()                          # BaseScraper._launch
        assert primeiro is segundo
        assert len(fake_playwright) == 1, "iniciou um segundo handle na thread"
        assert pr.refcount() == 2

    def test_release_intermediario_nao_para_o_handle(self, fake_playwright):
        handle, _ = pr.acquire(prefer_rebrowser=True)
        pr.acquire()
        pr.release()                       # o scraper terminou
        assert handle.stopped is False     # o Chrome local ainda usa
        assert pr.is_active() is True

    def test_ultimo_release_para_o_handle(self, fake_playwright):
        handle, _ = pr.acquire()
        pr.acquire()
        pr.release()
        pr.release()
        assert handle.stopped is True
        assert pr.is_active() is False
        assert pr.refcount() == 0

    def test_release_a_mais_nao_quebra(self, fake_playwright):
        pr.acquire()
        pr.release()
        pr.release()                       # release órfão (fluxo de erro)
        assert pr.is_active() is False

    def test_novo_acquire_apos_parar_reinicia(self, fake_playwright):
        pr.acquire()
        pr.release()
        pr.acquire()
        assert len(fake_playwright) == 2
        assert pr.is_active() is True


class TestFlavor:
    def test_prefere_rebrowser_quando_pedido(self, fake_playwright):
        _, flavor = pr.acquire(prefer_rebrowser=True)
        assert flavor == pr.FLAVOR_REBROWSER
        assert pr.active_flavor() == pr.FLAVOR_REBROWSER

    def test_padrao_e_o_stock(self, fake_playwright):
        """Na VM/Actions nada pede stealth — o caminho antigo não muda."""
        _, flavor = pr.acquire()
        assert flavor == pr.FLAVOR_STOCK

    def test_handle_vivo_vence_a_preferencia(self, fake_playwright):
        """Um segundo handle é impossível: reaproveita o que está de pé."""
        pr.acquire()                                  # stock primeiro
        handle, flavor = pr.acquire(prefer_rebrowser=True)
        assert flavor == pr.FLAVOR_STOCK
        assert len(fake_playwright) == 1

    def test_sem_playwright_instalado_devolve_none(self, monkeypatch):
        monkeypatch.setattr(
            pr, "_import_sync_playwright", lambda prefer_rebrowser: (None, "")
        )
        handle, flavor = pr.acquire()
        assert handle is None and flavor == ""
        assert pr.is_active() is False

    def test_falha_ao_iniciar_nao_deixa_estado_sujo(self, monkeypatch):
        def _explode(prefer_rebrowser):
            class _Ctx:
                def start(self):
                    raise RuntimeError("driver não subiu")
            return (lambda: _Ctx()), pr.FLAVOR_STOCK

        monkeypatch.setattr(pr, "_import_sync_playwright", _explode)
        handle, _ = pr.acquire()
        assert handle is None
        assert pr.is_active() is False
        assert pr.refcount() == 0


class TestIsolamentoPorThread:
    def test_cada_thread_tem_o_seu_handle(self, fake_playwright):
        """A restrição do asyncio é por thread — o compartilhamento também."""
        pr.acquire()
        da_outra = {}

        def _na_outra_thread():
            handle, _ = pr.acquire()
            da_outra["handle"] = handle
            da_outra["refs"] = pr.refcount()
            pr.release()

        t = threading.Thread(target=_na_outra_thread)
        t.start()
        t.join()

        assert da_outra["refs"] == 1, "contagem vazou entre threads"
        assert len(fake_playwright) == 2


class TestIsTargetClosed:
    @pytest.mark.parametrize(
        "msg",
        [
            "BrowserContext.new_page: Target page, context or browser has been closed",
            "Page.goto: Target closed",
            "Browser has been closed",
            "Connection closed while reading from the driver",
            "Target crashed",
        ],
    )
    def test_reconhece_browser_morto(self, msg):
        assert pr.is_target_closed(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "Timeout 40000ms exceeded.",
            "net::ERR_CONNECTION_REFUSED",
            "Execution context was destroyed",
        ],
    )
    def test_nao_confunde_com_timeout_ou_bloqueio(self, msg):
        # Timeout/bloqueio pedem parar a keyword; browser morto pede reabrir
        # a aba (ou degradar). Confundir os dois foi o que zerou keywords.
        assert pr.is_target_closed(Exception(msg)) is False

    def test_reconhece_pela_classe(self):
        class TargetClosedError(Exception):
            """O fork rebrowser tem a sua própria — casar por nome cobre as duas."""

        assert pr.is_target_closed(TargetClosedError("qualquer coisa")) is True


class TestBaseScraperDevolveAReferencia:
    """
    `__enter__` que levanta nunca chama `__exit__`.

    Sem cleanup dentro do `_launch`, um `new_context()` que falhasse deixava o
    Chromium órfão E a referência do handle presa — e o `main.py` seguia para o
    próximo scraper como se nada tivesse acontecido.
    """

    def _scraper(self, monkeypatch, browser):
        from scrapers.base import BaseScraper

        class _Scraper(BaseScraper):
            platform_name = "Teste"

            def search(self, *a, **k):
                return []

        s = _Scraper.__new__(_Scraper)
        s.platform_name = "Teste"
        s.headless = True
        s._user_agent = "UA"
        s.screenshot_manager = None
        s._playwright = None
        s._browser = None
        s._context = None
        s._page = None

        class _Chromium:
            def launch(self_inner, **_k):
                if browser is None:
                    raise RuntimeError("sem browser")
                return browser

        class _Handle:
            chromium = _Chromium()

        monkeypatch.setattr(
            pr, "_import_sync_playwright",
            lambda prefer_rebrowser: ((lambda: _Ctx(_Handle())), pr.FLAVOR_STOCK),
        )
        return s

    def test_falha_no_contexto_solta_handle_e_browser(self, monkeypatch):
        class _Browser:
            def __init__(self) -> None:
                self.fechado = False

            def new_context(self_inner, **_k):
                raise RuntimeError("contexto recusado")

            def close(self_inner) -> None:
                self_inner.fechado = True

        browser = _Browser()
        s = self._scraper(monkeypatch, browser)

        with pytest.raises(RuntimeError, match="contexto recusado"):
            s._launch()

        assert browser.fechado is True, "Chromium ficou órfão"
        assert pr.is_active() is False, "handle do Playwright ficou preso"

    def test_falha_no_launch_solta_o_handle(self, monkeypatch):
        s = self._scraper(monkeypatch, None)
        with pytest.raises(RuntimeError, match="nenhum browser"):
            s._launch()
        assert pr.is_active() is False


class _Ctx:
    """`sync_playwright()` de mentira: `.start()` devolve o handle dado."""

    def __init__(self, handle) -> None:
        self._handle = handle

    def start(self):
        self._handle.stop = lambda: None
        return self._handle


class TestComDriverReal:
    """Prova de que o contrato resolve o erro real do Playwright."""

    def test_dois_usuarios_no_mesmo_processo(self):
        pytest.importorskip("playwright")
        primeiro, _ = pr.acquire(prefer_rebrowser=True)
        if primeiro is None:
            pytest.skip("driver do Playwright indisponível neste ambiente")
        try:
            # Antes do runtime compartilhado, este segundo start levantava
            # "Sync API inside the asyncio loop" e derrubava o scraper inteiro.
            segundo, _ = pr.acquire()
            assert segundo is primeiro
            assert pr.refcount() == 2
        finally:
            pr.release()
            pr.release()
        assert pr.is_active() is False
