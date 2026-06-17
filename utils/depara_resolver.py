"""
utils/depara_resolver.py — Auto-resolução do de-para nome→família.

Decide o estado de um `nome_coletado` (MAPEADO / FORA_ESCOPO / REVISAR) e a
`familia` resolvida, reaproveitando as primitivas robustas de
`utils.normalize_product` (detecção de marca por linha comercial, extração de
BTU multi-formato, detecção de ciclo). Substitui as regex fracas e inline que
viviam em `scripts/montar_depara.py` e que jogavam dezenas de ACs triviais na
fila humana (ex.: "Midea AI Ecomaster 9.000 BTUs Quente/Frio" caía em REVISAR
com marca NULL).

Política (revisada Jun/2026 — "família-linha, sem SKU"):
    - Marca de catálogo + BTU detectados → MAPEADO, familia genérica
      `<MARCA>-<BTU>-<CICLO>` (ex.: "MIDEA-9000-QF"). Quando o catálogo tem
      exatamente UMA `familia_linha` para aquele (marca, BTU, ciclo) — ou seja,
      zero ambiguidade de linha — a família genérica é promovida para essa
      `familia_linha` exata do catálogo.
    - `sku` NUNCA é cravado aqui (mantém-se NULL). Atribuir SKU exige desempate
      de voltagem, fora do escopo conservador desta resolução.
    - Marca de AC reconhecida porém FORA do catálogo (Daikin, Consul, Carrier,
      Britânia…) → FORA_ESCOPO.
    - Sem marca identificável, ou marca de catálogo mas sem BTU → REVISAR
      (continua indo para a fila humana — não chutamos).

Esta função é pura (sem efeitos colaterais / sem rede): o mapa de famílias do
catálogo é injetado por quem chama, o que a mantém testável.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from utils.normalize_product import (
    _extract_btus_value,
    _identify_brand,
    _identify_cycle,
    _identify_line,
)

# Marcas que compõem o catálogo RAC High Wall → elegíveis a MAPEADO.
CATALOG_BRANDS: Set[str] = {
    "MIDEA", "LG", "SAMSUNG", "ELECTROLUX", "ELGIN",
    "PHILCO", "GREE", "TCL", "AGRATTO", "HISENSE",
}

# Marca title-case devolvida por `_identify_brand` → forma canônica UPPER usada
# em `produtos_depara_nome.marca_norm` / `produtos_catalogo.marca` (sem acento).
_BRAND_TO_NORM: Dict[str, str] = {
    "Midea": "MIDEA", "LG": "LG", "Samsung": "SAMSUNG",
    "Electrolux": "ELECTROLUX", "Elgin": "ELGIN", "Philco": "PHILCO",
    "Gree": "GREE", "TCL": "TCL", "Agratto": "AGRATTO", "Hisense": "HISENSE",
    # AC reconhecido, porém fora do catálogo:
    "Consul": "CONSUL", "Daikin": "DAIKIN", "Hitachi": "HITACHI",
    "Aufit": "AUFIT", "Britânia": "BRITANIA", "Carrier": "CARRIER",
    "Komeco": "KOMECO", "Haier": "HAIER", "Fujitsu": "FUJITSU",
    "Rheem": "RHEEM", "Vix": "VIX", "York": "YORK", "EOS": "EOS", "HQ": "HQ",
}

# Marcas de AC reconhecidamente FORA do catálogo que `_identify_brand` (de
# normalize_product) não detecta pelo nome. Espelha o BRAND_FROM_NAME do
# classificador antigo para não regredir a fila: sem isto, nomes Aiwa/Chigo/
# Fontaine/etc. caem em REVISAR em vez de FORA_ESCOPO.
_EXTRA_FORA_BRANDS = [
    (re.compile(r"\baiwa\b", re.IGNORECASE), "AIWA"),
    (re.compile(r"\bequation\b", re.IGNORECASE), "EQUATION"),
    (re.compile(r"\bfontaine\b", re.IGNORECASE), "FONTAINE"),
    (re.compile(r"\bdelonghi\b", re.IGNORECASE), "DELONGHI"),
    (re.compile(r"\bchigo\b", re.IGNORECASE), "CHIGO"),
    (re.compile(r"\bkian\b", re.IGNORECASE), "KIAN"),
    (re.compile(r"\beos\b", re.IGNORECASE), "EOS"),
    (re.compile(r"\bvix\b", re.IGNORECASE), "VIX"),
    (re.compile(r"\bhq\b", re.IGNORECASE), "HQ"),
]

# ── Guardas de pré-classificação (fonte única; reexportados por montar_depara) ─
# Rodam ANTES do matcher forte. Vivem aqui (módulo puro, sem loguru) para que
# tanto `scripts/montar_depara.py` quanto `utils/sku_matcher.py` os reutilizem.

# Não-AC: peças automotivas, eletrodomésticos, acessórios, climatizadores
NAO_AC_REGEX = [
    re.compile(p, re.IGNORECASE) for p in [
        r"report a violation",
        r"\bfiltro\b.*(palio|corolla|sedan|automotiv|veicular|fiat|toyota|honda|gm|chevrolet|ford|volkswagen|vw|kombi|stilo|craftsman)",
        r"\bradiador\b",
        r"\bevaporador\b(?!.*split)",
        r"\bcondensador\b.*(stilo|acquaflex|libell|original)",
        r"\bcompressor\b.*(delphi|sanden|valvula|válvula|torneira)",
        r"\b(polia|válvula|valvula|torneira|porca|cooler intel|injetora|garrafa)\b",
        r"\bshampoo\b|\bcondicionador\b.*(hidratante|capilar|antiqueda|aminoacid|aminoácid)",
        r"\bcolch[aã]o\b|\bbarraca\b|\binfl[aá]vel\b",
        r"\bgeladeira\b|\bfrigobar\b|\bfreezer\b|\bair fryer\b|\bfritadeira\b",
        r"\bumidificador\b|\bclimatizador\b|\bventilador\b|\baromatizador\b",
        r"\bmini ar condicionado\b|\busb\b.*ar condicionado|ar condicionado.*\busb\b",
        r"\bcontrole remoto\b|\bcapa para\b|\bsuporte para ar\b",
        r"\bmelhor (mini )?ar condicionado 20\d\d\b",
        r"\bcortina de ar\b",
        r"\bcervejeira\b|\blavadora\b|\bsecadora\b|\bmicro-?ondas\b|\bfog[aã]o\b",
        r"\bserpentina\b|\bcontrole\b|\bh[aá]lite\b|\borganizador\b|\bcarregador\b",
        r"\bprotetor\b|\bcapa\b|\bsuporte\b",
        r"\bunidade condensadora\b|\bhigienizador\b|\bmanifold\b|\bmangueira\b",
        # Componentes/peças avulsas que carregam marca+BTU (não são a unidade):
        r"\bcondensadora?\b|\bevaporadora\b|\bcompressor\b|\bturbina\b",
        r"\bplaca\b|\bgabinete\b|\bbobina\b|\bfus[íi]vel\b|\btermistor\b",
        r"\bjunta\b|\btransformador\b|\bpressostato\b|\bdifusor\b|\bdefletor\b",
        r"\binversor\b|\bbomba\b\s*dreno|\bdreno\b|\bman[ôo]metro\b|\bcoxim",
        r"\bauto[\s-]?transformador\b|\bcaixa\b.*\bpassagem\b",
    ]
]

# Fora-de-escopo por tipo (mesmo que marca seja catalogada)
FORA_TIPO_REGEX = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(janela|janeleiro|window)\b",
        r"\bport[aá]til\b",
        r"\bcassete\b|\bcassette\b",
        r"piso[ \-]teto",
        r"multi[ \-]?split|multisplit",
        r"bi[\s-]?split|\b2x9\b|\b2x12\b|\b2x18\b",
        r"\b(36|32|34|48|57|60)\.?000\s*btu",
        r"\b(36|32|34|48|57|60)k\s*btu",
        r"\b(7\.?000|7\.?500|16\.?000)\s*btu",
        r"\bsplit[aã]o\b|\btrif[aá]sico\b|\b7,?5\s*tr\b",
    ]
]

# Tipo do mapa de famílias do catálogo injetado em `resolve_depara`:
#   (marca_norm, btu, ciclo_code) → conjunto de `familia_linha` não-nulas.
CatalogFamilias = Dict[Tuple[str, int, str], Set[str]]


@dataclass
class DeParaResult:
    """Resultado da auto-resolução de um nome coletado."""

    estado: str                      # MAPEADO | FORA_ESCOPO | REVISAR
    familia: Optional[str]           # família resolvida (None p/ FORA_ESCOPO/REVISAR)
    sku: Optional[str]               # sempre None nesta política conservadora
    marca_norm: Optional[str]        # marca canônica UPPER, ou None
    confidence: str                  # "alta" | "media" | "baixa"
    reason: str                      # explicação curta (logs / CSV de auditoria)

    @property
    def changed_from_revisar(self) -> bool:
        """True quando a resolução tira o nome da fila humana."""
        return self.estado in ("MAPEADO", "FORA_ESCOPO")


def _ciclo_code(nome_lower: str) -> str:
    """Devolve 'F' ou 'QF' a partir do ciclo detectado (default Frio)."""
    return "QF" if _identify_cycle(nome_lower) == "Quente/Frio" else "F"


def _tokens(text: str) -> Set[str]:
    """Tokens alfanuméricos em minúsculas (separadores: espaço, hífen, etc.)."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _line_portion(familia_linha: str, marca_norm: str, btu: int, ciclo: str) -> str:
    """Extrai o trecho de LINHA de uma familia_linha do catálogo.

    Ex.: "MIDEA-ECOMASTER-9000-QF" → "ECOMASTER";
         "LG-DUAL-INVERTER-ARTCOOL-12000-QF" → "DUAL-INVERTER-ARTCOOL".
    """
    s = familia_linha
    prefix = f"{marca_norm}-"
    if s.startswith(prefix):
        s = s[len(prefix):]
    suffix = f"-{btu}-{ciclo}"
    if s.endswith(suffix):
        s = s[: -len(suffix)]
    return s


def _promote_to_familia_linha(
    nome_lower: str,
    brand_title: str,
    marca_norm: str,
    btu: int,
    ciclo: str,
    catalog_familias: CatalogFamilias,
) -> Optional[str]:
    """Promove a família genérica à `familia_linha` do catálogo, com segurança.

    Só promove quando a linha detectada no nome casa com EXATAMENTE uma linha do
    catálogo para (marca, BTU, ciclo) — i.e., os tokens da linha do catálogo
    estão todos contidos na linha detectada. Isso evita rotular errado um
    produto cuja linha real não está no catálogo (ex.: "Eco Dream" não vira
    "Eco Inverter II" só porque é a única linha catalogada naquele BTU).
    """
    detected = _identify_line(nome_lower, brand_title)
    if not detected:
        return None
    cands = catalog_familias.get((marca_norm, btu, ciclo))
    if not cands:
        return None
    detected_tokens = _tokens(detected)
    matches = [
        fam for fam in cands
        if (pt := _tokens(_line_portion(fam, marca_norm, btu, ciclo)))
        and pt <= detected_tokens
    ]
    return matches[0] if len(set(matches)) == 1 else None


def resolve_depara(
    nome: str,
    marca_raw: Optional[str] = None,
    catalog_familias: Optional[CatalogFamilias] = None,
    catalog_btus: Optional[Set[int]] = None,
) -> DeParaResult:
    """
    Auto-resolve um nome coletado para estado + família do de-para.

    Args:
        nome: Nome do produto coletado (ex.: título do marketplace).
        marca_raw: Marca já conhecida (coletas.marca / marca_norm existente).
            Usada como dica preferencial pela detecção de marca; opcional.
        catalog_familias: Mapa (marca_norm, btu, ciclo_code) → {familia_linha}
            do catálogo, usado para promover a família genérica à linha exata
            quando há uma única linha possível. Opcional — sem ele, só a
            família genérica é produzida.
        catalog_btus: Conjunto de capacidades (BTU) presentes no catálogo. Quando
            informado, capacidades fora dele (ex.: 7.500, 33.000, 55.000) viram
            FORA_ESCOPO em vez de MAPEADO — o catálogo é Hi-Wall residencial e
            não cobre essas faixas. Opcional.

    Returns:
        DeParaResult com estado, família, marca_norm e metadados de auditoria.
    """
    if not nome or not nome.strip():
        return DeParaResult("REVISAR", None, None, None, "baixa", "nome vazio")

    brand_title = _identify_brand(nome, marca_raw)
    if not brand_title:
        # Marcas de AC fora do catálogo que o detector principal não conhece.
        for pat, mnorm in _EXTRA_FORA_BRANDS:
            if pat.search(nome):
                return DeParaResult(
                    "FORA_ESCOPO", None, None, mnorm, "alta",
                    f"marca AC fora do catálogo ({mnorm})",
                )
        return DeParaResult(
            "REVISAR", None, None, None, "baixa", "marca não identificada"
        )

    marca_norm = _BRAND_TO_NORM.get(brand_title, brand_title.upper())

    if marca_norm not in CATALOG_BRANDS:
        return DeParaResult(
            "FORA_ESCOPO", None, None, marca_norm, "alta",
            f"marca AC fora do catálogo ({marca_norm})",
        )

    btu = _extract_btus_value(nome)
    if btu is None:
        return DeParaResult(
            "REVISAR", None, None, marca_norm, "media",
            "marca de catálogo mas BTU não detectado",
        )

    if catalog_btus is not None and btu not in catalog_btus:
        return DeParaResult(
            "FORA_ESCOPO", None, None, marca_norm, "alta",
            f"capacidade {btu} BTU fora do catálogo",
        )

    nome_lower = nome.lower()
    ciclo = _ciclo_code(nome_lower)
    familia = f"{marca_norm}-{btu}-{ciclo}"
    reason = "marca+BTU de catálogo → família genérica"

    # Promove à família-linha do catálogo apenas quando a linha detectada no
    # nome casa com a linha catalogada (evita rótulos errados).
    if catalog_familias:
        linha = _promote_to_familia_linha(
            nome_lower, brand_title, marca_norm, btu, ciclo, catalog_familias
        )
        if linha:
            familia = linha
            reason = "linha reconhecida no nome → família-linha do catálogo"

    return DeParaResult("MAPEADO", familia, None, marca_norm, "alta", reason)
