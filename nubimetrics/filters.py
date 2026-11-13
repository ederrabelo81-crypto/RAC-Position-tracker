"""Recorte High Wall Inverter, BTU, voltagem e marca — regra única do módulo.

O MCP da Nubimetrics NÃO devolve atributos de anúncio (tipo de ar, inverter,
BTU, voltagem — ver ``catalog/MAPPING.md`` §4.2). A classificação sai do texto:

- ``title_regex``: o título diz (ex.: "Split Hi Wall Inverter 12.000 Btus");
- ``model_regex``: o BTU sai do código de modelo (ex.: ``38EZVCC12M5`` → 12k),
  quando o título veio truncado;
- ``title_default``: o título não nomeia o tipo, mas também não nomeia nenhum
  tipo que NÃO seja hi-wall — no Mercado Livre o hi-wall é o padrão da
  categoria (o portátil, o janela e o piso-teto sempre se anunciam como tais).

``match_source = 'attribute'`` fica reservado para o dia em que o MCP passar a
entregar atributo: o painel mostra o % por origem, para auditoria.

Módulo puro (sem rede, sem Streamlit) — ``tests/test_nubimetrics_filters.py``.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

SEG_HW_INVERTER = "High Wall Inverter"
SEG_HW_ONOFF = "High Wall On/Off"
SEG_PORTATIL = "Portátil"
SEG_JANELA = "Janela"
SEG_PISO_TETO = "Piso-Teto/Cassete"
SEG_OUTROS = "Outros"
SEGMENTOS = (SEG_HW_INVERTER, SEG_HW_ONOFF, SEG_PORTATIL, SEG_JANELA, SEG_PISO_TETO, SEG_OUTROS)

FAIXAS_BTU = ("≤7k", "9k", "12k", "18k", "22–24k", "30k+")

_PORTATIL = re.compile(r"port[aá]til", re.I)
_JANELA = re.compile(r"\bjanela\b|\bde parede tipo janela\b|\bwindow\b", re.I)
_PISO_TETO = re.compile(r"piso[\s-]*teto|\bcassete\b|\bk7\b|\bduto\b|\bdutado\b", re.I)
_OUTROS = re.compile(r"\bmulti[\s-]*split\b|\bbi[\s-]*split\b|\btri[\s-]*split\b|\bclimatizador\b|"
                     r"\bpe[cç]as?\b|\bsuporte\b|\bkit (de )?instala", re.I)
#: Acessório/peça: nunca entra no hi-wall por padrão (o default só vale com o
#: núcleo do produto no título — "ar condicionado", "split", "hi wall", BTU).
_ACESSORIO = re.compile(
    # Ancorado no INÍCIO: o substantivo do produto abre o título ("Controle
    # Remoto ...", "Capa Para Ar ..."). No meio do título "filtro"/"placa" são
    # atributos de ar de verdade ("Split Inverter com Filtro de Prata").
    r"^\W*(controle remoto|capa\b|tubula[cç][aã]o|compressor|placa\b|filtro\b|dreno|bomba\b|"
    r"mangueira|cabo\b|g[aá]s\b|motor\b|kit\b|suporte|pe[cç]as?\b|ventilador\b|serpentina|"
    r"capacitor|sensor|termostato|v[aá]lvula)",
    re.I)
_NUCLEO = re.compile(r"ar[\s-]*condicionado|\bsplit\b|hi[\s-]?wall|high[\s-]?wall|\bbtus?\b|inverter", re.I)
_HW = re.compile(r"hi[\s-]?wall|high[\s-]?wall|\bsplit\b|\bhw\b|\bparede\b", re.I)
_INVERTER = re.compile(r"inverter|\binv\b|\binvt\b|\binvert\b", re.I)
_ONOFF = re.compile(r"on[\s/-]*off|convencional", re.I)
_BTU = re.compile(
    r"(?<![\d.])(\d{1,2}(?:[.\s]\d{3}|\d{3})|\d{1,2}\s?k)\s*(?:btu|btus|btu/h)?(?![\d])", re.I
)
_BTU_MODELO = re.compile(r"(?<!\d)(07|09|12|18|22|24|30|36)(?!\d)")
_VOLT = re.compile(r"\b(220|127|110)\s?v\b|\bbivolt\b", re.I)
_BIVOLT_PAR = re.compile(r"\b(110|127)\s?v?\s*(/|e|-)\s*220\s?v\b|\b220\s?v?\s*(/|e|-)\s*(110|127)\s?v\b", re.I)

#: Grafia de exibição por marca normalizada (cor em utils/ui_tokens.py).
_MARCA_EXIBICAO = {
    "MIDEA": "Midea", "LG": "LG", "PHILCO": "Philco", "GREE": "Gree",
    "SAMSUNG": "Samsung", "ELGIN": "Elgin", "TCL": "TCL", "ELECTROLUX": "Electrolux",
    "AGRATTO": "Agratto", "BRITANIA": "Britânia", "CARRIER": "Carrier",
    "SPRINGER": "Springer", "SPRINGER MIDEA": "Springer Midea", "DAIKIN": "Daikin",
    "FUJITSU": "Fujitsu", "HITACHI": "Hitachi", "CONSUL": "Consul", "KOMECO": "Komeco",
    "VIX": "Vix", "HISENSE": "Hisense", "GALLANT": "Gallant",
}


def normalizar_marca(raw: Optional[str]) -> str:
    """Caixa alta, sem acento e sem espaço sobrando (``Britânia`` = ``BRITANIA``)."""
    if not raw:
        return ""
    sem_acento = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def marca_exibicao(raw: Optional[str]) -> str:
    """Nome de marca para o painel (``MIDEA`` → ``Midea``)."""
    norm = normalizar_marca(raw)
    return _MARCA_EXIBICAO.get(norm, norm.title() if norm else "—")


def is_midea(raw: Optional[str]) -> bool:
    """Midea inclui as sub-linhas (AI Ecomaster, Xtreme Save…) — a marca é MIDEA."""
    # Token MIDEA em qualquer posição: MIDEA, MIDEA BRANCA, SPRINGER MIDEA,
    # "CARRIER | MIDEA" (grafias reais do ranking de out/2026). CARRIER e
    # SPRINGER sozinhos ficam de fora — são marcas próprias no ranking.
    return "MIDEA" in re.findall(r"[A-Z0-9]+", normalizar_marca(raw))


def parse_btu(texto: Optional[str]) -> Optional[int]:
    """BTU do texto: ``9.000``/``9000``/``9k``/``12 000 Btus`` → int. Fora de 5k–60k = None."""
    if not texto:
        return None
    for m in _BTU.finditer(str(texto)):
        bruto = m.group(1).lower().replace(" ", "")
        valor = int(bruto[:-1]) * 1000 if bruto.endswith("k") else int(re.sub(r"\D", "", bruto))
        # "Ar 220V 12000" — exige contexto: número de 4–5 dígitos ou 'k'
        if 5000 <= valor <= 60000:
            return valor
    return None


def btu_do_modelo(modelo: Optional[str]) -> Optional[int]:
    """BTU pelo código de modelo (``38EZVCC12M5`` → 12000). Só modelos com letras."""
    if not modelo or not re.search(r"[A-Za-z]", modelo):
        return None
    m = _BTU_MODELO.search(modelo)
    return int(m.group(1)) * 1000 if m else None


def faixa_btu(btu: Optional[int]) -> Optional[str]:
    """Faixa de BTU do painel."""
    if not btu:
        return None
    if btu < 8500:
        return "≤7k"
    if btu <= 10500:
        return "9k"
    if btu <= 13500:
        return "12k"
    if btu <= 19500:
        return "18k"
    if btu <= 25000:
        return "22–24k"
    return "30k+"


def parse_voltagem(texto: Optional[str]) -> Optional[str]:
    """``220v`` → ``220V``; ``110v``/``127v`` → ``127V``; ``bivolt``."""
    if not texto:
        return None
    if _BIVOLT_PAR.search(str(texto)):
        return "Bivolt"
    m = _VOLT.search(str(texto))
    if not m:
        return None
    if m.group(1) is None:
        return "Bivolt"
    return "220V" if m.group(1) == "220" else "127V"


@dataclass(frozen=True)
class Classificacao:
    """Resultado da classificação de um anúncio."""

    segmento: str
    inverter: Optional[bool]
    btu: Optional[int]
    faixa_btu: Optional[str]
    voltagem: Optional[str]
    match_source: str


def classificar(titulo: Optional[str], modelo: Optional[str] = None) -> Classificacao:
    """Segmento, inverter, BTU e voltagem de um anúncio de ar condicionado.

    Args:
        titulo: Título do anúncio (pode vir truncado em 40 caracteres).
        modelo: Código de modelo (fallback do BTU).
    """
    t = titulo or ""
    inverter = True if _INVERTER.search(t) else (False if _ONOFF.search(t) else None)
    btu = parse_btu(t)
    btu_pelo_modelo = False
    if btu is None:
        btu = btu_do_modelo(modelo)
        btu_pelo_modelo = btu is not None

    inferido = False
    if _PORTATIL.search(t):
        seg = SEG_PORTATIL
    elif _JANELA.search(t):
        seg = SEG_JANELA
    elif _PISO_TETO.search(t):
        seg = SEG_PISO_TETO
    elif _OUTROS.search(t) or _ACESSORIO.search(t) or not _NUCLEO.search(t):
        seg = SEG_OUTROS
    elif inverter is False:
        seg = SEG_HW_ONOFF
        inferido = not _HW.search(t)
    else:
        # Hi-wall é o padrão da categoria; sem "inverter" no título o anúncio
        # vai para inverter (≈ todo o hi-wall vendido no ML hoje), mas marcado
        # como inferido — o painel mostra a incerteza.
        seg = SEG_HW_INVERTER
        inferido = not _HW.search(t) or inverter is None

    # Origem mais fraca vence: o painel audita a classificação pela pior parte.
    fonte = "title_default" if inferido else ("model_regex" if btu_pelo_modelo else "title_regex")
    return Classificacao(seg, inverter, btu, faixa_btu(btu), parse_voltagem(t), fonte)
