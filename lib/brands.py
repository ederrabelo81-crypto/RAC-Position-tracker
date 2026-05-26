"""Brand and platform alias expansion / normalization."""


# ---------------------------------------------------------------------------
# Platform name normalization
#
# The DB may contain typos or variations of platform names (e.g.,
# "FerreiraCosta" and "FerreiraCoasta"). We normalize these to a single
# canonical name for display in filters, then expand back when querying.
# ---------------------------------------------------------------------------

_PLATFORM_ALIASES = {
    # Canonical display name -> all raw DB values that map to it
    "Ferreira Costa": ["FerreiraCosta", "FerreiraCoasta"],
    "WebContinental":  ["WebContinental", "Webcontinental"],
}

# Build reverse map: variation -> canonical
_VARIATION_TO_CANONICAL = {}
for canonical, variations in _PLATFORM_ALIASES.items():
    for var in variations:
        _VARIATION_TO_CANONICAL[var] = canonical


def _normalize_platform(platform: str) -> str:
    """Normalize a raw platform name from DB to its canonical form."""
    return _VARIATION_TO_CANONICAL.get(platform, platform)


def _expand_platforms(platforms: list[str]) -> list[str]:
    """
    Given a list of canonical platform names (what the user selected),
    return the full set of raw DB platform values to include in the query.
    E.g. ["Ferreira Costa"] -> ["FerreiraCosta", "FerreiraCoasta"]
    """
    expanded = set()
    for p in platforms:
        variants = _PLATFORM_ALIASES.get(p, [p])
        expanded.update(variants)
    return sorted(expanded)


# ---------------------------------------------------------------------------
# Brand alias expansion
#
# extract_brand() uses config.BRANDS verbatim, so the DB can contain:
#   "Springer Midea", "Midea Carrier", "Springer", "Midea" (all = Midea canonical)
#   "Britania" and "Britânia" (same brand, two spellings in BRANDS)
#
# We build two dicts from config.BRANDS + normalize_product._BRAND_ALIASES:
#   _MARCA_TO_CANONICAL: raw DB value  → canonical display name
#   _CANONICAL_TO_MARCAS: canonical   → [all raw DB values to query]
# ---------------------------------------------------------------------------

def _build_brand_maps() -> tuple:
    """Build brand normalization maps from config + normalize_product."""
    try:
        from utils.normalize_product import _BRAND_ALIASES
        from config import BRANDS
    except Exception:
        return {}, {}

    canonical_to_raws: dict = {}
    raw_to_canonical:  dict = {}

    for raw_brand in BRANDS:
        # _BRAND_ALIASES uses lowercase keys
        canonical = _BRAND_ALIASES.get(raw_brand.lower(), raw_brand)
        canonical_to_raws.setdefault(canonical, []).append(raw_brand)
        raw_to_canonical[raw_brand] = canonical

    return canonical_to_raws, raw_to_canonical


_CANONICAL_TO_MARCAS, _MARCA_TO_CANONICAL = _build_brand_maps()


def _expand_brands(brands: list) -> list:
    """
    Given a list of canonical brand names (what the user selected),
    return the full set of raw DB marca values to include in the query.
    E.g. ["Midea"] → ["Midea", "Springer Midea", "Midea Carrier", "Springer"]
    """
    expanded = set()
    for b in brands:
        variants = _CANONICAL_TO_MARCAS.get(b)
        if variants:
            expanded.update(variants)
        else:
            expanded.add(b)  # unknown brand — use as-is
    return sorted(expanded)
