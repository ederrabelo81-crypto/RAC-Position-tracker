"""
utils/pricetrack_audit.py — as coletas Python como auditoria do PriceTrack.

Por que existe (Out/2026): o PriceTrack é a fonte de preço do painel, e nada
conferia o que ele entrega. Na conferência de 16/10/2026 contra as nossas
coletas apareceram quatro problemas que nenhum monitor apontava:

1. **Degrau de volume** — 1,49 milhão de ofertas/dia em 30/09 → 548 mil em
   02/10, enquanto a nossa coleta, no mesmo período, ficou estável (±6%).
   O PriceTrack atribuiu à Black Friday; o mercado não mudou.
2. **Indisponibilidade sumiu** — zero ofertas indisponíveis em 14 dias. Num
   mercado real algo sempre sai de estoque; é o sinal de que o preço anterior
   é repetido quando a coleta falha.
3. **Marketplace fora** — Shopee sem nenhuma linha.
4. **Preço divergente** — mesmo SKU, mesma plataforma, mesmo dia, com 20–39%
   dos pares acima de 15% de diferença. A maior parte NÃO é erro de preço: no
   MESMO vendedor, 64–87% batem dentro de 5%. O que diverge é o conjunto de
   vendedores — o vendedor mais barato que vemos estava no PriceTrack em só
   17–35% dos SKUs, e o "menor preço" dele ficava >5% acima do mínimo real em
   35–47% dos SKUs. Por isso a comparação sai em duas leituras: mesmo vendedor
   (o preço coletado está certo?) e menor preço do SKU (ele vê o mercado?).

Módulo puro (sem I/O), para ser testável e rodar igual no PC e no Actions; a
leitura do banco fica em `scripts/pricetrack_audit.py`. Testes em
`tests/test_pricetrack_audit.py`.

Regras duras:
- Preço só compara anúncio de UNIDADE (`utils.offer_kind`). Kit, peça, meia
  unidade e "kit 2" saem antes do min() — eram a maior fonte de divergência
  falsa, e erro nosso.
- A auditoria aponta divergência, não decide quem está certo. Cada caso vai
  com a URL para conferência na tela.
- Degrau do PriceTrack só conta quando a nossa coleta NÃO caiu junto — se as
  duas caem, é coleta nossa ou mercado, não problema do fornecedor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from utils.offer_kind import UNIDADE, classify_offer
from utils.pricetrack_platforms import CANONICAL_TO_PT_PLATFORM
from utils.seller_names import normalize_seller_name, seller_key

#: Plataformas em que `seller` é placeholder quando falta a buy box observada
#: ("Amazon", "Casas Bahia") — regra 3 do Cockpit do Trade. Ali, sem
#: `buy_box_seller`, o vendedor é desconhecido e sai da leitura por vendedor.
_SELLER_PLACEHOLDER = {"Amazon", "Casas Bahia"}


@dataclass(frozen=True)
class AuditConfig:
    """Limiares da auditoria (padrões calibrados na conferência de 16/10/2026)."""

    janela_base: int = 7            # dias anteriores que formam a linha de base
    degrau_pt: float = 0.5          # PriceTrack < 50% da base = degrau
    estavel_nosso: float = 0.8      # nossa coleta ≥ 80% da base = "não caiu"
    dias_sem_indisp: int = 3        # dias seguidos com 0 indisponível = alerta
    tolerancia: float = 0.05        # até 5% = preço bate
    divergencia: float = 0.15       # acima de 15% = divergência a conferir
    top: int = 20                   # casos listados para conferência


@dataclass
class PriceComparison:
    """Resultado da comparação de preço de um dia."""

    pares: int = 0
    por_plataforma: Dict[str, Dict[str, float]] = field(default_factory=dict)
    excluidos_por_tipo: Dict[str, int] = field(default_factory=dict)
    divergencias: List[Dict[str, object]] = field(default_factory=list)
    #: Mesmo SKU E mesmo vendedor dos dois lados — mede o preço coletado.
    mesmo_vendedor: Dict[str, Dict[str, float]] = field(default_factory=dict)


def _base(serie: Mapping[str, float], dia: str, janela: int) -> Optional[float]:
    anteriores = [v for d, v in sorted(serie.items()) if d < dia][-janela:]
    return median(anteriores) if len(anteriores) >= max(3, janela // 2) else None


def volume_steps(
    pt_volume: Mapping[str, float],
    nosso_volume: Mapping[str, float],
    cfg: AuditConfig = AuditConfig(),
) -> List[Dict[str, object]]:
    """Dias em que o volume do PriceTrack despencou e o nosso não.

    Args:
        pt_volume: {data ISO: ofertas brutas do export do PriceTrack}.
        nosso_volume: {data ISO: ofertas distintas na nossa coleta}.

    Returns:
        Um registro por dia com degrau, com as razões contra a linha de base.
        A base é a mediana dos `janela_base` dias anteriores, então um
        patamar novo e baixo deixa de alarmar depois de uma janela — o alerta
        é o DEGRAU; o patamar fica documentado no próprio relatório.
    """
    saida: List[Dict[str, object]] = []
    for dia in sorted(pt_volume):
        base_pt = _base(pt_volume, dia, cfg.janela_base)
        base_nosso = _base(nosso_volume, dia, cfg.janela_base)
        if not base_pt or not base_nosso or dia not in nosso_volume:
            continue
        razao_pt = pt_volume[dia] / base_pt
        razao_nosso = nosso_volume[dia] / base_nosso
        if razao_pt < cfg.degrau_pt and razao_nosso >= cfg.estavel_nosso:
            saida.append({
                "data": dia,
                "pricetrack": int(pt_volume[dia]),
                "pricetrack_base": int(base_pt),
                "pricetrack_razao": round(razao_pt, 3),
                "nosso": int(nosso_volume[dia]),
                "nosso_base": int(base_nosso),
                "nosso_razao": round(razao_nosso, 3),
            })
    return saida


def unavailability_streak(indisp_por_dia: Mapping[str, int]) -> int:
    """Dias seguidos, a partir do mais recente, sem nenhuma oferta indisponível."""
    sequencia = 0
    for dia in sorted(indisp_por_dia, reverse=True):
        if indisp_por_dia[dia]:
            break
        sequencia += 1
    return sequencia


def coverage_gaps(
    nossas_plataformas: Iterable[str], pt_marketplaces: Iterable[str]
) -> List[str]:
    """Plataformas que coletamos, que o PriceTrack deveria cobrir e não cobre."""
    pt = {str(m).upper() for m in pt_marketplaces}
    return sorted(
        p for p in set(nossas_plataformas)
        if p in CANONICAL_TO_PT_PLATFORM and CANONICAL_TO_PT_PLATFORM[p] not in pt
    )


def compare_prices(
    nossos: Sequence[Mapping[str, object]],
    pricetrack: Sequence[Mapping[str, object]],
    cfg: AuditConfig = AuditConfig(),
) -> PriceComparison:
    """Mesmo SKU, mesma plataforma, mesmo dia: o preço bate?

    Args:
        nossos: linhas de `coletas` com `plataforma`, `sku_resolvido`,
            `preco`, `produto`, `url_produto` e, opcionais, `seller` e
            `buy_box_seller`.
        pricetrack: linhas de `pricetrack_daily` (turno Diário) com
            `marketplace`, `sku`, `min_price` e, opcional, `seller`.

    Returns:
        Pares comparados, faixas de concordância por plataforma, quantos
        anúncios saíram por não serem unidade, e as maiores divergências
        (com URL) para conferência na tela.
    """
    res = PriceComparison()
    melhor: Dict[tuple, Dict[str, object]] = {}
    por_vendedor: Dict[tuple, float] = {}
    for row in nossos:
        preco = _num(row.get("preco"))
        sku = str(row.get("sku_resolvido") or "").strip().upper()
        mkt = CANONICAL_TO_PT_PLATFORM.get(str(row.get("plataforma") or ""))
        if not preco or not sku or not mkt:
            continue
        tipo = classify_offer(row.get("produto"), row.get("url_produto"))
        if tipo != UNIDADE:
            res.excluidos_por_tipo[tipo] = res.excluidos_por_tipo.get(tipo, 0) + 1
            continue
        vend = _vendedor(row)
        chave = (mkt, sku)
        if chave not in melhor or preco < melhor[chave]["preco"]:
            melhor[chave] = {"preco": preco, "url": row.get("url_produto"),
                             "produto": row.get("produto"), "vendedor": vend}
        if vend:
            k = (mkt, sku, vend)
            por_vendedor[k] = min(preco, por_vendedor.get(k, preco))

    pt: Dict[tuple, float] = {}
    pt_vend: Dict[tuple, float] = {}
    pt_vendedores: Dict[tuple, set] = {}
    for row in pricetrack:
        preco = _num(row.get("min_price"))
        if not preco:
            continue
        chave = (str(row.get("marketplace") or "").upper(),
                 str(row.get("sku") or "").strip().upper())
        pt[chave] = min(preco, pt.get(chave, preco))
        vend = seller_key(normalize_seller_name(row.get("seller")))
        if vend:
            pt_vendedores.setdefault(chave, set()).add(vend)
            k = (*chave, vend)
            pt_vend[k] = min(preco, pt_vend.get(k, preco))

    faixas: Dict[str, List[float]] = {}
    for chave, nosso in melhor.items():
        if chave not in pt:
            continue
        dif = pt[chave] / nosso["preco"] - 1
        faixas.setdefault(chave[0], []).append(dif)
        if abs(dif) > cfg.divergencia:
            res.divergencias.append({
                "marketplace": chave[0], "sku": chave[1],
                "nosso": round(nosso["preco"], 2), "pricetrack": round(pt[chave], 2),
                "dif_pct": round(100 * dif, 1),
                "produto": nosso["produto"], "url": nosso["url"],
            })

    # Vendedor mais barato que vemos: o PriceTrack o acompanha?
    fora: Dict[str, List[bool]] = {}
    for chave, nosso in melhor.items():
        if chave in pt and nosso["vendedor"] and chave in pt_vendedores:
            fora.setdefault(chave[0], []).append(nosso["vendedor"] not in pt_vendedores[chave])

    for mkt, difs in sorted(faixas.items()):
        n = len(difs)
        res.pares += n
        res.por_plataforma[mkt] = {
            "pares": n,
            "pct_bate": round(100 * sum(abs(d) <= cfg.tolerancia for d in difs) / n, 1),
            "pct_divergente": round(100 * sum(abs(d) > cfg.divergencia for d in difs) / n, 1),
            "pct_pt_mais_caro": round(100 * sum(d > cfg.tolerancia for d in difs) / n, 1),
        }
        if fora.get(mkt):
            res.por_plataforma[mkt]["pct_mais_barato_fora_do_pt"] = round(
                100 * sum(fora[mkt]) / len(fora[mkt]), 1)

    mesmo: Dict[str, List[float]] = {}
    for k, preco in por_vendedor.items():
        if k in pt_vend:
            mesmo.setdefault(k[0], []).append(pt_vend[k] / preco - 1)
    for mkt, difs in sorted(mesmo.items()):
        n = len(difs)
        res.mesmo_vendedor[mkt] = {
            "pares": n,
            "pct_bate": round(100 * sum(abs(d) <= cfg.tolerancia for d in difs) / n, 1),
            "pct_divergente": round(100 * sum(abs(d) > cfg.divergencia for d in difs) / n, 1),
        }
    res.divergencias.sort(key=lambda r: -abs(r["dif_pct"]))
    res.divergencias = res.divergencias[: cfg.top]
    return res


def _vendedor(row: Mapping[str, object]) -> str:
    """Chave do vendedor efetivo (buy box, senão seller); '' se desconhecido."""
    bb = row.get("buy_box_seller")
    if not bb and str(row.get("plataforma") or "") in _SELLER_PLACEHOLDER:
        return ""
    return seller_key(normalize_seller_name(bb or row.get("seller")))


def _num(v: object) -> Optional[float]:
    """Preço como float positivo (o PostgREST devolve numeric como string)."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None
