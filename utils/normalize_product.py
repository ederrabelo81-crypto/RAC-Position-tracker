"""
utils/normalize_product.py — Normalização de nomes de produtos RAC.

Formato padrão:
    Ar Condicionado {Marca} {Linha} {Capacidade BTUs} {Tipo} {Ciclo} [{Forma}] [{Cor}]

Guia de referência: docs/normalizacao_rac_v1.0 (2026-04-13)

Exemplos:
    "Ar Condicionado Split 12.000 Btus Inverter Ai Ecomaster Frio 42EZVCA12M5 Springer Midea"
      → "Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio"

    "Ar Split LG Ai Dual Inverter Voice 9000 Btus Frio Branco"
      → "Ar Condicionado LG Dual Inverter AI Voice 9.000 BTUs Inverter Frio"

    "Ar Condicionado Split Hw Tcl T-pro 2.0 Inverter 12k F 220v"
      → "Ar Condicionado TCL T-Pro 2.0 12.000 BTUs Inverter Frio"

USO:
    from utils.normalize_product import normalize_product_name
    normalized = normalize_product_name(raw_title, raw_brand)
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Brand alias table  raw → normalized
# Ordered longest-first for greedy matching
# ---------------------------------------------------------------------------

_BRAND_ALIASES: dict = {
    'springer midea':  'Midea',
    'midea carrier':   'Midea',
    'springer':        'Midea',
    'midea':           'Midea',
    'lg':              'LG',
    'samsung':         'Samsung',
    'electrolux':      'Electrolux',
    'elgin':           'Elgin',
    'philco':          'Philco',
    'gree':            'Gree',
    'tcl semp':        'TCL',
    'tcl':             'TCL',
    'consul':          'Consul',
    'daikin':          'Daikin',
    'agratto':         'Agratto',
    'hitachi':         'Hitachi',
    'hisense':         'Hisense',
    'aufit':           'Aufit',
    'britânia':        'Britânia',
    'britania':        'Britânia',
    'carrier':         'Carrier',
    'komeco':          'Komeco',
    'eos':             'EOS',
    'hq':              'HQ',
    'haier':           'Haier',
    'fujitsu':         'Fujitsu',
    'rheem':           'Rheem',
    'vix':             'Vix',
    'york':            'York',
}

_SORTED_ALIASES = sorted(_BRAND_ALIASES.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------
# Brand detection patterns applied to product name text
# (used when raw_brand is 'Desconhecida' or missing)
# Order: most specific first
# ---------------------------------------------------------------------------

_BRAND_IN_NAME = [
    (r'springer\s*midea|midea\s*carrier',    'Midea'),
    (r'\bspringer\b',                         'Midea'),
    (r'\bmidea\b',                            'Midea'),
    (r'\blg\b',                               'LG'),
    (r'\bsamsung\b',                          'Samsung'),
    (r'\belectrolux\b',                       'Electrolux'),
    (r'\belgin\b',                            'Elgin'),
    (r'\bphilco\b',                           'Philco'),
    (r'\bgree\b',                             'Gree'),
    (r'\btcl\b',                              'TCL'),
    (r'\bconsul\b',                           'Consul'),
    (r'\bdaikin\b',                           'Daikin'),
    (r'\bagratto\b',                          'Agratto'),
    (r'\bhitachi\b',                          'Hitachi'),
    (r'\bhisense\b',                          'Hisense'),
    (r'\baufit\b',                            'Aufit'),
    (r'brit[aâ]nia',                          'Britânia'),
    (r'\bcarrier\b',                          'Carrier'),
    (r'\bkomeco\b',                           'Komeco'),
    (r'\bhaier\b',                            'Haier'),
    (r'\bfujitsu\b',                          'Fujitsu'),
    (r'\brheem\b',                            'Rheem'),
    # Identification from product line names when brand is 'Desconhecida'
    (r'ecomaster|xtreme\s*save|airvolution',  'Midea'),
    (r'windfree|wind\s*free|wfree',           'Samsung'),
    (r'dual\s*inverter',                      'LG'),
    (r'color\s*adapt|colour\s*adapt',         'Electrolux'),
    (r'eco\s*inverter\s*(iii|ii|3|2)|eco\s*dream', 'Elgin'),
    (r't[\s-]?pro\s*2|freshin\s*3|elite\s*gv', 'TCL'),
    (r'g[\s-]?clima|g[\s-]?top|g[\s-]?diamond', 'Gree'),
    (r'zen\s*top|liv\s*top|fit\s*top',        'Agratto'),
    (r'triple\s*inverter.*?economaxi|economaxi', 'Consul'),
    (r'ecoswing|eco\s*swing|skyair',          'Daikin'),
    (r'prime\s*air',                          'Britânia'),
    (r'xperience|xpower',                     'Carrier'),
]

# ---------------------------------------------------------------------------
# Line detection patterns per brand
# Each entry: (regex, normalized_line_name)
# Order: most specific first — first match wins
# ---------------------------------------------------------------------------

_LINE_PATTERNS: dict = {
    # Each product line is kept DISTINCT — we never collapse an older line
    # (e.g., Xtreme Save Connect) into a newer one (e.g., AI Ecomaster).
    # Preserving line identity is essential for phase-out tracking.
    'Midea': [
        # Most specific first
        (r'ecomaster\s*pro',                                        'AI Ecomaster Pro'),

        # ── Black Edition variants (check before base names) ──
        (r'xtreme\s*save\s*connect.*?black'
         r'|black.*?xtreme\s*save\s*connect',                       'Xtreme Save Connect Black Edition'),
        (r'xtreme\s*save.*?black|black.*?xtreme\s*save',            'Xtreme Save Black Edition'),
        (r'ecomaster.*?black|black.*?ecomaster',                    'AI Ecomaster Black Edition'),

        # ── Ecomaster / Xtreme Save (DISTINCT lines) ──
        (r'xtreme\s*save\s*connect',                                'Xtreme Save Connect'),
        (r'xtreme\s*save',                                          'Xtreme Save'),
        (r'ecomaster(?!\s*pro)',                                    'AI Ecomaster'),

        # ── AirVolution family (each sub-variant DISTINCT) ──
        (r'air\s*volution\s*connect\s*barril'
         r'|airvolution\s*connect\s*barril',                        'AI Airvolution Connect Barril'),
        (r'air\s*volution\s*connect|airvolution\s*connect',         'AI Airvolution Connect'),
        (r'air\s*volution\s*lite|airvolution\s*lite',               'Airvolution Lite'),
        (r'airvolution|air\s*volution',                             'AI Airvolution'),
    ],
    'LG': [
        (r'artcool|art\s*cool',                                     'Dual Inverter ARTCOOL'),
        (r'uv\s*nano',                                              'Dual Inverter UV Nano'),
        (r'\bcompact\b|dual.*?comp(?:act)?|\+ai|\+ia',              'Dual Inverter Compact +AI'),
        (r'voice',                                                  'Dual Inverter AI Voice'),
        (r'\bpower\b',                                              'Dual Inverter Power'),
        # Generic "Dual Inverter" stays generic — do NOT collapse into AI Voice
        (r'dual\s*inverter',                                        'Dual Inverter'),
    ],
    'Samsung': [
        (r'windfree.*?pro|wind\s*free.*?pro',                       'WindFree AI Pro'),
        (r'windfree.*?black|wind\s*free.*?black',                   'WindFree AI Black'),
        (r'windfree.*?connect|wind\s*free.*?connect',               'WindFree Connect AI'),
        (r'windfree|wind\s*free|wfree',                             'WindFree AI'),
        (r'digital\s*inverter\s*ultra|di\s*ultra',                  'Digital Inverter Ultra'),
    ],
    'Electrolux': [
        # Color Adapt is the single active Hi-Wall line (UI/UE = no Wi-Fi variant,
        # YI/YE = Frio, J/JI = Quente/Frio — all Color Adapt per manufacturer)
        (r'color\s*adapt|colour\s*adapt|\bcolor\b'
         r'|yi\d|ye\d|j[i]?\d|ui\d|ue\d',                           'Color Adapt'),
    ],
    'Elgin': [
        (r'eco\s*inverter\s*(?:iii|3)|hjfe',                        'Eco Inverter III'),
        (r'eco\s*inverter\s*(?:ii|2)|hjfc',                         'Eco Inverter II'),
        (r'eco\s*dream',                                            'Eco Dream'),
        (r'eco\s*inverter|eco\s*inv',                               'Eco Inverter'),
    ],
    'TCL': [
        (r'freshin.*?black'
         r'|fresh.*?in.*?3.*?(?:black|pret)'
         r'|fresh.*?in.*?(?:preto|black)',                          'FreshIN 3.0 Black'),
        (r'freshin\s*3|fresh[\s-]in\s*3'
         r'|freshin(?!\s*(?:black|3))'
         r'|fresh[\s-]in(?!\s*(?:black|3|pret))',                   'FreshIN 3.0'),
        (r't[\s-]?pro\s*2|tac.*?ctg2',                             'T-Pro 2.0'),
        (r'elite\s*g2',                                             'Elite G2'),
        (r'elite\s*gv|tac.*?sgv',                                  'Elite GV'),
        # Generic "Elite" is ambiguous — keep it generic rather than collapse to GV
        (r'\belite\b',                                              'Elite'),
        (r'(?:serie|series)\s*a2|\ba2\b',                           'Serie A2'),
        (r'(?:serie|series)\s*a1|tac.*?csa1|convencional',          'Serie A1'),
    ],
    'Gree': [
        (r'g[\s-]?clima',                                           'G-Clima'),
        (r'g[\s-]?top\s*connection',                                'G-Top Connection'),
        (r'g[\s-]?top',                                             'G-Top Auto'),
        (r'g[\s-]?diamond',                                         'G-Diamond'),
        (r'fresh\s*in\s*3|freshin',                                 'FreshIN 3.0'),
    ],
    'Agratto': [
        (r'zen\s*top|zen|zicst',                                    'Zen Top'),
        (r'liv\s*top|liv|lcst',                                     'Liv Top'),
        (r'fit\s*top|fit|ficst',                                    'Fit Top'),
        (r'neo|ics\d',                                              'Neo'),
        (r'one\s*top|\bone\b',                                      'One Top'),
    ],
    'Consul': [
        (r'triple\s*inverter|economaxi|econo\s*maxi',               'Triple Inverter EconoMaxi'),
        (r'janela.*?eletr[oô]nico|ccn',                             'Janela Eletrônico'),
        (r'janela.*?mec[aâ]nico|mec[aâ]nico|manual|ccb',            'Janela Mecânico'),
        (r'janela.*?inverter|inverter.*?janela',                    'Janela Inverter'),
        # Fallback only when it's clearly a Consul Hi-Wall inverter (not janela)
        (r'\bcbk\b|\bcbo\b',                                        'Inverter'),
    ],
    'Daikin': [
        (r'ecoswing|eco\s*swing',                                   'EcoSwing'),
        (r'skyair|sky\s*air',                                       'SkyAir'),
        (r'full',                                                   'Full Inverter'),
    ],
    'Philco': [
        (r'eco\s*inverter|eco\s*inv|pac\d',                         'Eco Inverter'),
        (r'convencional|pas\d',                                     'Convencional'),
    ],
    'Carrier': [
        (r'xperience',                                              'Xperience'),
        (r'xpower|x\s*power',                                       'XPower Connect'),
        # NOTE: generic Carrier cassete is NOT auto-mapped to "Connect" —
        # multiple cassete lines exist; leave line blank when unknown
    ],
    # Hitachi & Aufit currently market a single residential line each;
    # any older variants will surface as fallbacks (line left blank)
    'Hitachi':   [(r'\binverter\b',                                 'Inverter')],
    'Aufit':     [(r'\binverter\b',                                 'Inverter')],
    'Hisense': [
        (r'eco\s*plus|ecoplus',                                     'Eco Plus'),
        (r'connect',                                                'Connect'),
        # No fallback — unknown Hisense models keep blank line
    ],
    'Britânia': [
        (r'prime\s*air',                                            'Prime Air'),
    ],
    'EOS': [
        (r'master\s*comfort',                                       'Master Comfort'),
        (r'master',                                                 'Master Inverter'),
    ],
    'Komeco': [
        (r'eco\+|eco\s*plus',                                       'Eco+'),
    ],
}

# ---------------------------------------------------------------------------
# BTU normalization map
# ---------------------------------------------------------------------------

_BTU_NORMALIZED: dict = {
    v: f"{v:,.0f} BTUs".replace(",", ".")
    for v in [
        7000, 7500, 9000, 9500, 10000, 11000, 12000, 18000,
        22000, 24000, 28000, 30000, 32000, 34000, 36000,
        48000, 55000, 57000, 60000, 70000,
    ]
}


def _format_btus(value: int) -> str:
    return _BTU_NORMALIZED.get(value, f"{value:,.0f} BTUs".replace(",", "."))


def _extract_btus(text: str) -> Optional[str]:
    """Extract and normalize BTU value from raw product text."""
    # Normalize: remove thousands separator and lowercase
    t = text.lower()
    # Handle "18 000" (space as thousands separator)
    t = re.sub(r'(\d{2,3})\s(\d{3})\b', r'\1\2', t)
    # Remove dots used as thousands separators (e.g. "12.000")
    t_nodot = re.sub(r'(\d+)\.(\d{3})\b', r'\1\2', t)

    # Pattern 1: explicit BTU/BTUs/BTU-H suffix
    m = re.search(r'(\d{4,6})\s*(?:btus?|btu[/\-]?h)', t_nodot)
    if m:
        val = int(m.group(1))
        if 7000 <= val <= 70000:
            return _format_btus(val)

    # Pattern 2: "12k", "9K", "18 k"
    m = re.search(r'(\d{1,3})\s*k\b', t_nodot)
    if m:
        val = int(m.group(1)) * 1000
        if 7000 <= val <= 70000:
            return _format_btus(val)

    # Pattern 3: bare 4-6 digit number adjacent to AC context
    m = re.search(r'(\d{4,6})\s*(?:frio|quente|inverter|split|btu)', t_nodot)
    if m:
        val = int(m.group(1))
        if 7000 <= val <= 70000:
            return _format_btus(val)

    # Pattern 4: standalone 4-6 digit number that looks like BTUs
    for m in re.finditer(r'\b(\d{4,6})\b', t_nodot):
        val = int(m.group(1))
        if val in _BTU_NORMALIZED:
            return _format_btus(val)

    return None


def _identify_brand(raw_name: str, raw_brand: Optional[str]) -> Optional[str]:
    """Return normalized brand. Uses raw_brand first, then searches name."""
    if raw_brand and raw_brand.lower() not in ('desconhecida', 'unknown', '', 'none'):
        key = raw_brand.lower().strip()
        for alias in _SORTED_ALIASES:
            if key == alias:
                return _BRAND_ALIASES[alias]
        # raw_brand might already be normalized (e.g. "Midea" → "midea" → found)

    # Fallback: search within product name
    name_lower = raw_name.lower()
    for pattern, brand in _BRAND_IN_NAME:
        if re.search(pattern, name_lower):
            return brand

    return None


def _identify_line(name_lower: str, brand: str) -> Optional[str]:
    """Return normalized product line for the brand, or None."""
    for pattern, line in _LINE_PATTERNS.get(brand, []):
        if re.search(pattern, name_lower):
            return line
    return None


def _identify_type(name_lower: str, line: Optional[str]) -> str:
    """Return 'Inverter' or 'On/Off'."""
    _ON_OFF_LINES = {
        'Serie A1', 'One Top', 'Janela Mecânico',
        'Janela Eletrônico', 'Convencional', 'Eco Dream',
    }
    if line in _ON_OFF_LINES:
        return 'On/Off'

    _ON_OFF_RE = [
        r'\bon[\s/\-]?off\b', r'\bfixo\b', r'\bconvencional\b',
        r'só\s*frio.*?(?:sem\s*inverter|on.?off)',
    ]
    if any(re.search(p, name_lower) for p in _ON_OFF_RE):
        return 'On/Off'

    return 'Inverter'


def _identify_cycle(name_lower: str) -> str:
    """Return 'Quente/Frio' or 'Frio' (default)."""
    _QF = [
        r'quente[/\s\-]?frio', r'\bq[/\s]?f\b', r'\bqef\b',
        r'\bhp\b', r'quente\s*e\s*frio',
    ]
    if any(re.search(p, name_lower) for p in _QF):
        return 'Quente/Frio'
    return 'Frio'


def _identify_form(name_lower: str) -> Optional[str]:
    """Return installation form, or None when Hi-Wall (default — omit)."""
    _FORMS = [
        (r'piso[- ]?teto|piso\s*teto|\bpt\b|\bteto\b',         'Piso-Teto'),
        (r'\bcassete\b|\bcassette\b',                           'Cassete'),
        (r'\bjanela\b|\bde\s*janela\b|\bwindow\b',              'Janela'),
        (r'port[áa]til\b|portable\b',                           'Portátil'),
        (r'multi[- ]?split\b|multisplit\b',                     'Multi Split'),
    ]
    for pattern, form in _FORMS:
        if re.search(pattern, name_lower):
            return form
    return None


def _identify_color(name_lower: str, line: Optional[str]) -> Optional[str]:
    """Return color only when not white (white is the implicit default)."""
    # Lines with color already in the name — avoid duplicating
    if line and 'Black Edition' in line:
        return None
    if line in ('WindFree AI Black', 'FreshIN 3.0 Black'):
        return None

    _COLORS = [
        (r'\bblack\b|\bpreto\b',   'Preto'),
        (r'black\s*edition',       'Preto'),
    ]
    for pattern, color in _COLORS:
        if re.search(pattern, name_lower):
            return color
    return None


def normalize_product_name(
    raw_name: Optional[str],
    raw_brand: Optional[str] = None,
) -> Optional[str]:
    """
    Normalize an AC product name to the canonical RAC format:
        Ar Condicionado {Marca} {Linha} {Capacidade BTUs} {Tipo} {Ciclo}
        [{Forma}] [{Cor}]

    Graceful fallback: returns raw_name unchanged if brand or BTUs cannot
    be identified, so no data is ever lost.

    Args:
        raw_name:  Raw product title from the scraper.
        raw_brand: Brand already extracted by extract_brand() — may be
                   'Desconhecida'. Optional.

    Returns:
        Normalized name string, or raw_name if normalization is not possible.
    """
    if not raw_name:
        return raw_name

    name_lower = raw_name.lower()

    # 1. Brand
    brand = _identify_brand(raw_name, raw_brand)
    if not brand:
        return raw_name  # Keep original — unknown brand

    # 2. BTUs
    btus = _extract_btus(raw_name)
    if not btus:
        return raw_name  # Keep original — BTUs not found

    # 3. Line
    line = _identify_line(name_lower, brand)

    # 4. Type
    ac_type = _identify_type(name_lower, line)

    # 5. Cycle
    cycle = _identify_cycle(name_lower)

    # 6. Form (omit Hi-Wall — it's the default)
    form = _identify_form(name_lower)

    # 7. Color (omit white — it's the default)
    color = _identify_color(name_lower, line)

    # 8. Assemble
    parts = ['Ar Condicionado', brand]
    if line:
        parts.append(line)
    parts += [btus, ac_type, cycle]
    if form:
        parts.append(form)
    if color:
        parts.append(color)

    return ' '.join(parts)
