"""
Mapa canônico de sellers do PriceTrack.

Sellers chegam com grafias inconsistentes (cedilhas, prefixo "LOJA OFICIAL",
abreviações). Este módulo expõe `normalize_seller(raw)` que devolve um
nome canônico único por dealer/loja oficial.

Sellers sem match são registrados em `logs/pricetrack/unknown_sellers.log`
para revisão manual e expansão futura do mapa.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


# Mapa estático — sellers conhecidos e suas grafias variantes.
# Chave: forma uppercase/stripped como vem do PriceTrack
# Valor: nome canônico desejado
SELLER_CANONICAL: Dict[str, str] = {
    # Friopeças
    "FRIOPECAS": "FRIOPEÇAS",
    "FRIOPEÇAS": "FRIOPEÇAS",
    "LOJA OFICIAL FRIOPEÇAS": "FRIOPEÇAS",
    "LOJA OFICIAL FRIOPECAS": "FRIOPEÇAS",
    # Dufrio
    "DUFRIO": "DUFRIO",
    "LOJA OFICIAL DUFRIO": "DUFRIO",
    "DUFRIO REFRIGERACAO": "DUFRIO",
    "DUFRIO REFRIGERAÇÃO": "DUFRIO",
    # Central Ar
    "CENTRALAR.COM": "CENTRAL AR",
    "CENTRALAR": "CENTRAL AR",
    "CENTRAL AR": "CENTRAL AR",
    "LOJA OFICIAL CENTRALAR": "CENTRAL AR",
    "LOJA OFICIAL CENTRAL AR": "CENTRAL AR",
    # Climario
    "CLIMARIO": "CLIMARIO",
    "LOJA OFICIAL CLIMARIO": "CLIMARIO",
    # Web Continental
    "WEBCONTINENTAL": "WEB CONTINENTAL",
    "WEB CONTINENTAL": "WEB CONTINENTAL",
    "LOJA OFICIAL WEBCONTINENTAL": "WEB CONTINENTAL",
    "LOJA OFICIAL WEB CONTINENTAL": "WEB CONTINENTAL",
    # Leveros
    "LEVEROS": "LEVEROS",
    "LOJA OFICIAL LEVEROS": "LEVEROS",
    # ArCerto
    "ARCERTO": "ARCERTO",
    "AR CERTO": "ARCERTO",
    "LOJA OFICIAL ARCERTO": "ARCERTO",
    # PoloAr
    "POLOAR": "POLOAR",
    "POLO AR": "POLOAR",
    "LOJA OFICIAL POLOAR": "POLOAR",
    # Frigelar
    "FRIGELAR": "FRIGELAR",
    "LOJA OFICIAL FRIGELAR": "FRIGELAR",
    # Eletrozema
    "ELETROZEMA": "ELETROZEMA",
    "LOJA OFICIAL ELETROZEMA": "ELETROZEMA",
    # Engageletro / EngageEletro
    "ENGAGEELETRO": "ENGAGE ELETRO",
    "ENGAGE ELETRO": "ENGAGE ELETRO",
    "LOJA OFICIAL ENGAGEELETRO": "ENGAGE ELETRO",
    # CenterKennedy
    "CENTERKENNEDY": "CENTER KENNEDY",
    "CENTER KENNEDY": "CENTER KENNEDY",
    # Zenir
    "ZENIR": "ZENIR",
    "LOJA OFICIAL ZENIR": "ZENIR",
    # Bemol
    "BEMOL": "BEMOL",
    "LOJA OFICIAL BEMOL": "BEMOL",
    # Norte Refrigeração
    "NORTE REFRIGERACAO": "NORTE REFRIGERAÇÃO",
    "NORTE REFRIGERAÇÃO": "NORTE REFRIGERAÇÃO",
    # Zema
    "ZEMA": "ZEMA",
    "LOJAS ZEMA": "ZEMA",
    # Quero Quero
    "LOJAS QUERO QUERO": "QUERO QUERO",
    "QUERO QUERO": "QUERO QUERO",
    # Marketplaces 1P (lojas oficiais dos próprios marketplaces aparecem como sellers)
    "MAGAZINE LUIZA": "MAGAZINE LUIZA",
    "MAGALU": "MAGAZINE LUIZA",
    "CASAS BAHIA": "CASAS BAHIA",
    "PONTO": "PONTO",
    "EXTRA": "EXTRA",
    "AMERICANAS": "AMERICANAS",
    "SUBMARINO": "SUBMARINO",
    "SHOPTIME": "SHOPTIME",
    "CARREFOUR": "CARREFOUR",
    "AMAZON.COM.BR": "AMAZON",
    "AMAZON": "AMAZON",
    "MERCADO LIVRE": "MERCADO LIVRE",
    "SHOPEE": "SHOPEE",
    "FAST SHOP": "FAST SHOP",
    "FASTSHOP": "FAST SHOP",
    "PICHAU": "PICHAU",
    "KABUM": "KABUM",
}


_UNKNOWN_SELLERS_LOG_PATH: Path | None = None


def set_unknown_sellers_log_path(path: str | Path) -> None:
    """Define onde gravar sellers sem match no mapa canônico."""
    global _UNKNOWN_SELLERS_LOG_PATH
    _UNKNOWN_SELLERS_LOG_PATH = Path(path)
    _UNKNOWN_SELLERS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _log_unknown(raw: str, normalized: str) -> None:
    if _UNKNOWN_SELLERS_LOG_PATH is None:
        return
    try:
        with open(_UNKNOWN_SELLERS_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{raw}\t{normalized}\n")
    except OSError:
        pass


def normalize_seller(raw: str) -> str:
    """
    Devolve o nome canônico do seller.

    Aplica:
    1. strip + uppercase
    2. remove prefixo redundante "LOJA OFICIAL "
    3. match em SELLER_CANONICAL
    4. fallback: devolve o uppercase strip e loga como desconhecido
    """
    if raw is None:
        return ""

    cleaned = " ".join(raw.strip().upper().split())

    # 1) Lookup direto na forma original (alguns canonicals incluem o prefixo)
    if cleaned in SELLER_CANONICAL:
        return SELLER_CANONICAL[cleaned]

    # 2) Remove prefixo "LOJA OFICIAL " e tenta de novo
    if cleaned.startswith("LOJA OFICIAL "):
        without_prefix = cleaned[len("LOJA OFICIAL "):].strip()
        if without_prefix in SELLER_CANONICAL:
            return SELLER_CANONICAL[without_prefix]
        # Fallback ainda mais permissivo: usa o nome sem prefixo
        _log_unknown(raw, without_prefix)
        return without_prefix

    _log_unknown(raw, cleaned)
    return cleaned
