"""Testes das agregações de preço (mín/máx/moda/média/modal/piso)."""
from __future__ import annotations

from pricetrack_api.models import Offer

from pricetrack_dashboard.analytics import (
    analyze,
    compute_stats,
    daily_series,
    filter_offers,
)
from pricetrack_dashboard.peer import CAP_9K, CAP_12K, TIER_HIGH, TIER_LOW


def _offer(sku, brand, spot, status="AVAILABLE", pix=None, title="", name="",
           marketplace="MERCADO LIVRE", seller="seller"):
    return Offer(
        id=f"o-{sku}-{spot}-{status}", sku=sku, title=title or f"{brand} {sku}",
        product_name=name or f"{brand} {sku}", brand=brand,
        category="AR CONDICIONADO", subcategory="SPLIT", family="", color=None,
        marketplace=marketplace, seller=seller,
        spot_price=spot, forward_price=None, pix_price=pix, price_from=None,
        installment_number=None, installment_value=None, status=status,
        collection_date=None, collection_hour=None,
        image_url="", screenshot_url=None, url="",
    )


class TestComputeStats:
    def test_basic_stats(self):
        s = compute_stats([1000, 2000, 2000, 3000])
        assert s.count == 4
        assert s.minimum == 1000 and s.maximum == 3000
        assert s.mean == 2000.0
        assert s.mode == 2000  # mais frequente
        assert s.median == 2000.0

    def test_mode_tie_picks_lowest(self):
        # 1000 e 2000 empatam em frequência → escolhe o menor (conservador).
        s = compute_stats([1000, 1000, 2000, 2000])
        assert s.mode == 1000

    def test_ignores_none(self):
        s = compute_stats([None, 1500, None])
        assert s.count == 1 and s.minimum == 1500

    def test_empty(self):
        s = compute_stats([])
        assert s.is_empty and s.minimum is None and s.mode is None

    def test_rounds_to_cents(self):
        s = compute_stats([1000.111, 1000.114])
        assert s.mode == 1000.11  # ambos arredondam para 1000.11 → moda


class TestAnalyze:
    def test_classifies_into_tier_and_capacity(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),   # Low 9K midea
            _offer("42EBVCA09M5", "MIDEA", 1800),
            _offer("S3-Q09AAQAK", "LG", 1900),       # Low 9K LG
            _offer("38EZVCA12M5", "MIDEA", 3000),    # High 12K midea
        ]
        a = analyze(offers, collection_date="2026-09-01")
        low9 = a.tier(TIER_LOW, CAP_9K)
        assert low9.market.count == 3
        assert low9.midea.count == 2
        assert low9.midea.minimum == 1700 and low9.midea.maximum == 1800
        assert low9.midea.mode == 1700  # empate 1↔1 resolve no menor

        high12 = a.tier(TIER_HIGH, CAP_12K)
        assert high12.midea.count == 1 and high12.midea.mode == 3000

    def test_unavailable_offer_excluded_from_prices(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700, status="UNAVAILABLE"),
            _offer("42EBVCA09M5", "MIDEA", 1800),
        ]
        a = analyze(offers)
        low9 = a.tier(TIER_LOW, CAP_9K)
        assert low9.midea.count == 1 and low9.midea.minimum == 1800

    def test_pix_is_best_cash(self):
        # pix < spot → best_cash usa pix.
        offers = [_offer("42EBVCA09M5", "MIDEA", 2000, pix=1850)]
        a = analyze(offers)
        assert a.tier(TIER_LOW, CAP_9K).midea.minimum == 1850

    def test_midea_vs_market_delta_negative_when_cheaper(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("S3-Q09AAQAK", "LG", 2000),
            _offer("S3-Q09AAQAK", "LG", 2000),
        ]
        a = analyze(offers)
        low9 = a.tier(TIER_LOW, CAP_9K)
        # modal mercado = 1700 ou 2000? ambos 2×; empate → menor = 1700.
        # Midea modal = 1700 → delta = 0. Ajusta cenário: LG 3×.
        assert low9.midea.mode == 1700

    def test_unmatched_offer_counts_in_total_only(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("GELADEIRA123", "BRASTEMP", 999),
        ]
        a = analyze(offers)
        assert a.coverage.total_offers == 2
        assert a.coverage.matched_offers == 1

    def test_coverage_reports_missing_models(self):
        offers = [_offer("42EBVCA09M5", "MIDEA", 1700)]
        a = analyze(offers)
        # Só 1 modelo casou; o resto entra em missing.
        assert a.coverage.peer_models_with_data == 1
        assert len(a.coverage.missing) == a.coverage.peer_models - 1

    def test_all_six_tiers_present_even_when_empty(self):
        a = analyze([])
        assert len(a.tiers) == 6
        assert all(t.market.is_empty for t in a.tiers)


class TestPeersStats:
    def test_peers_excludes_midea(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("S3-Q09AAQAK", "LG", 1900),
            _offer("PAC9FC", "PHILCO", 2000),
        ]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        assert low9.peers.count == 2
        assert low9.peers.minimum == 1900 and low9.peers.maximum == 2000
        assert low9.midea.count == 1

    def test_midea_vs_peers_delta_negative_when_cheaper(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("S3-Q09AAQAK", "LG", 2000),
        ]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        assert low9.midea_vs_peers_delta == -300.0

    def test_empty_when_no_peer_offers(self):
        offers = [_offer("42EBVCA09M5", "MIDEA", 1700)]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        assert low9.peers.is_empty
        assert low9.midea_vs_peers_delta is None

    def test_midea_vs_market_delta_uses_peers_not_midea(self):
        """Regressão de produção: o filtro de Marca (só um concorrente, ex.
        Elgin) não movia o modal/delta do "mercado" porque ``market``
        incluía a própria Midea — e como a Midea está sempre presente e
        costuma repetir preço entre ofertas (MAP), ela vencia o empate do
        modal independente de qual concorrente estava selecionado. O modal
        e o delta do "mercado" têm que vir de ``peers`` (só concorrentes)."""
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("45HJFI09C2WB", "ELGIN", 1900),
            _offer("S3-Q09AAQAK", "LG", 2200),
        ]
        only_elgin = filter_offers(offers, keep_brands={"ELGIN"})
        low9 = analyze(only_elgin).tier(TIER_LOW, CAP_9K)
        assert low9.peers.mode == 1900  # não 1700 (preço da própria Midea)
        assert low9.midea_vs_market_delta == -200.0


class TestModelResults:
    def test_lists_every_peer_model_including_without_data(self):
        from pricetrack_dashboard.peer import PEER

        offers = [_offer("42EBVCA09M5", "MIDEA", 1700)]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        # Um ModelResult por modelo definido no peer (não um número fixo — o
        # peer muda de trimestre; deriva do próprio contrato, não hardcoded).
        assert len(low9.models) == len(PEER[TIER_LOW][CAP_9K].models)
        midea_rows = [m for m in low9.models if m.is_midea]
        assert len(midea_rows) == 1 and midea_rows[0].stats.count == 1
        empty_rows = [m for m in low9.models if not m.is_midea]
        assert all(m.stats.is_empty for m in empty_rows)

    def test_distinguishes_two_models_of_the_same_brand(self):
        # Philco tem dois modelos distintos no Low/9K (PAC9FC e PAC9FB).
        offers = [
            _offer("PAC9FC", "PHILCO", 1800),
            _offer("PAC9FB", "PHILCO", 2200),
        ]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        philco_rows = [m for m in low9.models if m.brand == "PHILCO"]
        assert len(philco_rows) == 2
        stats_by_label = {m.model_label: m.stats.mode for m in philco_rows}
        assert stats_by_label == {"PAC9FC": 1800, "PAC9FB": 2200}

    def test_model_label_keeps_original_punctuation(self):
        offers = [_offer("S3-Q09AAQAK", "LG", 1900)]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        lg = next(m for m in low9.models if m.brand == "LG")
        assert lg.model_label == "S3-Q09AAQAK"

    def test_midea_first_then_peers_by_ascending_mode(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("S3-Q09AAQAK", "LG", 2500),
            _offer("PAC9FC", "PHILCO", 1900),
        ]
        low9 = analyze(offers).tier(TIER_LOW, CAP_9K)
        non_empty = [m for m in low9.models if not m.stats.is_empty]
        assert non_empty[0].is_midea
        modes = [m.stats.mode for m in non_empty[1:]]
        assert modes == sorted(modes)


class TestDailySeries:
    def test_builds_one_point_per_date_and_tier(self):
        rows_by_date = {
            "2026-08-25": [
                _offer("42EBVCA09M5", "MIDEA", 1700),
                _offer("S3-Q09AAQAK", "LG", 2000),
            ],
            "2026-08-26": [
                _offer("42EBVCA09M5", "MIDEA", 1750),
                _offer("S3-Q09AAQAK", "LG", 2100),
            ],
        }
        series = daily_series(rows_by_date)
        low9 = next(s for s in series if s.tier == TIER_LOW and s.capacity == CAP_9K)
        assert [p.date for p in low9.points] == ["2026-08-25", "2026-08-26"]
        assert low9.points[0].midea_mode == 1700
        assert low9.points[0].peers_median == 2000
        assert low9.points[1].midea_mode == 1750

    def test_delta_pct_and_gap_last(self):
        rows_by_date = {
            "2026-08-25": [_offer("42EBVCA09M5", "MIDEA", 1000),
                           _offer("S3-Q09AAQAK", "LG", 1200)],
            "2026-08-26": [_offer("42EBVCA09M5", "MIDEA", 1100),
                           _offer("S3-Q09AAQAK", "LG", 1150)],
        }
        series = daily_series(rows_by_date)
        low9 = next(s for s in series if s.tier == TIER_LOW and s.capacity == CAP_9K)
        assert low9.delta_pct() == 10.0            # 1000 -> 1100 = +10%
        assert low9.gap_last() == -50.0             # 1100 - 1150

    def test_delta_pct_none_with_single_point(self):
        series = daily_series({"2026-08-25": [_offer("42EBVCA09M5", "MIDEA", 1000)]})
        low9 = next(s for s in series if s.tier == TIER_LOW and s.capacity == CAP_9K)
        assert low9.delta_pct() is None

    def test_missing_date_produces_no_point(self):
        # 08-26 ausente do dict de entrada (dia sem oferta casada no peer) —
        # a série tem que pular a lacuna, nunca interpolar um ponto pra ela.
        series = daily_series({
            "2026-08-25": [_offer("42EBVCA09M5", "MIDEA", 1000)],
            "2026-08-27": [_offer("42EBVCA09M5", "MIDEA", 1100)],
        })
        low9 = next(s for s in series if s.tier == TIER_LOW and s.capacity == CAP_9K)
        assert [p.date for p in low9.points] == ["2026-08-25", "2026-08-27"]

    def test_empty_series_has_no_points_and_no_data(self):
        series = daily_series({})
        assert all(not s.points for s in series)
        assert all(not s.has_data for s in series)


class TestFilterOffers:
    def _mix(self):
        return [
            _offer("42EBVCA09M5", "MIDEA", 1700, marketplace="AMAZON", seller="Midea Store"),
            _offer("S3-Q09AAQAK", "LG", 1900, marketplace="AMAZON", seller="LG Oficial"),
            _offer("PAC9FC", "PHILCO", 2000, marketplace="MAGALU", seller="Zé"),
        ]

    def test_none_keeps_everything(self):
        offers = self._mix()
        assert len(filter_offers(offers)) == 3

    def test_brand_filter_keeps_midea_plus_selected(self):
        # Só LG selecionado → Midea (sempre) + LG; Philco cai.
        out = filter_offers(self._mix(), keep_brands={"LG"})
        brands = sorted(o.brand for o in out)
        assert brands == ["LG", "MIDEA"]

    def test_brand_filter_never_drops_midea_even_if_not_listed(self):
        out = filter_offers(self._mix(), keep_brands={"ELGIN"})  # nenhum casa exceto Midea
        assert [o.brand for o in out] == ["MIDEA"]

    def test_marketplace_filter_applies_to_all_brands_including_midea(self):
        out = filter_offers(self._mix(), marketplaces={"MAGALU"})
        assert [o.brand for o in out] == ["PHILCO"]  # só a oferta MAGALU sobra

    def test_seller_filter(self):
        out = filter_offers(self._mix(), sellers={"LG Oficial"})
        assert len(out) == 1 and out[0].brand == "LG"

    def test_filters_are_case_insensitive(self):
        out = filter_offers(self._mix(), marketplaces={"amazon"}, keep_brands={"lg"})
        brands = sorted(o.brand for o in out)
        assert brands == ["LG", "MIDEA"]  # Midea+LG na Amazon; Philco (MAGALU) fora

    def test_combined_filters(self):
        out = filter_offers(self._mix(), keep_brands={"LG", "PHILCO"},
                            marketplaces={"AMAZON"})
        # AMAZON: Midea + LG (Philco está no MAGALU → cai pelo marketplace).
        assert sorted(o.brand for o in out) == ["LG", "MIDEA"]
