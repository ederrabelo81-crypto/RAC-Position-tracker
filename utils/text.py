"""
utils/text.py — Funções utilitárias de limpeza e normalização de texto.

Responsabilidades:
  - Converter strings de preço em float Python (R$ 2.799,90 → 2799.90)
  - Normalizar contagens de avaliações ("(1.234)" → 1234)
  - Determinar turno a partir do horário
  - Inferir categoria da keyword buscada
"""

import re
from datetime import datetime
from typing import Optional

from config import TURNO_ABERTURA_MAX_HOUR


def parse_price(raw: Optional[str]) -> Optional[float]:
    """
    Converte string de preço brasileiro para float.

    Exemplos:
        "R$ 2.799,90"  → 2799.90
        "2799,90"      → 2799.90
        "2.799"        → 2799.0   (sem centavos)
        None / ""      → None
    """
    if not raw:
        return None

    # remove símbolo de moeda e espaços
    cleaned = re.sub(r"[R$\s]", "", raw).strip()

    # padrão: ponto como separador de milhar, vírgula como decimal
    if "," in cleaned:
        # ex: "2.799,90" → "2799.90"
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # ex: "2.799" (sem centavos, ponto de milhar) → "2799"
        # heurística: se há exatamente um ponto e 3 dígitos após → milhar
        if re.match(r"^\d{1,3}\.\d{3}$", cleaned):
            cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_rating(raw: Optional[str]) -> Optional[float]:
    """
    Extrai nota float de 0 a 5.

    Exemplos: "4.8", "4,8", "(4.8)" → 4.8
    """
    if not raw:
        return None
    match = re.search(r"(\d+)[,.](\d+)", raw)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    match = re.search(r"(\d+)", raw)
    if match:
        val = float(match.group(1))
        return val if val <= 5 else None
    return None


def parse_review_count(raw: Optional[str]) -> Optional[int]:
    """
    Extrai número inteiro de avaliações.

    Exemplos: "(1.234)", "1234 avaliações", "1,234" → 1234
    """
    if not raw:
        return None
    # remove todos os não-dígitos exceto separadores de milhar
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def get_turno(hora: Optional[datetime] = None) -> str:
    """
    Retorna 'Abertura' se hora <= TURNO_ABERTURA_MAX_HOUR, senão 'Fechamento'.
    Se hora não fornecida, usa horário atual.
    """
    h = hora if hora else datetime.now()
    return "Abertura" if h.hour <= TURNO_ABERTURA_MAX_HOUR else "Fechamento"


def infer_keyword_category(keyword: str, category_map: dict) -> str:
    """
    Retorna a categoria da keyword consultando o mapa KEYWORDS do config.
    Se não encontrar, tenta inferir pela presença de termos conhecidos.

    Args:
        keyword:      string de busca exata
        category_map: dict {categoria: [keywords]} de config.KEYWORDS
    """
    keyword_lower = keyword.lower()

    # busca direta no mapa de configuração
    for category, kws in category_map.items():
        if keyword_lower in [k.lower() for k in kws]:
            return category

    # inferência por palavras-chave
    if any(t in keyword_lower for t in ["inverter", "wifi", "wi-fi"]):
        return "Tecnologia"
    if any(t in keyword_lower for t in ["portátil", "portatil"]):
        return "Portátil"
    if any(t in keyword_lower for t in ["janela"]):
        return "Janela"
    if re.search(r"\d{4,5}\s*btus?", keyword_lower):
        return "Capacidade"

    return "Geral"


def normalize_text(text: Optional[str]) -> Optional[str]:
    """Remove espaços extras e normaliza unicode básico."""
    if not text:
        return None
    return " ".join(text.split())
