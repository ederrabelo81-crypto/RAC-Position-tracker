"""
utils/sku_matcher.py — Resolução de SKU por IGUALDADE DE ATRIBUTOS (de-para v2).

Camada de SKU sobre o resolver de FAMÍLIA já testado (`utils.depara_resolver`).
Conserta os dois defeitos do de-para legado (ver reports/depara_baseline.md):

  A) SKU nulo  — re-deriva atributos do título e crava SKU quando há 1 único
                 candidato no catálogo; senão resolve até FAMÍLIA (não-nulo) ou
                 manda para pendências. Não confia no sku_resolvido/familia
                 legados (contaminados por seeds).
  B) Fusão     — modelos distintos JAMAIS caem no mesmo SKU, porque o match é por
                 (marca, BTU, ciclo, família-linha) + guarda de tecnologia e
                 desempate de voltagem. Inverter != On/Off; linhas diferentes →
                 famílias diferentes → conjuntos de candidatos disjuntos.

REGRA DE OURO: não chutar. O que não fixa 1 SKU com alta confiança fica com
`sku_v2 = None` e vai para pendências, sempre com os atributos parseados e os
candidatos. Família continua preenchida (granularidade honesta para o dashboard).

Funções puras (sem rede): o catálogo é injetado por quem chama (testável).
Reusa `attr_parser` (tecnologia/voltagem/cor None-safe) e `resolve_depara`
(marca/BTU/ciclo/família-linha, com as mesmas primitivas de normalize_product).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from utils.attr_parser import parse as parse_attrs
from utils.depara_resolver import (
    CATALOG_BRANDS,
    CatalogFamilias,
    FORA_TIPO_REGEX,
    NAO_AC_REGEX,
    resolve_depara,
)

_CICLO_CODE = {"FRIO": "F", "QUENTE/FRIO": "QF", "QUENTE": "Q"}


@dataclass(frozen=True)
class CatalogSku:
    """Um SKU do catálogo, com o que desempata dentro de uma família-linha."""
    sku: str
    voltagem: Optional[str] = None
    tecnologia: str = "Inverter"   # catálogo curado é inverter-only (migração 004)


@dataclass
class Catalog:
    """Visão do catálogo necessária para casar atributos → SKU.

    - `familias`: (marca, BTU, ciclo_code) → {familia_linha}  (p/ resolve_depara)
    - `btus`:     capacidades presentes no catálogo (gate de escopo)
    - `index`:    familia_linha → [CatalogSku]  (candidatos por linha)
    - `skus`:     conjunto de TODOS os SKUs válidos (namespace; p/ asserts)
    """
    familias: CatalogFamilias = field(default_factory=dict)
    btus: Set[int] = field(default_factory=set)
    index: Dict[str, List[CatalogSku]] = field(default_factory=dict)
    skus: Set[str] = field(default_factory=set)


def build_catalog(rows: List[dict]) -> Catalog:
    """Constrói o `Catalog` a partir de linhas de `produtos_catalogo`.

    Espera dicts com: sku, marca, capacidade_btu, ciclo, familia_linha,
    voltagem, ativo. Só linhas `ativo` entram nos candidatos/famílias.
    """
    cat = Catalog()
    for r in rows:
        sku = (r.get("sku") or "").strip()
        if sku:
            cat.skus.add(sku)
        if not r.get("ativo", True):
            continue
        marca = (r.get("marca") or "").strip().upper()
        btu = r.get("capacidade_btu")
        ciclo = _CICLO_CODE.get((r.get("ciclo") or "").upper())
        fam = r.get("familia_linha")
        try:
            btu = int(btu) if btu is not None else None
        except (TypeError, ValueError):
            btu = None
        if btu is not None:
            cat.btus.add(btu)
        if marca and btu is not None and ciclo and fam:
            cat.familias.setdefault((marca, btu, ciclo), set()).add(fam)
            cat.index.setdefault(fam, []).append(
                CatalogSku(sku=sku, voltagem=(r.get("voltagem") or None))
            )
    return cat


@dataclass
class SkuResolution:
    """Resultado da resolução de SKU de um título coletado."""
    estado: str                       # MAPEADO | FORA_ESCOPO | NAO_AC | REVISAR
    familia_v2: Optional[str]         # família-linha ou genérica (None se fora/nao_ac)
    sku_v2: Optional[str]             # SKU cravado (só com confiança alta)
    confianca: str                    # alta | ambigua | baixa
    metodo: str                       # como resolveu (auditoria)
    motivo: str                       # explicação curta (pendências / logs)
    candidatos: List[str] = field(default_factory=list)
    atributos: dict = field(default_factory=dict)

    @property
    def is_pendencia(self) -> bool:
        """True quando precisa de revisão humana (AC sem SKU cravado)."""
        return self.sku_v2 is None and self.estado in ("MAPEADO", "REVISAR")


def _attrs_dict(produto: str) -> dict:
    a = parse_attrs(produto)
    return {
        "marca": a.marca,
        "capacidade_btu": a.capacidade_btu,
        "ciclo": a.ciclo,
        "tecnologia": a.tecnologia,
        "edicao": a.edicao,
        "voltagem": a.voltagem,
        "cor": a.cor,
        "form_factor": a.form_factor,
        "sku_no_titulo": a.sku_no_titulo,
    }


def resolve_sku(
    produto: str,
    marca_raw: Optional[str],
    catalog: Catalog,
) -> SkuResolution:
    """Resolve um título coletado em estado + família + SKU (v2).

    Hierarquia (para por igualdade de atributos, nunca por "contains"):
      1. guardas NAO_AC / FORA_TIPO (peças, janela, portátil, ≥36k…);
      2. marca+BTU+ciclo → estado/família (resolve_depara, tested);
      3. família-linha do catálogo → candidatos;
      4. guarda de tecnologia (On/Off não casa catálogo inverter-only);
      5. 1 candidato → ALTA (crava SKU); >1 → desempate por voltagem;
         senão → pendência (AMBÍGUA), mantendo a família.
    """
    attrs = _attrs_dict(produto)

    if not produto or not produto.strip():
        return SkuResolution("REVISAR", None, None, "baixa", "vazio",
                             "título vazio", atributos=attrs)

    if any(p.search(produto) for p in NAO_AC_REGEX):
        return SkuResolution("NAO_AC", None, None, "baixa", "guarda_nao_ac",
                             "não é ar-condicionado", atributos=attrs)
    if any(p.search(produto) for p in FORA_TIPO_REGEX):
        return SkuResolution("FORA_ESCOPO", None, None, "baixa", "guarda_fora_tipo",
                             "tipo fora do escopo (janela/portátil/≥36k…)",
                             atributos=attrs)

    base = resolve_depara(produto, marca_raw, catalog.familias, catalog.btus)

    if base.estado != "MAPEADO":
        return SkuResolution(base.estado, None, None, "baixa",
                             f"depara_{base.estado.lower()}", base.reason,
                             atributos=attrs)

    familia = base.familia
    tec = attrs["tecnologia"]
    volt = attrs["voltagem"]
    cands = catalog.index.get(familia, [])

    # Família genérica (MARCA-BTU-CICLO) ou linha sem SKU no catálogo:
    # resolve até FAMÍLIA (não-nulo), mas SKU fica pendente.
    if not cands:
        return SkuResolution("MAPEADO", familia, None, "ambigua",
                             "familia_sem_sku",
                             "linha genérica/sem SKU único no catálogo",
                             atributos=attrs)

    # Guarda de tecnologia: catálogo curado é inverter-only. On/Off explícito
    # NÃO pode cair num SKU inverter (era a origem de 31 fusões).
    if tec == "On/Off":
        return SkuResolution("MAPEADO", familia, None, "baixa", "tec_conflito",
                             "título On/Off; catálogo curado é inverter-only",
                             candidatos=sorted(c.sku for c in cands),
                             atributos=attrs)

    if len(cands) == 1:
        return SkuResolution("MAPEADO", familia, cands[0].sku, "alta",
                             "familia_linha_unica", "1 SKU na família-linha",
                             candidatos=[cands[0].sku], atributos=attrs)

    # >1 candidato: desempata por voltagem quando o título a traz.
    if volt:
        vmatch = [c for c in cands if c.voltagem == volt]
        if len(vmatch) == 1:
            return SkuResolution("MAPEADO", familia, vmatch[0].sku, "alta",
                                 "familia_mais_voltagem",
                                 f"1 SKU após desempate por voltagem {volt}",
                                 candidatos=sorted(c.sku for c in cands),
                                 atributos=attrs)

    return SkuResolution("MAPEADO", familia, None, "ambigua", "ambiguo_multi_sku",
                         f"{len(cands)} SKUs na família; voltagem não desempata",
                         candidatos=sorted(c.sku for c in cands), atributos=attrs)
