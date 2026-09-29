"""
tests/test_bestsellers_magalu.py — População da lista de mais vendidos da Magalu.

Incidente (28–29/09/2026): a lista "ar condicionado" ordenada por quantidade
vendida saiu com 2 itens, e o nº 1 era uma "Geladeira Midea … Inverter" — um
anúncio patrocinado (`ads=patrocinado` na URL), de outro departamento
(`/p/…/ed/refr/`), classificado como SPLIT_HW por causa do "Inverter" e
contado no KPI do grupo Midea. Em 20/08 uma "máquina de corte" (`/pi/mqpr/`)
já tinha ocupado o nº 3. A busca da Magalu é difusa e aceita anúncio de
qualquer produto; estes testes fixam que nada disso vira posição do ranking.

Rode: pytest tests/test_bestsellers_magalu.py
"""

from typing import List

import pytest

from bestsellers.sources import magalu as fonte_magalu
from bestsellers.sources.magalu import MagaluBestSellers
from scrapers.magalu import MagaluScraper


def _card(href: str, title: str, price: str = "R$ 2.199,00") -> str:
    return f"""
    <li data-testid="product-card">
      <a data-testid="product-card-container" href="{href}">
        <h2 data-testid="product-title">{title}</h2>
        <p data-testid="price-value">{price}</p>
      </a>
    </li>
    """


def _serp(*cards: str) -> str:
    return f'<html><body><ul data-testid="list">{"".join(cards)}</ul></body></html>'


_ANUNCIO_GELADEIRA = _card(
    "/geladeira-midea-cycle-defrost-duplex-410l-inverter/p/cj10982ahd/ed/refr/"
    "?seller_id=mgshopgra&ads=patrocinado",
    "Geladeira Midea Cycle Defrost Duplex 410L Inverter",
    "R$ 2.077,77",
)
_ANUNCIO_SPLIT = _card(
    "/ar-condicionado-inverter-frio-split-zen-top-9000-btus-agratto/p/jkhh85efdc/"
    "ar/aciv/?seller_id=mgshopgra&ads=patrocinado",
    "Ar Condicionado Inverter Frio Split Zen Top 9000 Btus Agratto",
    "R$ 1.699,98",
)
_MAQUINA_DE_CORTE = _card(
    "/silhouette-portrait-4-maquina-de-corte-digital/p/ke5a11c1cg/pi/mqpr/"
    "?seller_id=silhouetteexperts",
    "Silhouette Portrait 4 - Máquina de Corte Digital",
    "R$ 1.499,00",
)
_SPLIT_PHILCO = _card(
    "/ar-condicionado-split-12-000-btus-philco-inverter/p/240084600/ar/aciv/"
    "?seller_id=magazineluiza",
    "Ar-condicionado Split 12.000 BTUs Philco Inverter Frio Hi Wall PAC12FC",
    "R$ 1.988,10",
)
_SPLIT_MIDEA = _card(
    "/ar-condicionado-split-hw-inverter-midea-airvolution-lite/p/cb396h28ea/ar/arsp/"
    "?seller_id=leveros3",
    "Ar-Condicionado Split HW Inverter Midea AirVolution Lite 12.000 BTUs",
    "R$ 1.979,10",
)


@pytest.fixture
def fonte(monkeypatch) -> MagaluBestSellers:
    """Fonte com o scraper real (parsers) mas sem rede nem browser."""
    src = MagaluBestSellers()
    src._mag = MagaluScraper()
    dumps: List[str] = []
    monkeypatch.setattr(
        src._mag, "_dump_block_html", lambda _html, label: dumps.append(label)
    )
    src.dumps = dumps
    return src


def _servir(monkeypatch, src: MagaluBestSellers, *paginas: str) -> None:
    """Faz `_baixar` devolver as páginas na ordem; depois disso, nada."""
    fila = list(paginas)
    monkeypatch.setattr(
        src, "_baixar", lambda _url, _pagina: fila.pop(0) if fila else None
    )


class TestPopulacaoDaLista:
    def test_anuncio_e_outro_departamento_nao_viram_posicao(self, fonte, monkeypatch):
        """O caso de 28/09: a geladeira patrocinada não pode ser o nº 1."""
        _servir(monkeypatch, fonte, _serp(
            _ANUNCIO_GELADEIRA, _SPLIT_PHILCO, _MAQUINA_DE_CORTE, _SPLIT_MIDEA,
        ))
        itens = fonte.coletar(paginas=1)

        assert [i.titulo for i in itens] == [
            "Ar-condicionado Split 12.000 BTUs Philco Inverter Frio Hi Wall PAC12FC",
            "Ar-Condicionado Split HW Inverter Midea AirVolution Lite 12.000 BTUs",
        ]
        assert [i.rank for i in itens] == [1, 2]
        assert all(i.tipo == "SPLIT_HW" for i in itens)

    def test_anuncio_de_ar_condicionado_tambem_sai(self, fonte, monkeypatch):
        """Anúncio é espaço comprado, não posição por vendas — mesmo sendo AC."""
        _servir(monkeypatch, fonte, _serp(_ANUNCIO_SPLIT, _SPLIT_PHILCO))
        itens = fonte.coletar(paginas=1)
        assert [i.sku_plataforma for i in itens] == ["240084600"]
        assert itens[0].rank == 1

    def test_pagina_so_com_intrusos_nomeia_a_causa(self, fonte, monkeypatch):
        """2 anúncios e mais nada: não é lista vazia legítima, é grade ausente."""
        _servir(monkeypatch, fonte, _serp(_ANUNCIO_GELADEIRA, _ANUNCIO_SPLIT))
        itens = fonte.coletar(paginas=1)

        assert itens == []
        assert "anúncio" in fonte.falha
        assert "grade orgânica ausente" in fonte.falha
        assert fonte.dumps and "so_intrusos" in fonte.dumps[0]

    def test_url_sem_departamento_fica(self, fonte, monkeypatch):
        """Sem o segmento de departamento não há prova — descartar no escuro
        tiraria ar condicionado de verdade da lista."""
        _servir(monkeypatch, fonte, _serp(_card(
            "/ar-condicionado-split-lg/p/ea11e17gh0/?seller_id=friopecas",
            "Ar Condicionado Split LG Dual Inverter 12000 BTUs",
        )))
        itens = fonte.coletar(paginas=1)
        assert len(itens) == 1

    def test_ventilador_do_departamento_ar_fica_para_o_portao(self, fonte, monkeypatch):
        """Contaminação DENTRO de Ar e Ventilação fica na base, marcada fora do
        escopo — é o portão de escopo do validate.py que a denuncia."""
        _servir(monkeypatch, fonte, _serp(
            _SPLIT_PHILCO,
            _card(
                "/ventilador-torre/p/aa11bb22cc/ar/arpr/?seller_id=magazineluiza",
                "Ventilador Torre Ar Condicionado Com Controle",
            ),
        ))
        itens = fonte.coletar(paginas=1)
        assert len(itens) == 2
        assert itens[1].no_escopo is False

    def test_filtro_roda_antes_da_numeracao_entre_paginas(self, fonte, monkeypatch):
        """O anúncio da página 2 não consome posição: a contagem continua."""
        _servir(
            monkeypatch, fonte,
            _serp(_SPLIT_PHILCO),
            _serp(_ANUNCIO_GELADEIRA, _SPLIT_MIDEA),
        )
        itens = fonte.coletar(paginas=2)
        assert [i.rank for i in itens] == [1, 2]
        assert itens[1].sku_plataforma == "CB396H28EA"


class TestDepartamento:
    @pytest.mark.parametrize("path, esperado", [
        ("/x/p/cj10982ahd/ed/refr/?seller_id=mgshopgra&ads=patrocinado", "ed"),
        ("/x/p/240084600/ar/aciv/?seller_id=magazineluiza", "ar"),
        ("https://m.magazineluiza.com.br/x/p/ke5a11c1cg/pi/mqpr/", "pi"),
        ("/x/p/ea11e17gh0/?seller_id=friopecas", None),
        ("", None),
    ])
    def test_le_o_departamento_da_url(self, path, esperado):
        assert MagaluBestSellers._departamento({"path": path}) == esperado


class TestEsperaPelaGradeOrganica:
    def test_espera_card_sem_ads(self):
        """O anúncio chega pronto do servidor; esperar por qualquer `/p/`
        liberava a leitura antes de a grade orgânica hidratar."""
        chamadas: List[str] = []

        class _Pagina:
            def wait_for_selector(self, seletor, timeout):
                chamadas.append(seletor)

        MagaluBestSellers._esperar_produtos(_Pagina())
        assert chamadas == [fonte_magalu._SELETOR_CARD_ORGANICO]
        assert ':not([href*="ads="])' in chamadas[0]
