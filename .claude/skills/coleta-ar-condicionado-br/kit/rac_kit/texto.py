"""Parsers de texto do mercado brasileiro de ar condicionado.

Preço em formato brasileiro, nota, capacidade em BTU, marca e o filtro "isto é
um ar condicionado?". Funções puras: sem rede, sem browser.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")

# Marcas monitoradas. A ORDEM importa: nome composto antes do simples, senão
# "Springer Midea" seria lido como "Midea". Edite à vontade para o seu recorte.
MARCAS: List[str] = [
    "Springer Midea", "Midea Carrier", "Midea", "Carrier", "Elgin",
    "Electrolux", "Agratto", "Springer", "Consul", "Daikin", "Fujitsu",
    "Hitachi", "York", "Gree", "TCL", "Hisense", "Aufit", "Haier", "Britânia",
    "Britania", "Vix", "HQ", "Colormaq", "EOS", "Komeco", "LG", "Samsung",
    "Philco", "Panasonic", "Trane", "Rheem", "Lennox", "AIWA",
    "American Range", "Geminis", "Fontaine", "Luxor", "Turbro", "Velleman",
    "Whynter", "DeLonghi", "Kian", "Equation",
]

_MARCA_PATTERNS = [
    (marca, re.compile(rf"\b{re.escape(marca)}\b", re.IGNORECASE))
    for marca in MARCAS
]

# Turnos de coleta (08h / 14h / 20h): até 11h é Abertura, até 17h é Tarde.
TURNO_ABERTURA_MAX_HOUR = 11
TURNO_TARDE_MAX_HOUR = 17


def now_brt() -> datetime:
    """Hora atual em Brasília, independente do fuso da máquina."""
    return datetime.now(BRT)


def get_turno(hora: Optional[datetime] = None) -> str:
    """Turno da coleta ('Abertura', 'Tarde' ou 'Fechamento') pela hora BRT."""
    h = hora if hora else now_brt()
    if h.hour <= TURNO_ABERTURA_MAX_HOUR:
        return "Abertura"
    if h.hour <= TURNO_TARDE_MAX_HOUR:
        return "Tarde"
    return "Fechamento"


def parse_price_brazil(raw_text: Optional[str]) -> Optional[float]:
    """Extrai o primeiro preço de um texto em formato brasileiro.

    Lida com "R$ 1.994,91", texto colado do DOM ("13% OFFR$ 1.709,91no pix"),
    espaço não separável ("R$\\xa02.184,05"), notação de float ("R$ 1829.0",
    comum em JSON) e inteiro cru ("2339").

    Args:
        raw_text: Texto bruto.

    Returns:
        O preço, ou None se não houver um valor válido (0 < preço <= 10 mi).
    """
    if not raw_text:
        return None

    # "1829.0" é float de JSON, não milhar: sem esta guarda viraria 18290.
    float_match = re.search(r"(?:R\$\s*)?(\d+\.\d{1,2})$", raw_text.strip())
    if float_match and "," not in raw_text:
        try:
            return float(float_match.group(1))
        except ValueError:
            pass

    match = re.search(r"R\$\s*([\d.,]+)|([\d]+\.[\d]{3},[\d]{2})", raw_text)
    if not match:
        match = re.search(
            r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)", raw_text
        )
        if not match:
            return None

    val = match.group(1) if match.group(1) else match.group(2)
    if not val:
        return None
    val = val.replace(".", "").replace(",", ".")
    try:
        result = float(val)
    except ValueError:
        return None
    if result <= 0 or result > 10_000_000:
        return None
    return result


def parse_rating(raw: Optional[str]) -> Optional[float]:
    """Nota de 0 a 5 ("4,8", "(4.8)"). Acima de 5 é lixo de parsing: None."""
    if not raw:
        return None
    match = re.search(r"(\d+)[,.](\d+)", raw)
    if match:
        val = float(f"{match.group(1)}.{match.group(2)}")
        return val if val <= 5 else None
    match = re.search(r"(\d+)", raw)
    if match:
        val = float(match.group(1))
        return val if val <= 5 else None
    return None


def extract_brand(title: Optional[str]) -> str:
    """Primeira marca de ``MARCAS`` presente no título, ou "Desconhecida".

    Usa fronteira de palavra: "portacarrier" não vira Carrier.
    """
    if not title:
        return "Desconhecida"
    for marca, pattern in _MARCA_PATTERNS:
        if pattern.search(title):
            return marca
    return "Desconhecida"


_BTU_RE = re.compile(r"(\d{1,2})[.,](\d{3})\s*btu|(\d{4,6})\s*btu", re.IGNORECASE)

# Formatos que não competem em preço com o split hi-wall no mesmo BTU.
_RE_FORA_HIWALL = re.compile(
    r"port[aá]til|janela|janeleiro|cassete|cassette|piso[\s-]?teto|multi[\s-]?split",
    re.IGNORECASE,
)


def extract_btu(produto: object) -> Optional[int]:
    """Capacidade em BTU lida do nome ("12.000 BTUs", "9000btu")."""
    if not isinstance(produto, str):
        return None
    m = _BTU_RE.search(produto)
    if not m:
        return None
    if m.group(3):
        return int(m.group(3))
    return int(m.group(1)) * 1000 + int(m.group(2))


def is_fora_hiwall(produto: object) -> bool:
    """True para portátil, janela, cassete, piso-teto e multi-split.

    No mesmo BTU esses formatos custam outra coisa: misturá-los numa mediana
    de preço muda o número sem o mercado mudar.
    """
    return isinstance(produto, str) and bool(_RE_FORA_HIWALL.search(produto))


# Qualquer termo forte confirma ar condicionado.
_AC_STRONG_TERMS = [
    r"\bar[- ]condicionado\b", r"\bbtu[s]?\b", r"\bevaporadora\b",
    r"\bcondensadora\b", r"\bhi[- ]?wall\b", r"\bmini[- ]?split\b",
    r"\bcassete\b",
]
# Termo fraco não prova sozinho: geladeira e lavadora também são "inverter".
_AC_WEAK_TERMS = [r"\bsplit\b", r"\binverter\b"]
_NON_AC_TERMS = [
    r"\biphones?\b", r"\bipad\b", r"\bnotebook\b", r"\blaptop\b",
    r"\bcelular\b", r"\bsmartphone\b", r"\bfralda[s]?\b", r"\bfrald[ao]\b",
    r"\bgeladeira\b", r"\brefrigerador\b", r"\bfog[aã]o\b", r"\bmicroondas\b",
    r"\btablet\b", r"\bairpods?\b", r"\bmacbook\b", r"\bcolch[aã]o\b",
    r"\bsof[aá]\b",
]


def is_valid_product(name: str, price: Optional[float] = None) -> bool:
    """True se o título é de um ar condicionado (e o preço, se dado, é plausível).

    Rejeita preço fora de R$ 200–80.000 e títulos da lista de exclusão; aprova
    com um termo forte ou dois fracos ("split" + "inverter").
    """
    if not name:
        return False
    if price is not None and (price < 200 or price > 80_000):
        return False
    low = name.lower()
    if any(re.search(p, low) for p in _NON_AC_TERMS):
        return False
    if any(re.search(p, low) for p in _AC_STRONG_TERMS):
        return True
    return sum(1 for p in _AC_WEAK_TERMS if re.search(p, low)) >= 2
