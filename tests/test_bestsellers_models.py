"""
tests/test_bestsellers_models.py — Taxonomia da coleta de mais vendidos.

Por que estes testes existem: a classificação (marca, grupo, tipo) é o que
torna "% do top 10 da Midea" a MESMA medida em seis plataformas. Se a Amazon
classificar "Ecomaster" como marca desconhecida e o Magalu como Midea, o KPI
para de fechar e ninguém percebe — o número continua saindo.

Rode: pytest tests/test_bestsellers_models.py
"""

import pytest

from bestsellers.models import (
    TIPO_FORA_ESCOPO,
    TIPO_JANELA,
    TIPO_PORTATIL,
    TIPO_SPLIT_HW,
    BestSellerItem,
    classificar_marca,
    classificar_tipo,
    eh_grupo_midea,
)


class TestClassificarTipo:
    def test_split_explicito(self):
        assert classificar_tipo(
            "Ar Condicionado Split Inverter Midea 12000 BTUs Frio"
        ) == TIPO_SPLIT_HW

    def test_portatil(self):
        assert classificar_tipo(
            "Ar Condicionado Portátil Elgin 12000 BTUs"
        ) == TIPO_PORTATIL

    def test_janela(self):
        assert classificar_tipo(
            "Ar Condicionado de Janela Consul 10000 BTUs"
        ) == TIPO_JANELA

    def test_acessorio_com_a_palavra_split_nao_e_split(self):
        """'Suporte para Ar Condicionado Split' contém SPLIT e viraria um
        split fantasma no topo do ranking."""
        assert classificar_tipo(
            "Suporte para Ar Condicionado Split até 12000 BTUs"
        ) == TIPO_FORA_ESCOPO

    @pytest.mark.parametrize("titulo", [
        "Umidificador de Ar Ultrassônico 3L",
        "Depurador de Ar Inox 60cm",
        "Ventilador de Coluna 40cm Turbo",
        "Climatizador de Ar Portátil 5L",
    ])
    def test_contaminacao_da_categoria(self, titulo):
        """A Casas Bahia trouxe 6 destes em 20 posições — inclusive #3, #4, #5."""
        assert classificar_tipo(titulo) == TIPO_FORA_ESCOPO

    def test_sem_formato_mas_com_btu_assume_split(self):
        assert classificar_tipo("Ar Condicionado 12000 BTUs Frio 220V") == TIPO_SPLIT_HW

    def test_sem_titulo(self):
        assert classificar_tipo(None) == "IND"


class TestClassificarMarca:
    def test_marca_no_titulo(self):
        assert classificar_marca("Ar Condicionado Split Midea 12000") == "Midea"

    def test_linha_comercial_sem_a_marca(self):
        """Anúncio que só cita a linha somem do KPI se o fallback não existir."""
        assert classificar_marca("Ar Condicionado Ecomaster 12000 BTUs") == "Midea"
        assert classificar_marca("Split Airvolution 9000 BTUs Inverter") == "Midea"

    def test_concorrente(self):
        assert classificar_marca("Ar Condicionado LG Dual Inverter 12000") == "LG"

    def test_desconhecida(self):
        assert classificar_marca("Ar Condicionado 12000 BTUs Genérico") == "Desconhecida"


class TestGrupoMidea:
    @pytest.mark.parametrize("marca", [
        "Midea", "Carrier", "Springer", "Comfee", "Springer Midea", "Midea Carrier",
    ])
    def test_membros_do_grupo(self, marca):
        assert eh_grupo_midea(marca) is True

    @pytest.mark.parametrize("marca", ["LG", "Samsung", "Gree", "Desconhecida", None])
    def test_fora_do_grupo(self, marca):
        assert eh_grupo_midea(marca) is False


class TestBestSellerItem:
    def test_classifica_no_construtor(self):
        item = BestSellerItem(rank=1, titulo="Ar Condicionado Split Midea 12000 BTUs")
        assert item.marca == "Midea"
        assert item.grupo_midea is True
        assert item.btu == 12000
        assert item.tipo == TIPO_SPLIT_HW
        assert item.no_escopo is True

    def test_item_fora_de_escopo_fica_na_base(self):
        """Contaminação precisa ficar visível para o portão medi-la."""
        item = BestSellerItem(rank=3, titulo="Umidificador de Ar 3L")
        assert item.no_escopo is False
        assert item.tipo == TIPO_FORA_ESCOPO

    def test_to_row_carrega_a_prova_da_ordenacao(self):
        from bestsellers.config import SOURCES

        item = BestSellerItem(rank=1, titulo="Split Midea 9000", preco=1899.0)
        linha = item.to_row(
            data="2026-08-10", horario="09:30", run_id="abc",
            spec=SOURCES["amazon"],
            url_coleta="https://www.amazon.com.br/gp/bestsellers/home/17125373011/",
            endpoint="https://www.amazon.com.br/gp/bestsellers/home/17125373011/",
        )
        assert linha["plataforma"] == "amazon"
        assert linha["parametro_ordenacao"] == "gp/bestsellers"
        assert linha["mecanica"] == "velocidade"
        assert linha["grupo_midea"] is True

    def test_base_vendidos_do_item_vence_a_da_fonte(self):
        """Um card sem '/Mês' na Shopee é acumulado, mesmo a fonte sendo mensal."""
        from bestsellers.config import SOURCES

        item = BestSellerItem(
            rank=1, titulo="Split Midea 9000", vendidos=100.0, base_vendidos="acumulado"
        )
        linha = item.to_row(
            data="2026-08-10", horario="09:30", run_id=None,
            spec=SOURCES["shopee"], url_coleta="x", endpoint="y",
        )
        assert linha["base_vendidos"] == "acumulado"


class TestFalsosPositivosDeEscopo:
    """
    Termos como CONTROLE, FILTRO, LIMPA, CAPA e BOMBA aparecem em títulos de
    split legítimos. Casá-los como substring solta derrubava esses itens para
    FORA_ESCOPO — e o erro era ASSIMÉTRICO: títulos Midea/Springer carregam
    essas palavras com frequência, então o KPI do grupo saía subestimado e a
    contaminação, superestimada.
    """

    @pytest.mark.parametrize("titulo", [
        "Ar Condicionado Split Inverter Midea 12000 BTUs com Controle Remoto",
        "Ar Condicionado Split Springer Midea 9000 BTUs Autolimpante",
        "Ar Condicionado Split LG Dual Inverter com Filtro Anti-Alérgico",
        "Ar Condicionado Split Carrier Bomba de Calor 12000 BTUs",
        "Ar Condicionado Split Inverter Capacidade Frio 18000 BTUs",
        "Split Hi Wall Midea 12000 BTUs Inverter Quente e Frio",
    ])
    def test_split_legitimo_continua_no_escopo(self, titulo):
        assert classificar_tipo(titulo) == TIPO_SPLIT_HW

    @pytest.mark.parametrize("titulo", [
        "Springer Midea Ar Condicionado Split Hi Wall Inverter 12000 BTUs com Controle Remoto",
        "Elgin Ar Condicionado Split Eco Power Bomba de Calor 12000 BTUs",
        "LG Ar Condicionado Split Dual Inverter Capacidade 12000 BTUs Frio",
        "Midea Ar Condicionado Split Inverter com Filtro HD e Função Limpeza",
        "Samsung Ar Condicionado WindFree Motor Inverter 9000 BTUs",
    ])
    def test_titulo_com_a_marca_primeiro(self, titulo):
        """Marca-primeiro é o padrão dominante em Amazon e Shopee. Exigir que
        o título COMECE com o núcleo jogava todos esses splits fora do KPI."""
        assert classificar_tipo(titulo) == TIPO_SPLIT_HW

    @pytest.mark.parametrize("titulo", [
        "Suporte para Ar Condicionado Split até 12000 BTUs",
        "Controle Remoto Universal para Ar Condicionado",
        "Kit Instalação Ar Condicionado 3 metros",
        "Elgin Capa Protetora para Ar Condicionado Split",
        "Limpa Ar Condicionado Spray 300ml",
        "Tubulação de Cobre 1/4 para Ar Condicionado",
        "Bomba de Vácuo para Ar Condicionado",
        "Suporte Universal de Parede 12000 BTUs",
    ])
    def test_acessorio_continua_fora(self, titulo):
        """O termo desqualifica quando vem ANTES do núcleo 'ar condicionado'
        (ou quando não há núcleo nenhum) — aí ele é o substantivo principal."""
        assert classificar_tipo(titulo) == TIPO_FORA_ESCOPO
