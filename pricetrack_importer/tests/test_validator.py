"""Testes do validator e da normalização de sellers."""
import pytest

from pricetrack_importer.seller_map import normalize_seller
from pricetrack_importer.validator import (
    is_invalid_seller,
    is_metadata_row,
    validate_row,
)


def _row(**overrides):
    base = {
        "collectionDate": "5/27/26",
        "brand": "MIDEA",
        "sku": "38EZVQA12M5",
        "title": "Ar Condicionado",
        "marketplace": "MERCADO LIVRE",
        "seller": "FRIOPEÇAS",
        "MIN PRICE": "1000.00",
        "AVG PRICE": "1100.00",
        "MODE PRICE": "1100.00",
        "MAX PRICE": "1200.00",
    }
    base.update(overrides)
    return base


class TestIsMetadataRow:
    def test_linha_valida(self):
        assert is_metadata_row(_row()) is False

    def test_filtros_aplicados(self):
        assert is_metadata_row({"collectionDate": "Filtros aplicados:"}) is True

    def test_total(self):
        assert is_metadata_row({"collectionDate": "Total"}) is True

    def test_header(self):
        assert is_metadata_row({"collectionDate": "collectionDate"}) is True


class TestIsInvalidSeller:
    def test_seller_valido(self):
        invalid, _ = is_invalid_seller("FRIOPEÇAS")
        assert invalid is False

    def test_seller_com_loja_oficial(self):
        invalid, _ = is_invalid_seller("LOJA OFICIAL DUFRIO")
        assert invalid is False

    def test_sku_pattern_numerico(self):
        invalid, reason = is_invalid_seller("38EZVQA12M5 - 220V")
        assert invalid is True
        assert reason in {"LOOKS_LIKE_SKU_NUM", "TITLE_FRAGMENT"}

    def test_parenthesizado(self):
        invalid, reason = is_invalid_seller("(ZQK215BB)")
        assert invalid is True
        assert reason == "PARENTHESIZED"

    def test_numerico_puro(self):
        invalid, reason = is_invalid_seller("530290740")
        assert invalid is True
        assert reason == "NUMERIC_ONLY"

    def test_fragmento_btu(self):
        invalid, reason = is_invalid_seller("ALGUM 12000 BTU")
        assert invalid is True
        assert reason == "TITLE_FRAGMENT"

    def test_vazio(self):
        invalid, reason = is_invalid_seller("")
        assert invalid is True
        assert reason == "EMPTY"


class TestValidateRow:
    def test_linha_valida(self):
        r = validate_row(_row())
        assert r.valid is True

    def test_metadata(self):
        r = validate_row({"collectionDate": "Total"})
        assert r.valid is False
        assert r.reason == "METADATA"

    def test_seller_corrompido(self):
        r = validate_row(_row(seller="38EZVQA12M5 - 220V"))
        assert r.valid is False
        assert r.reason == "INVALID_SELLER"

    def test_campo_obrigatorio_faltando(self):
        r = validate_row(_row(brand=""))
        assert r.valid is False
        assert r.reason == "MISSING_FIELD"


class TestNormalizeSeller:
    def test_friopecas_canonical(self):
        # Todas estas grafias devem virar "FRIOPEÇAS"
        for raw in [
            "FRIOPECAS",
            "FRIOPEÇAS",
            "LOJA OFICIAL FRIOPEÇAS",
            "LOJA OFICIAL FRIOPECAS",
            "  friopeças  ",
        ]:
            assert normalize_seller(raw) == "FRIOPEÇAS"

    def test_dufrio_canonical(self):
        assert normalize_seller("LOJA OFICIAL DUFRIO") == "DUFRIO"
        assert normalize_seller("DUFRIO") == "DUFRIO"

    def test_centralar_canonical(self):
        assert normalize_seller("CENTRALAR.COM") == "CENTRAL AR"
        assert normalize_seller("CENTRAL AR") == "CENTRAL AR"

    def test_webcontinental_canonical(self):
        assert normalize_seller("WEBCONTINENTAL") == "WEB CONTINENTAL"
        assert normalize_seller("WEB CONTINENTAL") == "WEB CONTINENTAL"

    def test_seller_desconhecido_devolve_upper(self):
        # Sem match → devolve a string strip+upper
        assert normalize_seller("Loja Aleatória XYZ") == "LOJA ALEATÓRIA XYZ"

    def test_none(self):
        assert normalize_seller(None) == ""
