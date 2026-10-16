"""
tests/test_offer_kind.py — classificação de anúncio (unidade × kit/peça/meia
unidade/múltiplo). Casos tirados de títulos e URLs REAIS de `coletas`
(15/10/2026), incluindo os que geravam as maiores divergências contra o
PriceTrack.
"""
import pytest

from utils.offer_kind import (
    ACESSORIO,
    COM_INSTALACAO,
    MULTIPLO,
    PARCIAL,
    UNIDADE,
    classify_offer,
    is_unit_offer,
)


@pytest.mark.parametrize("url,esperado", [
    # Kit de instalação de R$ 255 que virava "Ar Condicionado TCL Elite 9.000"
    ("https://www.amazon.com.br/Completo-Instala%C3%A7%C3%A3o-condicionado-branco-Suporte/dp/B0DGJ5KR94/ref=sr_1_96", ACESSORIO),
    ("https://www.amazon.com.br/instala%c3%a7%c3%a3o-completo-condesandora-condicionado-inverter/dp/b0ch8x1d4d/ref=sr_1_65", ACESSORIO),
    ("https://www.amazon.com.br/instala%c3%a7%c3%a3o-condicionado-split-12000-18000/dp/b0cwj4hsx9/ref=sr_1_38_mod_primary_new", ACESSORIO),
    ("https://www.leroymerlin.com.br/caixa-sob-medida-ripada-ar-condicionado-condensadora-protecao-de-madeira-cc10_1572460993", ACESSORIO),
    ("https://www.leroymerlin.com.br/motor-ventilador-da-condensadora-ar-condicionado-lg-eau62903305_1572785497", ACESSORIO),
    ("https://www.leroymerlin.com.br/telhado-de-protecao-para-condensadora-de-ar-condicionado-gallant_1567073088", ACESSORIO),
    ("https://m.magazineluiza.com.br/placa-evaporadora-22-000-btus-consul-original-cbp22-brastemp-consul/p/deghe41k3e/ar/plac/?seller_id=lojaartechrefrigera", ACESSORIO),
    ("https://m.magazineluiza.com.br/ventilador-torre-ar-condicionado-com-controle-e-temporizador-climatiza/p/dg3a28ak3d/ar/arpr/?seller_id=loarymix", ACESSORIO),
    # Meia unidade
    ("https://www.mercadolivre.com.br/condensadora-gree-inverter-qf-9000btuh-gwh09agad3dna1bo/up/MLBU4693809567?pdp_filters=item_id%3AMLB7406504062", PARCIAL),
    ("https://www.mercadolivre.com.br/evaporadora-consul-7000-fixo-nao-e-ar-split-completo-branco/p/mlb63736122", PARCIAL),
    ("https://m.magazineluiza.com.br/evaporadora-windfree-black-edition-samsung/p/akjdacjfjd/ar/aciv/?seller_id=samsung", PARCIAL),
    # Dois aparelhos num preço
    ("https://www.mercadolivre.com.br/kit-2-ar-condicionado-tcl-inverter-9000-btus-elite-gv-220v-branco/p/MLB67612826?pdp_filters=item_id%3AMLB6594551614", MULTIPLO),
    # Aparelhos de verdade — "controle", "unidade externa", "kit9426" e
    # "instalação" depois do núcleo não desqualificam
    ("https://www.mercadolivre.com.br/ar-condicionado-portatil-philco-10000-btus-quente-e-frio-4-em-1-com-controle-remoto-e-timer-pac10qn/p/mlb66170866", UNIDADE),
    ("https://www.amazon.com.br/condicionado-port%c3%a1til-kabum-branco-controle/dp/b0h3751mcv/ref=sr_1_84", UNIDADE),
    ("https://www.mercadolivre.com.br/ar-condicionado-samsung-windfree-cassete-1-via-qf-cor-branco-voltagem-da-unidade-externa/p/mlb46354011", UNIDADE),
    ("https://www.frigelar.com.br/ar-condicionado-split-inverter-18000-btus-electrolux-color-adapt-wi-fi-frio-yi18f-ye18f-220v/p/kit9426", UNIDADE),
    ("https://www.amazon.com.br/airo-comfort-condicionado-port%c3%a1til-instala%c3%a7%c3%a3o/dp/b09253hzzj/ref=sr_1_72", UNIDADE),
    ("https://www.mercadolivre.com.br/arcondicionado-consul-9000-btus-lacrado-pegar-e-instalar/up/mlbu3791755851", UNIDADE),
    ("https://www.mercadolivre.com.br/ar-condicionado-split-philco-18000-btus-inverter-frio/p/MLB52136012", UNIDADE),
    ("https://www.mercadolivre.com.br/condensadora-ar-split-inverter-9000-btus-frio-220v-elgin/up/MLBU5375904603", PARCIAL),
    ("https://m.magazineluiza.com.br/unidade-condensadora-aiwa-9000-btus-inverter-220v/p/jfch63fk8b/ar/aciv/", PARCIAL),
    ("https://m.magazineluiza.com.br/w10616425-evaporador-2-filas-ar-condicionado-split-consul-cbw12a-cbe12a-cbj12e-e-d-cbf12d-e-e-novo-original/p/jcbeecejj1/ar/aave/", ACESSORIO),
    ("https://m.magazineluiza.com.br/w11551335-condensador-montado-12k-hp-inverter-split-consul-cbs12cbxna-novo-original-ar-condicionado/p/fd90ga8368/ar/star/", PARCIAL),
    ("https://www.amazon.com.br/Placa-Display-Condicionado-Split-Electrolux/dp/B0BVT3HWM5/ref=sr_1_98", ACESSORIO),
    ("https://www.mercadolivre.com.br/mangueira-ar-condicionado-s10-trailblazer-28-2025-2026/up/MLBU5164531507", ACESSORIO),
    ("https://www.mercadolivre.com.br/kit-ar-condicionado-frio-220v-elgin-high-wall-eco-invert-ii/up/MLBU3386182436", UNIDADE),
    ("https://www.mercadolivre.com.br/ar-condicionado-springer-midea-ai-ecomaster-220vqfkit-inst/up/MLBU5277152925", UNIDADE),
    ("https://m.magazineluiza.com.br/ar-condicionado-split-hi-wall-samsung-digital-inverter-ultra-18000-btus-quente-e-frio-r410a-220v-com-garantia-de-dois-anos/p/ac4g1232cb/ar/aciv/", UNIDADE),
])
def test_urls_reais(url, esperado):
    assert classify_offer(url=url) == esperado


@pytest.mark.parametrize("titulo,esperado", [
    ("Kit Instalação Ar Condicionado Split 9000 12000 Btus 3 Metros", ACESSORIO),
    ("Suporte Para Ar Condicionado Split 9000 a 12000 Btus", ACESSORIO),
    ("Controle Remoto Para Ar Condicionado Midea Springer", ACESSORIO),
    ("Condensadora Ar Condicionado Gree 9000 Btus Inverter", PARCIAL),
    ("Ar Condicionado Split Midea 12000 Somente a Evaporadora", PARCIAL),
    ("Kit 2 Ar Condicionado TCL Inverter 9000 Btus Elite", MULTIPLO),
    ("Kit com 3 Ar Condicionado Split Philco 9000", MULTIPLO),
    ("2 unidades Ar Condicionado Midea 9000 Btus", MULTIPLO),
    ("Ar Condicionado Midea 12000 Btus Inverter com Instalação Inclusa", COM_INSTALACAO),
    ("Ar Condicionado LG 9000 Btus + Instalação Grátis", COM_INSTALACAO),
    ("Springer Midea Ar Condicionado Split 12000 Btus Inverter com Controle", UNIDADE),
    ("Bomba de Calor 12000 Btus Split Midea Quente Frio", UNIDADE),
    ("Ar Condicionado Split Hi Wall Evaporadora + Condensadora LG 9000", UNIDADE),
    ("Ar Condicionado Multi Split 2 Evaporadoras 18000 Btus", UNIDADE),
])
def test_titulos(titulo, esperado):
    assert classify_offer(titulo) == esperado


def test_titulo_ou_url_qualquer_um_denuncia():
    # O nome canônico (gravado nas linhas antigas) esconde o kit; a URL não.
    canonico = "Ar Condicionado TCL Elite 9.000 BTUs Inverter Frio"
    url = "https://www.amazon.com.br/Completo-Instala%C3%A7%C3%A3o-condicionado-branco-Suporte/dp/B0DGJ5KR94"
    assert classify_offer(canonico) == UNIDADE
    assert classify_offer(canonico, url) == ACESSORIO
    assert not is_unit_offer(canonico, url)


@pytest.mark.parametrize("vazio", [None, "", float("nan")])
def test_entrada_vazia_e_unidade(vazio):
    assert classify_offer(vazio if isinstance(vazio, str) or vazio is None else None) == UNIDADE


class TestNormalizacaoNaoCanonizaNaoUnidade:
    """Regressão do bug de 14–15/10/2026: kit/peça/meia unidade/kit 2 recebiam
    o nome canônico do modelo e, com ele, o SKU do aparelho no banco."""

    @pytest.mark.parametrize("titulo", [
        "Kit Instalação Completo Ar Condicionado TCL Elite 9000 Btus Suporte",
        "Condensadora Gree Inverter Quente Frio 9000 Btus",
        "Kit 2 Ar Condicionado TCL Inverter 9000 Btus Elite GV 220v",
        "Ar Condicionado Midea 12000 Btus Inverter com Instalação Inclusa",
    ])
    def test_titulo_cru_preservado(self, titulo):
        from utils.normalize_product import (
            normalize_product_name,
            normalize_product_name_v2,
        )
        assert normalize_product_name(titulo) == titulo
        assert normalize_product_name_v2(titulo) is None

    def test_unidade_continua_canonica(self):
        from utils.normalize_product import normalize_product_name
        out = normalize_product_name("Ar Split LG Ai Dual Inverter Voice 9000 Btus Frio Branco")
        assert out == "Ar Condicionado LG Dual Inverter AI Voice 9.000 BTUs Inverter Frio"
