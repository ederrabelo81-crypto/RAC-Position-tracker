"""Testes da taxonomia de keywords (`utils/keyword_taxonomy.py`).

A amostra `_PRODUCAO` é a lista REAL de `coletas.keyword` × `categoria` de
28–29/09/2026 (57 valores) — incluindo as 17 vitrines de dealer que apareciam
misturadas às buscas no filtro "Keywords" do dashboard.

Rodar:
    pytest tests/test_keyword_taxonomy.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from utils.keyword_taxonomy import (  # noqa: E402
    GROUP_TOKENS,
    TIPO_GENERICA,
    TIPO_MARCA_CONCORRENTE,
    TIPO_MARCA_PROPRIA,
    TIPO_VITRINE_DEALER,
    classify_keyword,
    expand_keyword_selection,
    format_keyword_option,
    keyword_options,
    keywords_by_tipo,
    sort_keywords,
)

_PRODUCAO = {
    "ar condicionado": "Genérica",
    "ar condicionado 12000 btus": "Capacidade BTU",
    "ar condicionado 12000 btus inverter": "Capacidade + Tipo",
    "ar condicionado 18000 btus": "Capacidade BTU",
    "ar condicionado 24000 btus": "Capacidade BTU",
    "ar condicionado 9000 btus": "Capacidade BTU",
    "ar condicionado 9000 btus inverter": "Capacidade + Tipo",
    "ar condicionado consul": "Marca",
    "ar condicionado daikin": "Marca",
    "ar condicionado electrolux": "Marca",
    "ar condicionado elgin": "Marca",
    "ar condicionado em promoção": "Preço / Promoção",
    "ar condicionado gree": "Marca",
    "ar condicionado hisense": "Marca",
    "ar condicionado inverter": "Genérica",
    "ar condicionado inverter mais econômico": "Conversacional IA",
    "ar condicionado lg": "Marca",
    "ar condicionado lg dual inverter 12000": "Marca",
    "ar condicionado midea": "Marca",
    "ar condicionado philco": "Marca",
    "ar condicionado portátil": "Segmento",
    "ar condicionado quente e frio": "Genérica",
    "ar condicionado samsung": "Marca",
    "ar condicionado silencioso": "Conversacional IA",
    "ar condicionado split": "Genérica",
    "ar condicionado split inverter": "Genérica",
    "ar condicionado tcl": "Marca",
    "ar condicionado wifi": "Conversacional IA",
    "ArCerto": "Dealers",
    "Belmicro": "Dealers",
    "Bemol": "Dealers",
    "CentralAr": "Dealers",
    "Climario": "Dealers",
    "comprar ar condicionado": "Intenção Compra",
    "Dufrio": "Dealers",
    "Eletrozema": "Dealers",
    "EngageEletro": "Dealers",
    "FerreiraCosta": "Dealers",
    "Frigelar": "Dealers",
    "FrioPecas": "Dealers",
    "Fujioka": "Dealers",
    "GBarbosa": "Dealers",
    "GoCompras": "Dealers",
    "Leveros": "Dealers",
    "lg dual inverter": "Modelo / Linha",
    "melhor ar condicionado 2026": "Intenção Compra",
    "melhor ar condicionado custo benefício": "Intenção Compra",
    "melhor ar condicionado para quarto": "Conversacional IA",
    "midea 12000 btus": "Marca",
    "midea airvolution": "Modelo / Linha",
    "midea ecomaster": "Modelo / Linha",
    "midea inverter": "Marca",
    "PoloAr": "Dealers",
    "samsung windfree": "Modelo / Linha",
    "split 12000 btus inverter": "Capacidade + Tipo",
    "split 9000 btus inverter": "Capacidade + Tipo",
    "WebContinental": "Dealers",
}


class TestClassificacaoDaProducao:
    def test_contagem_por_tipo(self):
        tipos = [classify_keyword(k, c) for k, c in _PRODUCAO.items()]
        assert tipos.count(TIPO_VITRINE_DEALER) == 17
        assert tipos.count(TIPO_MARCA_PROPRIA) == 5
        assert tipos.count(TIPO_MARCA_CONCORRENTE) == 13
        assert tipos.count(TIPO_GENERICA) == 22

    @pytest.mark.parametrize("kw", ["midea ecomaster", "midea airvolution", "ar condicionado midea"])
    def test_linhas_midea_sao_marca_propria(self, kw):
        assert classify_keyword(kw, _PRODUCAO[kw]) == TIPO_MARCA_PROPRIA

    @pytest.mark.parametrize("kw", ["lg dual inverter", "samsung windfree", "ar condicionado consul"])
    def test_rivais_sao_marca_concorrente(self, kw):
        assert classify_keyword(kw, _PRODUCAO[kw]) == TIPO_MARCA_CONCORRENTE

    def test_vitrine_de_dealer_sem_categoria(self):
        """Histórico sem categoria: o nome da loja própria basta."""
        assert classify_keyword("WebContinental") == TIPO_VITRINE_DEALER

    def test_sem_categoria_cai_no_config(self):
        assert classify_keyword("ar condicionado 12000 btus") == TIPO_GENERICA
        assert classify_keyword("midea inverter") == TIPO_MARCA_PROPRIA

    def test_keyword_nova_decide_pelo_texto(self):
        assert classify_keyword("springer midea 9000") == TIPO_MARCA_PROPRIA
        assert classify_keyword("gree g top") == TIPO_MARCA_CONCORRENTE
        assert classify_keyword("ar condicionado para escritório") == TIPO_GENERICA

    def test_carrier_e_do_grupo(self):
        assert classify_keyword("carrier inverter") == TIPO_MARCA_PROPRIA


class TestConferenciaComOConfig:
    """As categorias escritas no módulo não podem divergir do config."""

    def test_categorias_dirigidas_batem(self):
        from config import BRAND_DIRECTED_CATEGORIES
        from utils import keyword_taxonomy as kt
        assert set(BRAND_DIRECTED_CATEGORIES) == set(kt._CATEGORIAS_DIRIGIDAS)

    def test_toda_keyword_do_config_neutra_e_generica(self):
        from config import BRAND_NEUTRAL_CATEGORIES, KEYWORDS_LIST
        for k in KEYWORDS_LIST:
            if k.category in BRAND_NEUTRAL_CATEGORIES:
                assert classify_keyword(k.term, k.category) == TIPO_GENERICA, k.term


class TestOpcoesDoFiltro:
    def test_dealers_saem_da_lista_de_keywords(self):
        opcoes = keyword_options(_PRODUCAO.keys(), _PRODUCAO)
        assert "WebContinental" not in opcoes
        assert "PoloAr" not in opcoes
        assert "ar condicionado" in opcoes

    def test_atalhos_de_grupo_vem_primeiro(self):
        opcoes = keyword_options(_PRODUCAO.keys(), _PRODUCAO)
        assert opcoes[:3] == list(GROUP_TOKENS)

    def test_genericas_antes_das_de_marca(self):
        ordem = sort_keywords(_PRODUCAO.keys(), _PRODUCAO)
        assert ordem.index("ar condicionado 12000 btus") < ordem.index("ar condicionado midea")
        assert ordem.index("ar condicionado midea") < ordem.index("ar condicionado lg")
        assert ordem[-1] in {k for k, c in _PRODUCAO.items() if c == "Dealers"}

    def test_ordem_ignora_caixa(self):
        ordem = sort_keywords(["WebContinental", "ArCerto", "bemol"], {
            "WebContinental": "Dealers", "ArCerto": "Dealers", "bemol": "Dealers",
        })
        assert ordem == ["ArCerto", "bemol", "WebContinental"]

    def test_atalho_expande_para_todas_as_genericas(self):
        out = expand_keyword_selection(["▸ Todas as genéricas"], _PRODUCAO.keys(), _PRODUCAO)
        assert len(out) == 22
        assert "ar condicionado midea" not in out

    def test_atalho_mais_keyword_avulsa_sem_duplicar(self):
        out = expand_keyword_selection(
            ["▸ Todas de marca própria", "midea inverter", "ar condicionado lg"],
            _PRODUCAO.keys(), _PRODUCAO,
        )
        assert out.count("midea inverter") == 1
        assert "ar condicionado lg" in out
        assert len(out) == 6

    def test_selecao_vazia_e_sem_filtro(self):
        assert expand_keyword_selection([], _PRODUCAO.keys()) == []

    def test_rotulo_tem_icone_do_tipo(self):
        assert format_keyword_option("ar condicionado").startswith("🔎")
        assert format_keyword_option("midea inverter").startswith("🟦")
        assert format_keyword_option("▸ Todas as genéricas") == "▸ Todas as genéricas"

    def test_keywords_by_tipo(self):
        out = keywords_by_tipo(_PRODUCAO.keys(), [TIPO_MARCA_PROPRIA], _PRODUCAO)
        assert out == [
            "ar condicionado midea", "midea 12000 btus", "midea inverter",
            "midea airvolution", "midea ecomaster",
        ]
