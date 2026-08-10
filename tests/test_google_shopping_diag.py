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
    ])
    def test_challenge_antibot(self, scraper, html):
        assert scraper._classify_zero_result(html) == "challenge"

    @pytest.mark.parametrize("html", [
        "<html>Before you continue to Google</html>",
        "<html>Antes de continuar — Aceitar tudo</html>",
        '<html><div id="cookieconsent">x</div></html>',
    ])
    def test_muro_de_consentimento(self, scraper, html):
        assert scraper._classify_zero_result(html) == "consent"

    def test_busca_legitimamente_vazia(self, scraper):
        html = "<html>Sua pesquisa não encontrou nenhum documento</html>"
        assert scraper._classify_zero_result(html) == "sem_resultados"

    def test_redirect_de_login(self, scraper):
        html = '<html><a href="https://accounts.google.com/ServiceLogin">e</a></html>'
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
