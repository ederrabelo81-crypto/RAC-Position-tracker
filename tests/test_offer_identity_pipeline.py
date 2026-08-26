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

import csv  # noqa: E402

import pytest  # noqa: E402

from main import COLUMN_ORDER, _dump_raw_records, _export_csv  # noqa: E402
from scrapers.base import BaseScraper  # noqa: E402
from utils.supabase_client import (  # noqa: E402
    _COLUMN_MAP,
    _OPTIONAL_DEST_COLS,
    _map_record,
)


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
    """A dedup da Fase 5 depende disto: 82,8% das linhas são reobservação."""
    url = "https://www.casasbahia.com.br/x/p/1582007658?idLojista=19937"
    a = _record("Casas Bahia", url_produto=url + "&position=3",
                keyword="ar condicionado")
    b = _record("Casas Bahia", url_produto=url + "&position=11",
                keyword="ar condicionado 12000 btus")
    assert a["Keyword Buscada"] != b["Keyword Buscada"]
    assert a["Offer Key"] == b["Offer Key"]


# ── A cadeia de verdade: grava o CSV e lê de volta ─────────────────────────
# Os testes acima checam pertinência estática (a coluna está no COLUMN_ORDER,
# o destino está no _COLUMN_MAP). Isso não exercita `df[COLUMN_ORDER]` nem o
# `to_csv` — e as duas falhas que o docstring deste módulo cita moram
# exatamente aí. Estes testes fecham a lacuna.

def _csv_roundtrip(records, tmp_path):
    """Grava pelo exportador REAL e devolve as linhas lidas de volta."""
    caminho = _export_csv(records, str(tmp_path))
    with open(caminho, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def test_csv_roundtrip_preserva_identidade(tmp_path):
    """Ponta a ponta: _build_record → DataFrame → CSV → leitura."""
    rec = _record(
        "Casas Bahia",
        url_produto=(
            "https://www.casasbahia.com.br/ar-cond-42efvca12m5/p/1582007658"
            "?idLojista=19937&utm_source=busca"
        ),
        price_float=2392.0,
    )
    linha = _csv_roundtrip([rec], tmp_path)[0]

    assert linha["ID Produto Marketplace"] == "1582007658"
    assert linha["ID Seller"] == "19937"
    assert linha["Offer Key"] == "v1|CASASBAHIA|prod:1582007658@19937"
    assert linha["URL Canônica"] == (
        "https://www.casasbahia.com.br/ar-cond-42efvca12m5/p/1582007658"
    )
    # o que já existia continua no lugar
    assert linha["Preço (R$)"] == "2392.0"
    assert linha["Plataforma"] == "Casas Bahia"


def test_csv_roundtrip_todas_as_plataformas(tmp_path):
    """As 7 plataformas atravessam o exportador sem perder a chave."""
    casos = [
        ("Amazon", "https://www.amazon.com.br/x/dp/B0CPTGF6HY/ref=sr_1_93"),
        ("Casas Bahia", "https://www.casasbahia.com.br/x/p/1582007658"),
        ("Leroy Merlin", "https://www.leroymerlin.com.br/ar-cond-tcl_92311464"),
        ("Magalu", "https://m.magazineluiza.com.br/x/p/djc52g533e/ar/aciv/?seller_id=friopecas"),
        ("Mercado Livre", "https://www.mercadolivre.com.br/x/p/MLB54211169"),
        ("Shopee", "https://shopee.com.br/product/1207374375/22498850572"),
        ("Google Shopping", "https://www.google.com/shopping/product/123"),
    ]
    linhas = _csv_roundtrip(
        [_record(p, url_produto=u) for p, u in casos], tmp_path
    )
    assert len(linhas) == len(casos)
    for linha in linhas:
        assert linha["Offer Key"], f"{linha['Plataforma']} sem Offer Key no CSV"


def test_csv_nao_perde_coluna_na_reordenacao(tmp_path):
    """`df = df[COLUMN_ORDER]` descarta em silêncio o que não está na lista."""
    rec = _record("Shopee", url_produto="https://shopee.com.br/product/1/2",
                  marketplace_offer_id="1_2", seller_id="1")
    linha = _csv_roundtrip([rec], tmp_path)[0]
    for col in IDENTITY_COLUMNS:
        assert col in linha, f"{col} sumiu na escrita do CSV"


def test_dump_raw_preserva_identidade(tmp_path):
    """O dump de emergência (rede de segurança do run) também leva a identidade."""
    rec = _record("Amazon", url_produto="https://www.amazon.com.br/x/dp/B0CPTGF6HY")
    caminho = _dump_raw_records([rec], str(tmp_path), "run-de-teste")
    with open(caminho, encoding="utf-8-sig", newline="") as fh:
        linha = list(csv.DictReader(fh, delimiter=";"))[0]
    assert linha["Offer Key"] == "v1|AMAZON|prod:B0CPTGF6HY"


def test_supabase_row_carrega_as_colunas_de_identidade():
    """A conversão para o formato do banco leva os 5 campos com os nomes certos."""
    rec = _record(
        "Casas Bahia",
        url_produto="https://www.casasbahia.com.br/x/p/1582007658?idLojista=19937",
    )
    row = _map_record(rec)
    assert row["marketplace_product_id"] == "1582007658"
    assert row["seller_id"] == "19937"
    assert row["offer_key"] == "v1|CASASBAHIA|prod:1582007658@19937"
    assert row["canonical_url"].endswith("/p/1582007658")


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


# ── `_first_present`: o que conta como id presente ─────────────────────────
# Extraído durante a review da Fase 1 porque o padrão `a or b` descartava o
# zero. O bool foi o passo seguinte: `str(False)` é "False", que passaria por
# id. O guarda precisa vir ANTES de qualquer teste numérico — em Python
# `isinstance(True, int)` é verdadeiro.

@pytest.mark.parametrize("valores,esperado,motivo", [
    ((0, "REF-1"), "0", "zero é um id presente, não ausência"),
    ((False, "REF-1"), "REF-1", "bool nunca é id — cai para o próximo"),
    ((True, "REF-1"), "REF-1", "idem para True"),
    ((False, None), None, "só bool disponível → ausente"),
    ((None, "REF-1"), "REF-1", "None cai para o próximo"),
    (("", "REF-1"), "REF-1", "string vazia é ausência"),
    (("   ", "REF-1"), "REF-1", "só espaços é ausência"),
    ((None, None), None, "nada presente"),
    ((1582007658,), "1582007658", "int vira string"),
    (("  1582007658 ",), "1582007658", "espaços das bordas são aparados"),
])
def test_first_present(valores, esperado, motivo):
    from scrapers.casas_bahia import _first_present
    assert _first_present(*valores) == esperado, motivo
