"""
Normalização de campos do PriceTrack.

Funções puras (sem side effects) que convertem strings cruas do export
para tipos canônicos a serem persistidos no Supabase.

- `parse_pricetrack_date`: `5/27/26` → `date(2026, 5, 27)` (formato M/D/YY)
- `parse_decimal`: `7994.44` → `float(7994.44)` (origem usa ponto, mantém ponto)
- `normalize_text`: trim + colapso de whitespace
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional


# Regex estrita: M/D/YY com 1-2 dígitos em mês e dia, e exatamente 2 dígitos no ano
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2})\s*$")

# Formato ISO YYYY-MM-DD (opcional " HH:MM:SS" no caso de datetime stringificado
# vindo do openpyxl quando o parser não capturou o objeto datetime nativo).
_ISO_DATE_RE = re.compile(
    r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{1,2}(?::\d{1,2})?(?:\.\d+)?)?\s*$"
)


def parse_pricetrack_date(raw) -> Optional[date]:
    """
    Converte data do PriceTrack para `datetime.date`.

    Formato canônico do export: `5/27/26` (M/D/YY). Aceita também:
    - `datetime`/`date` nativos (defesa quando xlsx é parseado tipado);
    - ISO `2026-05-27` ou datetime stringificado `2026-05-27 00:00:00`
      (defesa quando o parser não normalizou a célula).

    Convenção dos anos com 2 dígitos: 00-68 → 2000-2068, 69-99 → 1969-1999
    (igual `datetime.strptime("%y")`).

    Returns:
        `date` parseado ou None se formato inválido.
    """
    if raw is None:
        return None

    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    s = str(raw).strip()

    m = _DATE_RE.match(s)
    if m:
        month, day, year_2d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year_full = 2000 + year_2d if year_2d < 69 else 1900 + year_2d
        try:
            return date(year_full, month, day)
        except ValueError:
            return None

    m_iso = _ISO_DATE_RE.match(s)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            return None

    return None


def is_pricetrack_date(raw: str) -> bool:
    """Indica se a string parece uma data PriceTrack válida."""
    return parse_pricetrack_date(raw) is not None


def parse_decimal(raw: str) -> Optional[float]:
    """
    Converte string de preço do PriceTrack para float.

    Origem usa ponto como separador decimal (formato US: `7994.44`).
    Nunca convertemos para vírgula no pipeline — só na camada de display.

    Args:
        raw: String bruta de preço (ex: `"7994.44"`, `"  1259.00 "`).

    Returns:
        float ou None se vazio/inválido.
    """
    if raw is None:
        return None

    s = str(raw).strip()
    if not s or s.upper() in {"NA", "N/A", "NULL", "NONE", "-"}:
        return None

    # Defesa: caso algum cliente já tenha aplicado vírgula decimal
    if "," in s and "." not in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except ValueError:
        return None


def normalize_text(raw: str) -> str:
    """Trim + colapso de whitespace interno. None vira string vazia."""
    if raw is None:
        return ""
    return " ".join(str(raw).split())


def iso_date(d: date | datetime | None) -> Optional[str]:
    """Devolve YYYY-MM-DD ou None."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return d.isoformat()
