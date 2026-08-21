"""
tests/test_magalu_parse.py — parsers e diagnóstico do scraper da Magalu.

Motivação (incidente de 05/08/2026): a coleta fechou 40 keywords em 0
produtos. O browser ENTREGAVA a página (1,1 MB no log de calibração), mas o
único parser existente (`__NEXT_DATA__`) não achava nada, o código caía
silenciosamente no curl_cffi — que leva 403 do Akamai por design — e o log
só dizia "0 itens". Três buracos: (a) faltava parser pro HTML que a Magalu
serve hoje, (b) o muro de login passava batido por toda a detecção de
bloqueio, (c) falha de parser e bloqueio anti-bot eram indistinguíveis.

Rode: pytest tests/test_magalu_parse.py
"""
import json

import pytest

from scrapers.magalu import MagaluScraper


@pytest.fixture(scope="module")
def scraper():
    return MagaluScraper()


# ---------------------------------------------------------------------------
# Fixtures de HTML
# ---------------------------------------------------------------------------

def _card(
    href="/ar-condicionado-split-midea-12000/p/abc123/ar/arsp/?seller_id=magazineluiza",
    title="Ar Condicionado Split Inverter Springer Midea 12000 BTUs Frio",
    price="R$ 2.199,00",
    rating="4.8 (321)",
    extra="",
) -> str:
    return f"""
    <li data-testid="product-card">
      <a data-testid="product-card-container" href="{href}">
        <h2 data-testid="product-title">{title}</h2>
        <p data-testid="price-value">{price}</p>
        <div data-testid="review"><span>{rating}</span></div>
        {extra}
      </a>
    </li>
    """


def _serp_html(cards: str) -> str:
    """SERP renderizada — inclui o 'Entre ou cadastre-se' do header padrão."""
    return f"""
    <html><body>
      <header><a href="/login/">Entre ou cadastre-se</a></header>
      <ul data-testid="list">{cards}</ul>
    </body></html>
    """


_LOGIN_HTML = """
<html><body>
  <main>
    <h1>Entre na sua conta</h1>
    <form>
      <input name="email" type="email" />
      <input name="password" type="password" />
      <button>Entrar</button>
    </form>
    <a href="/recuperar-senha/">Esqueci minha senha</a>
  </main>
</body></html>
"""


def _rsc_html(products: list, chunks: int = 3) -> str:
    """
    HTML do App Router: payload serializado em pedaços `__next_f.push`.

    O payload real vem quebrado em N chunks arbitrários — inclusive no meio
    de um token JSON —, por isso o teste fatia a string sem dó.
    """
    payload = '0:["$","div",null,{"searchResult":{"products":' + json.dumps(products) + "}}]"
    size = max(1, len(payload) // chunks)
    pieces = [payload[i:i + size] for i in range(0, len(payload), size)]
    scripts = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(piece)}])</script>"
        for piece in pieces
    )
    return f"<html><body>{scripts}</body></html>"


# ---------------------------------------------------------------------------
# Parser de DOM
# ---------------------------------------------------------------------------

class TestParseDomCards:
    def test_extrai_titulo_preco_e_url(self, scraper):
        products = scraper._parse_dom_cards(_serp_html(_card()))
        assert len(products) == 1
        prod = products[0]
        assert "Springer Midea" in prod["title"]
        assert prod["bestPriceTemplate"] == "R$ 2.199,00"
        assert prod["path"].startswith("/ar-condicionado-split-midea")
        assert scraper._extract_price(prod) == 2199.00

    def test_seller_vem_do_seller_id_da_url(self, scraper):
        """Buy box seller é o foco do projeto — a URL do card já o entrega."""
        html = _serp_html(_card(href="/x/p/xyz789/ar/arsp/?seller_id=lojaquentefrio"))
        prod = scraper._parse_dom_cards(html)[0]
        assert scraper._extract_seller(prod) == "lojaquentefrio"

    def test_seller_name_no_dom_vence_a_url(self, scraper):
        html = _serp_html(_card(extra='<span data-testid="seller-name">Casa do Ar</span>'))
        prod = scraper._parse_dom_cards(html)[0]
        assert scraper._extract_seller(prod) == "Casa do Ar"

    def test_rating_e_contagem(self, scraper):
        prod = scraper._parse_dom_cards(_serp_html(_card(rating="4.7 (1.204)")))[0]
        assert prod["rating"] == {"score": 4.7, "count": 1204}

    def test_patrocinado_detectado_pelo_badge(self, scraper):
        html = _serp_html(_card(extra="<span>Patrocinado</span>"))
        prod = scraper._parse_dom_cards(html)[0]
        assert prod["badge"] == "Patrocinado"

    def test_preco_por_regex_quando_nao_ha_seletor(self, scraper):
        """Magalu troca data-testid de preço com frequência — resta o texto."""
        html = _serp_html("""
        <li data-testid="product-card">
          <a data-testid="product-card-container" href="/x/p/abc123/ar/arsp/">
            <h2 data-testid="product-title">Ar Condicionado Split 9000</h2>
            <span>10% OFF</span><span>R$ 1.899,90</span><span>no pix</span>
          </a>
        </li>
        """)
        prod = scraper._parse_dom_cards(html)[0]
        assert scraper._extract_price(prod) == 1899.90

    def test_card_sem_preco_e_descartado(self, scraper):
        """Carrossel promocional entra sem preço — não pode virar registro."""
        html = _serp_html("""
        <li data-testid="product-card">
          <a data-testid="product-card-container" href="/x/p/abc123/ar/arsp/">
            <h2 data-testid="product-title">Ar Condicionado sem preço</h2>
          </a>
        </li>
        """)
        assert scraper._parse_dom_cards(html) == []

    def test_deduplica_por_href(self, scraper):
        html = _serp_html(_card() + _card())
        assert len(scraper._parse_dom_cards(html)) == 1

    def test_ignora_links_que_nao_sao_de_produto(self, scraper):
        html = _serp_html("""
        <li data-testid="product-card">
          <a data-testid="product-card-container" href="/ofertas-do-dia/">
            <h2 data-testid="product-title">Ofertas</h2>
            <p data-testid="price-value">R$ 1,00</p>
          </a>
        </li>
        """)
        assert scraper._parse_dom_cards(html) == []

    def test_titulo_fora_do_link_usa_o_container_do_card(self, scraper):
        """Layout em que o <a> é só a imagem e o título vive no <li>."""
        html = _serp_html("""
        <li data-testid="product-card">
          <a href="/x/p/abc123/ar/arsp/"><img alt="foto"/></a>
          <h2 data-testid="product-title">Ar Condicionado Split 18000</h2>
          <p data-testid="price-value">R$ 3.499,00</p>
        </li>
        """)
        products = scraper._parse_dom_cards(html)
        assert len(products) == 1
        assert products[0]["bestPriceTemplate"] == "R$ 3.499,00"

    def test_nao_vaza_preco_de_um_card_para_o_outro(self, scraper):
        """Subir do <a> até a <ul> misturaria os textos — não pode acontecer."""
        html = """
        <html><body><ul data-testid="product-list">
          <a href="/a/p/aaa111/ar/arsp/"><span>Ar Condicionado sem preço</span></a>
          <a href="/b/p/bbb222/ar/arsp/">
            <h2 data-testid="product-title">Ar Condicionado B</h2>
            <p data-testid="price-value">R$ 2.500,00</p>
          </a>
        </ul></body></html>
        """
        products = scraper._parse_dom_cards(html)
        assert [p["path"] for p in products] == ["/b/p/bbb222/ar/arsp/"]

    def test_pagina_sem_cards_retorna_vazio(self, scraper):
        assert scraper._parse_dom_cards(_LOGIN_HTML) == []


# ---------------------------------------------------------------------------
# Parser RSC (Next.js App Router)
# ---------------------------------------------------------------------------

class TestRscPayload:
    def test_reconstroi_payload_de_varios_chunks(self, scraper):
        products = [
            {"id": "1", "title": "Ar Condicionado A", "bestPrice": 1999.0},
            {"id": "2", "title": "Ar Condicionado B", "bestPrice": 2999.0},
        ]
        found = scraper._find_products_in_rsc(_rsc_html(products, chunks=5))
        assert [p["title"] for p in found] == ["Ar Condicionado A", "Ar Condicionado B"]

    def test_ignora_array_sem_precos(self, scraper):
        """Carrossel/breadcrumb tem title+id mas não tem preço — não é SERP."""
        carrossel = [{"id": "9", "title": "Ar Condicionado"} for _ in range(3)]
        assert scraper._find_products_in_rsc(_rsc_html(carrossel)) == []

    def test_html_sem_rsc_retorna_vazio(self, scraper):
        assert scraper._find_products_in_rsc(_serp_html(_card())) == []

    def test_balanced_slice_respeita_strings_e_escapes(self, scraper):
        text = 'lixo antes ["a]b", "c\\"]d", [1, 2]] lixo depois'
        blob = scraper._balanced_slice(text, text.index("["))
        assert json.loads(blob) == ["a]b", 'c"]d', [1, 2]]

    def test_balanced_slice_sem_fechamento(self, scraper):
        assert scraper._balanced_slice('["a", "b"', 0) is None


# ---------------------------------------------------------------------------
# Muro de login
# ---------------------------------------------------------------------------

class TestLoginWall:
    def test_detecta_tela_de_identificacao(self, scraper):
        assert scraper._looks_like_login_wall(_LOGIN_HTML) is True

    def test_serp_normal_nao_e_login(self, scraper):
        """O header da Magalu traz 'Entre ou cadastre-se' em TODA página."""
        assert scraper._looks_like_login_wall(_serp_html(_card())) is False

    def test_url_de_sso_basta(self, scraper):
        assert scraper._looks_like_login_wall(
            "", "https://sso.magazineluiza.com.br/login?redirect=/busca/"
        ) is True

    def test_serp_com_modal_por_cima_nao_conta(self, scraper):
        """Modal dispensável sobre a SERP: os produtos estão no DOM."""
        html = _serp_html(_card()) + _LOGIN_HTML
        assert scraper._looks_like_login_wall(html) is False

    def test_html_vazio(self, scraper):
        assert scraper._looks_like_login_wall("") is False


# ---------------------------------------------------------------------------
# Cascata de parsers + registros
# ---------------------------------------------------------------------------

class TestRecordsFromHtml:
    def test_dom_gera_registros_quando_nao_ha_json(self, scraper):
        html = _serp_html(_card() + _card(
            href="/y/p/def456/ar/arsp/?seller_id=friocerto",
            title="Ar Condicionado Split Inverter Elgin 9000 BTUs",
            price="R$ 1.799,00",
        ))
        records = scraper._records_from_html(
            html, "ar condicionado split", {}, page=1, source="browser"
        )
        assert len(records) == 2
        assert records[0]["Posição Geral"] == 1
        assert records[1]["Posição Geral"] == 2
        assert records[0]["Preço (R$)"] == 2199.00
        assert records[1]["Buy Box Seller"] == "friocerto"
        assert records[1]["Tipo Seller"] == "3P"

    def test_next_data_tem_prioridade(self, scraper):
        """Payload JSON é mais rico — só cai pro DOM quando ele não existe."""
        next_data = {"props": {"pageProps": {"searchResult": {"products": [
            {"id": "1", "title": "Ar Condicionado do JSON", "bestPrice": 1500.0,
             "path": "/x/p/abc123/ar/arsp/"},
        ]}}}}
        html = (
            f'<html><body><script id="__NEXT_DATA__">{json.dumps(next_data)}</script>'
            f"{_serp_html(_card())}</body></html>"
        )
        records = scraper._records_from_html(html, "kw", {}, page=1, source="browser")
        assert len(records) == 1
        assert "JSON" in records[0]["Produto / SKU"].upper()
        assert records[0]["Preço (R$)"] == 1500.0

    def test_paginas_seguintes_deslocam_a_posicao(self, scraper):
        records = scraper._records_from_html(
            _serp_html(_card()), "kw", {}, page=2, source="browser"
        )
        assert records[0]["Posição Geral"] == 61  # offset de 1 página (60 itens)

    def test_html_sem_produtos_nao_gera_registro(self, scraper):
        assert scraper._records_from_html(
            _LOGIN_HTML, "kw", {}, page=1, source="browser"
        ) == []


# ---------------------------------------------------------------------------
# Diagnóstico e circuit breaker
# ---------------------------------------------------------------------------

class _FakePage:
    """Página mínima: só o suficiente pro diagnóstico ler a URL."""

    def __init__(self, url: str = "https://www.magazineluiza.com.br/busca/x/"):
        self.url = url

    def is_closed(self) -> bool:
        return False


class TestDiagnostico:
    def test_login_wall_marca_causa_e_exige_acao(self, monkeypatch):
        s = MagaluScraper()
        s._pw_page = _FakePage("https://sso.magazineluiza.com.br/login")
        monkeypatch.setattr(s, "_dump_block_html", lambda *a, **k: None)
        s._diagnose_empty_html(_LOGIN_HTML, "kw", 1)
        assert s._login_required is True
        assert s._last_failure_reason == "login"

    def test_cards_sem_extracao_vira_causa_layout(self, monkeypatch):
        """Cards no DOM que nenhum parser leu = bug nosso, não Akamai."""
        s = MagaluScraper()
        s._pw_page = _FakePage()
        monkeypatch.setattr(s, "_dump_block_html", lambda *a, **k: None)
        html = _serp_html('<li data-testid="product-card">sem link</li>')
        s._diagnose_empty_html(html, "kw", 1)
        assert s._last_failure_reason == "layout"
        assert s._login_required is False

    def test_circuit_breaker_de_login_dispara_mais_cedo(self):
        s = MagaluScraper()
        s._login_required = True
        s._blocked_keyword_streak = 2
        s._maybe_trip_circuit_breaker()
        assert s.collection_aborted is True

    def test_circuit_breaker_akamai_espera_cinco_keywords(self):
        s = MagaluScraper()
        s._blocked_keyword_streak = 4
        s._maybe_trip_circuit_breaker()
        assert s.collection_aborted is False
        s._blocked_keyword_streak = 5
        s._maybe_trip_circuit_breaker()
        assert s.collection_aborted is True


class _FlakyPage(_FakePage):
    """Página cujo `content()` falha nas N primeiras chamadas."""

    def __init__(self, falhas: int):
        super().__init__()
        self.falhas = falhas
        self.chamadas = 0

    def content(self) -> str:
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise RuntimeError("Protocol error (Runtime.evaluate): session closed")
        return "<html>ok</html>"


class TestSafeContent:
    """
    O rebrowser perde o execution context se o evaluate cai no meio de uma
    navegação. A página está viva — sem retry, a tentativa da keyword era
    descartada por uma janela de milissegundos.
    """

    def test_retorna_html_na_segunda_tentativa(self, scraper, monkeypatch):
        monkeypatch.setattr("scrapers.magalu.time.sleep", lambda _s: None)
        page = _FlakyPage(falhas=1)
        assert scraper._safe_content(page) == "<html>ok</html>"
        assert page.chamadas == 2

    def test_desiste_apos_duas_falhas(self, scraper, monkeypatch):
        monkeypatch.setattr("scrapers.magalu.time.sleep", lambda _s: None)
        page = _FlakyPage(falhas=5)
        assert scraper._safe_content(page) == ""
        assert page.chamadas == 2

    def test_sem_pagina_retorna_vazio(self, scraper):
        assert scraper._safe_content(None) == ""


class TestCascataDeEstrategias:
    """
    O fallback curl_cffi só faz sentido quando o browser não entregou nada.

    No incidente, ele rodava em toda keyword mesmo com o browser saudável e
    consumia ~45s de timeout por página pra receber o 403 esperado.
    """

    def _scraper_com_browser(self, monkeypatch, browser_html):
        s = MagaluScraper()
        s._pw_page = _FakePage()
        s._cffi_session = object()
        s._cffi_calls = 0

        def _fake_cffi(keyword, page):
            s._cffi_calls += 1
            return None

        monkeypatch.setattr(s, "_search_via_browser", lambda k, p: browser_html)
        monkeypatch.setattr(s, "_fetch_html_search", _fake_cffi)
        monkeypatch.setattr(s, "_dump_block_html", lambda *a, **k: None)
        return s

    def test_browser_ok_nao_chama_cffi(self, monkeypatch):
        s = self._scraper_com_browser(monkeypatch, _serp_html(_card()))
        records = s._search_page("kw", {}, 1)
        assert len(records) == 1
        assert s._cffi_calls == 0

    def test_browser_entregou_html_ruim_tambem_nao_chama_cffi(self, monkeypatch):
        s = self._scraper_com_browser(monkeypatch, _LOGIN_HTML)
        assert s._search_page("kw", {}, 1) == []
        assert s._cffi_calls == 0

    def test_sem_browser_usa_cffi(self, monkeypatch):
        s = self._scraper_com_browser(monkeypatch, None)
        assert s._search_page("kw", {}, 1) == []
        assert s._cffi_calls == 1

    def test_cffi_desliga_apos_falhas_seguidas(self, monkeypatch):
        s = self._scraper_com_browser(monkeypatch, None)
        s._search_page("kw", {}, 1)
        s._search_page("kw", {}, 2)
        assert s._cffi_disabled is True
        s._search_page("kw", {}, 3)
        assert s._cffi_calls == 2  # a terceira nem tentou


# ---------------------------------------------------------------------------
# Sessão validada — regressão do AttributeID de 21/08/2026
# ---------------------------------------------------------------------------

class TestValidaSessao:
    """
    `_ensure_validated_session` chamava `_validate_session_via_browser`, um
    método que não existia — a coleta de mais vendidos da Magalu morria com
    `AttributeError` no fallback curl_cffi. Estes testes fixam o contrato.
    """

    def _scraper(self):
        s = MagaluScraper()
        s._session_validated = False
        s._browser_mode = True
        return s

    def test_metodo_existe(self):
        assert hasattr(MagaluScraper, "_validate_session_via_browser")

    def test_cache_vazio_colhe_cookies_do_browser(self, monkeypatch):
        s = self._scraper()
        cookies = [{"name": "_abck", "value": "x~0~y", "domain": ".magazineluiza.com.br"}]
        monkeypatch.setattr(s, "_load_cached_session", lambda: None)
        monkeypatch.setattr(s, "_revive_page", lambda: True)
        monkeypatch.setattr(s, "_refresh_browser_session", lambda: None)
        monkeypatch.setattr(s, "_save_cached_session", lambda c: None)
        monkeypatch.setattr(s, "_apply_cookies_to_cffi", lambda c: len(c))

        class _Ctx:
            def cookies(self_inner):
                return cookies

        s._pw_context = _Ctx()
        assert s._ensure_validated_session() is True
        assert s._session_validated is True

    def test_sem_browser_falha_sem_explodir(self, monkeypatch):
        """Browser indisponível → None, e a coleta segue (retorna False)."""
        s = self._scraper()
        monkeypatch.setattr(s, "_load_cached_session", lambda: None)
        monkeypatch.setattr(s, "_revive_page", lambda: False)
        assert s._validate_session_via_browser() is None
        assert s._ensure_validated_session() is False

    def test_abck_em_challenge_nao_e_cacheado(self, monkeypatch):
        """
        Sessão bloqueada não pode ser colhida nem cacheada: `_abck` sem `~0~`
        significa challenge do Akamai — reusá-la levaria 403 no curl_cffi e
        envenenaria o cache em disco.
        """
        s = self._scraper()
        cookies = [{"name": "_abck", "value": "abc~-1~challenge", "domain": ".x"}]
        monkeypatch.setattr(s, "_revive_page", lambda: True)
        monkeypatch.setattr(s, "_refresh_browser_session", lambda: None)
        salvos = []
        monkeypatch.setattr(s, "_save_cached_session", lambda c: salvos.append(c))
        monkeypatch.setattr(s, "_load_cached_session", lambda: None)

        class _Ctx:
            def cookies(self_inner):
                return cookies

        s._pw_context = _Ctx()
        assert s._validate_session_via_browser() is None
        assert s._ensure_validated_session() is False
        assert salvos == [], "sessão em challenge não pode ir para o cache"
