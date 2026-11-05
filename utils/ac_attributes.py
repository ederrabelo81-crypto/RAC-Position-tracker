"""
ac_attributes.py — atributos de ar-condicionado a partir do catálogo do PriceTrack.

O export do PriceTrack não tem campo de BTU, tecnologia, ciclo ou voltagem
(descoberta de 05/11/2026, `docs/diagnostico_fase0.md`). Mas o nome do produto
segue um padrão fixo do catálogo deles::

    AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - 42EZVCA12M5

Deste nome sai BTU (100% das linhas), voltagem (96%), tecnologia e ciclo. A
**subcategoria** (HI-WALL, CASSETE…) NÃO sai do nome: o AI Ecomaster da Midea
é Hi-Wall e se chama só "SPLIT". Ela vem do campo `subcategory` da API.

Regra dura de escopo do Diagnóstico: só ar-condicionado, padrão Split Hi-Wall
Inverter até 30.000 BTU. Ver ``in_default_scope``.

Funções puras: nada de rede, banco ou Streamlit.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

import pandas as pd

SUBCATEGORIA_HIWALL = "HI-WALL"
#: Teto de capacidade do escopo RAC residencial.
BTU_MAXIMO_ESCOPO = 30_000

TECNOLOGIA_INVERTER = "INVERTER"
TECNOLOGIA_ON_OFF = "ON/OFF"
CICLO_QUENTE_FRIO = "QUENTE/FRIO"
CICLO_FRIO = "FRIO"

#: Lojas próprias de fabricante que o PriceTrack lista como marketplace
#: (descoberta de 05/11/2026). Somadas às marcas do próprio dado, para pegar
#: também a loja de uma marca que não aparece na amostra de ar-condicionado.
LOJAS_DE_MARCA_CONHECIDAS = frozenset({
    "MIDEA", "LG", "SAMSUNG", "PHILCO", "CONSUL", "ELECTROLUX",
    "BRASTEMP", "PANASONIC", "BRITÂNIA",
})

_RE_BTU = re.compile(r"(\d{1,2}[.\s]?\d{3})\s*BTU", re.IGNORECASE)
_RE_INVERTER = re.compile(r"\bINVERTER\b", re.IGNORECASE)
_RE_ON_OFF = re.compile(r"\bON[\s/-]?OFF\b|\bCONVENCIONAL\b", re.IGNORECASE)
_RE_QUENTE_FRIO = re.compile(r"QUENTE\s*(?:E|/|-)?\s*FRIO|\bQ/F\b", re.IGNORECASE)
_RE_VOLTAGEM = re.compile(r"\b(110|127|220)\s*V\b|\b(BIVOLT)\b", re.IGNORECASE)


def parse_btu(title: Optional[str]) -> Optional[int]:
    """Capacidade em BTU lida do nome (``"12.000 BTUs"`` → 12000); None se ausente."""
    if not title:
        return None
    achado = _RE_BTU.search(str(title))
    return int(re.sub(r"\D", "", achado.group(1))) if achado else None


def parse_tecnologia(title: Optional[str]) -> Optional[str]:
    """``INVERTER`` / ``ON/OFF`` / None (nome não diz)."""
    if not title:
        return None
    texto = str(title)
    if _RE_INVERTER.search(texto):
        return TECNOLOGIA_INVERTER
    if _RE_ON_OFF.search(texto):
        return TECNOLOGIA_ON_OFF
    return None


def parse_ciclo(title: Optional[str]) -> Optional[str]:
    """``QUENTE/FRIO`` ou ``FRIO``.

    No catálogo do PriceTrack o ciclo vem sempre escrito: "QUENTE/FRIO" ou só
    "FRIO" — não existe a grafia "só frio". Nome sem nenhum dos dois → None.
    """
    if not title:
        return None
    texto = str(title)
    if _RE_QUENTE_FRIO.search(texto):
        return CICLO_QUENTE_FRIO
    if re.search(r"\bFRIO\b", texto, re.IGNORECASE):
        return CICLO_FRIO
    return None


def parse_voltagem(title: Optional[str]) -> Optional[str]:
    """``220V`` / ``127V`` / ``110V`` / ``BIVOLT`` / None."""
    if not title:
        return None
    achado = _RE_VOLTAGEM.search(str(title))
    if not achado:
        return None
    return "BIVOLT" if achado.group(2) else f"{achado.group(1)}V"


def add_attributes(df: pd.DataFrame, title_col: str = "title") -> pd.DataFrame:
    """Devolve cópia com ``btu``, ``tecnologia``, ``ciclo`` e ``voltagem``."""
    out = df.copy()
    titulos = out[title_col] if title_col in out.columns else pd.Series("", index=out.index)
    # Regex por título ÚNICO (alguns milhares) e não por linha (centenas de
    # milhares num mês): mesmo resultado, uma fração do tempo no Streamlit.
    unicos = pd.Series(titulos.dropna().unique())
    for coluna, parser in (
        ("btu", parse_btu), ("tecnologia", parse_tecnologia),
        ("ciclo", parse_ciclo), ("voltagem", parse_voltagem),
    ):
        out[coluna] = titulos.map(dict(zip(unicos, unicos.map(parser))))
    out["btu"] = pd.to_numeric(out["btu"], errors="coerce").astype("Int64")
    return out


def subcategory_map(df: pd.DataFrame) -> dict:
    """De-para SKU → subcategoria, a partir das linhas que trazem o campo.

    Subcategoria é atributo do PRODUTO, não muda de um dia para outro. Com o
    de-para dá para classificar o histórico gravado antes de o import passar a
    guardar o campo (até 05/11/2026), sem pedir exports novos à API. Havendo
    mais de uma grafia para o mesmo SKU, vale a mais frequente.
    """
    if "subcategory" not in df.columns or "sku" not in df.columns:
        return {}
    com = df[df["subcategory"].fillna("").astype(str).str.strip() != ""]
    if com.empty:
        return {}
    contagem = (
        com.assign(subcategory=com["subcategory"].astype(str).str.strip().str.upper())
        .groupby(["sku", "subcategory"]).size().reset_index(name="n")
        .sort_values(["sku", "n"], ascending=[True, False])
    )
    return dict(contagem.drop_duplicates("sku")[["sku", "subcategory"]].itertuples(index=False))


def fill_subcategory(df: pd.DataFrame, mapping: Mapping[str, str]) -> pd.DataFrame:
    """Preenche ``subcategory`` vazia pelo de-para SKU → subcategoria.

    Linha que já tem subcategoria fica como está. SKU fora do de-para fica
    vazio — e, portanto, FORA do escopo padrão: na dúvida, não entra.
    """
    out = df.copy()
    atual = (
        out["subcategory"].fillna("").astype(str).str.strip().str.upper()
        if "subcategory" in out.columns
        else pd.Series("", index=out.index)
    )
    faltando = atual == ""
    out["subcategory"] = atual.where(~faltando, out["sku"].map(mapping).fillna(""))
    return out


def in_default_scope(df: pd.DataFrame) -> pd.Series:
    """Máscara do escopo padrão: Hi-Wall + Inverter + até 30.000 BTU.

    Exige ``subcategory``, ``tecnologia`` e ``btu`` (ver ``add_attributes`` e
    ``fill_subcategory``). BTU desconhecido sai: sem capacidade não há como
    garantir que é residencial.
    """
    btu = pd.to_numeric(df["btu"], errors="coerce")
    return (
        df["subcategory"].fillna("").astype(str).str.upper().eq(SUBCATEGORIA_HIWALL)
        & df["tecnologia"].eq(TECNOLOGIA_INVERTER)
        & btu.le(BTU_MAXIMO_ESCOPO).fillna(False)
    )


def brand_store_marketplaces(marketplaces: Iterable[str], brands: Iterable[str]) -> set:
    """Marketplaces que são a loja própria de uma marca (MIDEA, LG, SAMSUNG…).

    O PriceTrack lista a loja oficial do fabricante como marketplace. Ali o
    fabricante define o preço sozinho, então ela não entra na mediana do
    mercado do Diagnóstico (decisão do mantenedor, 05/11/2026). Casamento
    exato, em maiúsculas, contra as marcas do próprio dado.
    """
    marcas = {str(b).strip().upper() for b in brands if b and str(b).strip()}
    marcas |= LOJAS_DE_MARCA_CONHECIDAS
    return {m for m in marketplaces if m and str(m).strip().upper() in marcas}
