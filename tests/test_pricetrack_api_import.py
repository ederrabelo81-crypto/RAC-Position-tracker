"""
tests/test_pricetrack_api_import.py — agregação do import diário da API PriceTrack.

Cobre o roadmap docs/PRICETRACK_INSIGHTS.md §3 item 9: ofertas sem `sku`
não reconciliam com o catálogo (join PT × coletas) e agora são rejeitadas
no `aggregate_offers`, com breakdown em `rejections` que alimenta o
rejection_log de `pricetrack_import_log`.

Rode: pytest tests/test_pricetrack_api_import.py
"""
import importlib.util
from datetime import date, timedelta
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
        # Sem `collection_hour` as ofertas entram só no agregado Diário.
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["turno"] == "Diário"
        assert row["min_price"] == 1800.0
        assert row["max_price"] == 2200.0
        assert row["avg_price"] == 2000.0
        assert row["collection_date"] == "2026-06-10"
        assert row["source_file"] == "api-2026-06-10"


class TestAggregateOffersTurno:
    """Recorte por turno via `collection_hour` (roadmap PRICETRACK_INSIGHTS §3 #10)."""

    def test_split_diario_manha_tarde(self):
        df = pd.DataFrame([
            _offer(price=1000.0, collection_hour=9),    # Manhã (08–12)
            _offer(price=1200.0, collection_hour=10),   # Manhã
            _offer(price=900.0,  collection_hour=20),   # Tarde (18–22)
            _offer(price=950.0,  collection_hour=15),   # fora das janelas → só Diário
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        by_turno = {t: g.iloc[0] for t, g in agg.groupby("turno")}

        assert set(by_turno) == {"Diário", "Manhã", "Tarde"}
        # Diário = dia inteiro
        assert by_turno["Diário"]["min_price"] == 900.0
        assert by_turno["Diário"]["max_price"] == 1200.0
        # Manhã = horas 9 e 10
        assert by_turno["Manhã"]["min_price"] == 1000.0
        assert by_turno["Manhã"]["max_price"] == 1200.0
        # Tarde = hora 20
        assert by_turno["Tarde"]["min_price"] == 900.0
        assert by_turno["Tarde"]["max_price"] == 900.0

    def test_sem_hora_apenas_diario(self):
        df = pd.DataFrame([_offer(price=1500.0)])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        assert list(agg["turno"].unique()) == ["Diário"]

    def test_apenas_manha_nao_cria_tarde(self):
        df = pd.DataFrame([
            _offer(price=1100.0, collection_hour=8),
            _offer(price=1300.0, collection_hour=12),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        assert set(agg["turno"].unique()) == {"Diário", "Manhã"}


class TestShouldRedownload:
    """Re-download dos 2 dias voláteis (hoje/ontem); cache para dias antigos."""

    _TODAY = date(2026, 6, 21)

    def test_arquivo_inexistente_sempre_baixa(self):
        # Mesmo um dia antigo precisa baixar se o arquivo não existe localmente.
        assert ptai._should_redownload(
            "2026-01-01", file_exists=False, today=self._TODAY
        ) is True

    def test_hoje_com_cache_rebaixa(self):
        # Export do dia corrente ainda cresce → ignora cache parcial.
        assert ptai._should_redownload(
            "2026-06-21", file_exists=True, today=self._TODAY
        ) is True

    def test_ontem_com_cache_rebaixa(self):
        # D-1 pode ter sido importado provisoriamente intra-dia → re-baixa.
        assert ptai._should_redownload(
            "2026-06-20", file_exists=True, today=self._TODAY
        ) is True

    def test_dia_antigo_com_cache_reaproveita(self):
        # Anteontem para trás é imutável no PriceTrack → usa o cache.
        assert ptai._should_redownload(
            "2026-06-19", file_exists=True, today=self._TODAY
        ) is False

    def test_default_today_usa_hoje_real(self):
        # Sem injetar `today`, ontem real ainda deve re-baixar.
        ontem = (date.today() - timedelta(days=1)).isoformat()
        assert ptai._should_redownload(ontem, file_exists=True) is True

    def test_data_invalida_com_cache_reaproveita(self):
        # Data malformada não derruba o import: cai no cache existente.
        assert ptai._should_redownload(
            "nao-e-data", file_exists=True, today=self._TODAY
        ) is False
