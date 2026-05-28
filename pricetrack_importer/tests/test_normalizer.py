"""Testes do normalizer."""
from datetime import date, datetime

import pytest

from pricetrack_importer.normalizer import (
    iso_date,
    is_pricetrack_date,
    normalize_text,
    parse_decimal,
    parse_pricetrack_date,
)


class TestParsePricetrackDate:
    def test_basico(self):
        assert parse_pricetrack_date("5/27/26") == date(2026, 5, 27)

    def test_dia_e_mes_um_digito(self):
        assert parse_pricetrack_date("1/2/26") == date(2026, 1, 2)

    def test_com_whitespace(self):
        assert parse_pricetrack_date("  12/31/25  ") == date(2025, 12, 31)

    def test_data_invalida_devolve_none(self):
        assert parse_pricetrack_date("13/45/26") is None
        assert parse_pricetrack_date("abc") is None
        assert parse_pricetrack_date("") is None

    def test_none(self):
        assert parse_pricetrack_date(None) is None

    def test_ano_70_vira_1970(self):
        assert parse_pricetrack_date("1/1/70") == date(1970, 1, 1)

    def test_iso_aceito_como_defesa_xlsx(self):
        # xlsx pode chegar como ISO se openpyxl serializar a data antes do parser
        assert parse_pricetrack_date("2026-05-27") == date(2026, 5, 27)

    def test_datetime_string_aceito(self):
        # openpyxl `str(datetime)` → "2026-05-27 00:00:00"
        assert parse_pricetrack_date("2026-05-27 00:00:00") == date(2026, 5, 27)

    def test_objeto_date_passthrough(self):
        assert parse_pricetrack_date(date(2026, 5, 27)) == date(2026, 5, 27)

    def test_objeto_datetime_passthrough(self):
        assert parse_pricetrack_date(datetime(2026, 5, 27, 12, 0)) == date(2026, 5, 27)


class TestIsPricetrackDate:
    def test_valida(self):
        assert is_pricetrack_date("5/27/26") is True

    def test_invalida(self):
        assert is_pricetrack_date("Filtros aplicados:") is False
        assert is_pricetrack_date("Total") is False


class TestParseDecimal:
    def test_basico(self):
        assert parse_decimal("7994.44") == 7994.44

    def test_com_whitespace(self):
        assert parse_decimal("  1259.00 ") == 1259.00

    def test_vazio(self):
        assert parse_decimal("") is None
        assert parse_decimal("   ") is None

    def test_none(self):
        assert parse_decimal(None) is None

    def test_invalido(self):
        assert parse_decimal("abc") is None
        assert parse_decimal("N/A") is None

    def test_virgula_decimal_fallback(self):
        # Se cliente já converteu para vírgula, ainda devolvemos float
        assert parse_decimal("7994,44") == 7994.44


class TestNormalizeText:
    def test_strip_e_colapso(self):
        assert normalize_text("  Ar   Condicionado   Midea  ") == "Ar Condicionado Midea"

    def test_none(self):
        assert normalize_text(None) == ""


class TestIsoDate:
    def test_basico(self):
        assert iso_date(date(2026, 5, 27)) == "2026-05-27"

    def test_none(self):
        assert iso_date(None) is None
