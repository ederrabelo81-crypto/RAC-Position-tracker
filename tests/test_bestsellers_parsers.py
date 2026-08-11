"""
tests/test_bestsellers_parsers.py — Parsers das listas de mais vendidos.

Cada varejista reescreve o markup sem avisar (a Amazon gera classes com hash
a cada deploy; a Shopee troca o invólucro do item na API). Estes testes travam
o contrato de cada parser contra HTML/payload representativo, para que a
próxima mudança apareça como teste vermelho em vez de "0 itens coletados" —
que é indistinguível de bloqueio no log.

Nenhum teste toca a rede.

Rode: pytest tests/test_bestsellers_parsers.py
"""

import pytest

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from bestsellers.sources.amazon import AmazonBestSellers
from bestsellers.sources.mercado_livre import MercadoLivreBestSellers
from bestsellers.sources.shopee import ShopeeBestSellers


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------

def _card_amazon(posicao: int, titulo: str, preco: str, asin: str) -> str:
    return f"""
    <div id="gridItemRoot" data-asin="{asin}">
      <span class="zg-bdg-text">#{posicao}</span>
      <a class="a-link-normal" href="/dp/{asin}/ref=zg_bs">
        <span><div class="_cDEzb_p13n-sc-css-line-clamp-3_g3dy1">{titulo}</div></span>
      </a>
      <span class="a-icon-alt">4,6 de 5 estrelas</span>
      <a href="#customerReviews"><span class="a-size-small">1.226</span></a>
      <span class="_cDEzb_p13n-sc-price_3mJ9Z">{preco}</span>
    </div>
    """


def _grid_amazon() -> str:
    itens = [
        (1, "Ar Condicionado Split Inverter LG Dual 12000 BTUs Frio", "R$ 2.199,00", "B0AAA00001"),
        (2, "Ar Condicionado Split Inverter Midea Ecomaster 9000 BTUs", "R$ 1.799,00", "B0AAA00002"),
        (3, "Ar Condicionado Split Springer Midea 12000 BTUs", "R$ 1.999,00", "B0AAA00003"),
        (4, "Umidificador de Ar Ultrassônico 3L", "R$ 199,00", "B0AAA00004"),
        (5, "Ar Condicionado Portátil Elgin 12000 BTUs", "R$ 2.499,00", "B0AAA00005"),
        (6, "Ar Condicionado Split Samsung WindFree 9000 BTUs", "R$ 2.899,00", "B0AAA00006"),
    ]
    return "<html><body>" + "".join(_card_amazon(*i) for i in itens) + "</body></html>"


class TestAmazonParser:
    @pytest.fixture
    def fonte(self):
        return AmazonBestSellers()

    def test_extrai_a_lista_inteira(self, fonte):
        itens = fonte._parse(_grid_amazon(), offset=0)
        assert len(itens) == 6

    def test_usa_a_posicao_impressa_no_card(self, fonte):
        """A Amazon estampa '#7'; inventar a posição pela ordem do DOM
        esconderia um item descartado no meio da lista."""
        itens = fonte._parse(_grid_amazon(), offset=0)
        assert [i.rank for i in itens] == [1, 2, 3, 4, 5, 6]

    def test_campos_do_card(self, fonte):
        item = fonte._parse(_grid_amazon(), offset=0)[1]
        assert item.titulo.startswith("Ar Condicionado Split Inverter Midea")
        assert item.preco == 1799.0
        assert item.rating == 4.6
        assert item.reviews == 1226
        assert item.sku_plataforma == "B0AAA00002"
        assert item.url_produto.startswith("https://www.amazon.com.br/dp/")

    def test_classificacao_do_grupo(self, fonte):
        itens = fonte._parse(_grid_amazon(), offset=0)
        assert [i.grupo_midea for i in itens] == [False, True, True, False, False, False]

    def test_lista_de_mais_vendidos_e_organica(self, fonte):
        """A Amazon não vende posição nesta lista: `False`, não `None`."""
        assert all(i.patrocinado is False for i in fonte._parse(_grid_amazon(), offset=0))

    def test_titulo_pelo_alt_da_imagem(self, fonte):
        """Quando a classe com hash do título muda, o alt da imagem segura."""
        html = """
        <div id="gridItemRoot" data-asin="B0XXX">
          <span class="zg-bdg-text">#1</span>
          <img alt="Ar Condicionado Split Inverter Midea 12000 BTUs Frio 220V">
          <span class="a-color-price">R$ 1.899,00</span>
        </div>
        """ * 6
        itens = fonte._parse(html, offset=0)
        assert itens and itens[0].titulo.startswith("Ar Condicionado Split")

    def test_grid_irreconhecivel_devolve_vazio(self, fonte):
        """Poucos matches é banner, não grid — melhor vazio (com dump) do que
        um ranking de 2 itens tratado como lista completa."""
        assert fonte._parse("<html><div data-asin='X'>oi</div></html>", offset=0) == []

    def test_paginacao_da_url(self, fonte):
        assert fonte._url_pagina(1).endswith("17125373011/")
        assert fonte._url_pagina(2).endswith("?pg=2")


# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------

def _card_ml(titulo: str, inteiro: str, centavos: str, seller: str, vendidos: str,
             mlb: str) -> str:
    return f"""
    <div class="poly-card">
      <a class="poly-component__title" href="https://produto.mercadolivre.com.br/{mlb}-ar">{titulo}</a>
      <div class="poly-component__price">
        <span class="andes-money-amount">
          <span class="andes-money-amount__fraction">{inteiro}</span>
          <span class="andes-money-amount__cents">{centavos}</span>
        </span>
      </div>
      <span class="poly-component__seller">Por {seller}</span>
      <span class="poly-component__sold-quantity">{vendidos}</span>
    </div>
    """


def _pagina_ml() -> str:
    cards = [
        _card_ml("Ar Condicionado Split Inverter Midea 12000 BTUs Frio", "1.899", "90",
                 "Webcontinental", "+5mil vendidos", "MLB1111111111"),
        _card_ml("Ar Condicionado Split LG Dual Inverter 9000 BTUs", "2.199", "00",
                 "Centralar.com", "+1mil vendidos", "MLB2222222222"),
        _card_ml("Ar Condicionado Split Samsung WindFree 12000 BTUs", "2.799", "90",
                 "Dufrio", "500 vendidos", "MLB3333333333"),
        _card_ml("Ar Condicionado Split Gree Eco Garden 9000 BTUs", "1.699", "00",
                 "Comprebel", "250 vendidos", "MLB4444444444"),
        _card_ml("Ar Condicionado Split Springer Midea 18000 BTUs", "2.999", "00",
                 "ClimaRio", "120 vendidos", "MLB5555555555"),
    ]
    return "<html><body>" + "".join(cards) + "</body></html>"


class TestMercadoLivreParser:
    @pytest.fixture
    def fonte(self):
        return MercadoLivreBestSellers()

    def test_extrai_a_lista(self, fonte):
        assert len(fonte._parse(_pagina_ml())) == 5

    def test_rank_pela_ordem_da_pagina(self, fonte):
        assert [i.rank for i in fonte._parse(_pagina_ml())] == [1, 2, 3, 4, 5]

    def test_preco_junta_fracao_e_centavos(self, fonte):
        assert fonte._parse(_pagina_ml())[0].preco == 1899.90

    def test_seller_sem_o_prefixo_por(self, fonte):
        """O seller é a leitura de Buy Box desta lista — 'Por Fulano' é ruído."""
        assert fonte._parse(_pagina_ml())[0].seller == "Webcontinental"

    def test_id_mlb_e_a_chave_de_pareamento(self, fonte):
        assert fonte._parse(_pagina_ml())[0].sku_plataforma == "MLB1111111111"

    def test_vendidos_e_acumulado_vitalicio(self, fonte):
        """'+5mil vendidos' é acumulado do anúncio, não velocidade — marcar
        como mensal permitiria somá-lo com a Shopee."""
        item = fonte._parse(_pagina_ml())[0]
        assert item.vendidos == 5000.0
        assert item.base_vendidos == "acumulado"

    def test_vendidos_sem_multiplicador(self, fonte):
        assert fonte._parse(_pagina_ml())[2].vendidos == 500.0

    def test_pagina_de_gate_nao_vira_lista_curta(self, fonte):
        """Sem cards, o parser devolve vazio — quem distingue gate de layout
        novo é `_is_login_gate`, no coletor."""
        assert fonte._parse("<html><body><h1>Acesse sua conta</h1></body></html>") == []


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------

def _item_shopee(itemid: int, nome: str, preco: int, vendidos: str, loja: str) -> dict:
    return {
        "item_basic": {
            "itemid": itemid,
            "shopid": 900,
            "name": nome,
            "price": preco,
            "shop_name": loja,
            "item_rating": {"rating_star": 4.8, "rating_count": [321, 1, 2, 3, 4]},
            "item_card_display_sold_count": {"historical_sold_count_text": vendidos},
        }
    }


class TestShopeeParser:
    @pytest.fixture
    def fonte(self):
        fonte = ShopeeBestSellers()
        from scrapers.shopee import ShopeeScraper

        fonte._shopee = ShopeeScraper()
        return fonte

    def test_extrai_itens_da_api(self, fonte):
        brutos = [
            _item_shopee(1, "Ar Condicionado Split Inverter Midea 12000 BTUs",
                         179900000, "948 Vendido/Mês", "Midea Oficial"),
            _item_shopee(2, "Ar Condicionado Split Gree 9000 BTUs",
                         159900000, "312 Vendido/Mês", "Gree Store"),
        ]
        itens = fonte._parse(brutos, offset=0)
        assert len(itens) == 2
        assert itens[0].preco == 1799.0
        assert itens[0].seller == "Midea Oficial"
        assert itens[0].sku_plataforma == "900_1"
        assert itens[0].rating == 4.8
        assert itens[0].reviews == 321

    def test_offset_continua_a_numeracao_entre_paginas(self, fonte):
        brutos = [_item_shopee(9, "Ar Condicionado Split Midea 9000", 1_500_00000, "10 vendidos", "Loja")]
        assert fonte._parse(brutos, offset=30)[0].rank == 31

    def test_unidades_por_mes(self, fonte):
        """A Shopee é a única fonte de velocidade real do conjunto."""
        assert ShopeeBestSellers._extrair_vendidos("948 Vendido/Mês") == (948.0, "mes")

    def test_vendidos_sem_qualificacao_e_acumulado(self, fonte):
        """Somar '948 vendidos' como mensal misturaria as duas unidades."""
        assert ShopeeBestSellers._extrair_vendidos("948 vendidos") == (948.0, "acumulado")

    def test_vendidos_com_multiplicador(self, fonte):
        assert ShopeeBestSellers._extrair_vendidos("1,2mil Vendido/Mês") == (1200.0, "mes")

    def test_sem_texto_de_vendas(self, fonte):
        assert ShopeeBestSellers._extrair_vendidos(None) == (None, None)

    def test_item_sem_nome_e_descartado(self, fonte):
        brutos = [{"item_basic": {"itemid": 5, "shopid": 9, "price": 100000000}}]
        assert fonte._parse(brutos, offset=0) == []


# ---------------------------------------------------------------------------
# Normalização comum a todas as fontes
# ---------------------------------------------------------------------------

class _FonteFalsa(BestSellerSource):
    """Fonte de teste: devolve exatamente os itens que recebeu."""

    key = "amazon"

    def __init__(self, itens, ranks_explicitos=False):
        super().__init__()
        self._itens = itens
        self.ranks_explicitos = ranks_explicitos

    def _coletar(self, paginas):
        self.endpoint = "https://www.amazon.com.br/gp/bestsellers/home/17125373011/"
        return self._itens


class TestNormalizacao:
    def test_dedup_mantem_a_melhor_posicao(self):
        """Carrossel repete o mesmo anúncio; contar duas vezes infla o KPI."""
        itens = [
            BestSellerItem(rank=1, titulo="Split Midea 12000", sku_plataforma="A1"),
            BestSellerItem(rank=2, titulo="Split LG 9000", sku_plataforma="A2"),
            BestSellerItem(rank=7, titulo="Split Midea 12000", sku_plataforma="A1"),
        ]
        coletados = _FonteFalsa(itens).coletar()
        assert len(coletados) == 2
        assert coletados[0].sku_plataforma == "A1"
        assert coletados[0].rank == 1

    def test_dedup_guarda_o_registro_da_melhor_posicao(self):
        """
        Quando a ocorrência PIOR aparece primeiro (card patrocinado no topo da
        página, orgânico mais abaixo), copiar só o `rank` para o primeiro
        registro atribuiria à melhor posição o preço e as flags do card pior —
        a leitura do topo do ranking sairia do card errado.
        """
        itens = [
            BestSellerItem(rank=7, titulo="Split Midea 12000", sku_plataforma="A1",
                           preco=2400.0, patrocinado=True),
            BestSellerItem(rank=2, titulo="Split LG 9000", sku_plataforma="A2",
                           preco=2100.0),
            BestSellerItem(rank=1, titulo="Split Midea 12000", sku_plataforma="A1",
                           preco=1899.0, patrocinado=False),
        ]
        coletados = _FonteFalsa(itens).coletar()
        assert len(coletados) == 2
        # Posição da PRIMEIRA aparição, dados da MELHOR posição.
        assert coletados[0].sku_plataforma == "A1"
        assert coletados[0].rank == 1
        assert coletados[0].preco == 1899.0
        assert coletados[0].patrocinado is False

    def test_dedup_cai_no_titulo_sem_sku(self):
        itens = [
            BestSellerItem(rank=1, titulo="Split Midea 12000"),
            BestSellerItem(rank=4, titulo="split midea 12000"),
        ]
        assert len(_FonteFalsa(itens).coletar()) == 1

    def test_posicoes_duplicadas_sao_reescritas(self):
        itens = [
            BestSellerItem(rank=1, titulo="Split A", sku_plataforma="A"),
            BestSellerItem(rank=1, titulo="Split B", sku_plataforma="B"),
            BestSellerItem(rank=2, titulo="Split C", sku_plataforma="C"),
        ]
        assert [i.rank for i in _FonteFalsa(itens).coletar()] == [1, 2, 3]

    def test_ranks_explicitos_sao_preservados(self):
        """Item descartado por falta de título deixa um buraco REAL em 1..N —
        reescrever a numeração inventaria uma posição que a página não tinha."""
        itens = [
            BestSellerItem(rank=1, titulo="Split A", sku_plataforma="A"),
            BestSellerItem(rank=3, titulo="Split C", sku_plataforma="C"),
        ]
        coletados = _FonteFalsa(itens, ranks_explicitos=True).coletar()
        assert [i.rank for i in coletados] == [1, 3]

    def test_falha_do_coletor_nao_derruba_a_coleta(self):
        class _Quebrada(_FonteFalsa):
            def _coletar(self, paginas):
                raise RuntimeError("Akamai bloqueou")

        fonte = _Quebrada([])
        assert fonte.coletar() == []
        assert "Akamai bloqueou" in fonte.falha

    def test_lista_vazia_registra_o_motivo(self):
        fonte = _FonteFalsa([])
        assert fonte.coletar() == []
        assert fonte.falha and "vazia" in fonte.falha


class TestEndpointEstavelEntrePaginas:
    """
    Todas as linhas da coleta recebem o mesmo `endpoint`. Se cada página o
    sobrescrevesse, a URL da página 2 (`?pg=2`) seria carimbada também nos
    registros da página 1 — e `comparar_endpoints` acusaria "endpoint mudou"
    sempre que o nº de páginas variasse, derrubando a plataforma dos deltas
    de preço e estabilidade sem nada ter mudado.
    """

    def test_primeira_pagina_vence(self):
        fonte = _FonteFalsa([])
        fonte.registrar_endpoint("https://exemplo/gp/bestsellers/")
        fonte.registrar_endpoint("https://exemplo/gp/bestsellers/?pg=2")
        assert fonte.endpoint == "https://exemplo/gp/bestsellers/"

    def test_url_vazia_nao_fixa_o_endpoint(self):
        fonte = _FonteFalsa([])
        fonte.registrar_endpoint("")
        fonte.registrar_endpoint("https://exemplo/gp/bestsellers/")
        assert fonte.endpoint == "https://exemplo/gp/bestsellers/"


class TestLiberacaoDeRecursos:
    def test_falha_na_abertura_libera_o_que_ja_subiu(self):
        """`__exit__` não roda quando `__enter__` levanta: sem liberar aqui, o
        Chromium ficaria vivo pelo resto da coleta das outras plataformas."""
        class _AberturaQuebrada(_FonteFalsa):
            def __init__(self):
                super().__init__([])
                self.liberou = False

            def _abrir(self):
                raise RuntimeError("new_context estourou")

            def _fechar(self):
                self.liberou = True

        fonte = _AberturaQuebrada()
        with pytest.raises(RuntimeError, match="new_context"):
            with fonte:
                pass
        assert fonte.liberou is True

    def test_erro_no_fechamento_nao_propaga(self):
        class _FechamentoQuebrado(_FonteFalsa):
            def _fechar(self):
                raise RuntimeError("browser já morreu")

        with _FechamentoQuebrado([]):
            pass  # não deve levantar


# ---------------------------------------------------------------------------
# Casas Bahia (VTEX Intelligent Search)
# ---------------------------------------------------------------------------

def _produto_vtex(pid: str, nome: str, preco: float, seller: str, seller_id: str,
                  disponivel: bool = True) -> dict:
    return {
        "productId": pid,
        "productName": nome,
        "linkText": nome.lower().replace(" ", "-"),
        "rating": {"average": 4.4, "totalCount": 87},
        "items": [{
            "sellers": [{
                "sellerId": seller_id,
                "sellerName": seller,
                "sellerDefault": True,
                "commertialOffer": {"Price": preco, "IsAvailable": disponivel},
            }],
        }],
    }


class TestCasasBahiaParser:
    @pytest.fixture
    def fonte(self):
        from bestsellers.sources.casas_bahia import CasasBahiaBestSellers
        from scrapers.casas_bahia import CasasBahiaScraper

        fonte = CasasBahiaBestSellers()
        fonte._cb = CasasBahiaScraper()
        return fonte

    def test_extrai_produtos_e_ranks(self, fonte):
        produtos = [
            _produto_vtex("1", "Ar Condicionado Split Midea 12000 BTUs", 1899.0, "Casas Bahia", "1"),
            _produto_vtex("2", "Ar Condicionado Split LG 9000 BTUs", 2199.0, "Loja Parceira", "77"),
        ]
        itens = fonte._parse(produtos, offset=0)
        assert [i.rank for i in itens] == [1, 2]
        assert itens[0].preco == 1899.0
        assert itens[0].sku_plataforma == "1"
        assert itens[0].rating == 4.4
        assert itens[0].reviews == 87

    def test_buy_box_seller_do_array_vtex(self, fonte):
        """`sellers[]` é o motivo de usar a API em vez do DOM: só ele expõe
        quem vence a buy box."""
        itens = fonte._parse(
            [_produto_vtex("1", "Ar Condicionado Split Gree 12000", 1799.0, "Loja Parceira", "77")],
            offset=0,
        )
        assert itens[0].seller == "Loja Parceira"

    def test_offset_continua_a_numeracao(self, fonte):
        produtos = [_produto_vtex("9", "Ar Condicionado Split Midea 9000", 1699.0, "Casas Bahia", "1")]
        assert fonte._parse(produtos, offset=24)[0].rank == 25

    def test_contaminacao_entra_na_base_marcada(self, fonte):
        """6 de 20 itens não-RAC em 10/08/2026 — o portão precisa vê-los."""
        produtos = [
            _produto_vtex("1", "Umidificador de Ar Ultrassônico 3L", 199.0, "Casas Bahia", "1"),
            _produto_vtex("2", "Ar Condicionado Split Midea 12000 BTUs", 1899.0, "Casas Bahia", "1"),
        ]
        itens = fonte._parse(produtos, offset=0)
        assert [i.no_escopo for i in itens] == [False, True]

    def test_produto_sem_nome_e_descartado(self, fonte):
        assert fonte._parse([{"productId": "5", "items": []}], offset=0) == []

    def test_url_absoluta(self, fonte):
        itens = fonte._parse(
            [_produto_vtex("1", "Ar Condicionado Split Midea 12000", 1899.0, "Casas Bahia", "1")],
            offset=0,
        )
        assert itens[0].url_produto.startswith("https://www.casasbahia.com.br/")

    def test_resposta_html_vira_erro_de_bloqueio(self, fonte):
        """HTML no lugar de JSON é desafio do Akamai — não lista curta."""
        class _Resposta:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}

            def json(self):
                return {}

        class _Sessao:
            def get(self, *_a, **_k):
                return _Resposta()

        with pytest.raises(RuntimeError, match="Akamai"):
            fonte._consultar(_Sessao(), {"query": "ar condicionado"}, 1)


# ---------------------------------------------------------------------------
# Leroy Merlin (Algolia)
# ---------------------------------------------------------------------------

class TestLeroyMerlinParser:
    @pytest.fixture
    def fonte(self):
        from bestsellers.sources.leroy_merlin import LeroyMerlinBestSellers
        from scrapers.leroy_merlin import LeroyMerlinScraper

        fonte = LeroyMerlinBestSellers()
        fonte._leroy = LeroyMerlinScraper()
        return fonte

    def _hit(self, oid, nome, preco, **extra):
        base = {
            "objectID": oid,
            "name": nome,
            "averagePromotionalPrice": preco,
            "linkText": nome.lower().replace(" ", "-"),
        }
        base.update(extra)
        return base

    def test_extrai_hits(self, fonte):
        hits = [
            self._hit("A1", "Ar Condicionado Split Inverter Midea 12000 BTUs", 1899.0),
            self._hit("A2", "Ar Condicionado Split LG Dual 9000 BTUs", 2199.0),
        ]
        itens = fonte._parse(hits, offset=0)
        assert [i.rank for i in itens] == [1, 2]
        assert itens[0].preco == 1899.0
        assert itens[0].sku_plataforma == "A1"

    def test_produto_1p_sem_marketplace_sellers(self, fonte):
        item = fonte._parse([self._hit("A1", "Ar Condicionado Split Midea 12000", 1899.0)], offset=0)[0]
        assert item.seller == "Leroy Merlin"

    def test_seller_3p_pendente_e_resolvido_via_pdp(self, fonte, monkeypatch):
        """O índice devolve o lojista 3P como ObjectId opaco. Sem a passada de
        resolução, todo parceiro novo entraria com seller vazio e o cache
        nunca aprenderia o nome."""
        chamadas = {}

        def _resolver(pendentes):
            chamadas.update(pendentes)
            return {"5f3a": "Parceiro Clima"}

        monkeypatch.setattr(fonte._leroy, "_resolve_pending_sellers", _resolver)
        hits = [self._hit(
            "A1", "Ar Condicionado Split Gree 12000 BTUs", 1799.0,
            marketplaceSellers=["5f3a"],
        )]
        item = fonte._parse(hits, offset=0)[0]
        assert item.seller == "Parceiro Clima"
        assert "5f3a" in chamadas          # 1 PDP por seller novo, não por produto

    def test_hit_sem_titulo_e_descartado(self, fonte):
        assert fonte._parse([{"objectID": "A9"}], offset=0) == []


class TestShopeeCamposSensiveis:
    @pytest.fixture
    def fonte(self):
        fonte = ShopeeBestSellers()
        from scrapers.shopee import ShopeeScraper

        fonte._shopee = ShopeeScraper()
        return fonte

    def test_unidades_por_mes_sobrevivem_ao_campo_numerico(self, fonte):
        """
        O payload de produção traz o numérico `historical_sold_count` E o
        texto com "/Mês". `ShopeeScraper._extract_sold` prioriza o numérico e
        devolve "948 vendidos" — sem o qualificador, a Shopee inteira entraria
        como 'acumulado' e o motor de métricas descartaria a ÚNICA medida de
        velocidade real do conjunto.
        """
        payload = {
            "itemid": 1, "shopid": 9, "name": "Ar Condicionado Split Midea 12000",
            "price": 179900000, "shop_name": "Loja Fria",
            "item_card_display_sold_count": {
                "historical_sold_count": 948,
                "historical_sold_count_text": "948 Vendido/Mês",
            },
        }
        item = fonte._parse([{"item_basic": payload}], offset=0)[0]
        assert item.vendidos == 948.0
        assert item.base_vendidos == "mes"

    def test_somente_numerico_e_acumulado(self, fonte):
        """`historical_sold_count` sem texto é acumulado por definição —
        marcar como mensal misturaria unidades na soma de velocidade."""
        payload = {
            "itemid": 2, "shopid": 9, "name": "Ar Condicionado Split LG 9000",
            "price": 219900000, "shop_name": "Loja Quente",
            "item_card_display_sold_count": {"historical_sold_count": 312},
        }
        item = fonte._parse([{"item_basic": payload}], offset=0)[0]
        assert item.vendidos == 312.0
        assert item.base_vendidos == "acumulado"

    def test_cidade_nao_vira_seller(self, fonte):
        """`shop_location` é a cidade de origem do envio. Gravá-la no campo de
        seller corromperia a leitura de quem vende."""
        payload = {
            "itemid": 3, "shopid": 9, "name": "Ar Condicionado Split Gree 12000",
            "price": 159900000, "shop_location": "São Paulo",
        }
        item = fonte._parse([{"item_basic": payload}], offset=0)[0]
        assert item.seller is None

    def test_seller_aninhado_em_shop_data(self, fonte):
        payload = {
            "itemid": 4, "shopid": 9, "name": "Ar Condicionado Split Midea 9000",
            "price": 149900000, "shop_location": "Curitiba",
            "shop_data": {"shop_name": "Midea Oficial"},
        }
        item = fonte._parse([{"item_basic": payload}], offset=0)[0]
        assert item.seller == "Midea Oficial"

    def test_modo_browser_local_nao_usa_a_sessao_http(self, fonte):
        """Com RAC_LOCAL_CHROME o scraper não cria `_session`: chamar
        `_fetch_page` ali estoura em `None.get` e a Shopee sai como ausente."""
        fonte._shopee._local_active = True
        fonte._shopee._session = None

        def _explode(*_a, **_k):
            raise AssertionError("_fetch_page não deve ser chamado no modo browser")

        fonte._shopee._fetch_page = _explode
        chamou = {}

        def _via_browser(paginas):
            chamou["paginas"] = paginas
            return []

        fonte._coletar_via_browser = _via_browser
        assert fonte._coletar(2) == []
        assert chamou["paginas"] == 2


class TestShopeeMudancaDeEstrutura:
    """
    O caminho de browser local é a rota PRINCIPAL de produção. Uma troca de
    invólucro da API precisa falhar alto ali também — tratar "itens presentes,
    nada parseou" como "acabou a lista" gravaria uma série curta em silêncio.
    """

    @pytest.fixture
    def fonte(self):
        from scrapers.shopee import ShopeeScraper

        fonte = ShopeeBestSellers()
        fonte._shopee = ShopeeScraper()
        fonte._shopee._local_active = True
        fonte._shopee._page = object()
        fonte._shopee._local_browser = None
        return fonte

    def _preparar(self, fonte, capturadas, monkeypatch):
        shopee = fonte._shopee
        monkeypatch.setattr(shopee, "_await_captured", lambda timeout_s: capturadas)
        monkeypatch.setattr(shopee, "_dump_debug_response", lambda *a, **k: None)

        class _Pagina:
            def goto(self, *_a, **_k):
                return None

        shopee._page = _Pagina()

    def test_itens_sem_parse_levantam_erro(self, fonte, monkeypatch):
        # `items` presente, mas o invólucro não é reconhecido por nenhum parser.
        capturadas = [{"items": [{"formato_novo": {"id": 1}}]}]
        self._preparar(fonte, capturadas, monkeypatch)
        with pytest.raises(RuntimeError, match="nenhum parseou"):
            fonte._coletar_via_browser(1)

    def test_lista_sem_itens_apenas_termina(self, fonte, monkeypatch):
        """Nenhuma resposta com item = a lista acabou; não é regressão."""
        self._preparar(fonte, [{"items": []}], monkeypatch)
        assert fonte._coletar_via_browser(1) == []

    def test_url_do_browser_carrega_a_ordenacao(self, fonte, monkeypatch):
        """Sem `sortBy=sales` a página serve relevância — outro universo."""
        urls = []

        class _Pagina:
            def goto(self, url, **_k):
                urls.append(url)

        fonte._shopee._page = _Pagina()
        monkeypatch.setattr(fonte._shopee, "_await_captured", lambda timeout_s: [{"items": []}])
        fonte._coletar_via_browser(1)
        assert "sortBy=sales" in urls[0]


class TestLeroyOrcamentoDePdp:
    @pytest.fixture
    def fonte(self):
        from bestsellers.sources.leroy_merlin import LeroyMerlinBestSellers
        from scrapers.leroy_merlin import LeroyMerlinScraper

        fonte = LeroyMerlinBestSellers()
        fonte._leroy = LeroyMerlinScraper()
        return fonte

    def test_hit_sem_titulo_nao_gasta_pdp(self, fonte, monkeypatch):
        """
        O orçamento de PDP é por execução. Gastá-lo com um hit que vai ser
        descartado por falta de título deixa produtos válidos mais adiante
        sem seller.
        """
        pedidos = {}

        def _resolver(pendentes):
            pedidos.update(pendentes)
            return {}

        monkeypatch.setattr(fonte._leroy, "_resolve_pending_sellers", _resolver)
        hits = [
            {"objectID": "SEM_TITULO", "marketplaceSellers": ["aaa"], "linkText": "x"},
            {
                "objectID": "COM_TITULO",
                "name": "Ar Condicionado Split Midea 12000 BTUs",
                "marketplaceSellers": ["bbb"],
                "linkText": "y",
            },
        ]
        itens = fonte._parse(hits, offset=0)
        assert len(itens) == 1
        assert "aaa" not in pedidos      # não gastou PDP com o descartado
        assert "bbb" in pedidos
