"""Integração da identidade de oferta: `_build_record` → CSV → Supabase.

O módulo puro é testado em `test_offer_identity.py`. Aqui o alvo é a
**encanação**: adiantou pouco derivar a identidade se ela não chega ao CSV
nem ao banco. Cada passo da cadeia tem um teste próprio porque cada um já
quebrou isolado em algum momento do projeto (ver COMMON_MISTAKES #9).

Rodar:
    pytest tests/test_offer_identity_pipeline.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from main import COLUMN_ORDER  # noqa: E402
from scrapers.base import BaseScraper  # noqa: E402
from utils.supabase_client import _COLUMN_MAP, _OPTIONAL_DEST_COLS  # noqa: E402


IDENTITY_COLUMNS = [
    "ID Produto Marketplace",
    "ID Oferta Marketplace",
    "ID Seller",
    "URL Canônica",
    "Offer Key",
]


class _FakeScraper(BaseScraper):
    """Scraper mínimo — só para exercitar `_build_record` sem subir browser."""

    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name
        self.headless = True
        self._user_agent = "test"
        self.screenshot_manager = None
        self._last_screenshot_busca = None

    def search(self, keyword, keyword_category_map, page_limit=3):  # pragma: no cover
        return []


def _record(platform: str, **kwargs):
    scraper = _FakeScraper(platform)
    base = dict(
        keyword="ar condicionado 12000 btus",
        keyword_category_map={},
        title="Ar Condicionado Midea AI Airvolution 12.000 BTUs Frio",
        position_general=1,
        position_organic=1,
        position_sponsored=None,
    )
    base.update(kwargs)
    return scraper._build_record(**base)


# ── A cadeia: registro → CSV → banco ───────────────────────────────────────
def test_build_record_emits_every_identity_column():
    rec = _record("Amazon", url_produto="https://www.amazon.com.br/x/dp/B0CPTGF6HY")
    for col in IDENTITY_COLUMNS:
        assert col in rec, f"{col} ausente no registro"


def test_identity_columns_are_in_csv_column_order():
    """Sem isto o campo existe no dict e o `df[COLUMN_ORDER]` o descarta em silêncio."""
    for col in IDENTITY_COLUMNS:
        assert col in COLUMN_ORDER, f"{col} fora do COLUMN_ORDER"


def test_identity_columns_are_appended_not_inserted():
    """Colunas novas vão no FIM — planilhas do time leem por posição."""
    assert COLUMN_ORDER[-5:] == IDENTITY_COLUMNS


def test_identity_columns_map_to_supabase():
    for col in IDENTITY_COLUMNS:
        assert col in _COLUMN_MAP, f"{col} sem destino no _COLUMN_MAP"


def test_identity_columns_are_optional_in_supabase():
    """Banco sem a migração 014 precisa degradar, não estourar o upload."""
    for col in IDENTITY_COLUMNS:
        assert _COLUMN_MAP[col] in _OPTIONAL_DEST_COLS, f"{col} não é opcional"


# ── Comportamento por plataforma, ponta a ponta ────────────────────────────
def test_amazon_record_carries_asin():
    rec = _record(
        "Amazon",
        url_produto="https://www.amazon.com.br/x/dp/B0CPTGF6HY/ref=sr_1_93",
        marketplace_product_id="B0CPTGF6HY",
    )
    assert rec["ID Produto Marketplace"] == "B0CPTGF6HY"
    assert rec["URL Canônica"] == "https://www.amazon.com.br/x/dp/B0CPTGF6HY"
    assert rec["Offer Key"] == "v1|AMAZON|prod:B0CPTGF6HY"


def test_casas_bahia_record_splits_product_from_lojista():
    """O caso do 42EFVCA12M5: mesma página, `idLojista` distinguindo a oferta."""
    rec = _record(
        "Casas Bahia",
        url_produto=(
            "https://www.casasbahia.com.br/ar-condicionado-split-inverter-12000-btus-"
            "midea-ai-airvolution-frio-42efvca12m5-220v/p/1582007658?idLojista=19937"
        ),
    )
    assert rec["ID Produto Marketplace"] == "1582007658"
    assert rec["ID Seller"] == "19937"
    assert "idLojista" not in rec["URL Canônica"]
    assert rec["Offer Key"] == "v1|CASASBAHIA|prod:1582007658@19937"


def test_shopee_record_has_a_native_offer_id():
    rec = _record(
        "Shopee",
        url_produto="https://shopee.com.br/product/1207374375/22498850572",
        marketplace_product_id="22498850572",
        marketplace_offer_id="1207374375_22498850572",
        seller_id="1207374375",
    )
    assert rec["ID Oferta Marketplace"] == "1207374375_22498850572"
    assert rec["Offer Key"] == "v1|SHOPEE|offer:1207374375_22498850572"


def test_record_without_url_still_gets_a_key():
    """Coleta degradada não pode produzir registro sem identidade nenhuma."""
    rec = _record("Magalu", url_produto=None)
    assert rec["URL Canônica"] is None
    assert rec["Offer Key"] is not None


def test_same_offer_two_keywords_same_key():
    """A dedup da Fase 5 depende disto: 66,7% das linhas são reobservação."""
    url = "https://www.casasbahia.com.br/x/p/1582007658?idLojista=19937"
    a = _record("Casas Bahia", url_produto=url + "&position=3",
                keyword="ar condicionado")
    b = _record("Casas Bahia", url_produto=url + "&position=11",
                keyword="ar condicionado 12000 btus")
    assert a["Keyword Buscada"] != b["Keyword Buscada"]
    assert a["Offer Key"] == b["Offer Key"]


# ── Não-regressão: nada do que já existia pode ter mudado ──────────────────
LEGACY_COLUMNS = [
    "Data", "Turno", "Horário", "Analista", "Plataforma", "Tipo Plataforma",
    "Keyword Buscada", "Categoria Keyword", "Marca Monitorada", "Produto / SKU",
    "Posição Orgânica", "Posição Patrocinada", "Posição Geral", "Patrocinado?",
    "Buy Box Seller", "Qtd Sellers", "Tipo Seller", "Reputação Seller",
    "Seller / Vendedor", "Fulfillment?", "Avaliação", "Qtd Avaliações",
    "Tag Destaque", "Preço (R$)", "URL Produto", "Screenshot Busca",
    "Screenshot Produto",
]


def test_legacy_columns_keep_their_exact_positions():
    """A Fase 1 é aditiva: nenhuma coluna existente muda de nome ou de lugar."""
    assert COLUMN_ORDER[: len(LEGACY_COLUMNS)] == LEGACY_COLUMNS


def test_build_record_still_works_without_any_identity_argument():
    """Coletor não migrado continua funcionando — os parâmetros são opcionais."""
    rec = _record("Google Shopping")
    assert rec["Plataforma"] == "Google Shopping"
    assert rec["Preço (R$)"] is None
    assert rec["ID Produto Marketplace"] is None


@pytest.mark.parametrize("platform", [
    "Amazon", "Casas Bahia", "Leroy Merlin", "Magalu",
    "Mercado Livre", "Shopee", "Google Shopping",
])
def test_every_platform_builds_a_record_without_raising(platform):
    rec = _record(platform, url_produto="https://exemplo.com.br/x/p/123")
    assert set(IDENTITY_COLUMNS).issubset(rec)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
