"""
attr_parser.py — Parser de atributos de títulos de RAC (ar-condicionado).

Uso no de-para do RAC Monitor: transforma um título bruto (coletas.produto ou
pricetrack_daily.title) em atributos estruturados, para casar contra o catálogo
canônico por IGUALDADE DE ATRIBUTOS (e não por "título contém X").

Escopo (Anexo A):
  VALIDADO contra títulos reais das duas fontes — seções 0 a 4:
    - norm / strip_sku            (0)
    - capacidade_btu              (1)
    - ciclo                       (2)
    - tecnologia                  (3)
    - voltagem / form_factor      (4)
  SEED de domínio (estender minerando pricetrack_daily.title) — seções 5 e 6:
    - marca, edição, cor

NÃO faz match contra catálogo nem resolve SKU (seção 7): isso depende do catálogo
vivo e é testado à parte. Regra de ouro: o que não resolver com confiança retorna
None. Nunca chutar.

Sem dependências externas (somente stdlib). pytest é dependência só de teste.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Optional

# ─────────────────────────────────────────────────────────────── seção 0
def norm(s) -> str:
    """Maiúsculas, sem acento, só [A-Z0-9 / . -] e espaço único."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.upper()
    s = re.sub(r"[^A-Z0-9/\.\-\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Token de SKU no fim (formato pricetrack: "... - 42EZVCA12M5").
# Exige ter dígito E letra e tamanho >= 6, para nunca capturar palavra
# portuguesa (QUENTE/FRIO, PRETO) nem voltagem (220V).
_SKU_TAIL = re.compile(
    r"[-\s]+("
    r"(?=[A-Z0-9/\-]*[0-9])"      # contém >=1 dígito
    r"(?=[A-Z0-9/\-]*[A-Z])"      # contém >=1 letra
    r"[A-Z0-9][A-Z0-9/\-]{5,}"    # >= 6 chars alfanum/sep
    r")\s*$"
)


def strip_sku(n: str):
    """Remove o token de SKU do fim. Retorna (texto_sem_sku, sku_ou_None)."""
    m = _SKU_TAIL.search(n)
    return (n[: m.start()].strip(), m.group(1)) if m else (n, None)


# ─────────────────────────────────────────────────────────────── seção 1
_BTU_WL = {7000, 9000, 10000, 12000, 18000, 22000, 24000,
           28000, 30000, 31000, 36000, 48000, 58000, 60000}
_BTU_ANCHORED = re.compile(r"(\d{1,2})[\.\s]?000\s*BTU|(\d{1,2})\s?K\s*BTU")
_BTU_BARE = re.compile(r"\b(\d{1,2})[\.\s]?000\b|\b(\d{1,2})\s?K\b")


def parse_btu(n: str) -> Optional[int]:
    """Capacidade em BTU. Whitelist mata model numbers aleatórios."""
    m = _BTU_ANCHORED.search(n) or _BTU_BARE.search(n)
    if not m:
        return None
    v = int(next(g for g in m.groups() if g)) * 1000
    return v if v in _BTU_WL else None


# ─────────────────────────────────────────────────────────────── seção 2
# ORDEM IMPORTA: Quente/Frio é testado antes de Frio (contém a palavra "FRIO").
_CICLO_QF = re.compile(
    r"QUENTE\s*[/E\-\s]+\s*FRIO|\bQ\s*/\s*F\b|\bQF\b|REVERSO|\bHEAT\b")
_CICLO_F = re.compile(
    r"\bFRIO\b|SO\s+FRIO|SOMENTE\s+FRIO|APENAS\s+FRIO|\bCOLD\b")


def parse_ciclo(n: str) -> Optional[str]:
    if _CICLO_QF.search(n):
        return "Quente/Frio"
    if _CICLO_F.search(n):
        return "Frio"
    return None


# ─────────────────────────────────────────────────────────────── seção 3
# Dual Inverter (LG) é valor próprio; testar antes de Inverter.
_TEC_DUAL = re.compile(r"DUAL\s+INVERTER")
_TEC_INV = re.compile(r"\bINVERTER\b|\bINV\b|SMART\s+INVERTER|ALL\s+INVERTER")
_TEC_ONOFF = re.compile(
    r"ON\s*[/\-]?\s*OFF|\bON\s*OFF\b|CONVENCIONAL|TRADICIONAL|FIXED\s+SPEED")


def parse_tec(n: str) -> Optional[str]:
    if _TEC_DUAL.search(n):
        return "Dual Inverter"
    if _TEC_INV.search(n):
        return "Inverter"
    if _TEC_ONOFF.search(n):
        return "On/Off"
    return None   # AUSENTE != On/Off. Inferência fica para o matcher (seção 7).


# ─────────────────────────────────────────────────────────────── seção 4
_VOLT_BIVOLT = re.compile(r"\bBIVOLT\b")
_VOLT_NUM = re.compile(r"\b(220|127|115|110)\s*V\b")


def parse_voltagem(n: str) -> Optional[str]:
    if _VOLT_BIVOLT.search(n):
        return "Bivolt"
    m = _VOLT_NUM.search(n)
    return f"{m.group(1)}V" if m else None


# Guarda de form factor: foco é HIGH WALL. Não-HW não casa SKU HW.
_NAO_HW = re.compile(
    r"CASSETE|PISO\s*[-\s]?TETO|JANELA|PORTATIL|MULTI\s*SPLIT|\bMULTI\b|DUTAD|BUILT\s*IN")
_HW = re.compile(r"HI\s*[-\s]?WALL|HIGH\s*WALL|SPLIT|PAREDE")


def parse_form_factor(n: str) -> str:
    if _NAO_HW.search(n):
        return "NAO_HW"
    if _HW.search(n):
        return "HW"
    return "HW_PRESUMIDO"   # nada indica formato: assume HW, mas sinaliza presunção


# ─────────────────────────────────────────────────── seções 5-6 (SEED)
# Marca: Springer/Comfee ficam SEPARADAS de Midea de propósito (catálogo é o
# árbitro; fundir sub-marca seria o mesmo defeito que estamos consertando).
_MARCA = [
    ("Springer", r"\bSPRINGER\b"),
    ("Comfee", r"\bCOMFEE\b"),
    ("Midea", r"\bMIDEA\b"),
    ("LG", r"\bLG\b"),
    ("Samsung", r"\bSAMSUNG\b"),
    ("Gree", r"\bGREE\b"),
    ("TCL", r"\bTCL\b|\bAIWA\b"),
    ("Elgin", r"\bELGIN\b"),
    ("Philco", r"\bPHILCO\b"),
    ("Hisense", r"\bHISENSE\b"),
    ("Daikin", r"\bDAIKIN\b"),
    ("Fujitsu", r"\bFUJITSU\b"),
    ("Carrier", r"\bCARRIER\b"),
    ("Consul", r"\bCONSUL\b"),
    ("Electrolux", r"\bELECTROLUX\b"),
]
_MARCA = [(nome, re.compile(rx)) for nome, rx in _MARCA]


def parse_marca(n: str) -> Optional[str]:
    for nome, rx in _MARCA:
        if rx.search(n):
            return nome
    return None


# Edição/linha por marca. Específico antes de genérico. Filtra pela marca
# detectada para evitar contaminação cruzada de tokens ("Eco", "Pro"...).
_EDICAO = [
    ("Midea", "Ecomaster Pro", r"ECOMASTER\s+PRO"),
    ("Midea", "Ecomaster", r"\bECOMASTER\b"),
    ("Midea", "Airvolution Lite", r"AIRVOLUTION\s+LITE|\bLITE\b"),
    ("Midea", "Airvolution", r"\bAIRVOLUTION\b"),
    ("Midea", "Xtreme Save", r"XTREME\s+SAVE"),
    ("LG", "Dual Inverter Voltage", r"DUAL\s+INVERTER\s+VOLTAGE|\bVOLTAGE\b"),
    ("LG", "Artcool", r"ARTCOOL"),
    ("TCL", "Elite", r"\bELITE\b"),
    ("TCL", "T-Pro", r"T\s*-?\s*PRO"),
    ("Gree", "Fresh In 3.0", r"FRESH\s+IN\s+3"),
    ("Gree", "Fresh In", r"FRESH\s+IN"),
    ("Gree", "G-Top", r"G\s*-?\s*TOP"),
    ("Gree", "Eco Garden", r"ECO\s+GARDEN"),
    ("Samsung", "WindFree Connect", r"WIND\s*-?\s*FREE\s+CONNECT"),
    ("Samsung", "WindFree", r"WIND\s*-?\s*FREE"),
    ("Elgin", "Eco Inverter", r"ECO\s+INVERTER"),
    ("Elgin", "Eco Power", r"ECO\s+POWER"),
]
_EDICAO = [(m, canon, re.compile(rx)) for m, canon, rx in _EDICAO]


def parse_edicao(n: str, marca: Optional[str]) -> Optional[str]:
    # Edições são específicas por marca: sem marca não dá para atribuir uma
    # (tokens genéricos como LITE/ELITE contaminariam a chave). Sem marca → None.
    if marca is None:
        return None
    for m, canon, rx in _EDICAO:
        if m != marca:
            continue
        if rx.search(n):
            return canon
    return None


# Cor é atributo à parte. Por R4, só separa SKUs se o catálogo distinguir;
# por isso fica fora da chave primária e é decidido pelo matcher.
_COR = re.compile(r"\bPRETO\b|\bBLACK\b|\bDARK\b")


def parse_cor(n: str) -> Optional[str]:
    return "Preto" if _COR.search(n) else None


# ─────────────────────────────────────────────────────────────── saída
@dataclass
class Atributos:
    titulo_raw: str
    titulo_norm: str
    sku_no_titulo: Optional[str]
    form_factor: str
    marca: Optional[str]
    capacidade_btu: Optional[int]
    ciclo: Optional[str]
    tecnologia: Optional[str]
    edicao: Optional[str]
    cor: Optional[str]
    voltagem: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def chave(self) -> tuple:
        """Chave canônica para match (seção 7). Cor fica de fora (R4)."""
        return (self.marca, self.capacidade_btu, self.ciclo,
                self.tecnologia, self.edicao)


def parse(titulo) -> Atributos:
    raw = str(titulo)
    n_full = norm(raw)
    n, sku = strip_sku(n_full)          # parseia BTU etc. já sem o código de SKU
    marca = parse_marca(n)
    return Atributos(
        titulo_raw=raw,
        titulo_norm=n_full,
        sku_no_titulo=sku,
        form_factor=parse_form_factor(n),
        marca=marca,
        capacidade_btu=parse_btu(n),
        ciclo=parse_ciclo(n),
        tecnologia=parse_tec(n),
        edicao=parse_edicao(n, marca),
        cor=parse_cor(n),
        voltagem=parse_voltagem(n),
    )


def parse_many(titulos) -> list[Atributos]:
    return [parse(t) for t in titulos]


if __name__ == "__main__":
    import json
    exemplos = [
        "Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio",
        "AR CONDICIONADO SPLIT 12000 BTU QUENTE/FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - 42EZVQA12M5",
        "TCL 12.000 On/Off Frio",
        "LG Dual Inverter 18.000 Q/F",
    ]
    for a in parse_many(exemplos):
        print(json.dumps(a.to_dict(), ensure_ascii=False, indent=2))
