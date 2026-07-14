"""
tests/test_shopee_parse.py — parsing resiliente da resposta search_items.

Motivação: a coleta da Shopee passou a retornar "0 produtos" mesmo com a API v4
respondendo com itens — a Shopee trocou o invólucro de cada item no
`search_items` e o parser só reconhecia `item_basic`. Estes testes cobrem o
extrator de payload (`_extract_item_payload`) contra os formatos de wrapper já
vistos e a normalização de preço, garantindo que uma nova troca de estrutura
seja detectada (dump) em vez de virar coleta silenciosamente vazia.

Rode: pytest tests/test_shopee_parse.py
"""
import pytest

from scrapers.shopee import ShopeeScraper


@pytest.fixture(scope="module")
def scraper():
    return ShopeeScraper()


def _item_fields(**over):
    base = {
        "itemid": 111,
        "shopid": 222,
        "name": "Ar Condicionado Split Inverter 12000 BTUs",
        "price": 199900000,  # escala ×100000 → R$ 1.999,00
        "shop_name": "Loja Fria",
        "is_official_shop": False,
        "historical_sold": 42,
    }
    base.update(over)
    return base


class TestExtractItemPayload:
    def test_wrapper_item_basic(self, scraper):
        """Formato clássico: produto sob item_basic."""
        payload = scraper._extract_item_payload({"item_basic": _item_fields()})
        assert payload.get("itemid") == 111

    def test_wrapper_item(self, scraper):
        payload = scraper._extract_item_payload({"item": _item_fields()})
        assert payload.get("itemid") == 111

    def test_wrapper_item_data(self, scraper):
        payload = scraper._extract_item_payload({"item_data": _item_fields()})
        assert payload.get("itemid") == 111

    def test_flat_format(self, scraper):
        """Formato novo: campos do produto direto no wrapper (sem invólucro)."""
        payload = scraper._extract_item_payload(_item_fields())
        assert payload.get("itemid") == 111

    def test_flat_format_item_id_alias(self, scraper):
        payload = scraper._extract_item_payload(_item_fields(itemid=None, item_id=999))
        assert payload.get("item_id") == 999

    def test_nested_wrapper(self, scraper):
        """Invólucro que carrega outro invólucro (item_data.item_basic)."""
        wrapper = {"item_data": {"item_basic": _item_fields()}}
        payload = scraper._extract_item_payload(wrapper)
        assert payload.get("itemid") == 111

    def test_unknown_structure_returns_empty(self, scraper):
        """Estrutura desconhecida → {} (dispara dump de diagnóstico)."""
        assert scraper._extract_item_payload({"foo": {"bar": 1}}) == {}

    def test_non_dict_returns_empty(self, scraper):
        assert scraper._extract_item_payload("nope") == {}


class TestNormalizePrice:
    def test_scale_100000(self, scraper):
        assert scraper._normalize_price(199900000) == 1999.00

    def test_already_in_reais(self, scraper):
        assert scraper._normalize_price(2599) == 2599.00

    def test_zero_and_negative(self, scraper):
        assert scraper._normalize_price(0) is None
        assert scraper._normalize_price(-5) is None

    def test_non_numeric(self, scraper):
        assert scraper._normalize_price(None) is None
        assert scraper._normalize_price("R$ 10") is None

    def test_numeric_string_scale(self, scraper):
        """String puramente numérica é coagida e escalada."""
        assert scraper._normalize_price("199900000") == 1999.00

    def test_bool_rejected(self, scraper):
        assert scraper._normalize_price(True) is None

    def test_non_finite_rejected(self, scraper):
        """nan / inf (numérico ou string) nunca viram preço."""
        assert scraper._normalize_price(float("nan")) is None
        assert scraper._normalize_price(float("inf")) is None
        assert scraper._normalize_price("nan") is None
        assert scraper._normalize_price("inf") is None

    def test_brazilian_decimal_string_rejected(self, scraper):
        """Decimal BR ("1999,00") é rejeitado, não inflado 100x para 199900."""
        assert scraper._normalize_price("1999,00") is None
        assert scraper._normalize_price("1.999,00") is None


class TestExtractNameAndPrice:
    def test_name_alias_keys(self, scraper):
        assert scraper._extract_name({"title": "AC T"}) == "AC T"
        assert scraper._extract_name({"item_name": "AC I"}) == "AC I"
        assert scraper._extract_name({"display_name": "AC D"}) == "AC D"

    def test_name_missing(self, scraper):
        assert scraper._extract_name({"foo": "bar"}) is None
        assert scraper._extract_name({"name": "   "}) is None

    def test_price_alias_keys(self, scraper):
        assert scraper._extract_raw_price({"price_min": 199900000}) == 199900000
        assert scraper._extract_raw_price(
            {"price_before_discount": 250000000}
        ) == 250000000

    def test_price_nested_holder(self, scraper):
        """Preço aninhado sob price_info (formato novo)."""
        raw = scraper._extract_raw_price({"price_info": {"price": 199900000}})
        assert scraper._normalize_price(raw) == 1999.00

    def test_name_from_displayed_asset(self, scraper):
        """Formato Jul/2026: nome vive em item_card_displayed_asset.name."""
        asset = {"name": "Ar Condicionado LG 18000 BTU"}
        assert scraper._extract_name({}, asset) == "Ar Condicionado LG 18000 BTU"

    def test_price_from_item_card_display_price(self, scraper):
        """Formato Jul/2026: preço em item_data.item_card_display_price.price."""
        item = {"item_card_display_price": {"price": 307401000}}
        assert scraper._normalize_price(scraper._extract_raw_price(item, {})) == 3074.01

    def test_price_from_asset_display_price(self, scraper):
        asset = {"display_price": {"price": 199900000}}
        assert scraper._normalize_price(scraper._extract_raw_price({}, asset)) == 1999.00


def _wrapper_jul2026(**over):
    """Wrapper realista do search_items (formato Jul/2026): dados em item_data
    + item_card_displayed_asset, com itemid/shopid no topo."""
    w = {
        "itemid": 58260116699,
        "shopid": 1009975506,
        "item_card_displayed_asset": {
            "name": "Ar condicionado LG Dual Inverter 18000 BTU Quente Frio 220v",
            "seller_flag": {"name": "OFFICIAL_SHOP"},
            "sold_count": {"text": "192 Vendido(s)"},
            "display_price": {"price": 307401000},
            "rating": {"rating_text": "5.0"},
        },
        "item_data": {
            "itemid": 58260116699,
            "shopid": 1009975506,
            "item_card_display_price": {"price": 307401000, "original_price": 559900000},
            "item_card_display_sold_count": {"historical_sold_count": 192},
            "shop_data": {"shop_name": "Engage Eletro "},
            "item_rating": {"rating_star": 5, "rating_count": [25, 0, 0, 0, 0, 25]},
            "global_brand": {"display_name": "LG"},
        },
    }
    w.update(over)
    return w


class TestJul2026Wrapper:
    def test_parses_name_price_seller(self, scraper):
        recs = scraper._parse_items([_wrapper_jul2026()], "ar condicionado split", {}, page=0)
        assert len(recs) == 1
        r = recs[0]
        # produto é o nome normalizado pelo _build_record; basta estar populado
        # (não-NULL) e preservar marca/capacidade — o que destrava o dashboard.
        assert r["Produto / SKU"]
        assert "LG" in r["Produto / SKU"]
        assert "18" in r["Produto / SKU"]
        assert r["Preço (R$)"] == 3074.01
        assert r["Seller / Vendedor"] == "Engage Eletro"  # normalize_text apara o espaço
        assert r["Buy Box Seller"] == "Engage Eletro"
        assert r["Tipo Seller"] == "Shopee Mall"  # seller_flag OFFICIAL_SHOP
        assert r["Avaliação"] == 5.0
        assert r["Qtd Avaliações"] == 50
        assert "192" in r["Tag Destaque"]
        assert r["URL Produto"] == "https://shopee.com.br/product/1009975506/58260116699"

    def test_sold_zero_is_valid(self, scraper):
        """0 vendas é valor válido, não 'ausente'."""
        item = {"item_card_display_sold_count": {"historical_sold_count": 0}}
        assert scraper._extract_sold(item, {}) == "0 vendidos"

    def test_asset_only_card_skipped(self, scraper):
        """Card só-asset (sem item_data) não vira registro (sem ids/URL)."""
        recs = scraper._parse_items(
            [{"item_card_displayed_asset": {"name": "AC sem ids"}}], "kw", {}, page=0
        )
        assert recs == []

    def test_shop_location_not_used_as_seller(self, scraper):
        """shop_location (cidade) nunca vira Seller/Buy Box."""
        w = _wrapper_jul2026()
        del w["item_data"]["shop_data"]
        w["item_data"]["shop_location"] = "Minas Gerais"
        w["item_card_displayed_asset"]["shop_location"] = "Minas Gerais"
        recs = scraper._parse_items([w], "kw", {}, page=0)
        assert recs[0]["Seller / Vendedor"] is None
        assert recs[0]["Buy Box Seller"] is None

    def test_positions_contiguous_after_skip(self, scraper):
        """Card só-asset pulado no meio não deixa buraco na Posição Geral."""
        items = [
            _wrapper_jul2026(itemid=1),
            {"item_card_displayed_asset": {"name": "placeholder"}},  # pulado
            _wrapper_jul2026(itemid=3),
        ]
        recs = scraper._parse_items(items, "kw", {}, page=0)
        assert [r["Posição Geral"] for r in recs] == [1, 2]

    def test_positions_use_page_offset(self, scraper):
        recs = scraper._parse_items([_wrapper_jul2026()], "kw", {}, page=1)
        assert recs[0]["Posição Geral"] == 61  # page 1 → offset 60 + 1

    def test_positions_carry_offset_across_pages(self, scraper):
        """start_offset mantém a numeração contígua entre páginas, mesmo quando
        uma página anterior emitiu menos de 60 (card pulado)."""
        # Página 1 emite 2 (com 1 card pulado no meio).
        p1 = scraper._parse_items(
            [_wrapper_jul2026(itemid=1),
             {"item_card_displayed_asset": {"name": "skip"}},
             _wrapper_jul2026(itemid=3)],
            "kw", {}, page=0, start_offset=0,
        )
        assert [r["Posição Geral"] for r in p1] == [1, 2]
        # Página 2 continua de onde parou (start_offset = 2), sem buraco.
        p2 = scraper._parse_items(
            [_wrapper_jul2026(itemid=4)], "kw", {}, page=1, start_offset=len(p1),
        )
        assert p2[0]["Posição Geral"] == 3

    def test_rating_fallback_from_asset(self, scraper):
        """Sem item_rating, cai no asset.rating.rating_text."""
        w = _wrapper_jul2026()
        del w["item_data"]["item_rating"]
        recs = scraper._parse_items([w], "kw", {}, page=0)
        assert recs[0]["Avaliação"] == 5.0

    def test_no_hollow_dump_for_jul2026(self, scraper, tmp_path, monkeypatch):
        """Formato novo agora parseia name/price → não dispara o dump."""
        monkeypatch.chdir(tmp_path)
        s = ShopeeScraper()
        recs = s._parse_items([_wrapper_jul2026(), _wrapper_jul2026()], "kw", {}, page=0)
        s._maybe_dump_hollow_parse("kw", 0, [_wrapper_jul2026()], recs)
        assert s._shape_dumped is False


class TestHollowParseDump:
    def test_dump_fires_when_name_and_price_missing(self, scraper, tmp_path, monkeypatch):
        """Itens parseiam pelo id mas sem name/price → dispara o dump de amostra."""
        monkeypatch.chdir(tmp_path)
        s = ShopeeScraper()
        # Wrapper flat com id/seller mas SEM name e SEM price (regressão Jul/2026).
        items = [
            {"itemid": i, "shopid": 9, "shop_name": "Loja X"} for i in range(1, 5)
        ]
        recs = s._parse_items(items, "ar condicionado", {}, page=0)
        assert len(recs) == 4
        assert all(r["Produto / SKU"] in (None, "") for r in recs)
        assert all(r["Preço (R$)"] is None for r in recs)

        s._maybe_dump_hollow_parse("ar condicionado", 0, items, recs)
        assert s._shape_dumped is True
        dumps = list((tmp_path / "logs").glob("shopee_debug_*.json"))
        assert dumps, "esperava um dump de amostra crua em logs/"

    def test_dump_skipped_when_core_fields_present(self, scraper, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s = ShopeeScraper()
        items = [_item_fields(itemid=1), _item_fields(itemid=2)]
        recs = s._parse_items(items, "kw", {}, page=0)
        s._maybe_dump_hollow_parse("kw", 0, items, recs)
        assert s._shape_dumped is False
        assert not list((tmp_path / "logs").glob("shopee_debug_*.json"))


class TestParseItems:
    def test_parses_flat_and_wrapped(self, scraper):
        """Mistura de formatos na mesma resposta: todos devem parsear."""
        items = [
            {"item_basic": _item_fields(itemid=1, name="AC A")},
            _item_fields(itemid=2, name="AC B"),  # flat
            {"item_data": _item_fields(itemid=3, name="AC C")},
        ]
        recs = scraper._parse_items(items, "ar condicionado", {}, page=0)
        assert len(recs) == 3
        assert recs[0]["Produto / SKU"] == "AC A"
        assert recs[0]["Buy Box Seller"] == "Loja Fria"
        assert recs[0]["Preço (R$)"] == 1999.00

    def test_seller_type_official_shop(self, scraper):
        items = [_item_fields(is_official_shop=True)]
        recs = scraper._parse_items(items, "ar condicionado", {}, page=0)
        assert recs[0]["Tipo Seller"] == "Shopee Mall"

    def test_unknown_structure_yields_nothing(self, scraper):
        """Wrapper irreconhecível → 0 registros (não crash)."""
        recs = scraper._parse_items([{"foo": 1}, {"bar": 2}], "kw", {}, page=0)
        assert recs == []
