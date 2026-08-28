"""
tests/test_google_shopping_diag.py — diagnóstico de resultado vazio.

Contexto (10/08/2026): o Google Shopping está com ZERO registros desde
23/05/2026 — 79 dias. O remédio óbvio (mais seletores de fallback) já tinha
sido aplicado em Mai/2026, são 13 hoje, e não resolveu. O log dizia apenas
"0 cards encontrados", que é compatível com todas as causas possíveis, então
não dava para escolher entre consertar o parser, trocar o IP ou aceitar que a
busca veio vazia.

Estes testes cobrem a classificação que separa essas causas — em particular a
distinção entre `layout` (único caso em que mexer no parser é o conserto certo)
e as causas de acesso, em que o parser está correto.

Rode: pytest tests/test_google_shopping_diag.py
"""
import pytest

from scrapers.google_shopping import GoogleShoppingScraper


@pytest.fixture(scope="module")
def scraper():
    return GoogleShoppingScraper()


class TestClassifyZeroResult:
    @pytest.mark.parametrize("html", [
        "<html>Our systems have detected unusual traffic</html>",
        "<html>Detectamos tráfego incomum da sua rede</html>",
        '<html><a href="/sorry/index?continue=x">x</a></html>',
        "<html><title>Sorry...</title><body>x</body></html>",
    ])
    def test_challenge_antibot(self, scraper, html):
        assert scraper._classify_zero_result(html) == "challenge"

    @pytest.mark.parametrize("html", [
        "<html>Before you continue to Google Search</html>",
        "<html><title>Antes de continuar</title><body>x</body></html>",
    ])
    def test_muro_de_consentimento(self, scraper, html):
        assert scraper._classify_zero_result(html) == "consent"

    def test_busca_legitimamente_vazia(self, scraper):
        html = "<html>Sua pesquisa não encontrou nenhum documento</html>"
        assert scraper._classify_zero_result(html) == "sem_resultados"

    def test_pagina_de_login(self, scraper):
        html = "<html><title>Fazer login - Contas do Google</title>x</html>"
        assert scraper._classify_zero_result(html) == "login"

    def test_pagina_normal_com_layout_novo(self, scraper):
        """Sem marcador de bloqueio, a hipótese é layout — aí sim é parser."""
        html = '<html><body><div class="classe-nova">Ar Condicionado</div></body></html>'
        assert scraper._classify_zero_result(html) == "layout"

    def test_html_vazio_nao_estoura(self, scraper):
        assert scraper._classify_zero_result("") == "layout"

    def test_none_nao_estoura(self, scraper):
        assert scraper._classify_zero_result(None) == "layout"

    def test_case_insensitive(self, scraper):
        html = "<html>OUR SYSTEMS HAVE DETECTED UNUSUAL TRAFFIC</html>"
        assert scraper._classify_zero_result(html) == "challenge"


class TestAcaoPorCausa:
    def test_toda_causa_tem_acao_descrita(self, scraper):
        """O log precisa dizer o que fazer, não só o que aconteceu."""
        causas = {c for c, _ in scraper._CAUSAS_ZERO} | {"layout"}
        assert causas <= set(scraper._ACAO_POR_CAUSA)

    def test_so_layout_manda_mexer_no_parser(self, scraper):
        assert "parser" in scraper._ACAO_POR_CAUSA["layout"].lower()
        for causa in ("challenge", "consent"):
            assert "parser está ok" in scraper._ACAO_POR_CAUSA[causa].lower()


class TestFalsoPositivoDeRodape:
    """
    O risco real da classificação por substring.

    Toda SERP do Google carrega link de cookies/privacidade/login no rodapé.
    Se um scan ingênuo achar "aceitar tudo" ali, uma página perfeitamente
    normal — o caso `layout`, o único em que mexer no parser é o conserto
    certo — seria diagnosticada como muro de consentimento, e mandaria alguém
    trocar de IP ou de perfil à toa. É exatamente a conclusão enganosa que
    esta feature existe para evitar, então os casos abaixo travam o
    comportamento correto.
    """

    _RODAPE = (
        '<div id="footer">'
        '<a href="https://policies.google.com/privacy">Privacidade</a>'
        '<a href="https://consent.google.com/">Cookies</a>'
        '<a href="https://accounts.google.com/ServiceLogin">Fazer login</a>'
        "<button>Aceitar tudo</button>"
        "</div>"
    )

    def test_serp_normal_com_rodape_completo_e_layout(self, scraper):
        html = (
            "<html><title>ar condicionado - Google Shopping</title>"
            f"<body><div class='classe-nova'>produtos</div>{self._RODAPE}</body></html>"
        )
        assert scraper._classify_zero_result(html) == "layout"

    def test_link_de_consent_no_rodape_nao_vira_consent(self, scraper):
        html = f"<html><body>resultados{self._RODAPE}</body></html>"
        assert scraper._classify_zero_result(html) == "layout"

    def test_link_de_login_no_rodape_nao_vira_login(self, scraper):
        html = (
            '<html><body>resultados'
            '<a href="https://accounts.google.com/ServiceLogin">Fazer login</a>'
            "</body></html>"
        )
        assert scraper._classify_zero_result(html) == "layout"

    def test_muro_real_ainda_e_detectado_apesar_do_rodape(self, scraper):
        """A tolerância ao rodapé não pode cegar o caso verdadeiro."""
        html = (
            "<html><title>Antes de continuar</title>"
            f"<body>Before you continue to Google Search{self._RODAPE}</body></html>"
        )
        assert scraper._classify_zero_result(html) == "consent"


class TestChromeLocalMode:
    """Cobre a fiação do Chrome real local (RAC_LOCAL_CHROME) no Google Shopping.

    Sem essa fiação o scraper ignorava o perfil logado e caía no launch próprio
    do BaseScraper, tomando reCAPTCHA de imediato — a causa do Google zerar
    mesmo com "perfil do Chrome carregado" (Ago/2026).
    """

    def test_launch_cai_no_base_quando_local_desligado(self, monkeypatch, scraper):
        import scrapers.google_shopping as gs

        monkeypatch.setattr(gs, "is_local_chrome_enabled", lambda: False)
        chamou = {"base": False}

        def _fake_super_launch(self):
            chamou["base"] = True

        monkeypatch.setattr(gs.BaseScraper, "_launch", _fake_super_launch)
        scraper._local_active = False
        scraper._launch()
        assert chamou["base"] is True
        assert scraper._local_active is False

    def test_launch_cai_no_base_quando_chrome_local_nao_abre(self, monkeypatch):
        """RAC_LOCAL_CHROME=1 mas get_local_browser() None → fallback sem raise.

        É o caminho que protege VM/GH Actions e qualquer host com a flag ligada
        sem Chrome de pé. Também é o caminho do browser próprio, então uma
        regressão aqui estoura (e pula o cleanup) em vez de degradar.
        """
        import scrapers.google_shopping as gs

        monkeypatch.setattr(gs, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(gs, "get_local_browser", lambda: None)
        chamou = {"base": False}

        def _fake_super_launch(self):
            chamou["base"] = True

        monkeypatch.setattr(gs.BaseScraper, "_launch", _fake_super_launch)
        s = gs.GoogleShoppingScraper()
        s._launch()
        assert chamou["base"] is True
        assert s._local_active is False

    def test_launch_usa_chrome_local_quando_disponivel(self, monkeypatch):
        import scrapers.google_shopping as gs

        class _FakePage:
            def set_default_timeout(self, _):
                pass

        class _FakeLB:
            context = object()

            def new_page(self):
                return _FakePage()

        monkeypatch.setattr(gs, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(gs, "get_local_browser", lambda: _FakeLB())

        s = gs.GoogleShoppingScraper()
        s._launch()
        assert s._local_active is True
        assert s._page is not None

    def test_manual_captcha_default_e_toggle(self, monkeypatch):
        from scrapers.google_shopping import GoogleShoppingScraper as G

        monkeypatch.delenv("RAC_GOOGLE_MANUAL_CAPTCHA", raising=False)
        assert G._manual_captcha_enabled() is True
        monkeypatch.setenv("RAC_GOOGLE_MANUAL_CAPTCHA", "0")
        assert G._manual_captcha_enabled() is False

    def test_manual_captcha_timeout_parsing(self, monkeypatch):
        from scrapers.google_shopping import GoogleShoppingScraper as G

        monkeypatch.delenv("RAC_GOOGLE_MANUAL_CAPTCHA_TIMEOUT", raising=False)
        assert G._manual_captcha_timeout() == 180.0
        monkeypatch.setenv("RAC_GOOGLE_MANUAL_CAPTCHA_TIMEOUT", "45")
        assert G._manual_captcha_timeout() == 45.0
        monkeypatch.setenv("RAC_GOOGLE_MANUAL_CAPTCHA_TIMEOUT", "lixo")
        assert G._manual_captcha_timeout() == 180.0

    def test_await_manual_captcha_desligado_fora_do_modo_local(self, scraper):
        scraper._local_active = False
        assert scraper._await_manual_captcha_solution() is False


class _StubPage:
    """Página mínima que devolve um HTML fixo em .content()."""

    def __init__(self, html: str):
        self._html = html

    def content(self) -> str:
        return self._html


class TestCaptchaCleared:
    """Cobre o sinal de conclusão do reCAPTCHA (não usa existência do widget)."""

    def _scraper_com_html(self, html):
        s = GoogleShoppingScraper()
        s._page = _StubPage(html)
        return s

    def test_cards_presentes_liberam_mesmo_com_widget_no_dom(self):
        # Widget de captcha AINDA no DOM, mas cards de resultado presentes:
        # a página foi liberada — não pode esperar o timeout inteiro.
        cards = "".join("<div class='Ez5pwe'>produto</div>" for _ in range(3))
        html = f"<html><body><div id='recaptcha'></div>{cards}</body></html>"
        assert self._scraper_com_html(html)._captcha_cleared() is True

    def test_captcha_sem_cards_nao_liberou(self):
        html = "<html><body><form id='captcha-form'></form></body></html>"
        assert self._scraper_com_html(html)._captcha_cleared() is False

    def test_pagina_sem_captcha_e_sem_cards_conta_como_liberada(self):
        html = "<html><body>nenhum resultado</body></html>"
        assert self._scraper_com_html(html)._captcha_cleared() is True


class TestWarmupRetry:
    """A falha do warm-up não pode marcar a sessão como aquecida (retry)."""

    def test_falha_do_goto_nao_marca_aquecido(self):
        s = GoogleShoppingScraper()
        s._local_active = True

        class _PageBoom:
            def goto(self, *a, **k):
                raise RuntimeError("sem rede")

        s._page = _PageBoom()
        s._warmup_google_home()
        assert s._google_warmed is False
