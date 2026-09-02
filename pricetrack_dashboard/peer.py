"""
Peer competitivo RAC (Só Frio / CO) — 9.000 e 12.000 BTU.

Fonte: planilha ``Peer to Peer`` do time comercial (aba ``Peer to Peer CO``).
Cada tier corresponde a uma linha Midea e ao grupo de concorrentes com quem
ela briga no mesmo ponto de preço:

    Low  → Inverter Lite (entry)
    Mid  → AI AirVolution
    High → AI Ecomaster

O peer é o **contrato** que define quais SKUs entram em cada faixa. Ele não é
lido de preço nenhum: só diz *quem compete com quem*. O casamento de uma oferta
coletada com um modelo do peer é feito por **código de modelo do fabricante**
(ex.: ``42EBVCA09M5``), que é distintivo o suficiente para casar por substring
no texto normalizado da oferta (sku + título + nome do produto).

Regra dura: código de modelo é a chave. Nunca inferir tier por preço — isso
inverteria a lógica (o objetivo é justamente medir o preço *dado* o tier).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# Rótulos de tier ------------------------------------------------------------
TIER_LOW = "Low"
TIER_MID = "Mid"
TIER_HIGH = "High"

# Nome comercial da linha Midea por tier
TIER_MIDEA_LINE: Dict[str, str] = {
    TIER_LOW: "Inverter Lite",
    TIER_MID: "AI AirVolution",
    TIER_HIGH: "AI Ecomaster",
}
TIER_ORDER: List[str] = [TIER_LOW, TIER_MID, TIER_HIGH]

# Capacidades no escopo (BTU)
CAP_9K = "9K"
CAP_12K = "12K"
CAP_ORDER: List[str] = [CAP_9K, CAP_12K]

MIDEA = "MIDEA"


@dataclass(frozen=True, slots=True)
class PeerModel:
    """Um modelo do peer: marca + os códigos de fabricante que o identificam."""

    brand: str
    codes: Sequence[str]           # já normalizados (só A-Z0-9)
    raw: str                       # grafia original, para depuração/UI

    @property
    def is_midea(self) -> bool:
        return self.brand.upper() == MIDEA


@dataclass(frozen=True, slots=True)
class PeerTier:
    """Um (tier, capacidade): a linha Midea + os concorrentes daquela faixa."""

    tier: str
    capacity: str
    midea_line: str
    models: List[PeerModel] = field(default_factory=list)

    @property
    def midea_models(self) -> List[PeerModel]:
        return [m for m in self.models if m.is_midea]

    @property
    def competitor_models(self) -> List[PeerModel]:
        return [m for m in self.models if not m.is_midea]


# ── Normalização de código de modelo ────────────────────────────────────────

_ANNOTATION_RE = re.compile(r"\((?:phase\s*in|phase\s*out|fs)\)", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")
# Códigos curtos demais casariam com qualquer coisa (falso positivo). Um código
# de modelo real de ar-condicionado tem pelo menos ~6 caracteres.
_MIN_CODE_LEN = 6


def normalize_code(text: str) -> str:
    """Reduz um código a letras/dígitos maiúsculos, para casamento robusto."""
    return _NON_ALNUM_RE.sub("", text.upper())


def pretty_first_token(raw: str) -> str:
    """Primeiro código de uma célula do peer, na grafia ORIGINAL (para exibição).

    ``split_model_codes`` normaliza para casamento (só A-Z0-9, sem hífen); aqui
    mantemos a pontuação original (``"TAC-09CSGV-INV"``, não
    ``"TAC09CSGVINV"``) — só as anotações de fase/FS são removidas.
    """
    cleaned = _ANNOTATION_RE.sub(" ", raw)
    for part in re.split(r"[/+|,]", cleaned):
        text = part.strip()
        if text:
            return text
    return raw.strip()


def split_model_codes(raw: str) -> List[str]:
    """Quebra uma célula do peer em códigos de modelo normalizados.

    Uma célula pode trazer combos (evaporadora/condensadora) e anotações:
    ``"38TAVCA09M5+ 42EFVCA09M5"``, ``"S3-Q09AA31F (phase in)"``,
    ``"QCL078RB (FS)"``. Split em ``/``, ``+``, ``|`` e vírgula; anotações
    entre parênteses são descartadas.
    """
    cleaned = _ANNOTATION_RE.sub(" ", raw)
    parts = re.split(r"[/+|,]", cleaned)
    codes: List[str] = []
    for part in parts:
        code = normalize_code(part)
        if len(code) >= _MIN_CODE_LEN and code not in codes:
            codes.append(code)
    return codes


# ── Definição do peer (aba "Peer to Peer CO") ────────────────────────────────
# Estrutura crua: {tier: {capacity: [(brand, "grafia crua do(s) código(s)"), ...]}}
# Mantida legível de propósito — é o espelho da planilha e o ponto onde o time
# comercial confere/edita quando o peer muda de trimestre.
_PEER_RAW: Dict[str, Dict[str, List[tuple]]] = {
    TIER_LOW: {  # Inverter Lite
        CAP_9K: [
            (MIDEA, "42EBVCA09M5/38TBVCA09M5"),
            ("AGRATTO", "LCST9F-02I"),
            ("ELGIN", "45HJFI09C2WB"),
            ("GREE", "GWC09ATA-D6DNA2C"),
            ("LG", "S3-Q09AAQAK"),
            ("PHILCO", "PAC9FC"),
            ("PHILCO", "PAC9FB"),
            ("TCL", "TAC-09CSGV-INV"),
            ("HISENSE", "AS-09UW2RLD00C"),
        ],
        CAP_12K: [
            (MIDEA, "42EBVCA12M5/38TBVCA12M5"),
            ("AGRATTO", "LCST12F-02I"),
            ("ELGIN", "HIFC12C2WACA"),
            ("LG", "S3-Q12JAQAL"),
            ("GREE", "GWC12ATB-D6DNA4A"),
            ("PHILCO", "PAC12FC"),
            ("PHILCO", "PAC12FB"),
            ("HISENSE", "AS-12TW2RLRCK00E"),
            ("TCL", "TAC-12CGV"),
        ],
    },
    TIER_MID: {  # AI AirVolution
        CAP_9K: [
            (MIDEA, "38TAVCA09M5+ 42EFVCA09M5"),
            ("GREE", "GWC09ATBXB-D6DNA3B (phase in)"),
            ("ELGIN", "HJFI09C2WC"),
            ("HISENSE", "AS-09TW2RLR00"),
            ("TCL", "TAC-09CTG2-INV"),
        ],
        CAP_12K: [
            (MIDEA, "38TAVCA12M5 + 42EFVCA12M5"),
            ("GREE", "GWC12ATCXB-D6DNA3C (phase in)"),
            ("ELGIN", "45HJFE12C2CC"),
            ("HISENSE", "AS-12TW2RLD00C"),
            ("LG", "S3-Q12JA31E (phase in)"),
            ("TCL", "TAC-12CTG2-INV"),
        ],
    },
    TIER_HIGH: {  # AI Ecomaster
        CAP_9K: [
            (MIDEA, "38EZVCA09M5 + 42EZVCA09M5"),
            ("GREE", "GWC09ATB-D6DNA1A (phase out)"),
            ("LG", "S3-Q09AA31F (phase in)"),
            ("SAMSUNG", "AR09DYFAAWKNAZ"),
        ],
        CAP_12K: [
            (MIDEA, "38EZVCA12M5 + 42EZVCA12M5"),
            ("GREE", "GWC12ATC-D6DNA1A (phase out)"),
            ("GREE", "GWC12AVCXB-D6DNA1D (phase in)"),
            ("LG", "S3-Q12JA31L"),
            ("SAMSUNG", "AR12DYFAAWKNAZ"),
            ("TCL", "TAC-12CFG3O"),
        ],
    },
}


def _build_peer() -> Dict[str, Dict[str, PeerTier]]:
    peer: Dict[str, Dict[str, PeerTier]] = {}
    for tier, caps in _PEER_RAW.items():
        peer[tier] = {}
        for cap, rows in caps.items():
            models: List[PeerModel] = []
            for brand, raw in rows:
                codes = split_model_codes(raw)
                if not codes:
                    continue
                models.append(PeerModel(brand=brand, codes=tuple(codes), raw=raw))
            peer[tier][cap] = PeerTier(
                tier=tier,
                capacity=cap,
                midea_line=TIER_MIDEA_LINE[tier],
                models=models,
            )
    return peer


PEER: Dict[str, Dict[str, PeerTier]] = _build_peer()


def all_tiers() -> List[PeerTier]:
    """Todos os (tier, capacidade) na ordem canônica Low→High, 9K→12K."""
    return [PEER[t][c] for t in TIER_ORDER for c in CAP_ORDER]


# Índice invertido: código normalizado → (tier, capacity, brand, is_midea).
# Códigos mais longos primeiro para casar o mais específico quando um é prefixo
# do outro.
@dataclass(frozen=True, slots=True)
class CodeHit:
    tier: str
    capacity: str
    brand: str
    is_midea: bool
    code: str
    model_raw: str      # grafia crua da célula do peer — chave do modelo EXATO
                         # (um brand pode ter mais de um modelo no mesmo tier/cap,
                         # ex.: Philco PAC9FC e PAC9FB no Low/9K — `raw` os distingue)


def _build_index() -> List[CodeHit]:
    hits: List[CodeHit] = []
    for pt in all_tiers():
        for m in pt.models:
            for code in m.codes:
                hits.append(
                    CodeHit(pt.tier, pt.capacity, m.brand, m.is_midea, code, m.raw)
                )
    hits.sort(key=lambda h: len(h.code), reverse=True)
    return hits


_CODE_INDEX: List[CodeHit] = _build_index()


def match_haystack(*fields: Optional[str]) -> Optional[CodeHit]:
    """Casa o texto de uma oferta com um modelo do peer, ou None.

    Concatena os campos textuais da oferta (sku, título, nome do produto),
    normaliza para só letras/dígitos e procura o primeiro código de modelo do
    peer contido nele. Retorna o hit mais específico (código mais longo) por
    causa da ordenação do índice.
    """
    haystack = normalize_code(" ".join(f for f in fields if f))
    if not haystack:
        return None
    for hit in _CODE_INDEX:
        if hit.code in haystack:
            return hit
    return None
