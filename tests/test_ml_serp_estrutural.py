"""
SERP do ML com DOM desconhecido (quebra de 19/10/2026).

A coleta do ML zerou nos três turnos a partir de 19/10/2026 com sessão logada,
Chrome local e IP residencial ("0/40 kw ok, gate [login/verificação de
dispositivo] + vazias sem gate"), enquanto o Mais Vendidos — mesmo PC, mesmo
perfil, `www.` — seguia coletando. Com nenhum seletor de card casando, o
detector de gate lia o `/gz/webdevice` e o CTA de login que TODA página do ML
carrega e chamava a SERP de gate. Estes testes fixam as duas defesas:

* `_has_serp_cards` reconhece a SERP por links para produtos distintos;
* `_select_items` acha os cards pela estrutura e o parser extrai título,
  preço, seller e patrocinado sem nenhuma classe conhecida.
"""

from typing import Any, List

import pytest
from bs4 import BeautifulSoup

from scrapers.mercado_livre import (
    MLScraper,
    _ML_HOME,
    _STRUCTURAL_SELECTOR,
)


def _card(i: int, *, ad: bool = False) -> str:
    """Card com classes inventadas — nada que o `_SELECTORS` conheça."""
    href = (
        f"https://click1.mercadolivre.com.br/mclics/clicks/external/MLB/count?"
        f"a=x&pdp_filters=item_id%3AMLB90000{i:04d}"
        if ad
        else f"https://www.mercadolivre.com.br/ar-condicionado-{i}/p/MLB3000{i:04d}"
        f"#polycard_client=search&wid=MLB90000{i:04d}&sid=search"
    )
    selo = "<span class='zz-tag'>Patrocinado</span>" if ad else ""
    return f"""
    <section class="sr-x91">
      <a href="{href}"><img alt="Ar Condicionado Split {i} 12000 BTUs" src="x.webp"/></a>
      <div class="sr-x92">
        <h3><a class="sr-t" href="{href}">Ar Condicionado Split Inverter Marca{i} 12000 BTUs</a></h3>
        <span class="sr-s">Por <span>Loja{i}</span></span>
        <div class="andes-money-amount">
          <span class="andes-money-amount__fraction">2.{i:03d}</span>
          <span class="andes-money-amount__cents">90</span>
        </div>
        {selo}
      </div>
    </section>"""


def _serp(n: int = 8, ads: int = 1) -> str:
    cards = "".join(_card(i, ad=(i < ads)) for i in range(n))
    return f"""<html><head>
      <script src="https://www.mercadolivre.com.br/gz/webdevice/config"></script>
    </head><body>
      <header><a href="https://www.mercadolivre.com.br/login">
        Para continuar, acesse sua conta</a></header>
      <main><div class="sr-grid">{cards}</div></main>
    </body></html>"""


class _Page:
    def __init__(self, html: str, url: str) -> None:
        self.html, self.url = html, url

    def content(self) -> str:
        return self.html


class TestEvidenciaDeSerp:
    def test_serp_redesenhada_tem_cards(self) -> None:
        assert MLScraper._has_serp_cards(_serp())

    def test_tela_de_gate_nao_tem(self) -> None:
        gate = "<html><body>Para continuar, acesse sua conta /gz/webdevice</body></html>"
        assert not MLScraper._has_serp_cards(gate)

    def test_poucos_links_nao_bastam(self) -> None:
        # página de erro com 2 recomendações não é SERP
        assert not MLScraper._has_serp_cards(_serp(n=2, ads=0))

    def test_serp_redesenhada_nao_e_gate(self) -> None:
        s = MLScraper.__new__(MLScraper)
        s._page = _Page(_serp(), "https://lista.mercadolivre.com.br/ar-condicionado")
        assert s._gate_reason() is None

    def test_home_nao_passa_por_serp(self) -> None:
        # a home tem carrossel de produtos: não pode virar resultado da keyword
        s = MLScraper.__new__(MLScraper)
        s._page = _Page(_serp(), _ML_HOME)
        assert s._gate_reason() is not None


class TestCardsPelaEstrutura:
    def test_um_card_por_produto_em_ordem(self) -> None:
        items, sel = MLScraper._select_items(BeautifulSoup(_serp(), "html.parser"))
        assert sel == _STRUCTURAL_SELECTOR
        assert len(items) == 8
        # catálogo `/p/MLB…` + `wid=MLB…` no mesmo href = um produto só, e o
        # card sobe até o <section> (imagem e título juntos)
        assert all(it.name == "section" for it in items)

    def test_seletor_conhecido_tem_prioridade(self) -> None:
        html = _serp().replace('class="sr-x91"', 'class="ui-search-result"')
        _, sel = MLScraper._select_items(BeautifulSoup(html, "html.parser"))
        assert sel == ".ui-search-result"

    def test_parse_extrai_campos(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        s = MLScraper.__new__(MLScraper)
        s._last_screenshot_busca = None
        recs: List[dict] = s._parse_results(_serp(), "ar condicionado", {}, page_offset=0)
        assert len(recs) == 8

        anuncio, organico = recs[0], recs[1]
        assert anuncio["Patrocinado?"] in ("Sim", True, "SIM", "sim")
        assert anuncio["Posição Patrocinada"] == 1
        assert organico["Posição Orgânica"] == 1
        assert organico["Produto / SKU"].startswith("Ar Condicionado Split Inverter Marca1")
        assert organico["Preço (R$)"] == pytest.approx(2001.90)
        assert organico["Seller / Vendedor"] == "Loja1"
        assert "/p/MLB30000001" in organico["URL Produto"]
        # link de ad vira o permalink do item
        assert anuncio["URL Produto"].endswith("MLB-900000000")

    def test_titulo_com_por_nao_vira_seller(self) -> None:
        card = BeautifulSoup(
            _card(1).replace("Ar Condicionado Split Inverter Marca1", "Por Que Comprar Split")
            .replace('<span class="sr-s">Por <span>Loja1</span></span>', ""),
            "html.parser",
        )
        assert MLScraper._structural_seller(card) is None
