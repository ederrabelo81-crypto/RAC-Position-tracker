"""
Fonte de dados do dashboard — 3 fontes: Supabase, API ao vivo e demo.

``fetch_supabase`` lê a tabela ``pricetrack_daily`` (o import diário da API já
mora lá, agregado por listagem). É a fonte **rápida e padrão** — a API de
coleta responde em ~2min por consulta, inviável para uso interativo.

``fetch_live`` bate direto na API do PriceTrack (``pricetrack_api``). Fica como
opção; a sonda de data e o timeout foram endurecidos, mas o endpoint é lento.

``demo_offers`` gera uma amostra sintética cobrindo os modelos do peer, para a
página renderizar sem rede/credencial. Marcada como demo na UI.

Todas as fontes entregam ``list[Offer]``, então ``analytics.analyze`` roda igual
em cima das três.

Em ``pricetrack_daily`` **cada linha é uma listagem-dia agregada**, não uma
oferta: por trás dela há N coletas (``obs_count``), colapsadas em
min/média/moda/máx. A oferta sintética usa ``last_price`` — o preço da ÚLTIMA
coleta da janela, que é o que o painel do PriceTrack mostra ("Preço exibido:
última coleta"). Linhas anteriores à migração 006 não têm ``last_price`` e caem
para ``min_price`` (o piso da janela), que só coincide com o painel quando o
preço não mexeu no dia.

⚠️ Uma linha ``price_basis='spot_legacy'`` foi gravada com a base de preço
errada (spot em vez do menor entre spot e PIX) e **não é comparável** com uma
``best_cash``. ``LiveResult.price_basis`` conta as bases lidas para que a UI
consiga avisar em vez de emendar as duas numa série só.
"""
from __future__ import annotations

import os
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from pricetrack_api import PriceTrackClient, PriceTrackSettings
from pricetrack_api.exceptions import PriceTrackNoCollectionError
from pricetrack_api.models import CollectQuery, Offer

from .peer import all_tiers

# Piso de read timeout (s) para a API de coleta, que pode responder devagar.
# Só eleva; nunca reduz um valor maior configurado via PRICETRACK_TIMEOUT_SECONDS.
_MIN_READ_TIMEOUT_SECONDS = 60.0

# Guarda de plausibilidade de preço (R$): ar-condicionado novo não custa menos
# que isso, e valores absurdos são placeholder de indisponível (…9999) ou erro
# de coleta. Fora da faixa a linha é descartada — não contamina piso/média.
_PRICE_MIN_BRL = 300.0
_PRICE_MAX_BRL = 60_000.0

# PostgREST devolve no máximo ~1000 linhas por request; paginamos por range.
_SUPABASE_PAGE = 1000
_SUPABASE_MAX_ROWS = 20_000          # teto para 1 dia (fetch_supabase) — de sobra
# Teto para o intervalo de dias (fetch_supabase_range — gráfico de evolução).
# Bem mais alto que o de 1 dia: sem filtro de marca, 15-30 dias de
# `pricetrack_daily` podem passar de 20k linhas fácil, e um teto baixo aqui
# truncava a janela em silêncio (o gráfico calculava a tendência com menos
# dias do que o pedido, sem avisar ninguém).
_SUPABASE_RANGE_MAX_ROWS = 300_000


def peer_brands() -> List[str]:
    """Marcas presentes no peer (para o filtro server-side de marca)."""
    brands = {m.brand.upper() for pt in all_tiers() for m in pt.models}
    return sorted(brands)


#: Base de preço de uma linha de ``pricetrack_daily`` (migração 006).
#: ``best_cash`` = menor entre spot e PIX, só ofertas AVAILABLE — a base que o
#: painel do PriceTrack exibe. ``spot_legacy`` = base antiga (spot com fallback
#: para preço a prazo, indisponível incluída), gravada até 01/09/2026 e **não
#: comparável** com a nova: em marketplace com desconto PIX ela fica ~10% acima.
PRICE_BASIS_BEST_CASH = "best_cash"
PRICE_BASIS_SPOT_LEGACY = "spot_legacy"


@dataclass(frozen=True, slots=True)
class LiveResult:
    """Resultado de um pull ao vivo."""

    offers: List[Offer]
    collection_date: Optional[str]
    days_back: int                  # 0 = hoje, 1 = ontem, ...
    #: {price_basis: nº de linhas} do que foi lido. Mais de uma chave = a
    #: janela mistura bases de preço e nenhuma comparação entre elas vale.
    price_basis: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RangeResult:
    """Leitura de um intervalo de dias, agrupada por data."""

    by_date: Dict[str, List[Offer]]
    #: {price_basis: nº de linhas} de TODO o intervalo. Duas chaves aqui é o
    #: caso perigoso: a série de evolução emendaria bases diferentes e o degrau
    #: do dia da correção pareceria movimento de mercado.
    price_basis: Dict[str, int] = field(default_factory=dict)


def find_latest_date(
    client: PriceTrackClient,
    reference: Optional[date] = None,
    max_days_back: int = 10,
    brands: Optional[Sequence[str]] = None,
) -> Optional[tuple]:
    """Acha a data mais recente com coleta. Retorna (iso, days_back) ou None.

    A sonda conta apenas as ofertas das marcas do peer (``product_brand``). Sem
    esse filtro, o ``count`` percorre a coleta INTEIRA do dia (dezenas de
    milhares de linhas) e o servidor estoura o read timeout — era o motivo de a
    página ficar minutos girando em ``Read timed out``.
    """
    reference = reference or date.today()
    brand_filter = list(brands) if brands else None
    for i in range(max_days_back + 1):
        d = reference - timedelta(days=i)
        try:
            total = client.count_offers(
                CollectQuery(d.isoformat(), product_brand=brand_filter, take=1)
            )
        except PriceTrackNoCollectionError:
            continue
        except Exception:
            continue
        if total and total > 0:
            return d.isoformat(), i
    return None


def fetch_live(
    settings: Optional[PriceTrackSettings] = None,
    collection_date: Optional[str] = None,
    brands: Optional[Sequence[str]] = None,
    reference: Optional[date] = None,
) -> LiveResult:
    """Puxa ofertas ao vivo da API.

    Args:
        settings: config já montada; se None, ``PriceTrackSettings.from_env()``
            (exige ``PRICETRACK_API_KEY`` no ambiente/secret).
        collection_date: data ISO específica; se None, usa a mais recente.
        brands: filtro de marca (default: as marcas do peer). Passe ``[]``/None
            explícito só se quiser puxar tudo e casar 100% client-side.
        reference: data-base da busca pela mais recente (default hoje) — testes.

    Raises:
        PriceTrackConfigError: key ausente.
        RuntimeError: nenhuma data com coleta encontrada.
    """
    settings = settings or PriceTrackSettings.from_env()
    # A API de coleta pode responder devagar; um piso de read timeout mais
    # folgado evita o loop de retries por timeout. Respeita um valor maior já
    # vindo do ambiente (PRICETRACK_TIMEOUT_SECONDS).
    if settings.timeout_seconds < _MIN_READ_TIMEOUT_SECONDS:
        settings.timeout_seconds = _MIN_READ_TIMEOUT_SECONDS
    client = PriceTrackClient(settings)

    brand_filter = list(brands) if brands is not None else peer_brands()

    days_back = 0
    if collection_date is None:
        found = find_latest_date(
            client, reference=reference, brands=brand_filter or None
        )
        if found is None:
            raise RuntimeError(
                "Nenhuma coleta encontrada nos últimos dias na API do PriceTrack."
            )
        collection_date, days_back = found

    query = CollectQuery(
        collection_date,
        product_brand=brand_filter or None,
        status="AVAILABLE",
        take=settings.page_take,
    )
    offers = list(client.iter_offers(query))
    return LiveResult(
        offers=offers, collection_date=collection_date, days_back=days_back,
        # Ao vivo o preço sai de `effective_price` (menor à vista, só
        # AVAILABLE) — a mesma base `best_cash` que o import corrigido grava.
        price_basis={PRICE_BASIS_BEST_CASH: len(offers)},
    )


# ── Fonte Supabase (pricetrack_daily) — rápida, padrão ───────────────────────

def _supabase_client():
    """Cria o client Supabase a partir de SUPABASE_URL/SUPABASE_KEY (env).

    A ponte st.secrets→env é feita na camada Streamlit (app.py), então aqui só
    lemos o ambiente. Retorna None se pacote/credencial ausentes.
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client
    except Exception:  # noqa: BLE001 — pacote ausente
        return None
    return create_client(url, key)


def supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip()
                and os.getenv("SUPABASE_KEY", "").strip())


def _representative_price(row: Dict[str, Any]) -> Optional[float]:
    """Preço-observação de uma listagem, na ordem que reconcilia com o painel.

    ``last_price`` primeiro: é o preço da ÚLTIMA coleta da janela, exatamente o
    que o painel do PriceTrack exibe ("Preço exibido: última coleta"). Antes
    esta função começava por ``min_price``, que é o **piso da janela inteira** —
    quando o preço mexia no dia os dois divergiam e o painel não fechava com o
    dashboard, sem nada na tela explicando por quê.

    ``min_price`` segue como fallback para as linhas gravadas antes da migração
    006 (que não têm ``last_price``), e ``mode``/``avg`` para linha capenga.
    """
    for col in ("last_price", "min_price", "mode_price", "avg_price"):
        v = row.get(col)
        if v is None:
            continue
        try:
            price = float(v)
        except (TypeError, ValueError):
            continue
        if _PRICE_MIN_BRL <= price <= _PRICE_MAX_BRL:
            return round(price, 2)
    return None


def _basis_counter(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Conta as bases de preço presentes nas linhas lidas.

    Linha sem ``price_basis`` é do histórico anterior à migração 006 e conta
    como ``spot_legacy`` — a base errada. Chamador algum deve tratar ausência
    de carimbo como "provavelmente está certo".
    """
    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("price_basis") or PRICE_BASIS_SPOT_LEGACY)] += 1
    return dict(counts)


def _row_to_offer(row: Dict[str, Any]) -> Optional[Offer]:
    """Converte uma linha de ``pricetrack_daily`` numa ``Offer`` sintética.

    ``status`` sai como ``AVAILABLE`` porque em ``price_basis='best_cash'`` a
    disponibilidade **já foi aplicada no import** (só observação AVAILABLE entra
    no preço; grupo 100% indisponível chega com preço NULL e é descartado logo
    acima). Em linha ``spot_legacy`` isso é uma promessa que o dado não cumpre:
    a base antiga somava oferta indisponível no mínimo de mercado e não há como
    desfazer isso na leitura — só reimportando o dia.
    """
    price = _representative_price(row)
    if price is None:
        return None
    title = str(row.get("title") or "")
    return Offer(
        id=str(row.get("id") or f"{row.get('sku')}-{row.get('seller')}-{price}"),
        sku=str(row.get("sku") or ""),
        title=title,
        product_name=title,
        brand=str(row.get("brand") or "").upper(),
        category="", subcategory="", family="", color=None,
        marketplace=str(row.get("marketplace") or ""),
        # seller_canonical colapsa grafias do mesmo lojista (FRIOPECAS=FRIOPEÇAS
        # =LOJA OFICIAL FRIOPEÇAS) — dropdown de Vendedor mais limpo. Cai para
        # `seller` cru quando a coluna não vem (ex.: amostra de teste).
        seller=str(row.get("seller_canonical") or row.get("seller") or ""),
        spot_price=price, forward_price=None, pix_price=None, price_from=None,
        installment_number=None, installment_value=None,
        status="AVAILABLE",
        collection_date=None, collection_hour=None,
        image_url="", screenshot_url=None, url="",
    )


def supabase_latest_date(client, turno: str = "Diário") -> Optional[str]:
    """Data (ISO) mais recente com linha em ``pricetrack_daily`` para o turno."""
    resp = (
        client.table("pricetrack_daily")
        .select("collection_date")
        .eq("turno", turno)
        .order("collection_date", desc=True)
        .limit(1)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    if not data:
        return None
    return str(data[0].get("collection_date"))[:10]


def fetch_supabase(
    collection_date: Optional[str] = None,
    brands: Optional[Sequence[str]] = None,
    turno: str = "Diário",
    reference: Optional[date] = None,
    client=None,
) -> LiveResult:
    """Lê ofertas do dia em ``pricetrack_daily`` (fonte rápida).

    Args:
        collection_date: data ISO; se None, usa a mais recente disponível.
        brands: filtro de marca (default: marcas do peer).
        turno: agregado diário por padrão ("Diário").
        client: client Supabase (injetável em teste); senão monta do ambiente.

    Raises:
        RuntimeError: Supabase não configurado ou sem dados.
    """
    client = client or _supabase_client()
    if client is None:
        raise RuntimeError(
            "Supabase não configurado — defina SUPABASE_URL e SUPABASE_KEY "
            "(env ou .streamlit/secrets.toml)."
        )
    brand_filter = [b.upper() for b in (brands if brands is not None else peer_brands())]

    if collection_date is None:
        collection_date = supabase_latest_date(client, turno)
        if not collection_date:
            raise RuntimeError("Sem dados em pricetrack_daily para o turno.")

    rows: List[Dict[str, Any]] = []
    offset = 0
    while offset < _SUPABASE_MAX_ROWS:
        q = (
            client.table("pricetrack_daily")
            .select("collection_date,turno,brand,sku,title,marketplace,seller,"
                    "seller_canonical,min_price,avg_price,mode_price,max_price,"
                    "price_basis,last_price,obs_count,id")
            .eq("collection_date", collection_date)
            .eq("turno", turno)
        )
        if brand_filter:
            q = q.in_("brand", brand_filter)
        resp = q.range(offset, offset + _SUPABASE_PAGE - 1).execute()
        page = getattr(resp, "data", None) or []
        rows.extend(page)
        if len(page) < _SUPABASE_PAGE:
            break
        offset += _SUPABASE_PAGE

    offers = [o for o in (_row_to_offer(r) for r in rows) if o is not None]

    days_back = 0
    ref = reference or date.today()
    try:
        days_back = max(0, (ref - date.fromisoformat(collection_date)).days)
    except (TypeError, ValueError):
        days_back = 0
    return LiveResult(
        offers=offers, collection_date=collection_date, days_back=days_back,
        price_basis=_basis_counter(rows),
    )


def fetch_supabase_range_detailed(
    start_date: str,
    end_date: str,
    brands: Optional[Sequence[str]] = None,
    turno: str = "Diário",
    client=None,
) -> RangeResult:
    """Lê ``pricetrack_daily`` num intervalo de datas, agrupado por dia.

    Para o gráfico de evolução (Midea moda × mediana dos peers): uma consulta
    só, depois agrupamos em Python por ``collection_date`` — mais barato que
    uma chamada por dia.

    Args:
        start_date, end_date: ISO, inclusive nos dois extremos.
        brands: filtro de marca (default: marcas do peer).
        turno: agregado diário por padrão ("Diário").
        client: client Supabase (injetável em teste); senão monta do ambiente.

    Returns:
        ``RangeResult`` com ``by_date`` (``{data ISO: [Offer, ...]}`` — dias sem
        nenhuma linha simplesmente não aparecem) e ``price_basis``, a contagem
        de bases de preço do intervalo inteiro. Duas bases num mesmo intervalo
        é o caso que o chamador PRECISA avisar na tela: a série emendaria dado
        corrigido com dado da base antiga e o degrau pareceria mercado.

    Raises:
        RuntimeError: Supabase não configurado.
    """
    client = client or _supabase_client()
    if client is None:
        raise RuntimeError(
            "Supabase não configurado — defina SUPABASE_URL e SUPABASE_KEY "
            "(env ou .streamlit/secrets.toml)."
        )
    brand_filter = [b.upper() for b in (brands if brands is not None else peer_brands())]

    rows: List[Dict[str, Any]] = []
    offset = 0
    truncated = False
    while offset < _SUPABASE_RANGE_MAX_ROWS:
        q = (
            client.table("pricetrack_daily")
            .select("collection_date,turno,brand,sku,title,marketplace,seller,"
                    "seller_canonical,min_price,avg_price,mode_price,max_price,"
                    "price_basis,last_price,obs_count,id")
            .gte("collection_date", start_date)
            .lte("collection_date", end_date)
            .eq("turno", turno)
        )
        if brand_filter:
            q = q.in_("brand", brand_filter)
        resp = q.range(offset, offset + _SUPABASE_PAGE - 1).execute()
        page = getattr(resp, "data", None) or []
        rows.extend(page)
        if len(page) < _SUPABASE_PAGE:
            break
        offset += _SUPABASE_PAGE
    else:
        # Saiu do while por ter atingido o teto, não porque a última página
        # veio curta — pode haver mais linhas no intervalo que não lemos.
        truncated = True

    if truncated:
        logger.warning(
            f"pricetrack_dashboard.fetch_supabase_range: teto de "
            f"{_SUPABASE_RANGE_MAX_ROWS} linhas atingido para {start_date}..{end_date} "
            "— a série de evolução pode estar incompleta nesse intervalo."
        )

    by_date: Dict[str, List[Offer]] = defaultdict(list)
    for row in rows:
        offer = _row_to_offer(row)
        if offer is None:
            continue
        d = str(row.get("collection_date") or "")[:10]
        if d:
            by_date[d].append(offer)
    return RangeResult(by_date=dict(by_date), price_basis=_basis_counter(rows))


def fetch_supabase_range(
    start_date: str,
    end_date: str,
    brands: Optional[Sequence[str]] = None,
    turno: str = "Diário",
    client=None,
) -> Dict[str, List[Offer]]:
    """``fetch_supabase_range_detailed`` sem o carimbo de base (compatível)."""
    return fetch_supabase_range_detailed(
        start_date, end_date, brands=brands, turno=turno, client=client
    ).by_date


# ── Modo demo (offline) ──────────────────────────────────────────────────────

# Preços-âncora plausíveis por (tier, capacidade) em R$, à vista. Só para a
# amostra sintética renderizar — NÃO são referência de mercado.
_DEMO_ANCHOR = {
    ("Low", "9K"): 1750,
    ("Low", "12K"): 1950,
    ("Mid", "9K"): 2150,
    ("Mid", "12K"): 2450,
    ("High", "9K"): 2650,
    ("High", "12K"): 2990,
}
_DEMO_MARKETPLACES = [
    "MERCADO LIVRE", "AMAZON", "MAGALU", "CASAS BAHIA", "LEROY MERLIN",
]


def demo_offers(seed: int = 42) -> List[Offer]:
    """Amostra sintética cobrindo os modelos do peer (para render offline)."""
    rng = random.Random(seed)
    offers: List[Offer] = []
    oid = 0
    for pt in all_tiers():
        anchor = _DEMO_ANCHOR[(pt.tier, pt.capacity)]
        for model in pt.models:
            code = model.codes[0]
            # Midea um pouco mais barata; concorrentes espalhados em torno da âncora.
            base = anchor * (0.95 if model.is_midea else rng.uniform(0.9, 1.15))
            n_sellers = rng.randint(2, 6)
            for _ in range(n_sellers):
                oid += 1
                price = round(base * rng.uniform(0.93, 1.07), 2)
                offers.append(
                    Offer(
                        id=f"demo-{oid}",
                        sku=code,
                        title=f"Ar Condicionado {model.brand} {pt.capacity} {code}",
                        product_name=f"{model.brand} {pt.midea_line if model.is_midea else ''} {code}".strip(),
                        brand=model.brand,
                        category="AR CONDICIONADO",
                        subcategory="SPLIT",
                        family=pt.midea_line if model.is_midea else "",
                        color=None,
                        marketplace=rng.choice(_DEMO_MARKETPLACES),
                        seller=rng.choice(["WebContinental", "Leveros", "Dufrio", "Midea Store"]),
                        spot_price=price,
                        forward_price=round(price * 1.12, 2),
                        pix_price=round(price * 0.97, 2),
                        price_from=round(price * 1.2, 2),
                        installment_number=10,
                        installment_value=round(price / 10, 2),
                        status="AVAILABLE",
                        collection_date=None,
                        collection_hour=8,
                        image_url="",
                        screenshot_url=None,
                        url="https://example.com/demo",
                    )
                )
    return offers
