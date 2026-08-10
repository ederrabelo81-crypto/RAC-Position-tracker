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
