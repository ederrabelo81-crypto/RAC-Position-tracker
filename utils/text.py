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
    
    CORREÇÃO PROBLEMA #4: Implementa sanitização robusta para formato pt-BR,
    removendo primeiro separadores de milhar antes de converter decimal.

    Exemplos:
        "R$ 2.799,90"      → 2799.90
        "R$\xa02.184,05"   → 2184.05  (non-breaking space do Google Shopping)
        "2799,90"          → 2799.90
        "2.799"            → 2799.0   (sem centavos)
        "1520.1"           → 1520.1   (já formatado incorretamente, preserva)
        None / ""          → None
    """
    if not raw:
        return None

    # remove símbolo de moeda, espaços normais e \xa0 (non-breaking space do Google)
    cleaned = re.sub(r"[R$\s\xa0]", "", raw).strip()
    
    # Remove tudo que não é dígito, ponto ou vírgula (sanitização defensiva)
    cleaned = re.sub(r'[^\d.,]', '', cleaned)
    
    if not cleaned:
        return None

    # CORREÇÃO: Lógica aprimorada para distinguir milhar vs decimal
    # Caso 1: Tem vírgula → formato brasileiro (ponto=milhar, vírgula=decimal)
    if "," in cleaned:
        # Remove pontos (milhar) e converte vírgula para ponto decimal
        # ex: "2.799,90" → "2799.90", "1.994,91" → "1994.91"
        cleaned = cleaned.replace(".", "").replace(",", ".")
    
    # Caso 2: Sem vírgula, mas tem múltiplos pontos → pode ser erro de formatação
    elif cleaned.count('.') > 1:
        # Múltiplos pontos sem vírgula → assume todos são separadores de milhar
        # ex: "1.520.100" → "1520100"
        cleaned = cleaned.replace(".", "")
    
    # Caso 3: Sem vírgula, apenas um ponto → ambíguo, tenta heurística
    elif "." in cleaned:
        # Se tem exatamente um ponto e 3 dígitos após → provavelmente milhar
        # ex: "2.799" → "2799"
        # Mas se não tiver 3 dígitos após → pode ser decimal já formatado
        # ex: "1520.1" → preserva como "1520.1"
        if re.match(r"^\d{1,3}\.\d{3}$", cleaned):
            cleaned = cleaned.replace(".", "")
        # else: preserva o ponto como decimal (formato já convertido)

    try:
        result = float(cleaned)
        # Validação defensiva: preço deve ser positivo e razoável
        if result <= 0:
            return None
        if result > 10_000_000:  # Preço acima de 10 milhões provavelmente é erro
            return None
        return result
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
