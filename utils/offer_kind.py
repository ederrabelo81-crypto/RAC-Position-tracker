"""
utils/offer_kind.py — O anúncio é UM ar condicionado completo, vendido sozinho?

Por que existe (Out/2026): `normalize_product_name` reescrevia todo título com
marca + BTU para o nome canônico do modelo. "Kit Instalação Completo Ar
Condicionado TCL 9000" (R$ 255), "Condensadora Gree 9000" e "Kit 2 Ar
Condicionado TCL Elite GV" viravam o mesmo "Ar Condicionado TCL Elite 9.000
BTUs Inverter Frio" — e o gatilho `trg_resolve_familia_coletas` casava esse nome
com o SKU do aparelho (`estado_match = MAPEADO`). A série de preço por SKU
recebia preço de kit, de meia unidade e de duas unidades. Na comparação com o
PriceTrack (14–15/10/2026), as maiores divergências eram esse erro nosso.

Regra: só `UNIDADE` ganha nome canônico (e, com ele, SKU). Os outros tipos
seguem com o título cru — continuam na coleta (share de prateleira, posição,
buy box), mas não entram em série de preço por SKU nem na auditoria do
PriceTrack.

Funciona sobre o título cru OU sobre a URL do anúncio (o slug é o único rastro
do título que sobrevive nas linhas antigas, já gravadas com nome canônico).
Módulo puro: sem I/O. Testes em `tests/test_offer_kind.py`, com títulos e URLs
reais da base.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional
from urllib.parse import unquote, urlsplit

UNIDADE = "UNIDADE"              # um aparelho completo, vendido sozinho
ACESSORIO = "ACESSORIO"          # kit de instalação, suporte, peça, capa...
PARCIAL = "PARCIAL"              # só a condensadora ou só a evaporadora
MULTIPLO = "MULTIPLO"            # kit com 2+ aparelhos no mesmo preço
COM_INSTALACAO = "COM_INSTALACAO"  # aparelho + serviço de instalação no preço

#: Tipos cujo preço NÃO é o preço de uma unidade do SKU.
NAO_UNIDADE = frozenset({ACESSORIO, PARCIAL, MULTIPLO, COM_INSTALACAO})

# Núcleo que identifica o aparelho. "condicionado" sozinho cobre o slug da
# Amazon, que corta o "ar" ("condicionado-portatil-...").
_RE_NUCLEO = re.compile(
    r"\b(?:ar\s*condicionado|condicionador\s+de\s+ar|condicionado|split|"
    r"hi\s*wall|high\s*wall|air\s+conditioner)\b"
)

# Termos de acessório/peça. Só desqualificam quando vêm ANTES do núcleo —
# "Ar Condicionado Portátil com Controle Remoto" é aparelho; "Controle Remoto
# para Ar Condicionado" é acessório. Mesmo critério posicional de
# `bestsellers/models.py::_e_acessorio`.
_RE_ACESSORIO = re.compile(
    r"\b(?:kit\s+instala\w*|instala\w*|suporte|capa|controle|higieniza\w*|"
    r"limpa\w*|tubula\w*|dreno|silicone|filtro|spray|bomba|mangueira|manifold|"
    r"gas\s+refrigerante|vacuo|motor|placa|caixa|telhado|protetor|protecao|"
    r"compressor|turbina|capacitor|sensor|termistor|ventilador|evaporador|"
    r"serpentina|display)\b"
)
# Frases com termo de acessório que descrevem o próprio aparelho.
_RE_FALSO_ACESSORIO = re.compile(r"\bbomba\s+de\s+calor\b")

# Só uma das metades do split. "condesandora" é grafia real de anúncio.
_RE_CONDENSADORA = re.compile(r"\b(?:condensadora|condesandora|condensador)\b")
_RE_EVAPORADORA = re.compile(r"\bevaporadora\b")
_RE_SO_METADE = re.compile(
    r"\b(?:somente|apenas|so|avulsa|sem)\s+(?:a\s+)?(?:condensadora|evaporadora)\b"
    r"|\bnao\s+e\s+(?:o\s+)?(?:ar\s+)?split\s+completo\b"
)

# Dois ou mais aparelhos num preço só. Exige separador depois de "kit": o
# "kit9426" da Frigelar é código de produto, não kit com 9 aparelhos.
_RE_MULTIPLO = re.compile(
    r"\bkit\s+(?:com\s+|c\s+)?[2-9]\s*(?:x\s*)?"
    r"(?:un\w*|aparelhos?|ar(?:es)?|splits?|condicionados?|pecas)?\b"
    r"|\b(?:[2-9]|dois|duas|tres)\s+(?:unidades|aparelhos|ares\s+condicionados)\b"
)

# Serviço de instalação embutido no preço. "Pegar e instalar" (anúncio de
# usado) e o kit de janela do portátil NÃO são serviço.
_RE_COM_INSTALACAO = re.compile(
    r"\b(?:com\s+)?instala\w*\s+(?:inclusa|incluida|incluso|gratis|gratuita)\b"
    r"|\bcom\s+instalacao\b|\+\s*instala\w*"
)


def _normalizar(texto: str) -> str:
    """Minúsculas, sem acento, só letras/dígitos/espaço (e '+')."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    texto = re.sub(r"[^a-z0-9+]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def texto_da_url(url: Optional[str]) -> str:
    """Título aproximado a partir do slug da URL (path decodificado, sem host
    nem query). Linhas antigas só guardam o título por este caminho."""
    if not url or not isinstance(url, str):
        return ""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    return unquote(path).replace("-", " ").replace("_", " ").replace("/", " ")


def _e_acessorio(t: str) -> bool:
    t = _RE_FALSO_ACESSORIO.sub(" ", t)
    termo = _RE_ACESSORIO.search(t)
    if termo is None:
        return False
    nucleo = _RE_NUCLEO.search(t)
    return nucleo is None or termo.start() < nucleo.start()


def _e_parcial(t: str) -> bool:
    if _RE_SO_METADE.search(t):
        return True
    cond = _RE_CONDENSADORA.search(t)
    evap = _RE_EVAPORADORA.search(t)
    if bool(cond) == bool(evap):
        # Nenhuma das metades (o normal) ou as duas ("evaporadora +
        # condensadora", a descrição do split completo).
        return False
    metade = cond or evap
    nucleo = _RE_NUCLEO.search(t)
    # Metade como substantivo principal ("Condensadora Gree 9000") ou anúncio
    # sem núcleo de aparelho. Depois do núcleo ela é atributo ("Ar
    # Condicionado ... voltagem da condensadora 220V").
    return nucleo is None or metade.start() < nucleo.start()


def classify_offer(title: Optional[str] = None, url: Optional[str] = None) -> str:
    """Classifica o anúncio pelo título cru e/ou pelo slug da URL.

    Args:
        title: Título do anúncio como veio do marketplace (cru, não o
            canônico — o canônico já perdeu "kit", "condensadora" etc.).
        url: URL do anúncio; o slug é usado quando o título falta ou quando
            ele já foi reescrito para o nome canônico.

    Returns:
        Um de UNIDADE, ACESSORIO, PARCIAL, MULTIPLO, COM_INSTALACAO. Na dúvida,
        UNIDADE: esta função só tira do preço o que tem evidência de não ser
        uma unidade, para não esvaziar a série por excesso de zelo.

    Example:
        >>> classify_offer("Kit 2 Ar Condicionado TCL Inverter 9000 Btus")
        'MULTIPLO'
        >>> classify_offer("Ar Condicionado Portátil Philco com Controle Remoto")
        'UNIDADE'
    """
    textos = [_normalizar(x) for x in (title or "", texto_da_url(url)) if x]
    textos = [t for t in textos if t]
    # Ordem importa: multiplo antes de acessório ("kit 2 ar" não é kit de
    # instalação) e acessório antes de parcial ("placa evaporadora" é peça).
    for teste, tipo in (
        (_RE_MULTIPLO.search, MULTIPLO),
        (_e_acessorio, ACESSORIO),
        (_e_parcial, PARCIAL),
        (_RE_COM_INSTALACAO.search, COM_INSTALACAO),
    ):
        if any(teste(t) for t in textos):
            return tipo
    return UNIDADE


def is_unit_offer(title: Optional[str] = None, url: Optional[str] = None) -> bool:
    """Atalho: o preço deste anúncio é o preço de UMA unidade do aparelho?"""
    return classify_offer(title, url) == UNIDADE
