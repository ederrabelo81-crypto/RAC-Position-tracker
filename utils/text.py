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
from zoneinfo import ZoneInfo

from config import TURNO_ABERTURA_MAX_HOUR

BRT = ZoneInfo("America/Sao_Paulo")


def now_brt() -> datetime:
    """
    Retorna datetime atual em horário de Brasília (America/Sao_Paulo),
    independente da timezone do sistema operacional.

    Crítico para servidores em UTC: garante que turno, data e horário
    refletem o horário comercial brasileiro, não o do datacenter.
    """
    return datetime.now(BRT).replace(tzinfo=None)


def parse_price_brazil(raw_text: Optional[str]) -> Optional[float]:
    """
    Parser robusto de preço brasileiro com regex.

    Extrai o primeiro padrão monetário válido de uma string, lidando com:
      - Formato brasileiro: R$ 1.994,91 (ponto=milhar, vírgula=decimal)
      - Concatenação de DOM: "13% OFFR$ 1.994,91no pix"
      - Python float notation: "R$ 1829.0" → 1829.0  (não 18290.0)
      - Múltiplos preços na mesma string (usa o primeiro)
      - Strings vazias ou inválidas

    Testes:
        >>> parse_price_brazil("R$ 1.994,91")
        1994.91
        >>> parse_price_brazil("13% OFFR$ 1.709,91no pix")
        1709.91
        >>> parse_price_brazil("R$ 2.309,90em 10x")
        2309.9
        >>> parse_price_brazil("R$ 1829.0")
        1829.0
        >>> parse_price_brazil("R$ 2.799,90 à vista")
        2799.9
        >>> parse_price_brazil("1.520,10")
        1520.1
        >>> parse_price_brazil("125.0")
        125.0
        >>> parse_price_brazil("")
        None
        >>> parse_price_brazil(None)
        None
    """
    if not raw_text:
        return None

    # Guard: Python float notation — "R$ 1829.0" or "1829.0"
    # parse_price_brazil would strip the dot → "18290" (×10 bug).
    # A dot followed by 1-2 digits with no comma = decimal separator, not thousands.
    _float_match = re.search(r'(?:R\$\s*)?(\d+\.\d{1,2})$', raw_text.strip())
    if _float_match and ',' not in raw_text:
        try:
            return float(_float_match.group(1))
        except ValueError:
            pass

    # Extrai primeiro padrão monetário: R$ 1.994,91 ou R$1994,91 ou 1.994,91
    match = re.search(r'R\$\s*([\d.,]+)|([\d]+\.[\d]{3},[\d]{2})', raw_text)
    if not match:
        # Tenta extrair número no formato brasileiro: NNNN,NN ou N.NNN,NN
        match = re.search(r'(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)', raw_text)
        if not match:
            return None

    val = match.group(1) if match.group(1) else match.group(2)
    if not val:
        return None

    # Formato brasileiro: remove pontos (milhar) e converte vírgula para ponto decimal
    # ex: "1.994,91" → "1994.91", "1829,00" → "1829.00"
    val = val.replace('.', '').replace(',', '.')

    try:
        result = float(val)
        if result <= 0:
            return None
        if result > 10_000_000:
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


# Teto de sanidade para contagens (avaliações, sellers). Nenhum marketplace
# brasileiro de RAC passa disso; acima daqui o valor é lixo de parsing, não
# dado. Existe porque a versão antiga desta função concatenava TODOS os dígitos
# do texto — "4,7 de 5 estrelas, 12.345 avaliações" virava 47512345 — e um
# aria-label longo o bastante gerava um inteiro acima de 2^53. O pandas então
# perdia precisão ao converter a coluna para float64 e o `astype("Int64")` do
# _export_csv abortava com "cannot safely cast non-equivalent float64 to
# int64", derrubando a exportação inteira (run #174, 6.047 registros perdidos).
MAX_COUNT_SANE = 50_000_000

# Sufixos de escala usados nas SERPs (PT-BR e EN). Ordem importa: a alternação
# é avaliada da esquerda para a direita, então "milhões" precisa vir antes de
# "mil", e "mil" antes de "mi".
_SCALE_SUFFIXES: tuple = (
    (r"milh(?:ões|oes|ão|ao)", 1_000_000),
    (r"mil",                   1_000),
    (r"mi",                    1_000_000),
    (r"kk",                    1_000_000),
    (r"k",                     1_000),
    (r"m",                     1_000_000),
)
_RE_SCALE = re.compile(
    r"^\s*(" + "|".join(pat for pat, _ in _SCALE_SUFFIXES) + r")\b",
    re.IGNORECASE,
)
_SCALE_LOOKUP = {
    "milhões": 1_000_000, "milhoes": 1_000_000,
    "milhão": 1_000_000,  "milhao": 1_000_000,
    "mil": 1_000, "mi": 1_000_000, "kk": 1_000_000,
    "k": 1_000,   "m": 1_000_000,
}

# Cláusulas de nota que precedem a contagem no mesmo texto: "4,7 de 5 estrelas",
# "4.7 out of 5 stars", "5 estrelas". Precisam sair ANTES de procurar a
# contagem, senão os dígitos da nota entram no número.
_RE_RATING_CLAUSE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:de|of|out\s+of)\s*\d+(?:[.,]\d+)?\s*"
    r"(?:estrelas?|stars?)",
    re.IGNORECASE,
)
_RE_STAR_TOKEN = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:estrelas?|stars?)", re.IGNORECASE
)

# Sequência numérica maximal, já tolerando separadores internos e NBSP.
_RE_NUMBER_RUN = re.compile(r"\d[\d.,\s ]*\d|\d")

# Palavras que identificam o número como sendo a CONTAGEM (e não preço, posição,
# BTUs do título…). Quando alguma aparece logo depois do número, ele é preferido.
_REVIEW_WORDS: tuple = (
    "avaliaç", "avaliac", "review", "classificaç", "classificac",
    "rating", "opini", "comentári", "comentari", "voto",
)


def _digits_to_int(raw_number: str) -> Optional[int]:
    """Converte uma corrida numérica SEM sufixo de escala em inteiro.

    Todos os separadores (ponto, vírgula, espaço, NBSP) são tratados como
    separador de milhar — é o que "(1.234)", "1,234" e "1 234" significam numa
    contagem de avaliações, onde casa decimal não existe.

    Args:
        raw_number: trecho numérico bruto (ex: ``"1.234"``).

    Returns:
        Inteiro parseado, ou None se não sobrar dígito algum.
    """
    digits = re.sub(r"[^\d]", "", raw_number)
    return int(digits) if digits else None


def _scaled_to_int(raw_number: str, multiplier: int) -> Optional[int]:
    """Converte "1,2" + escala 1.000 em 1200 (inteiro, nunca fracionário).

    Com sufixo de escala presente ("1,2 mil"), o separador passa a ser decimal
    — é o oposto do caso sem sufixo. O resultado é arredondado aqui mesmo para
    que a coluna nunca receba um float: era exatamente esse valor fracionário
    que o `astype("Int64")` do CSV recusava.

    Args:
        raw_number: trecho numérico bruto (ex: ``"1,2"``).
        multiplier: fator da escala (1.000 para "mil"/"k", 1.000.000 para "mi").

    Returns:
        Inteiro já escalado, ou None se o trecho não for numérico.
    """
    cleaned = re.sub(r"[\s ]", "", raw_number).replace(",", ".")
    # "1.234,5 mil" → o último separador é o decimal; os anteriores, milhar.
    if cleaned.count(".") > 1:
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    try:
        return int(round(float(cleaned) * multiplier))
    except (TypeError, ValueError):
        return None


def parse_review_count(raw: Optional[str]) -> Optional[int]:
    """
    Extrai a contagem de avaliações como inteiro, a partir do texto do card.

    O resultado é **sempre** um int (ou None): sufixos de escala são resolvidos
    aqui, nunca repassados como fração para a camada de exportação.

    Args:
        raw: texto ou aria-label bruto do elemento (ex: ``"(1.234)"``,
            ``"4,7 de 5 estrelas, 12.345 avaliações"``, ``"1,2 mil"``).

    Returns:
        Contagem inteira, ou None quando não há número plausível — inclusive
        quando o valor encontrado passa de :data:`MAX_COUNT_SANE`, sinal de
        texto colado/ruído em vez de contagem real.

    Example:
        >>> parse_review_count("(1.234)")
        1234
        >>> parse_review_count("4,7 de 5 estrelas, 12.345 avaliações")
        12345
        >>> parse_review_count("1,2 mil avaliações")
        1200

    Note:
        A versão anterior fazia ``re.sub(r"[^\\d]", "", raw)``, concatenando os
        dígitos da nota com os da contagem ("4,7 … 12.345" → 47512345). Além de
        inflar a métrica, um texto com dígitos suficientes produzia um inteiro
        acima de 2^53 — que o pandas não consegue converter para Int64 sem
        perda e que abortava a exportação do CSV (run #174).
    """
    if raw is None:
        return None
    text = str(raw).replace(" ", " ").strip()
    if not text:
        return None

    # Tira a nota do caminho antes de procurar a contagem.
    text = _RE_RATING_CLAUSE.sub(" ", text)
    text = _RE_STAR_TOKEN.sub(" ", text)

    fallback: Optional[int] = None
    for match in _RE_NUMBER_RUN.finditer(text):
        tail = text[match.end():]
        scale_match = _RE_SCALE.match(tail)
        if scale_match:
            multiplier = _SCALE_LOOKUP[scale_match.group(1).lower()]
            value = _scaled_to_int(match.group(0), multiplier)
            tail = tail[scale_match.end():]
        else:
            value = _digits_to_int(match.group(0))

        if value is None or value < 0 or value > MAX_COUNT_SANE:
            continue

        # Número seguido de palavra de avaliação vence qualquer outro candidato.
        if any(word in tail[:30].lower() for word in _REVIEW_WORDS):
            return value
        if fallback is None:
            fallback = value

    return fallback


def get_turno(hora: Optional[datetime] = None) -> str:
    """
    Retorna 'Abertura' se hora <= TURNO_ABERTURA_MAX_HOUR, senão 'Fechamento'.
    Se hora não fornecida, usa horário atual em Brasília (independente do SO).
    """
    h = hora if hora else now_brt()
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
