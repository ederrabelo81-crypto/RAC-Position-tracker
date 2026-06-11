"""
tests/test_pricetrack_api_import.py — agregação do import diário da API PriceTrack.

Cobre o roadmap docs/PRICETRACK_INSIGHTS.md §3 item 9: ofertas sem `sku`
não reconciliam com o catálogo (join PT × coletas) e agora são rejeitadas
no `aggregate_offers`, com breakdown em `rejections` que alimenta o
rejection_log de `pricetrack_import_log`.

Rode: pytest tests/test_pricetrack_api_import.py
"""
import importlib.util
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "pricetrack_api_import",
    Path(__file__).resolve().parent.parent / "scripts" / "pricetrack_api_import.py",
)
ptai = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ptai)


def _offer(sku: str = "42MACA09S5", price=1999.90, **overrides) -> dict:
    """Oferta NDJSON mínima no formato do export (snake_case)."""
    base = {
        "category": "AR CONDICIONADO",
        "brand": "MIDEA",
        "sku": sku,
        "product_name": "Ar Condicionado Split Midea 9000 Btus Frio",
        "marketplace": "MERCADO LIVRE",
        "seller": "WEBCONTINENTAL",
        "spot_price": price,
    }
    base.update(overrides)
    return base


class TestAggregateOffersMissingSku:
    def test_rejeita_e_contabiliza_sku_vazio(self):
        df = pd.DataFrame([
            _offer(),
            _offer(sku=""),
            _offer(sku="   "),       # _pick_text normaliza p/ "" → rejeita
            _offer(price=None),      # sem preço — rejeição independente
        ])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections.get("MISSING_SKU") == 2
        assert rejections.get("NO_PRICE") == 1
        assert list(agg["sku"].unique()) == ["42MACA09S5"]

    def test_todas_sem_sku_retorna_vazio(self):
        df = pd.DataFrame([_offer(sku=""), _offer(sku="")])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert agg.empty
        assert rejections.get("MISSING_SKU") == 2

    def test_fora_de_categoria_contabilizada(self):
        df = pd.DataFrame([_offer(), _offer(category="GELADEIRA")])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections.get("OUT_OF_CATEGORY") == 1
        assert len(agg) == 1

    def test_sem_rejeicoes_dict_vazio(self):
        df = pd.DataFrame([_offer()])
        _, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections == {}


class TestAggregateOffersAgrega:
    def test_min_avg_max_por_grupo(self):
        df = pd.DataFrame([
            _offer(price=1800.0),
            _offer(price=2200.0),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-10")
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["min_price"] == 1800.0
        assert row["max_price"] == 2200.0
        assert row["avg_price"] == 2000.0
        assert row["collection_date"] == "2026-06-10"
        assert row["source_file"] == "api-2026-06-10"
