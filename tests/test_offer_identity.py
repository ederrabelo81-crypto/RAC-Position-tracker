"""Testes da identidade de oferta (`utils/offer_identity.py`) — Fase 1.

Todas as URLs deste arquivo são **reais**, amostradas da tabela `coletas` em
produção (recorte 2026-08-20+). Regex de identidade escrito contra formato
imaginado é regex que quebra na primeira coleta; aqui cada padrão é ancorado
no que a plataforma de fato devolveu.

Rodar:
    pytest tests/test_offer_identity.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from utils.offer_identity import (  # noqa: E402
    OFFER_KEY_VERSION,
    build_identity,
    build_offer_key,
    canonicalize_url,
    derive_from_url,
)


# ── URLs reais de produção: (plataforma, url, product_id, offer_id, seller_id) ──
REAL_URLS = [
    (
        "Amazon",
        "https://www.amazon.com.br/Condicionado-LG-Inverter-Quente-S4-W36R43FB-EB2GAM1/dp/B0CPTGF6HY/ref=sr_1_93",
        "B0CPTGF6HY", None, None,
    ),
    (
        "Amazon",
        "https://www.amazon.com.br/Ar-Condicionado-Inverter-Gree-9000BTU-GWH09ATBXB-D6DNA3B/dp/B081DJW1HF/ref=sr_1_73",
        "B081DJW1HF", None, None,
    ),
    (
        "Casas Bahia",
        "https://www.casasbahia.com.br/ar-condicionado-split-inverter-12000-btus-midea-ai-airvolution-frio-42efvca12m5-220v/p/1582007658?idLojista=19937",
        "1582007658", None, "19937",
    ),
    (
        "Casas Bahia",
        "https://www.casasbahia.com.br/ar-condicionado-tcl-split-inverter-18000-btus-frio-hi-wall-renovacao-de-ar-freshin-30/p/1580108082",
        "1580108082", None, None,
    ),
    (
        "Leroy Merlin",
        "https://www.leroymerlin.com.br/ar-condicionado-split-inverter-quente-e-frio-12000-btus-com-wi-fi-220v-tpro-2-0-tcl_92311464",
        "92311464", None, None,
    ),
    (
        "Magalu",
        "https://m.magazineluiza.com.br/ar-condicionado-split-lg-dual-inverter-ai-voice-12000-btu-h-frio-s3-q12ja31q-127-volts/p/djc52g533e/ar/aciv/?seller_id=friopecas",
        "DJC52G533E", None, "friopecas",
    ),
    (
        "Mercado Livre",
        "https://www.mercadolivre.com.br/ar-condicionado-daikin-9000-btus-full-inverter-frio-br-branco/p/MLB54211169",
        "MLB54211169", None, None,
    ),
    (
        "Mercado Livre",
        "https://www.mercadolivre.com.br/1un-ar-condicionado-lj-sad1102-profissional-p-telecom-nf/up/MLBU1175327411",
        None, "MLBU1175327411", None,
    ),
    (
        "Shopee",
        "https://shopee.com.br/product/1207374375/22498850572",
        "22498850572", "1207374375_22498850572", "1207374375",
    ),
]


@pytest.mark.parametrize("plataforma,url,prod_id,offer_id,seller_id", REAL_URLS)
def test_derive_from_real_production_urls(plataforma, url, prod_id, offer_id, seller_id):
    got = derive_from_url(url, plataforma)
    assert got["marketplace_product_id"] == prod_id, f"product_id em {plataforma}"
    assert got["marketplace_offer_id"] == offer_id, f"offer_id em {plataforma}"
    assert got["seller_id"] == seller_id, f"seller_id em {plataforma}"


# ── Canonicalização ────────────────────────────────────────────────────────
def test_canonical_strips_amazon_ref_segment():
    """`/ref=sr_1_93` é posição na busca — muda a cada coleta, não é identidade."""
    a = canonicalize_url(
        "https://www.amazon.com.br/x/dp/B0CPTGF6HY/ref=sr_1_93", "Amazon")
    b = canonicalize_url(
        "https://www.amazon.com.br/x/dp/B0CPTGF6HY/ref=sr_1_12", "Amazon")
    assert a == b == "https://www.amazon.com.br/x/dp/B0CPTGF6HY"


def test_canonical_strips_seller_param_but_derive_keeps_it():
    """O lojista sai da URL canônica e vira campo — a página do produto é a mesma.

    Sem isso, a mesma oferta 1P e 3P viraria duas séries distintas só porque a
    vitrine anexou `?idLojista=` numa coleta e não na outra — foi exatamente o
    que a auditoria §3.1 observou no 42EFVCA12M5.
    """
    com = "https://www.casasbahia.com.br/x/p/1582007658?idLojista=19937"
    sem = "https://www.casasbahia.com.br/x/p/1582007658"
    assert canonicalize_url(com, "Casas Bahia") == canonicalize_url(sem, "Casas Bahia")
    assert derive_from_url(com, "Casas Bahia")["seller_id"] == "19937"
    assert derive_from_url(sem, "Casas Bahia")["seller_id"] is None


def test_canonical_normalizes_magalu_mobile_host():
    """m.magazineluiza e www.magazineluiza são a mesma oferta."""
    m = canonicalize_url("https://m.magazineluiza.com.br/x/p/abc123/", "Magalu")
    w = canonicalize_url("https://www.magazineluiza.com.br/x/p/abc123", "Magalu")
    assert m == w


def test_canonical_drops_utm_and_fragment():
    got = canonicalize_url(
        "https://www.casasbahia.com.br/x/p/123?utm_source=x&gclid=y#reviews",
        "Casas Bahia",
    )
    assert got == "https://www.casasbahia.com.br/x/p/123"


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-url", "ftp://x/y", "javascript:void(0)"])
def test_canonical_rejects_non_http(bad):
    assert canonicalize_url(bad, "Amazon") is None


# ── offer_key ──────────────────────────────────────────────────────────────
def test_offer_key_prefers_native_offer_id():
    key = build_offer_key(
        "Shopee",
        marketplace_offer_id="123_456",
        marketplace_product_id="456",
        seller_id="123",
        canonical_url="https://shopee.com.br/product/123/456",
    )
    assert key == f"{OFFER_KEY_VERSION}|SHOPEE|offer:123_456"


def test_offer_key_falls_back_through_the_ladder():
    """Cada degrau só entra quando o de cima falta."""
    assert build_offer_key("Casas Bahia", marketplace_product_id="1", seller_id="9") \
        == f"{OFFER_KEY_VERSION}|CASASBAHIA|prod:1@9"
    assert build_offer_key("Casas Bahia", marketplace_product_id="1") \
        == f"{OFFER_KEY_VERSION}|CASASBAHIA|prod:1"
    url_key = build_offer_key("Casas Bahia", canonical_url="https://x/p/1")
    assert url_key.startswith(f"{OFFER_KEY_VERSION}|CASASBAHIA|url:")
    txt_key = build_offer_key("Casas Bahia", fallback="Ar Condicionado Midea 12k")
    assert txt_key.startswith(f"{OFFER_KEY_VERSION}|CASASBAHIA|txt:")


def test_offer_key_is_none_without_any_signal():
    assert build_offer_key("Amazon") is None
    assert build_offer_key("Amazon", fallback="   ") is None


def test_offer_key_is_versioned():
    """A versão precisa estar na chave — séries de regras diferentes não casam."""
    key = build_offer_key("Amazon", marketplace_product_id="B0CPTGF6HY")
    assert key.startswith("v1|")


def test_offer_key_text_fallback_is_case_and_space_insensitive():
    a = build_offer_key("Magalu", fallback="Ar  Condicionado Midea")
    b = build_offer_key("Magalu", fallback="ar condicionado midea")
    assert a == b


def test_offer_key_separates_platforms():
    """Mesmo id em plataformas diferentes NÃO é a mesma oferta."""
    a = build_offer_key("Amazon", marketplace_product_id="X1")
    b = build_offer_key("Magalu", marketplace_product_id="X1")
    assert a != b


# ── build_identity ─────────────────────────────────────────────────────────
def test_explicit_ids_win_over_url_derivation():
    """O coletor que leu `data-asin` tem dado de primeira mão; a URL é reconstrução."""
    ident = build_identity(
        "Amazon",
        "https://www.amazon.com.br/x/dp/B000000000/ref=sr_1_1",
        marketplace_product_id="B0CPTGF6HY",
    )
    assert ident.marketplace_product_id == "B0CPTGF6HY"


def test_identity_falls_back_to_url_when_collector_passes_nothing():
    ident = build_identity(
        "Casas Bahia",
        "https://www.casasbahia.com.br/x/p/1582007658?idLojista=19937",
    )
    assert ident.marketplace_product_id == "1582007658"
    assert ident.seller_id == "19937"
    assert ident.offer_key == f"{OFFER_KEY_VERSION}|CASASBAHIA|prod:1582007658@19937"


def test_identity_survives_missing_url():
    """Sem URL a identidade não estoura — cai no título e segue."""
    ident = build_identity("Google Shopping", None, title="Ar Condicionado LG 12k")
    assert ident.canonical_url is None
    assert ident.marketplace_product_id is None
    assert ident.offer_key.startswith(f"{OFFER_KEY_VERSION}|GOOGLESHOPPING|txt:")


def test_identity_empty_strings_are_treated_as_absent():
    """String vazia vinda de payload não pode virar id ''."""
    ident = build_identity(
        "Leroy Merlin", "https://www.leroymerlin.com.br/x_92311464",
        marketplace_product_id="  ", seller_id="",
    )
    assert ident.marketplace_product_id == "92311464"  # caiu para a URL
    assert ident.seller_id is None


def test_same_offer_across_keywords_yields_same_key():
    """O mesmo anúncio visto por 2 keywords tem UMA identidade.

    É a base da camada `normalized_offers`: 66,7% das linhas da base são
    reobservação da mesma oferta no mesmo turno (auditoria §5).
    """
    base = "https://www.casasbahia.com.br/x/p/1582007658"
    k1 = build_identity("Casas Bahia", base + "?idLojista=19937&utm_source=busca1")
    k2 = build_identity("Casas Bahia", base + "?idLojista=19937&position=7")
    assert k1.offer_key == k2.offer_key


def test_different_sellers_same_product_are_different_offers():
    """1P e 3P do mesmo produto são ofertas distintas — não podem colidir."""
    a = build_identity("Casas Bahia", "https://www.casasbahia.com.br/x/p/158?idLojista=19937")
    b = build_identity("Casas Bahia", "https://www.casasbahia.com.br/x/p/158?idLojista=14785")
    assert a.offer_key != b.offer_key
    assert a.canonical_url == b.canonical_url  # mesma página de produto


def test_ml_catalog_and_listing_do_not_collide():
    """/p/MLB… (catálogo) e /MLB-… (anúncio) são namespaces diferentes."""
    catalogo = build_identity(
        "Mercado Livre", "https://www.mercadolivre.com.br/x/p/MLB54211169")
    anuncio = build_identity(
        "Mercado Livre", "https://produto.mercadolivre.com.br/MLB-6968369576-ar-cond")
    assert catalogo.marketplace_product_id == "MLB54211169"
    assert catalogo.marketplace_offer_id is None
    assert anuncio.marketplace_offer_id == "MLB6968369576"
    assert catalogo.offer_key != anuncio.offer_key


def test_offer_id_is_never_synthesized():
    """Plataformas sem id de oferta nativo devem deixar o campo VAZIO.

    Um id sintético pareceria autoridade que o dado não tem. Quem precisa de
    chave sempre-presente usa `offer_key`, que é explicitamente derivada.
    """
    for plataforma, url in (
        ("Amazon", "https://www.amazon.com.br/x/dp/B0CPTGF6HY"),
        ("Casas Bahia", "https://www.casasbahia.com.br/x/p/158?idLojista=1"),
        ("Magalu", "https://www.magazineluiza.com.br/x/p/abc123/?seller_id=s"),
        ("Leroy Merlin", "https://www.leroymerlin.com.br/x_92311464"),
    ):
        ident = build_identity(plataforma, url)
        assert ident.marketplace_offer_id is None, plataforma
        assert ident.offer_key is not None, plataforma


def test_google_shopping_help_url_yields_no_false_product_id():
    """A SERP do Google às vezes devolve link de ajuda em vez do produto.

    Observado em 100% das linhas de Google Shopping da semana auditada. O
    módulo não pode inventar um id de produto a partir disso — na pior das
    hipóteses a chave cai no hash da URL, que é honesto.
    """
    ident = build_identity(
        "Google Shopping",
        "https://support.google.com/googleshopping/answer/9128904",
    )
    assert ident.marketplace_product_id is None
    assert ident.marketplace_offer_id is None
    assert ident.offer_key.startswith(f"{OFFER_KEY_VERSION}|GOOGLESHOPPING|url:")


if __name__ == "__main__":  # execução standalone
    raise SystemExit(pytest.main([__file__, "-q"]))
