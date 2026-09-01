"""Testes do contrato do peer e do casamento por código de modelo."""
from __future__ import annotations

from pricetrack_dashboard.peer import (
    CAP_9K,
    CAP_12K,
    TIER_HIGH,
    TIER_LOW,
    TIER_MID,
    TIER_ORDER,
    all_tiers,
    match_haystack,
    normalize_code,
    split_model_codes,
)


class TestNormalizeCode:
    def test_strips_non_alnum_and_uppercases(self):
        assert normalize_code("42EBVCA09M5/38TBVCA09M5") == "42EBVCA09M538TBVCA09M5"
        assert normalize_code("s3-q09aaqak") == "S3Q09AAQAK"


class TestSplitModelCodes:
    def test_splits_slash_combo(self):
        assert split_model_codes("42EBVCA09M5/38TBVCA09M5") == [
            "42EBVCA09M5", "38TBVCA09M5",
        ]

    def test_splits_plus_combo_with_spaces(self):
        assert split_model_codes("38TAVCA09M5+ 42EFVCA09M5") == [
            "38TAVCA09M5", "42EFVCA09M5",
        ]

    def test_drops_phase_annotations(self):
        assert split_model_codes("S3-Q09AA31F (phase in)") == ["S3Q09AA31F"]
        assert split_model_codes("GWC09ATB-D6DNA1A (phase out)") == [
            "GWC09ATBD6DNA1A",
        ]

    def test_drops_fs_annotation(self):
        assert split_model_codes("QCL078RB (FS)") == ["QCL078RB"]

    def test_drops_too_short_tokens(self):
        # "PAC9FC" tem 6 chars — mantém; um token de 3 chars não entra.
        assert "PAC9FC" in split_model_codes("PAC9FC")
        assert split_model_codes("ABC") == []


class TestPeerStructure:
    def test_three_tiers_two_caps_each(self):
        tiers = all_tiers()
        assert len(tiers) == 6  # 3 tiers × 2 capacidades
        assert TIER_ORDER == [TIER_LOW, TIER_MID, TIER_HIGH]

    def test_every_tier_has_exactly_one_midea(self):
        for pt in all_tiers():
            assert len(pt.midea_models) == 1, f"{pt.tier}/{pt.capacity}"

    def test_midea_line_labels(self):
        from pricetrack_dashboard.peer import PEER
        assert PEER[TIER_LOW][CAP_9K].midea_line == "Inverter Lite"
        assert PEER[TIER_MID][CAP_12K].midea_line == "AI AirVolution"
        assert PEER[TIER_HIGH][CAP_9K].midea_line == "AI Ecomaster"

    def test_competitors_present(self):
        from pricetrack_dashboard.peer import PEER
        brands = {m.brand for m in PEER[TIER_LOW][CAP_9K].competitor_models}
        assert {"AGRATTO", "ELGIN", "GREE", "LG", "PHILCO", "TCL", "HISENSE"} <= brands


class TestMatchHaystack:
    def test_matches_midea_entry_9k_by_code(self):
        hit = match_haystack(
            "42EBVCA09M5", "Ar Condicionado Split Midea Inverter 9000 BTU", "MIDEA"
        )
        assert hit is not None
        assert hit.tier == TIER_LOW and hit.capacity == CAP_9K
        assert hit.is_midea

    def test_matches_competitor_by_code(self):
        hit = match_haystack("", "Split LG Dual Inverter S3-Q12JAQAL 12000", "LG")
        assert hit is not None
        assert hit.tier == TIER_LOW and hit.capacity == CAP_12K
        assert not hit.is_midea and hit.brand == "LG"

    def test_matches_ecomaster_combo_condensadora(self):
        # Só a condensadora no título ainda casa (combo split).
        hit = match_haystack("42EZVCA12M5", "Ar Condicionado Midea Ecomaster", "")
        assert hit is not None
        assert hit.tier == TIER_HIGH and hit.capacity == CAP_12K
        assert hit.is_midea

    def test_no_match_returns_none(self):
        assert match_haystack("XYZ123", "Geladeira Brastemp", "BRASTEMP") is None

    def test_normalization_tolerates_formatting(self):
        # Código com espaços/caixa diferente ainda casa.
        hit = match_haystack("tac-09csgv-inv", "TCL 9000", "TCL")
        assert hit is not None and hit.tier == TIER_LOW and hit.capacity == CAP_9K
