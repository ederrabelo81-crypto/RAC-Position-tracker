"""
Nomes canônicos de seller — fonte única de verdade do RAC Position Tracker.

Problema (Ago/2026): o mesmo lojista aparece na coleta com N grafias porque
cada marketplace impõe um formato de apelido diferente. O Mercado Livre usa
nickname colado e minúsculo com sufixo numérico quando o nome já existe
(`friopecas`, `frigelar2`, `leveros3`); a Amazon e a Casas Bahia usam a razão
comercial com acento e sufixo de marca (`Friopeças`, `Belmicro Oficial`); a
Magalu grava o slug da loja (`lojawebcontinentalmarketplace`). O resultado é
que o mesmo dealer se fragmenta em várias linhas no share de buy box —
Web Continental aparecia como 5 sellers distintos somando 12,3% enquanto o
maior pedaço isolado marcava 7,1%, e o ranking mentia sobre quem lidera.

Regra dura: o nome canônico é sempre uma grafia OBSERVADA na coleta ou o
`nome` já padronizado em `bestsellers/config.py`. Nome canônico inventado
parece autoridade que o dado não tem — e some do de-para na primeira
conferência manual contra a tela do marketplace.

Segunda regra dura: variante só entra no mapa com identidade CONFIRMADA. Um
apelido opaco (`mgshopgra`) fica como está até alguém abrir a loja no
marketplace; agrupar por semelhança de string transferiria buy box de um
seller para outro, que é pior que a fragmentação que este módulo resolve.
`GoCompras` era o exemplo canônico disso até 05/09/2026, quando o mantenedor
confirmou a identidade (grupo "Denteck") — ver comentário no mapa abaixo.

Uso:
    from utils.seller_names import normalize_seller_name
    normalize_seller_name("continentalcenter")   # -> "Web Continental"
    normalize_seller_name("Loja Nova Ltda")      # -> "Loja Nova Ltda" (passa)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

__all__ = [
    "SELLER_GROUPS",
    "normalize_seller_name",
    "strip_comparador_suffix",
    "is_brand_byline",
    "seller_key",
    "variants_for",
    "canonical_names",
]


# ---------------------------------------------------------------------------
# Ruído de captura — texto que veio no lugar do seller e NÃO é um lojista
# ---------------------------------------------------------------------------
# Byline de MARCA da Amazon ("Visite a loja PHILCO", "Marca: TCL"): é o link da
# loja do fabricante no topo do PDP, não o vendedor da buy box. Chegou à base
# porque o fallback do PDP lia `#bylineInfo` quando o bloco de compra não
# carregava (conferido em 30/09/2026: 21 grafias, ~560 linhas em 2 dias).
# Tratar como seller transferiria buy box para a marca — o vendedor real
# simplesmente não foi observado, e o campo fica vazio.
_RE_BYLINE_MARCA = re.compile(r"^(?:visite\s+a\s+loja|marca\s*:)", re.IGNORECASE)

# Sufixo "e mais" do Google Shopping: o card mostra "<loja> e mais" quando há
# outras lojas ofertando o mesmo produto, em dois <span> que o
# `get_text(strip=True)` colava sem espaço — "Magalue mais", "Frigelare mais".
# Conferido em 30/09/2026: 82% das linhas do Google Shopping (131 grafias) e
# ZERO fora dele. Minúsculo de propósito: "Compre Mais" (loja real, "M" maiúsculo)
# não casa. Um seller minúsculo terminado em " e mais" fora do comparador seria
# cortado — por isso a função é separada de `normalize_seller_name` e quem a
# chama decide pelo contexto (plataforma = comparador).
_RE_COMPARADOR_E_MAIS = re.compile(r"\s*e mais$")


def is_brand_byline(raw: Optional[str]) -> bool:
    """True quando o texto é o byline de marca da Amazon, não um vendedor.

    Args:
        raw: texto capturado no lugar do seller.

    Returns:
        True para "Visite a loja X" / "Marca: X".

    Example:
        >>> is_brand_byline("Visite a loja PHILCO")
        True
        >>> is_brand_byline("Loja Electrolux")
        False
    """
    if not raw:
        return False
    return bool(_RE_BYLINE_MARCA.match(" ".join(str(raw).split())))


def strip_comparador_suffix(raw: Optional[str]) -> Optional[str]:
    """Remove o sufixo "e mais" que o Google Shopping cola no nome da loja.

    Args:
        raw: seller como veio do card do comparador.

    Returns:
        O nome sem o sufixo; None se não sobrar nada.

    Example:
        >>> strip_comparador_suffix("Magalue mais")
        'Magalu'
        >>> strip_comparador_suffix("Compre Mais")
        'Compre Mais'
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    text = _RE_COMPARADOR_E_MAIS.sub("", text).strip()
    return text or None


# ---------------------------------------------------------------------------
# Grupos canônicos — {nome canônico: [grafias observadas na coleta]}
# ---------------------------------------------------------------------------
# A chave de comparação ignora caixa, acento e pontuação (ver `seller_key`),
# então NÃO é preciso listar "Dufrio"/"dufrio"/"DUFRIO" separadamente: basta
# uma grafia por STEM diferente. As variantes abaixo são as que mudam o stem
# (prefixo `loja`, sufixo de filial/fulfillment, nome comercial alternativo).
SELLER_GROUPS: Dict[str, List[str]] = {
    # ── Dealers de climatização ─────────────────────────────────────────────
    "Clima Rio": [
        "Clima Rio", "ClimaRio", "Climario",
        # Nome da loja na Shopee — mesma loja, identidade confirmada pelo
        # mantenedor em 08/10/2026 (aparecia como seller próprio desde
        # 27/09/2026, fatiando a caixa da Clima Rio na Shopee).
        "Clima Rio - Ar Condicionado",
    ],
    "Frio Peças": [
        "Frio Peças", "Friopeças", "FrioPecas",
    ],
    "Central Ar": [
        "Central Ar", "Centralar", "Centralar.com", "CentralAr",
    ],
    "Web Continental": [
        "Web Continental", "Webcontinental",
        # Filial ES e a loja de marketplace são CNPJs/contas diferentes do
        # mesmo grupo — o mantenedor confirmou o agrupamento em 27/08/2026.
        "Webcontinental ES", "Webcontinental_ES",
        "Webcontinental Marketplace", "lojawebcontinentalmarketplace",
        # ContinentalCenter é a segunda conta do grupo no Mercado Livre.
        "ContinentalCenter",
        # Varredura de 08/10/2026 (identidade confirmada pelo mantenedor):
        # loja da Shopee. "Webco Prime" NÃO entra: identidade não confirmada.
        "Webcontinental Climatização",
    ],
    "Engage Eletro": [
        "Engage Eletro", "EngageEletro",
        # sufixo `ful` = conta de fulfillment do ML (mesmo lojista)
        "engageeletroful",
    ],
    "Dufrio": [
        "Dufrio", "Dufrio Refrigeração",
    ],
    "Frigelar": [
        # o "2" é o desambiguador que o ML anexa quando o nickname já existe
        "Frigelar", "frigelar2",
    ],
    "Bel Micro": [
        "Bel Micro", "Belmicro", "Belmicro Oficial",
        # Comprebel é a mesma loja sob outra conta/apelido de marketplace —
        # identidade confirmada pelo mantenedor em 05/09/2026 (antes vivia
        # como grupo canônico próprio "Comprebel", fragmentando a caixa).
        "Comprebel", "comprebel2",
        # Varredura de 08/10/2026 (identidade confirmada pelo mantenedor):
        # conta da Amazon e domínio que o comparador mostra.
        "Belmicro.Shop", "belmicro.com.br",
    ],
    "Denteck": [
        "Denteck", "Denteck Ar Condicionado",
        # Go Compras é a mesma loja sob outro apelido de marketplace —
        # identidade confirmada pelo mantenedor em 05/09/2026 (era o exemplo
        # de "apelido opaco" citado no docstring deste módulo). As três
        # grafias abaixo são STEMS diferentes (espaço e ® não são só caixa),
        # então as três precisam estar listadas: `app.py::_expand_sellers`
        # só expande .lower()/.upper() de cada entrada explícita — ele NÃO
        # remove espaço/símbolo como `seller_key()` faz. Sem isto, o filtro
        # do dashboard por "Denteck" perderia ~98% do histórico (14.638 das
        # 14.902 linhas observadas são "GoCompras"/"GoCompras®", só 264 são
        # "Go Compras" com espaço).
        "Go Compras", "GoCompras", "GoCompras®",
    ],
    "Leveros": [
        "Leveros", "leveros3",
    ],
    "Ar Certo": [
        "Ar Certo", "ArCerto", "ar-certo",
    ],
    "Polo Ar": [
        "Polo Ar", "PoloAr", "POLO AR INDUSTRIA LTDA",
    ],
    "Refricril": [
        # Até 08/10/2026 o canônico era "Refricril Refrigeração" e a conta da
        # Amazon ("Refricril Ar Condicionados") ficava fora — o mesmo lojista
        # aparecia como 3 sellers. Unificado a pedido do mantenedor.
        "Refricril", "Refricril Refrigeração", "refricrilrefrigeracaoepecas",
        "Refricril Ar Condicionados",
    ],
    "Norte Refrigeração": [
        "Norte Refrigeração", "NorteRefrigeracao",
        "norterefrigeracaolojaoficial",
    ],
    "Ferreira Costa": [
        "Ferreira Costa", "FerreiraCosta",
        # typo herdado da coleta antiga, já corrigido em `coletas`
        "FerreiraCoasta",
    ],

    # ── Varejo generalista ──────────────────────────────────────────────────
    "A.Dias": [
        "A.Dias", "A Dias", "ADias",
    ],
    "Fast Shop": [
        "Fast Shop", "fastshop2", "Fast Shop Oficial", "Fast Shop Loja Oficial",
    ],
    "Bagatoli": [
        "Bagatoli", "bagatolionline", "bagatolishop",
    ],
    "Ultrafeu": [
        "Ultrafeu", "loja-ultrafeu",
    ],
    "Lojas Colombo": [
        "Lojas Colombo", "lojascolombooficial",
    ],
    "Angeloni": [
        "Angeloni", "angeloni2", "angeloni.com.br",
    ],
    "Gazin": [
        "Gazin", "gazinshop", "Gazin Oficial", "Loja Gazin",
    ],
    "E-Fácil": [
        "E-Fácil", "Efácil", "Efácil Oficial",
    ],
    "Bemol": [
        "Bemol",
    ],
    "Carrefour": [
        "Carrefour", "carrefouroficial",
    ],
    "Casas Bahia": [
        "Casas Bahia", "Casas Bahia Oficial",
    ],
    "Magazine Luiza": [
        "Magazine Luiza", "magazineluiza", "Magalu",
    ],
    "Mercado Livre": [
        "Mercado Livre", "mercadolivre.com.br",
    ],
    "Amazon": [
        # "Amazon.com.br - Seller" e "Amazon Global" NÃO entram: no comparador
        # o primeiro é loja 3P anunciando via Amazon, o segundo é cross-border.
        "Amazon", "Amazon.com.br", "Amazon.com.br - Retail",
    ],
    "Shopee": [
        "Shopee", "shopee.com.br",
    ],
    "KaBuM!": [
        "KaBuM!", "Kabum",
    ],
    "Lojas Koerich": [
        "Lojas Koerich", "Koerich",
    ],
    "Lojas Benoit": [
        "Lojas Benoit", "LojasBenoit",
    ],
    "Lojas Guaibim": [
        "Lojas Guaibim", "lojasguaibim1",
    ],
    "Lojas Unilar": [
        "Lojas Unilar", "Lojas Unilar LTDA",
    ],
    "Le biscuit": [
        "Le biscuit", "lojaslebiscuit",
    ],
    "Casa & Video": [
        "Casa & Video", "casa-e-video",
    ],
    "Casa do Pica-Pau": [
        "Casa do Pica-Pau", "casadopica-pau",
    ],
    "taQi": [
        "taQi", "taQi Oficial", "lojastaqi",
    ],
    "ibyte": [
        "ibyte", "lojaibyte",
    ],
    "Compra Turbo": [
        "Compra Turbo", "Compra Turbo Oficial",
    ],
    "Tudão Tech": [
        "Tudão Tech", "Tudão Tech Ltda",
    ],
    "APA MÓVEIS": [
        "APA MÓVEIS", "apamoveis.com.br",
    ],
    "VOLIX STORE": [
        "VOLIX STORE", "volixstore",
    ],
    "LCG ELETRO COM": [
        # "LCG ELETRO FILIAL SC" fica de fora até o mantenedor confirmar.
        "LCG ELETRO COM", "LCG ELETRO",
    ],
    "Refriparts": [
        "Refriparts",
    ],
    "Copafer": [
        "Copafer",
    ],
    "Bela Magazine": [
        # NÃO é Magazine Luiza — ver COMMON_MISTAKES §23.
        "Bela Magazine",
    ],
    "ARPRIX DISTRIBUIDORA": [
        "ARPRIX DISTRIBUIDORA",
    ],
    "MERCADO IMPORTSSM": [
        "MERCADO IMPORTSSM",
    ],
    "TOTAL AR": [
        "TOTAL AR",
    ],
    "Zema": [
        # Relação com "Eletrozema" (loja própria) não confirmada — separado.
        "Zema", "lojaszema", "Zema Oficial",
    ],

    # ── Lojas oficiais de marca (1P do fabricante) ──────────────────────────
    # Contam como seller na buy box e sofrem a mesma fragmentação de caixa.
    # Varredura de 08/10/2026: a mesma loja oficial aparece como "Oficial",
    # "Loja X", slug da Magalu ou domínio no comparador.
    "Electrolux": [
        "Electrolux", "Loja Electrolux",
    ],
    "Samsung": [
        "Samsung", "Samsung Loja Oficial", "Samsung Brasil", "shop.samsung.com/br",
    ],
    "LG": [
        # "LG Importados" NÃO entra: revendedor, não a loja da marca.
        "LG", "lgelectronicsdobrasil", "LG Electronics Brasil",
        "LG Electronics Oficial", "LG Oficial",
    ],
    "TCL SEMP": [
        "TCL SEMP", "lojatclsemp", "TCL", "Loja TCL",
    ],
    "Midea": [
        "Midea", "Midea Store", "Midea Oficial", "Midea Brasil", "mideacarrier",
    ],
    "Daikin": [
        "Daikin", "daikinbrasil", "Loja Daikin",
    ],
    "Consul": [
        "Consul", "Consul Oficial", "Consul - Oficial", "Consul - Loja Oficial",
    ],
    "Philco": [
        "Philco", "Philco Oficial", "philcooficial",
    ],
    "Britânia": [
        "Britânia", "britaniaoficial",
    ],
    "Rheem": [
        "Rheem", "RHEEM DO BRASIL",
    ],
    "Komeco": [
        "Komeco", "lojakomeco", "Loja Komeco",
    ],
}


# ---------------------------------------------------------------------------
# Chave de comparação
# ---------------------------------------------------------------------------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Sufixos de tipo societário: "Tudão Tech Ltda" e "Tudão Tech" são o mesmo
# lojista. Só entram formas longas e inequívocas — "me", "sa" e "epp" ficaram
# de fora de propósito: são terminações comuns de nome comercial ("Fast Home",
# "Loja Ursa") e cortá-las inventaria colisão entre sellers diferentes.
_LEGAL_SUFFIXES = ("ltda", "eireli")


def seller_key(raw: Optional[str]) -> str:
    """
    Reduz um nome de seller à chave de comparação.

    Ignora caixa, acento, pontuação, espaço e símbolo de marca registrada —
    tudo que muda entre marketplaces sem mudar o lojista. Assim
    "Ar Certo", "ar-certo" e "ARCERTO" colapsam sozinhos, sem entrada no mapa.

    Args:
        raw: nome como veio da coleta (pode ser None).

    Returns:
        Chave minúscula só com [a-z0-9], ou "" quando não há nome.

    Example:
        >>> seller_key("Frigelar®")
        'frigelar'
        >>> seller_key("Centralar.com")
        'centralarcom'
    """
    if not raw:
        return ""

    # NFKD separa o acento da letra; o filtro de combining marks o descarta.
    decomposed = unicodedata.normalize("NFKD", str(raw))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    key = _NON_ALNUM.sub("", ascii_only.lower())

    for suffix in _LEGAL_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[: -len(suffix)]
            break

    return key


def _build_lookup() -> Dict[str, str]:
    """Achata SELLER_GROUPS em {chave: nome canônico}, detectando colisão."""
    lookup: Dict[str, str] = {}
    for canonical, variants in SELLER_GROUPS.items():
        for variant in (canonical, *variants):
            key = seller_key(variant)
            if not key:
                continue
            previous = lookup.get(key)
            if previous is not None and previous != canonical:
                # Duas famílias reivindicando a mesma chave é bug de edição do
                # mapa, e silenciar transferiria buy box entre sellers.
                raise ValueError(
                    f"Variante {variant!r} mapeada para {previous!r} e "
                    f"{canonical!r} — resolva a duplicidade em SELLER_GROUPS."
                )
            lookup[key] = canonical
    return lookup


_LOOKUP: Dict[str, str] = _build_lookup()


def normalize_seller_name(raw: Optional[str]) -> Optional[str]:
    """
    Devolve o nome canônico do seller.

    Sem match no mapa o nome volta apenas com espaços colapsados e símbolo de
    marca registrada removido — nunca é descartado nem chutado para um grupo
    parecido. Seller desconhecido continua sendo um seller.

    Args:
        raw: nome como veio da coleta (`Buy Box Seller` / `Seller / Vendedor`).

    Returns:
        Nome canônico, o nome original limpo, ou None quando não há nome —
        inclusive quando o texto é o byline de marca da Amazon
        ("Visite a loja X"), que não é vendedor.

    Example:
        >>> normalize_seller_name("continentalcenter")
        'Web Continental'
        >>> normalize_seller_name("mgshopgra")
        'mgshopgra'
        >>> normalize_seller_name("  ")
    """
    if raw is None:
        return None

    cleaned = " ".join(str(raw).replace("®", " ").replace("™", " ").split())
    if not cleaned:
        return None

    if is_brand_byline(cleaned):
        # Link da loja da MARCA, não vendedor — ver `_RE_BYLINE_MARCA`.
        return None

    key = seller_key(cleaned)
    if not key and not any(c.isalnum() for c in cleaned):
        # Só pontuação ("--", "!!!"): não há lojista nenhum aqui, e devolver o
        # texto cru criaria um seller espúrio no ranking de buy box.
        # A chave vazia sozinha NÃO basta como prova: ela também fica vazia
        # para um nome inteiramente fora do alfabeto latino ("Мосторг"), que é
        # um seller legítimo e passa inalterado como qualquer outro
        # desconhecido. `str.isalnum()` é Unicode-aware e separa os dois casos.
        return None

    return _LOOKUP.get(key, cleaned)


def variants_for(canonical: str) -> List[str]:
    """
    Lista as grafias conhecidas de um seller canônico.

    Necessário enquanto a base tiver linhas antigas ainda não reescritas: o
    filtro de seller do dashboard consulta o Supabase pelo valor BRUTO, então
    filtrar por "Web Continental" precisa expandir para as 5 grafias ou o
    recorte volta vazio.

    Args:
        canonical: nome canônico (ou qualquer variante dele).

    Returns:
        Lista com o canônico e suas variantes; `[canonical]` se desconhecido.
    """
    resolved = normalize_seller_name(canonical)
    if resolved is None:
        return []
    variants = SELLER_GROUPS.get(resolved)
    if variants is None:
        return [resolved]
    return list(dict.fromkeys([resolved, *variants]))


def canonical_names() -> List[str]:
    """Nomes canônicos conhecidos, em ordem alfabética."""
    return sorted(SELLER_GROUPS)
