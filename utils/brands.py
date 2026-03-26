"""
utils/brands.py — Extração de marca monitorada a partir do título do produto.

Usa Regex com word-boundary para evitar falsos positivos
(ex: não confundir "Carrier" dentro de "portacarrier").
"""

import re
from typing import Optional

from config import BRANDS


# Pré-compila os padrões para desempenho (importante ao processar centenas de linhas)
_BRAND_PATTERNS = [
    (brand, re.compile(rf"\b{re.escape(brand)}\b", re.IGNORECASE))
    for brand in BRANDS
]


def extract_brand(title: Optional[str]) -> str:
    """
    Retorna a primeira marca encontrada no título do produto.
    Prioriza a ordem definida em config.BRANDS (mais específicas primeiro).

    Args:
        title: string completa do título / nome do produto

    Returns:
        Nome da marca (ex: "Midea") ou "Desconhecida" se não identificada.
    """
    if not title:
        return "Desconhecida"

    for brand, pattern in _BRAND_PATTERNS:
        if pattern.search(title):
            return brand

    return "Desconhecida"
