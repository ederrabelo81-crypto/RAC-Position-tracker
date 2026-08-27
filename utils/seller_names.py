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
apelido opaco (`mgshopgra`, `GoCompras`) fica como está até alguém abrir a
loja no marketplace; agrupar por semelhança de string transferiria buy box de
um seller para outro, que é pior que a fragmentação que este módulo resolve.

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
    "seller_key",
    "variants_for",
    "canonical_names",
]


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
    ],
    "Denteck": [
        "Denteck", "Denteck Ar Condicionado",
    ],
    "Leveros": [
        "Leveros", "leveros3",
    ],
    "Ar Certo": [
        "Ar Certo", "ArCerto", "ar-certo",
    ],
    "Polo Ar": [
        "Polo Ar", "PoloAr",
    ],
    "Refricril Refrigeração": [
        "Refricril Refrigeração", "refricrilrefrigeracaoepecas",
    ],
    "Norte Refrigeração": [
        "Norte Refrigeração", "NorteRefrigeracao",
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
        "Fast Shop", "fastshop2",
    ],
    "Bagatoli": [
        "Bagatoli", "bagatolionline", "bagatolishop",
    ],
    "Comprebel": [
        "Comprebel", "comprebel2",
    ],
    "Ultrafeu": [
        "Ultrafeu", "loja-ultrafeu",
    ],
    "Lojas Colombo": [
        "Lojas Colombo", "lojascolombooficial",
    ],
    "Angeloni": [
        "Angeloni", "angeloni2",
    ],
    "Gazin": [
        "Gazin", "gazinshop",
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
    "Magazine Luiza": [
        "Magazine Luiza", "magazineluiza", "Magalu",
    ],
    "Mercado Livre": [
        "Mercado Livre",
    ],

    # ── Lojas oficiais de marca (1P do fabricante) ──────────────────────────
    # Contam como seller na buy box e sofrem a mesma fragmentação de caixa.
    "Electrolux": [
        "Electrolux",
    ],
    "Samsung": [
        "Samsung",
    ],
    "LG": [
        "LG", "lgelectronicsdobrasil",
    ],
    "TCL SEMP": [
        "TCL SEMP", "lojatclsemp",
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
        Nome canônico, o nome original limpo, ou None quando não há nome.

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

    return _LOOKUP.get(seller_key(cleaned), cleaned)


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
