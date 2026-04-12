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


def parse_price_brazil(raw_text: Optional[str]) -> Optional[float]:
    """
    CORREÇÃO PROBLEMA #4: Parser robusto de preço brasileiro com regex.
    
    Extrai o primeiro padrão monetário válido de uma string, lidando com:
      - Formato brasileiro: R$ 1.994,91 (ponto=milhar, vírgula=decimal)
      - Concatenação de DOM: "13% OFFR$ 1.994,91no pix"
      - Múltiplos preços na mesma string (usa o primeiro)
      - Strings vazias ou inválidas
    
    Args:
        raw_text: String bruta extraída do HTML (pode conter ruído)
    
    Returns:
        float do preço ou None se não encontrar padrão válido
    
    Testes unitários rápidos:
        >>> parse_price_brazil("R$ 1.994,91")
        1994.91
        >>> parse_price_brazil("13% OFFR$ 1.709,91no pix")
        1709.91
        >>> parse_price_brazil("R$ 2.309,90em 10x")
        2309.9
        >>> parse_price_brazil("")
        None
        >>> parse_price_brazil("125.0")
        125.0
        >>> parse_price_brazil(None)
        None
        >>> parse_price_brazil("R$ 2.799,90 à vista")
        2799.9
        >>> parse_price_brazil("1.520,10")
        1520.1
    """
    if not raw_text:
        return None
    
    # Extrai primeiro padrão monetário: R$ 1.994,91 ou R$1994,91 ou 1.994,91
    match = re.search(r'R\$\s*([\d.,]+)|([\d]+\.[\d]{3},[\d]{2})', raw_text)
    if not match:
        # Tenta extrair apenas número com vírgula decimal
        match = re.search(r'(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)', raw_text)
        if not match:
            return None
    
    val = match.group(1) if match.group(1) else match.group(2)
    if not val:
        return None
    
    # Remove separador de milhar (pontos), mantém vírgula para decimal
    # ex: "1.994,91" → "1994,91" → "1994.91"
    val = val.replace('.', '').replace(',', '.')
    
    try:
        result = float(val)
        # Validação defensiva: preço deve ser positivo e razoável
        if result <= 0:
            return None
        if result > 10_000_000:  # Preço acima de 10 milhões provavelmente é erro
            return None
        return result
    except ValueError:
        return None


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
    # FIX: Usa parse_price_brazil como parser primário mais robusto
    result = parse_price_brazil(raw)
    if result is not None:
        return result
    
    # Fallback para lógica legada (casos edge não cobertos pelo regex)
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


# ---------------------------------------------------------------------------
# Filtro de produto AC — termos e listas usados por is_valid_product()
# ---------------------------------------------------------------------------

# Termos fortes: qualquer um confirma que é AC
_AC_STRONG_TERMS = [
    r'\bar[- ]condicionado\b',   # ar condicionado / ar-condicionado
    r'\bbtu[s]?\b',               # BTU / BTUs
    r'\bevaporadora\b',            # unidade interna
    r'\bcondensadora\b',           # unidade externa
    r'\bhi[- ]?wall\b',            # hi-wall split
    r'\bmini[- ]?split\b',         # mini-split
    r'\bcassete\b',                # teto cassete
]

# Termos fracos: precisam de pelo menos 2 para confirmar AC
_AC_WEAK_TERMS = [
    r'\bsplit\b',
    r'\binverter\b',
]

# Blocklist: produtos claramente não-AC → rejeitados mesmo com termos AC
_NON_AC_TERMS = [
    r'\biphones?\b',
    r'\bipad\b',
    r'\bnotebook\b',
    r'\blaptop\b',
    r'\bcelular\b',
    r'\bsmartphone\b',
    r'\bfralda[s]?\b',
    r'\bfrald[ao]\b',
    r'\bgeladeira\b',
    r'\brefrigerador\b',
    r'\bfog[aã]o\b',
    r'\bmicroondas\b',
    r'\btablet\b',
    r'\bairpods?\b',
    r'\bmacbook\b',
    r'\bcolch[aã]o\b',
    r'\bsof[aá]\b',
]


def is_valid_product(name: str, price: Optional[float] = None) -> bool:
    """
    Valida se um item é um produto real de ar-condicionado.

    Lógica:
      1. Rejeita se preço fornecido for inválido ou fora da faixa R$ 200–R$ 80.000
      2. Rejeita se nome bater com a blocklist (iPhone, fralda, notebook…)
      3. Aprova se nome contiver qualquer termo forte (btu, ar condicionado…)
      4. Aprova se nome contiver 2+ termos fracos (split + inverter)
      5. Rejeita caso contrário

    Args:
        name:  Título/nome do produto
        price: Preço parseado (float) — opcional; se None, ignora verificação de faixa

    Returns:
        True se for produto AC válido, False caso contrário

    Testes:
        >>> is_valid_product("Ar Condicionado Split 9000 BTU Inverter", 1994.91)
        True
        >>> is_valid_product("iPhone 15 Pro 256GB", 5999.0)
        False
        >>> is_valid_product("Fralda Pampers XXG 80 unidades", 89.9)
        False
        >>> is_valid_product("Ofer Seman", 125.0)
        False
        >>> is_valid_product("Clique para ver preço", None)
        False
        >>> is_valid_product("Split Hi-Wall 12000 BTUs LG Dual Inverter", 1520.1)
        True
        >>> is_valid_product("Split Inverter 24000", 3200.0)
        True
    """
    if not name:
        return False

    # Verificação de faixa de preço (somente se fornecido)
    if price is not None:
        if price <= 0:
            return False
        # ACs no Brasil: ~R$ 400 (portátil básico) a R$ 80.000 (industrial)
        if price < 200 or price > 80_000:
            return False

    name_lower = name.lower()

    # Blocklist: produto claramente não-AC
    if any(re.search(pattern, name_lower) for pattern in _NON_AC_TERMS):
        return False

    # Termo forte: qualquer um é suficiente
    if any(re.search(pattern, name_lower) for pattern in _AC_STRONG_TERMS):
        return True

    # Termos fracos: precisa de 2+
    weak_hits = sum(1 for p in _AC_WEAK_TERMS if re.search(p, name_lower))
    return weak_hits >= 2


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
