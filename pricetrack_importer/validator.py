"""
Validação de linhas parseadas do PriceTrack.

Aplica as regras documentadas no prompt:

- Rejeita linhas onde `collectionDate` não match `M/D/YY` (metadados).
- Rejeita linhas onde `seller` parece SKU ou fragmento de título.
- Devolve um motivo de rejeição classificado para o log estruturado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .normalizer import is_pricetrack_date


# Padrões de seller corrompido — match qualquer um → rejeita
_SKU_LEADING_NUM = re.compile(r"^[0-9]{2,3}[A-Z]{4,}")
_SKU_LEADING_ALPHA = re.compile(r"^[A-Z]{2,4}[0-9]{2,}")
_PUREL_NUMERIC = re.compile(r"^[0-9]+$")
_PARENTHESIZED = re.compile(r"^\(.+\)$")

# Substrings que denunciam fragmento de título no campo seller
_TITLE_FRAGMENTS = (
    " - 220V",
    " - 127V",
    " 220V",
    " 127V",
    " Q&F",
    " BTU",
    " BTUS",
)


@dataclass
class ValidationResult:
    """Resultado da validação de uma linha."""

    valid: bool
    reason: Optional[str] = None
    detail: Optional[str] = None


def is_metadata_row(row: Dict[str, str]) -> bool:
    """
    Linhas de metadados (cabeçalho, separador, "Filtros aplicados:", "Total").

    Detecção: `collectionDate` não match M/D/YY.
    """
    date_raw = row.get("collectionDate", "")
    return not is_pricetrack_date(date_raw)


def is_invalid_seller(seller_raw: str) -> Tuple[bool, Optional[str]]:
    """
    Verifica se o campo seller está corrompido (fragmento de título / SKU / numérico).

    Returns:
        (True, motivo) se inválido, (False, None) se válido.
    """
    if seller_raw is None:
        return True, "EMPTY"

    s = seller_raw.strip()
    if not s:
        return True, "EMPTY"

    upper = s.upper()

    if _SKU_LEADING_NUM.match(upper):
        return True, "LOOKS_LIKE_SKU_NUM"
    if _SKU_LEADING_ALPHA.match(upper) and not _is_known_brand_prefix(upper):
        return True, "LOOKS_LIKE_SKU_ALPHA"
    if _PUREL_NUMERIC.match(s):
        return True, "NUMERIC_ONLY"
    if _PARENTHESIZED.match(s):
        return True, "PARENTHESIZED"

    for frag in _TITLE_FRAGMENTS:
        if frag in upper:
            return True, "TITLE_FRAGMENT"

    return False, None


# Prefixos válidos que poderiam disparar falso positivo do padrão SKU_LEADING_ALPHA
# (ex: "LG", "LGE", "JBL"). Lista curta — expandir só se houver falso positivo real.
_KNOWN_BRAND_LIKE_PREFIXES = {"LG", "LGE", "JBL", "TCL"}


def _is_known_brand_prefix(s: str) -> bool:
    """Detecta prefixos de marca para evitar falso positivo na regex de SKU."""
    first_token = s.split()[0] if s else ""
    return first_token in _KNOWN_BRAND_LIKE_PREFIXES


def validate_row(row: Dict[str, str]) -> ValidationResult:
    """
    Valida uma linha parseada.

    Args:
        row: dict com as 10 colunas do PriceTrack.

    Returns:
        `ValidationResult` com `valid=True` ou `False` + motivo.
    """
    if is_metadata_row(row):
        return ValidationResult(valid=False, reason="METADATA")

    invalid_seller, seller_reason = is_invalid_seller(row.get("seller", ""))
    if invalid_seller:
        return ValidationResult(
            valid=False, reason="INVALID_SELLER", detail=seller_reason
        )

    # Campos obrigatórios mínimos pra entrar no DB
    for required in ("brand", "sku", "marketplace"):
        if not (row.get(required) or "").strip():
            return ValidationResult(
                valid=False, reason="MISSING_FIELD", detail=required
            )

    return ValidationResult(valid=True)
