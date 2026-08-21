"""
tests/test_bestsellers_referencia.py — Separação Mais Vendidos × Relevância e
os coletores genéricos de dealer (VTEX/HTML).

O que estes testes protegem:

  * a REFERÊNCIA de cada lista (mais_vendidos × relevancia) é uma dimensão de
    primeira classe, propagada até a linha da série, e nunca vaza vazia;
  * as duas referências não se confundem — uma lista de relevância que perde
    sua âncora de ordenação é reprovada com a mensagem certa, não com a de
    "mais vendidos";
  * cada dealer registrado tem um coletor, e a URL que o coletor monta carrega
    o parâmetro de ordenação que o portão de validação exige.

Rode: pytest tests/test_bestsellers_referencia.py
"""

import pandas as pd

from bestsellers import config as cfg
from bestsellers import metrics
from bestsellers.config import (
    COLETA,
    REFERENCIA_MAIS_VENDIDOS,
    REFERENCIA_RELEVANCIA,
    SOURCES,
    coleta_de,
    referencia_de,
)
from bestsellers.models import COLUNAS, BestSellerItem
from bestsellers.sources import SOURCE_CLASSES
from bestsellers.sources.html_generic import HtmlBestSellers, make_html_source
from bestsellers.sources.vtex_generic import VtexBestSellers, make_vtex_source
from bestsellers.storage import to_dataframe
from bestsellers.validate import NIVEL_QUARENTENA, validar


# ---------------------------------------------------------------------------
# Integridade do registro
# ---------------------------------------------------------------------------

class TestRegistro:
    def test_toda_fonte_tem_referencia_valida(self):
        for chave, spec in SOURCES.items():
            assert spec.referencia in cfg.REFERENCIAS, chave

    def test_referencia_default_e_mais_vendidos(self):
        # Fontes legadas (registradas sem `referencia`) seguem mais_vendidos.
        assert SOURCES["amazon"].referencia == REFERENCIA_MAIS_VENDIDOS
        assert SOURCES["mercadolivre"].referencia == REFERENCIA_MAIS_VENDIDOS

    def test_dealers_de_relevancia_marcados(self):
        esperado = {"dufrio", "centralar", "leveros", "ferreiracosta", "engage"}
        marcados = {
            k for k, s in SOURCES.items() if s.referencia == REFERENCIA_RELEVANCIA
        }
        assert marcados == esperado

    def test_relevancia_usa_mecanica_relevancia(self):
        for chave, spec in SOURCES.items():
            if spec.referencia == REFERENCIA_RELEVANCIA:
                assert spec.mecanica == cfg.MECANICA_RELEVANCIA, chave

    def test_toda_plataforma_tem_coletor(self):
        assert not [k for k in SOURCES if k not in SOURCE_CLASSES]

    def test_coleta_so_referencia_chaves_de_sources(self):
        assert set(COLETA) <= set(SOURCES)

    def test_referencia_de_desconhecida_cai_em_mais_vendidos(self):
        assert referencia_de("plataforma_inexistente") == REFERENCIA_MAIS_VENDIDOS


# ---------------------------------------------------------------------------
# Propagação até a linha da série
# ---------------------------------------------------------------------------

class TestPropagacao:
    def test_referencia_na_coluna_e_na_linha(self):
        assert "referencia" in COLUNAS
        item = BestSellerItem(rank=1, titulo="Ar Condicionado Split Inverter 12000")
        linha = item.to_row(
            data="2026-08-21", horario="09:30", run_id=None,
            spec=SOURCES["dufrio"], url_coleta="u", endpoint="e",
        )
        assert linha["referencia"] == REFERENCIA_RELEVANCIA

    def test_preparar_backfilla_referencia_ausente(self):
        # Série antiga, gravada antes da coluna existir.
        df = pd.DataFrame({
            "data": ["2026-08-10", "2026-08-10"],
            "plataforma": ["amazon", "dufrio"],
            "grupo_midea": [True, False],
            "rank": [1, 2],
            "tipo": ["SPLIT_HW", "SPLIT_HW"],
        })
        out = metrics.preparar(df)
        ref = dict(zip(out["plataforma"], out["referencia"]))
        assert ref["amazon"] == REFERENCIA_MAIS_VENDIDOS
        assert ref["dufrio"] == REFERENCIA_RELEVANCIA

    def test_preparar_repara_celula_em_branco(self):
        df = pd.DataFrame({
            "data": ["2026-08-10"],
            "plataforma": ["leveros"],
            "referencia": [""],           # célula vazia no CSV
            "grupo_midea": [False],
            "rank": [1],
            "tipo": ["SPLIT_HW"],
        })
        out = metrics.preparar(df)
        assert out["referencia"].iloc[0] == REFERENCIA_RELEVANCIA


# ---------------------------------------------------------------------------
# Validação referência-aware
# ---------------------------------------------------------------------------

def _coleta(plataforma, titulos, *, endpoint):
    spec = SOURCES[plataforma]
    linhas = [
        BestSellerItem(rank=i, titulo=t, preco=1999.0).to_row(
            data="2026-08-21", horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica, endpoint=endpoint,
        )
        for i, t in enumerate(titulos, start=1)
    ]
    return to_dataframe(linhas)


_SPLITS = [f"Ar Condicionado Split Inverter M{i} 12000 BTUs" for i in range(12)]


class TestValidacaoPorReferencia:
    def test_relevancia_com_ancora_passa(self):
        df = _coleta("dufrio", _SPLITS, endpoint=SOURCES["dufrio"].url_publica
                     + "?O=OrderByScoreDESC")
        assert not [o for o in validar(df) if o.nivel == NIVEL_QUARENTENA]

    def test_relevancia_sem_ancora_quarentena_com_mensagem_de_relevancia(self):
        df = _coleta("dufrio", _SPLITS,
                     endpoint="https://www.dufrio.com.br/api/catalog_system/"
                              "pub/products/search/ar-condicionado?O=OrderByPriceASC")
        quarentena = [o for o in validar(df) if o.nivel == NIVEL_QUARENTENA]
        assert len(quarentena) == 1
        assert "relev" in quarentena[0].mensagem.lower()

    def test_mais_vendidos_sem_sort_quarentena_com_mensagem_de_vendas(self):
        df = _coleta("webcontinental", _SPLITS,
                     endpoint="https://www.webcontinental.com.br/api/catalog_"
                              "system/pub/products/search/climatizacao?O=OrderByPriceASC")
        quarentena = [o for o in validar(df) if o.nivel == NIVEL_QUARENTENA]
        assert len(quarentena) == 1
        assert "não é de mais vendidos" in quarentena[0].mensagem.lower()


# ---------------------------------------------------------------------------
# Coletores genéricos
# ---------------------------------------------------------------------------

class TestColetorVtex:
    def test_fabrica_amarra_a_chave(self):
        cls = make_vtex_source("belmicro")
        assert cls.key == "belmicro"
        assert issubclass(cls, VtexBestSellers)

    def test_url_carrega_ordem_e_categoria(self):
        fonte = SOURCE_CLASSES["webcontinental"](headless=True)
        fonte._coleta = coleta_de("webcontinental")
        url = fonte._url(0, 23)
        assert "OrderByTopSaleDESC" in url
        assert "climatizacao/ar-condicionado/ar-condicionado-split-hi-wall" in url
        assert "_from=0" in url and "_to=23" in url
        # A URL montada satisfaz o próprio portão de ordenação.
        assert SOURCES["webcontinental"].ordenacao_comprovada(url)

    def test_url_relevancia_usa_score(self):
        fonte = SOURCE_CLASSES["dufrio"](headless=True)
        fonte._coleta = coleta_de("dufrio")
        url = fonte._url(0, 23)
        assert "OrderByScoreDESC" in url
        assert SOURCES["dufrio"].ordenacao_comprovada(url)

    def test_oferta_pega_seller_default_nao_menor_preco(self):
        produto = {
            "productName": "Ar Condicionado Split Inverter 12000",
            "productId": "42",
            "items": [{
                "sellers": [
                    {"sellerName": "3P Barato", "sellerDefault": False,
                     "commertialOffer": {"Price": 1000.0, "IsAvailable": True}},
                    {"sellerName": "Loja", "sellerDefault": True,
                     "commertialOffer": {"Price": 1999.0, "ListPrice": 2500.0}},
                ],
            }],
        }
        preco, preco_de, seller = VtexBestSellers._oferta(produto)
        assert preco == 1999.0
        assert preco_de == 2500.0
        assert seller == "Loja"

    def test_parse_numera_rank_por_ordem(self):
        fonte = SOURCE_CLASSES["belmicro"](headless=True)
        produtos = [
            {"productName": "Split A", "productId": "1", "items": []},
            {"productName": "Split B", "productId": "2", "items": []},
        ]
        itens = fonte._parse(produtos, offset=0)
        assert [i.rank for i in itens] == [1, 2]
        assert [i.titulo for i in itens] == ["Split A", "Split B"]


class TestColetorHtml:
    def test_fabrica_amarra_a_chave(self):
        cls = make_html_source("centralar")
        assert cls.key == "centralar"
        assert issubclass(cls, HtmlBestSellers)

    def test_jsonld_itemlist_vira_ranking(self):
        fonte = SOURCE_CLASSES["ferreiracosta"](headless=True)
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
          {"@type":"ListItem","position":2,"item":{"@type":"Product",
            "name":"Split Inverter Midea 12000","sku":"B",
            "offers":{"@type":"Offer","price":"2199.00"}}},
          {"@type":"ListItem","position":1,"item":{"@type":"Product",
            "name":"Split Inverter LG 9000","sku":"A",
            "offers":{"@type":"Offer","price":2499.0}}}
        ]}
        </script></head><body></body></html>
        """
        itens = fonte._parse_jsonld(html)
        # Ordenado por position: A (#1) antes de B (#2).
        assert [i.titulo for i in itens] == [
            "Split Inverter LG 9000", "Split Inverter Midea 12000",
        ]
        assert [i.rank for i in itens] == [1, 2]
        assert itens[0].preco == 2499.0
        assert itens[1].preco == 2199.0
        assert itens[0].sku_plataforma == "A"

    def test_sem_jsonld_devolve_vazio(self):
        fonte = SOURCE_CLASSES["engage"](headless=True)
        assert fonte._parse_jsonld("<html><body>nada</body></html>") == []
