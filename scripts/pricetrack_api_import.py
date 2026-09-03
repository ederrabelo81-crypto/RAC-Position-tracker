#!/usr/bin/env python3
"""
pricetrack_api_import.py — Importação histórica 2026 via PriceTrack API.

Usa o endpoint bulk export (NDJSON.gz) para baixar ofertas dia a dia,
através do pacote ``pricetrack_api`` (cliente tipado com retry/backoff,
polling assíncrono, renovação de downloadUrl expirada e até 3 exports
concorrentes — o pipeline de download roda em lote via ExportManager).
Depois agrega preços (min/avg/mode/max) por (data, turno, brand, sku,
marketplace, seller) e persiste em `pricetrack_daily`. O turno
(Diário/Manhã/Tarde) recorta as ofertas por `collection_hour`
(08–12h / 18–22h BRT) para alimentar os turnos do dashboard.

Requer no .env:
    PRICETRACK_API_KEY=<token>   ← obrigatório
    SUPABASE_URL + SUPABASE_KEY  ← já configurados

Uso:
    python scripts/pricetrack_api_import.py
    python scripts/pricetrack_api_import.py --start 2026-01-01 --end 2026-05-11
    python scripts/pricetrack_api_import.py --dry-run
    python scripts/pricetrack_api_import.py --force --start 2026-01-01
    python scripts/pricetrack_api_import.py --no-upload   # só baixa arquivos
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── Project root no path ────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

import pandas as pd
from loguru import logger

from pricetrack_api import (
    OUTCOME_NO_DATA,
    OUTCOME_OK,
    ExportManager,
    ExportRequest,
    PriceTrackClient,
    PriceTrackSettings,
)

try:
    from supabase import create_client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False
    logger.warning("supabase-py não instalado — upload desabilitado")

try:
    from pricetrack_importer.seller_map import normalize_seller
    _HAS_SELLER_MAP = True
except ImportError:
    _HAS_SELLER_MAP = False

    def normalize_seller(raw: str) -> str:  # fallback simples
        if not raw:
            return ""
        return " ".join(raw.strip().upper().split())


# ── Constantes ──────────────────────────────────────────────────────────────
_MAX_CONCURRENT = 3       # limite da API (exports concorrentes por organização)
_DOWNLOAD_DIR = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "raw"
_PROGRESS_FILE = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "progress.json"
_BATCH_SIZE = 500
_TABLE = "pricetrack_daily"
_LOG_TABLE = "pricetrack_import_log"


# ── Download em lote via pricetrack_api ─────────────────────────────────────

def _offers_dest(collection_date: str) -> Path:
    return _DOWNLOAD_DIR / f"offers-{collection_date}.ndjson.gz"


def prefetch_exports(token: str, dates: List[str], concurrent: int) -> Dict[str, str]:
    """Baixa em lote os NDJSON.gz das datas que precisam de (re)download.

    Delegado ao ``ExportManager`` do pacote ``pricetrack_api``: até
    ``concurrent`` exports em voo (respeitando o limite de 3 da API),
    retry com backoff exponencial, tratamento tipado de 401/400/409/429 e
    renovação da downloadUrl expirada (TTL 1h).

    Args:
        token: PRICETRACK_API_KEY (nunca é logado).
        dates: datas ISO a garantir em disco.
        concurrent: exports simultâneos (1-3).

    Returns:
        {data: status} com status ∈ {"ok", "cached", "no_data", "failed"}.
    """
    statuses: Dict[str, str] = {}
    to_fetch: List[str] = []
    for ds in dates:
        if _should_redownload(ds, _offers_dest(ds).exists()):
            to_fetch.append(ds)
        else:
            statuses[ds] = "cached"
            logger.info(f"{ds} — arquivo já existe, pulando download")

    if not to_fetch:
        return statuses

    # data_dir explicitamente ABSOLUTO: além do NDJSON, é onde mora o diário
    # de exports (`exports_state.json`), que só serve se a próxima execução o
    # encontrar. Com o default relativo, rodar o script de outro diretório
    # gravaria o diário em outro lugar — e a adoção do export órfão, que é o
    # que evita o 429, silenciosamente não aconteceria.
    settings = PriceTrackSettings.from_env(
        api_key=token,
        **({} if os.getenv("PRICETRACK_DATA_DIR") else {"data_dir": _DOWNLOAD_DIR.parent}),
    )
    manager = ExportManager(
        PriceTrackClient(settings), dataset="offers", max_concurrent=concurrent
    )
    logger.info(
        f"Baixando {len(to_fetch)} export(s) com até {concurrent} em voo: "
        f"{', '.join(to_fetch)}"
    )
    outcomes = manager.run_many(
        [ExportRequest(collection_date=ds) for ds in to_fetch],
        dest_fn=lambda req: _offers_dest(req.collection_date.isoformat()),
    )
    adotados = sum(1 for o in outcomes if o.adopted)
    if adotados:
        logger.info(
            f"{adotados} export(s) retomados de execução anterior (diário) — "
            f"nenhum slot da organização foi gasto com duplicata."
        )
    for outcome in outcomes:
        ds = outcome.request.collection_date.isoformat()
        if outcome.status == OUTCOME_OK:
            statuses[ds] = "ok"
        elif outcome.status == OUTCOME_NO_DATA:
            statuses[ds] = "no_data"
            logger.warning(f"{ds} — sem dados na API (409)")
        else:
            statuses[ds] = "failed"
            logger.error(f"{ds} — export/download falhou: {outcome.error}")
    return statuses


# ── Parsing e agregação ─────────────────────────────────────────────────────

def parse_ndjson_gz(path: Path) -> pd.DataFrame:
    """Lê arquivo NDJSON.gz e retorna DataFrame com uma linha por oferta."""
    records: List[Dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return pd.DataFrame(records) if records else pd.DataFrame()


# Candidatos de nome de coluna (lowercase) por campo lógico.
# O NDJSON do export usa snake_case (spot_price, pix_price) enquanto o
# schema OpenAPI documenta camelCase (spotPrice, pixPrice). Ambas as
# grafias estão listadas para robustez.
#
# ⚠️ Base de preço — os campos são SEPARADOS de propósito (Set/2026).
# Até 01/09/2026 havia uma tupla `_PRICE_FIELDS` única e o preço da oferta era
# o primeiro campo não-nulo dela: `spot_price` primeiro, `pix_price` só como
# tapa-buraco, e — o pior — `forward_price` (a prazo) quando não havia à vista.
# Isso produzia dois erros que se somavam:
#   1. o painel do PriceTrack exibe o MENOR à vista (À Vista + PIX + "Menor"),
#      então em marketplace com desconto PIX (Magazine Luiza, 10%) o número
#      gravado ficava ~10% acima do que o painel — e o comprador — vê;
#   2. preço a prazo entrava na mesma série de min/média/moda que preço à
#      vista, misturando bases dentro do mesmo número.
# Agora: preço à vista = MENOR entre spot e PIX (contrato `best_cash` de
# `pricetrack_api.normalize`). `forward` é lido só para diagnóstico e NUNCA
# entra no preço. Ao mexer aqui, mexa também em `normalize.best_cash` — as duas
# pontas têm de contar a mesma história.
_SPOT_FIELDS = ("spot_price", "spotprice", "preco_avista")
_PIX_FIELDS = ("pix_price", "pixprice")
_FORWARD_FIELDS = ("forward_price", "forwardprice")
# Último recurso, só se o export deixar de trazer spot E pix (mudança de
# schema). Nunca inclui campo a prazo.
_GENERIC_PRICE_FIELDS = ("price", "sale_price", "saleprice", "preco", "valor")
_STATUS_FIELDS = ("status",)
_BRAND_FIELDS = ("brand", "productbrand", "product_brand", "marca")
_SKU_FIELDS = ("sku", "productsku", "product_sku", "sku_code", "codigo", "cod")
_TITLE_FIELDS = ("product_name", "productname", "title", "name", "produto", "titulo")
_MARKETPLACE_FIELDS = ("marketplace", "market", "loja_marketplace")
_SELLER_FIELDS = ("seller", "vendedor", "store", "loja")
_CATEGORY_FIELDS = ("category", "categoria", "product_category", "productcategory")
_HOUR_FIELDS = ("collection_hour", "collectionhour")

# Categorias de ar condicionado aceitas (uppercase, comparação exata).
# Configurável via --categories na CLI.
DEFAULT_CATEGORIES: List[str] = ["AR CONDICIONADO"]

# Janelas de turno por hora de coleta (`collection_hour`, em BRT). O PriceTrack
# carimba a hora real do crawl — verificado no export bruto: `collection_hour`
# == hora de `collection_hour_execution` em 100% das ofertas de AR CONDICIONADO,
# com coletas distribuídas pelas 24h. Manhã = 08–12h, Tarde = 18–22h
# (inclusivas). Horas fora das janelas entram só no agregado "Diário".
TURNO_DIARIO = "Diário"
TURNO_MANHA = "Manhã"
TURNO_TARDE = "Tarde"
TURNO_MANHA_HOURS: Set[int] = set(range(8, 13))    # 8, 9, 10, 11, 12
TURNO_TARDE_HOURS: Set[int] = set(range(18, 23))   # 18, 19, 20, 21, 22

# Base de preço carimbada em cada linha (migração 006). Sem esse carimbo não há
# como distinguir uma linha corrigida de uma linha da base antiga, e um gráfico
# de evolução emendaria as duas numa série só — degrau artificial de ~10% no
# dia da correção, que é exatamente o tipo de mentira que este import combate.
PRICE_BASIS_BEST_CASH = "best_cash"       # menor entre spot e PIX, só AVAILABLE
PRICE_BASIS_SPOT_LEGACY = "spot_legacy"   # base antiga (≤ 01/09/2026)

# Motivos que o `rejection_log` registra para diagnóstico mas que NÃO somam em
# `rows_rejected`: cada um é subconjunto de um motivo já contado, e somá-los
# contaria a mesma oferta duas vezes. FORWARD_PRICE_ONLY ⊂ NO_CASH_PRICE.
_DIAGNOSTIC_REASONS = frozenset({"FORWARD_PRICE_ONLY"})

# Colunas da migração 006. Sem elas o import ABORTA (ver `insert_rows`): uma
# linha corrigida sem carimbo de base é pior que import nenhum.
_MIGRATION_006_COLUMNS = (
    "price_basis", "last_price", "last_hour",
    "spot_min_price", "pix_min_price", "obs_count", "unavailable_count",
)


def _mode(series: pd.Series) -> float:
    m = series.dropna().mode()
    if len(m) > 0:
        return float(m.iloc[0])
    v = series.dropna()
    # Grupo sem nenhum preço válido devolve NaN (vira NULL na tabela), não 0.0:
    # moda R$ 0,00 seria lida como preço real por todo consumidor a jusante.
    return float(v.mean()) if len(v) > 0 else float("nan")


def _pick_text(df: pd.DataFrame, lookup: Dict[str, str],
               candidates: Tuple[str, ...], default: str = "") -> pd.Series:
    """Devolve a primeira coluna textual encontrada (case-insensitive)."""
    for cand in candidates:
        if cand in lookup:
            return df[lookup[cand]].fillna(default).astype(str).str.strip()
    return pd.Series([default] * len(df), index=df.index, dtype="object")


def _pick_numeric(
    df: pd.DataFrame, lookup: Dict[str, str], candidates: Tuple[str, ...]
) -> pd.Series:
    """Coalesce das colunas candidatas, saneada. NaN quando nenhuma existe.

    Os candidatos são GRAFIAS do mesmo campo lógico (``spot_price`` vs
    ``spotPrice``), então o valor de qualquer uma serve. Coalescemos todas as
    presentes em vez de parar na primeira: um export que traga as duas grafias
    com a primeira nula perderia o preço válido da segunda — oferta rejeitada
    ou preço subestimado, em silêncio.

    Saneamento espelha ``pricetrack_api.normalize.clean_price``: não-numérico,
    ±inf e valores ≤ 0 viram NaN (preço ausente) — nunca 0.0, que contaminaria
    mínimos e médias.
    """
    out = pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    for cand in candidates:
        if cand in lookup:
            col = pd.to_numeric(df[lookup[cand]], errors="coerce")
            out = out.fillna(col)
    return out.where(out > 0).replace([float("inf"), float("-inf")], float("nan"))


def _cash_price(spot: pd.Series, pix: pd.Series, generic: pd.Series) -> pd.Series:
    """Preço à vista efetivo da oferta: o MENOR entre spot e PIX.

    É o mesmo contrato de ``pricetrack_api.normalize.NormalizedPrices.best_cash``
    e o mesmo que o painel do PriceTrack exibe com "À Vista + PIX + Menor" —
    ou seja, o preço que o comprador de fato paga à vista.

    ``generic`` só entra onde spot E pix faltam, para o caso de o export mudar
    de schema. Preço a prazo nunca participa: somar parcelamento a uma série de
    preço à vista inventa um número que não existe em nenhuma vitrine.
    """
    best = pd.concat([spot, pix], axis=1).min(axis=1)   # min ignora NaN
    return best.fillna(generic)


def aggregate_offers(
    df: pd.DataFrame,
    collection_date: str,
    categories: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Filtra por categoria e agrega ofertas para o formato daily.

    Grupo: (collection_date, turno, brand, sku, title, marketplace, seller),
    onde turno ∈ {Diário (dia inteiro), Manhã (08–12h), Tarde (18–22h)}
    recortado por `collection_hour`.
    Resolve nomes de campo de forma case-insensitive para lidar com
    snake_case do NDJSON vs camelCase do schema OpenAPI.

    Preço agregado é o **menor à vista** (spot vs PIX) das observações
    **AVAILABLE** — a base `best_cash`, que é a que o painel do PriceTrack
    exibe. Ofertas UNAVAILABLE não entram em preço nenhum (indisponível não
    compete no piso), mas o grupo sobrevive com preços NULL e
    `unavailable_count` > 0: a listagem existiu, só não competiu por preço.

    Filtros aplicados em ordem:
      1. Categoria (campo `category`) — default: AR CONDICIONADO
      2. Preço à vista válido (> 0) — a prazo NUNCA vira preço
      3. SKU presente — linhas sem `sku` não reconciliam com o catálogo
         (join PT × coletas) e são rejeitadas, espelhando o validador do
         importador manual (MISSING_FIELD/sku). Roadmap §3 item 9.
      4. `status` — UNAVAILABLE sai das estatísticas de preço (mas é contada)

    brand/title vazios são mantidos — melhor importar com identificador
    parcial do que perder a oferta.

    Returns:
        Tupla (df_agregado, rejeições) onde rejeições é um breakdown
        {motivo: nº de linhas} para o rejection_log do import.
    """
    rejections: Dict[str, int] = {}
    if df.empty:
        return pd.DataFrame(), rejections

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lookup = {c.lower(): c for c in df.columns}

    # ── 1. Filtro de categoria ────────────────────────────────────────────
    allowed = [c.upper().strip() for c in (categories or DEFAULT_CATEGORIES)]
    cat_col_orig = None
    for cand in _CATEGORY_FIELDS:
        if cand in lookup:
            cat_col_orig = lookup[cand]
            break

    total_raw = len(df)
    if cat_col_orig:
        cat_series = df[cat_col_orig].fillna("").astype(str).str.strip().str.upper()
        df = df[cat_series.isin(allowed)]
        n_after_cat = len(df)
        if total_raw - n_after_cat:
            rejections["OUT_OF_CATEGORY"] = total_raw - n_after_cat
        logger.info(
            f"{collection_date} — filtro de categoria {allowed}: "
            f"{n_after_cat:,}/{total_raw:,} linhas mantidas"
        )
    else:
        n_after_cat = total_raw
        logger.warning(
            f"{collection_date} — coluna de categoria não encontrada; "
            f"importando todas as {total_raw:,} linhas (sem filtro de categoria)"
        )

    if df.empty:
        logger.warning(
            f"{collection_date} — nenhuma oferta para categorias {allowed}. "
            f"Verifique o valor exato com --inspect e ajuste --categories."
        )
        return pd.DataFrame(), rejections

    # Reconstrói lookup após filtro
    lookup = {c.lower(): c for c in df.columns}

    # ── 2. Preço à vista (menor entre spot e PIX) ────────────────────────
    spot = _pick_numeric(df, lookup, _SPOT_FIELDS)
    pix = _pick_numeric(df, lookup, _PIX_FIELDS)
    forward = _pick_numeric(df, lookup, _FORWARD_FIELDS)
    generic = _pick_numeric(df, lookup, _GENERIC_PRICE_FIELDS)
    price = _cash_price(spot, pix, generic)
    n_with_price = int(price.notna().sum())

    # Quantas ofertas o PIX de fato barateia — é a medida direta do erro que a
    # base antiga cometia (ela gravava o spot e ignorava esse desconto).
    n_pix_wins = int((pix.notna() & spot.notna() & (pix < spot)).sum())
    if n_pix_wins:
        logger.info(
            f"{collection_date} — PIX é o menor à vista em {n_pix_wins:,} "
            f"oferta(s); a base antiga gravava o spot nessas linhas."
        )
    elif pix.notna().any():
        logger.info(f"{collection_date} — PIX presente mas nunca menor que o spot.")
    else:
        logger.warning(
            f"{collection_date} — export SEM campo de PIX preenchido. O preço "
            f"gravado é o spot; confira com scripts/pricetrack_price_audit.py."
        )

    # Ofertas que só têm preço a prazo: antes viravam "preço" via fallback
    # (misturando base), agora são rejeitadas explicitamente.
    n_forward_only = int((price.isna() & forward.notna()).sum())

    status = _pick_text(df, lookup, _STATUS_FIELDS).str.upper().str.strip()
    if _STATUS_FIELDS[0] in lookup:
        # Estritamente AVAILABLE. Status desconhecido NÃO é "disponível": o
        # ponto desta correção é que o preço signifique exatamente uma coisa, e
        # "não sei se dá para comprar" não entra num piso de mercado. Nada some
        # em silêncio — o que não é AVAILABLE entra em `unavailable_count`, e
        # valor inesperado vira WARNING nomeando as grafias vistas.
        unexpected = sorted(
            set(status[~status.isin(("AVAILABLE", "UNAVAILABLE"))].unique())
        )
        if unexpected:
            logger.warning(
                f"{collection_date} — `status` com valor(es) fora de "
                f"AVAILABLE/UNAVAILABLE: {unexpected}. Tratados como "
                f"indisponíveis (fora do preço, contados em unavailable_count)."
            )
        available = status.eq("AVAILABLE")
    else:
        # Export sem coluna de status: nada a filtrar, tudo conta como
        # disponível (mantém o comportamento anterior em vez de zerar o dia).
        logger.warning(
            f"{collection_date} — export sem campo `status`; nenhuma oferta "
            f"pôde ser excluída por indisponibilidade."
        )
        available = pd.Series(True, index=df.index)

    work = pd.DataFrame({
        "_price": price,
        "_spot": spot,
        "_pix": pix,
        "_available": available,
        "_hour": pd.to_numeric(
            _pick_text(df, lookup, _HOUR_FIELDS), errors="coerce"
        ).astype("Int64"),
        "brand": _pick_text(df, lookup, _BRAND_FIELDS).str.upper(),
        "sku": _pick_text(df, lookup, _SKU_FIELDS),
        "title": _pick_text(df, lookup, _TITLE_FIELDS),
        "marketplace": _pick_text(df, lookup, _MARKETPLACE_FIELDS),
        "seller": _pick_text(df, lookup, _SELLER_FIELDS),
    })

    before = len(work)
    # Descarta APENAS a observação que não consegue ser preço: disponível e sem
    # à vista. A indisponível segue viva mesmo sem preço — é ela que sustenta o
    # `unavailable_count` e mantém a listagem na tabela quando o grupo inteiro
    # esteve fora do ar. Filtrar por `_price` antes de agrupar (como a primeira
    # versão fazia) apagava justamente esses grupos, que é o oposto do
    # prometido: indisponível não compete no piso, mas não desaparece.
    work = work[work["_price"].notna() | ~work["_available"]]
    dropped_no_price = before - len(work)
    if dropped_no_price:
        rejections["NO_CASH_PRICE"] = dropped_no_price
    if n_forward_only:
        # Diagnóstico, NÃO um motivo de rejeição próprio: é um subconjunto de
        # NO_CASH_PRICE, e somá-lo contaria a mesma oferta duas vezes em
        # `rows_rejected`. O nome fica legível no `rejection_log`; quem exclui
        # do total é `_DIAGNOSTIC_REASONS`, não uma convenção de prefixo.
        rejections["FORWARD_PRICE_ONLY"] = n_forward_only

    if work.empty:
        logger.warning(
            f"{collection_date} — 0 linhas válidas após filtro de preço à vista.\n"
            f"  Preço à vista presente em {n_with_price:,}/{before:,} ofertas de AC.\n"
            f"  ({n_forward_only:,} tinham só preço a prazo — não viram preço.)\n"
            f"  Colunas no arquivo ({len(df.columns)}): {list(df.columns)}"
        )
        sample = df.iloc[0].to_dict()
        logger.warning(
            f"{collection_date} — registro de exemplo:\n"
            f"{json.dumps(sample, ensure_ascii=False, default=str)[:2000]}"
        )
        return pd.DataFrame(), rejections

    logger.debug(
        f"{collection_date} — preço à vista presente em {n_with_price:,}/{before:,} "
        f"ofertas AC; {dropped_no_price:,} descartadas sem preço à vista"
    )

    # ── 3. SKU obrigatório (roadmap §3 item 9) ───────────────────────────
    # Sem sku a linha não resolve no catálogo (produtos_depara_nome) e o
    # cruzamento PriceTrack × coletas nunca acontece — entraria como ruído
    # permanente. Rejeita e registra no rejection_log para auditoria.
    before_sku = len(work)
    work = work[work["sku"].str.strip() != ""]
    dropped_no_sku = before_sku - len(work)
    if dropped_no_sku:
        rejections["MISSING_SKU"] = dropped_no_sku
        logger.warning(
            f"{collection_date} — {dropped_no_sku:,} oferta(s) sem SKU "
            f"rejeitada(s) (não reconciliam com o catálogo)"
        )

    if work.empty:
        logger.warning(f"{collection_date} — 0 linhas válidas após filtro de SKU.")
        return pd.DataFrame(), rejections

    # A chave de agrupamento é EXATAMENTE a UNIQUE de `pricetrack_daily`
    # (collection_date, turno, brand, sku, marketplace, seller) — sem `title`.
    # Agrupar por título também, como a primeira versão fazia, produzia duas
    # linhas agregadas para o mesmo grupo do banco quando o marketplace mudava
    # o título no meio do dia; o upsert então resolvia o conflito guardando só
    # a última do lote e a outra sumia em silêncio, levando junto as coletas
    # que ela agregava. Agrupamento e armazenamento têm de falar da mesma chave.
    group_keys = ["brand", "sku", "marketplace", "seller"]

    def _title_of(series: pd.Series) -> str:
        """Título representativo do grupo: o mais frequente na janela."""
        titles = series.dropna()
        titles = titles[titles.astype(str).str.strip() != ""]
        if titles.empty:
            return ""
        return str(titles.mode().iloc[0])

    def _agg_turno(rows: pd.DataFrame, turno: str) -> pd.DataFrame:
        """Agrega uma janela do dia. Preço só de observação AVAILABLE."""
        avail = rows[rows["_available"]]
        unavail = rows[~rows["_available"]]

        stats = avail.groupby(group_keys).agg(
            min_price=("_price", "min"),
            avg_price=("_price", "mean"),
            max_price=("_price", "max"),
            mode_price=("_price", _mode),
            spot_min_price=("_spot", "min"),
            pix_min_price=("_pix", "min"),
            obs_count=("_price", "size"),
        )
        # Título vem de TODAS as observações do grupo (inclusive as
        # indisponíveis): grupo 100% indisponível fica sem preço, mas não pode
        # ficar sem título — `title` é NOT NULL na tabela.
        titles = rows.groupby(group_keys)["title"].agg(_title_of).rename("title")

        # `last_price` = o preço da ÚLTIMA coleta da janela, que é o que o
        # painel do PriceTrack exibe ("Preço exibido: última coleta"). Sem ele
        # não há como reconciliar o dashboard com a tela: min_price é o piso da
        # janela inteira e só coincide com o painel quando o preço não mexeu.
        # mergesort é estável — dentro da mesma hora vale a ordem do arquivo.
        ordered = avail.sort_values("_hour", na_position="first", kind="mergesort")
        last = (
            ordered.groupby(group_keys, sort=False)
            .tail(1)
            .set_index(group_keys)[["_price", "_hour"]]
            .rename(columns={"_price": "last_price", "_hour": "last_hour"})
        )
        # Grupos duplicados no índice quebrariam o concat — não deve acontecer
        # (tail(1) por grupo), mas garantir é barato.
        last = last[~last.index.duplicated(keep="last")]

        unav_count = unavail.groupby(group_keys).size().rename("unavailable_count")

        # `outer`: um grupo que só teve observação UNAVAILABLE sobrevive com
        # preços NULL — a listagem existiu (share of shelf), só não competiu
        # por preço. Foi o que a base antiga escondeu ao somar indisponível
        # dentro do mínimo de mercado.
        a = pd.concat([titles, stats, last, unav_count], axis=1).reset_index()
        a["title"] = a["title"].fillna("")
        a["obs_count"] = a["obs_count"].fillna(0).astype(int)
        a["unavailable_count"] = a["unavailable_count"].fillna(0).astype(int)
        a["collection_date"] = collection_date
        a["turno"] = turno
        a["price_basis"] = PRICE_BASIS_BEST_CASH
        a["seller_canonical"] = a["seller"].apply(normalize_seller)
        a["source_file"] = f"api-{collection_date}"
        return a

    # "Diário" = dia inteiro (comportamento histórico, 1 linha por grupo).
    # Manhã/Tarde recortam por `collection_hour` para alimentar os turnos do
    # dashboard — PriceTrack passa a ser a fonte de Manhã/Tarde; as coletas
    # próprias viram fallback. Ofertas sem hora entram apenas no Diário.
    parts = [_agg_turno(work, TURNO_DIARIO)]
    manha = work[work["_hour"].isin(TURNO_MANHA_HOURS)]
    if not manha.empty:
        parts.append(_agg_turno(manha, TURNO_MANHA))
    tarde = work[work["_hour"].isin(TURNO_TARDE_HOURS)]
    if not tarde.empty:
        parts.append(_agg_turno(tarde, TURNO_TARDE))
    agg = pd.concat(parts, ignore_index=True)

    return agg[[
        "collection_date", "turno", "brand", "sku", "title",
        "marketplace", "seller", "seller_canonical",
        "min_price", "avg_price", "mode_price", "max_price",
        "price_basis", "last_price", "last_hour",
        "spot_min_price", "pix_min_price", "obs_count", "unavailable_count",
        "source_file",
    ]], rejections


def inspect_file(path: Path) -> None:
    """Imprime o schema real de um arquivo NDJSON.gz já baixado (diagnóstico)."""
    if not path.exists():
        available = sorted(_DOWNLOAD_DIR.glob("offers-*.ndjson.gz"))
        print(f"Arquivo não encontrado: {path}")
        if available:
            print("\nArquivos disponíveis:")
            for p in available:
                print(f"  {p.name}")
        else:
            print(f"Nenhum arquivo em {_DOWNLOAD_DIR}")
        return

    df = parse_ndjson_gz(path)
    print(f"Arquivo: {path}")
    print(f"Total de registros: {len(df):,}")
    if df.empty:
        return

    print(f"\nColunas ({len(df.columns)}):")
    for c in df.columns:
        non_null = int(df[c].notna().sum())
        sample_val = df[c].dropna().iloc[0] if non_null > 0 else "—"
        sample_str = str(sample_val)[:50]
        print(f"  {c:28s} | {non_null:>8,} preenchidos | ex: {sample_str}")

    print("\n--- Primeiro registro (JSON) ---")
    print(json.dumps(df.iloc[0].to_dict(), ensure_ascii=False, indent=2, default=str))

    # Mostra distribuição de categorias (muito útil para confirmar o filtro)
    for cat_col in ("category", "categoria", "product_category"):
        if cat_col in df.columns:
            print(f"\n--- Categorias presentes ({cat_col}) ---")
            for cat, cnt in df[cat_col].value_counts().items():
                print(f"  {cat:40s} {cnt:>8,}")
            break


# ── Supabase ────────────────────────────────────────────────────────────────

_CLIENT = None


def _supabase_client():
    """Cria (e memoiza) o cliente Supabase reutilizado em todo o script."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise EnvironmentError("SUPABASE_URL e SUPABASE_KEY não configurados no .env")
    _CLIENT = create_client(url, key)
    return _CLIENT


def date_exists(collection_date: str, dry_run: bool = False) -> bool:
    """
    Verifica se uma data já tem linhas em pricetrack_daily.

    Faz uma query pontual `limit(1)` por data — exato e barato, evitando o
    bug do `select` global (PostgREST devolve no máx. 1000 linhas por padrão,
    o que fazia a contagem de datas existentes ficar incorreta).
    """
    if dry_run or not _HAS_SUPABASE:
        return False
    try:
        client = _supabase_client()
        resp = (
            client.table(_TABLE)
            .select("id")
            .eq("collection_date", collection_date)
            .limit(1)
            .execute()
        )
        return len(resp.data) > 0
    except Exception as e:
        logger.warning(f"Não foi possível verificar {collection_date} no banco: {e}")
        return False


def insert_rows(records: List[Dict], dry_run: bool = False) -> int:
    """Insere registros em lotes de _BATCH_SIZE. Retorna total inserido."""
    if dry_run:
        return len(records)
    if not _HAS_SUPABASE:
        logger.warning("supabase-py não disponível — pulando upload")
        return 0

    client = _supabase_client()
    inserted = 0
    for i in range(0, len(records), _BATCH_SIZE):
        batch = records[i : i + _BATCH_SIZE]
        try:
            # Upsert idempotente: o conflict target casa a UNIQUE de
            # pricetrack_daily (migration 003 inclui `turno`), então
            # reimportar uma data (backfill/--force) atualiza em vez de
            # estourar violação de unicidade nas linhas já presentes.
            client.table(_TABLE).upsert(
                batch,
                on_conflict="collection_date,turno,brand,sku,marketplace,seller",
            ).execute()
            inserted += len(batch)
        except Exception as e:
            if _is_unknown_column_error(e):
                # Migração 006 ausente. Abortamos em vez de gravar sem as
                # colunas novas: uma linha `best_cash` sem o carimbo
                # `price_basis` seria depois rotulada `spot_legacy` pelo
                # DEFAULT da própria migração — dado CERTO marcado como errado,
                # sem como recuperar qual era qual. Falhar aqui não perde nada:
                # o NDJSON continua em disco e o import roda de novo depois.
                raise RuntimeError(
                    "pricetrack_daily não tem as colunas da migração 006 "
                    f"({', '.join(_MIGRATION_006_COLUMNS)}). O import foi "
                    "ABORTADO de propósito: gravar agora produziria linha "
                    "corrigida sem carimbo de base, que a migração depois "
                    "marcaria como `spot_legacy`. Aplique e rode de novo:\n"
                    "  psql \"$SUPABASE_DSN\" -f "
                    "migrations/006_pricetrack_price_basis.sql"
                ) from e
            logger.error(f"Erro ao inserir lote {i//_BATCH_SIZE + 1}: {e}")
    return inserted


def _is_unknown_column_error(exc: Exception) -> bool:
    """True se o erro do PostgREST é 'coluna X não existe' (PGRST204/42703)."""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("pgrst204", "42703", "could not find", "does not exist")
    ) and any(col in text for col in _MIGRATION_006_COLUMNS)


def log_import(
    source_file: str,
    rows_total: int,
    rows_inserted: int,
    rows_rejected: int,
    status: str,
    rejection_log: Optional[List] = None,
    dry_run: bool = False,
) -> None:
    if dry_run or not _HAS_SUPABASE:
        return
    try:
        client = _supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        client.table(_LOG_TABLE).insert({
            "source_file": source_file,
            "import_started": now_iso,
            "import_finished": now_iso,
            "rows_total": rows_total,
            "rows_inserted": rows_inserted,
            "rows_updated": 0,
            "rows_rejected": rows_rejected,
            "rejection_log": rejection_log or [],
            "status": status,
        }).execute()
    except Exception as e:
        logger.warning(f"Não foi possível gravar log de importação: {e}")


# ── Progress file ────────────────────────────────────────────────────────────

def load_progress() -> Dict:
    if _PROGRESS_FILE.exists():
        with open(_PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": [], "skipped": [], "failed": []}


def save_progress(progress: Dict) -> None:
    _PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# ── Ciclo de download/processamento ─────────────────────────────────────────

def _should_redownload(
    collection_date: str,
    file_exists: bool,
    today: Optional[date] = None,
) -> bool:
    """Decide se o NDJSON.gz da data deve ser (re)baixado.

    Sempre baixa quando o arquivo ainda não existe. Além disso, força o
    re-download dos 2 dias mais recentes (hoje e ontem): o export do dia
    corrente ainda *cresce* ao longo do dia — o import intra-dia (~13h BRT)
    baixa só as ofertas coletadas até ali, então um arquivo já em cache é
    parcial — e o D-1 pode ter sido importado provisoriamente intra-dia,
    precisando ser substituído pela versão completa. Dias mais antigos são
    imutáveis no PriceTrack e reaproveitam o cache (economia de banda no
    backfill da VM).

    Args:
        collection_date: data alvo em ISO (YYYY-MM-DD).
        file_exists: se o arquivo local já existe.
        today: injeção de data para teste (default: ``date.today()``).

    Returns:
        True se deve baixar; False se pode reaproveitar o cache.
    """
    if not file_exists:
        return True
    ref = today or date.today()
    try:
        cd = date.fromisoformat(collection_date)
    except ValueError:
        return False
    return cd >= ref - timedelta(days=1)


def write_history(
    records: List[Dict],
    collection_date: str,
    dry_run: bool = False,
) -> List[str]:
    """Grava o dia importado no histórico frio, em paralelo ao Supabase.

    Contraparte, para o PriceTrack, da escrita dupla que a coleta faz desde
    Jul/2026. Sem ela o dia só chegava ao Drive quando a migração
    (`history_cli.py tier`) rodava — então um import feito com o banco fora
    ficava sem nenhuma cópia durável.

    O `run_id` é **estável por data** (``import``), de propósito: reimportar a
    mesma data sobrescreve a partição em vez de duplicá-la, que é o
    comportamento certo para o import horário do intra-dia. Quando a migração
    passar por esse dia, ela grava a versão do banco e apaga esta — a
    resolvida é a autoritativa.

    Args:
        records: Linhas já no schema de `pricetrack_daily`.
        collection_date: Dia importado (``YYYY-MM-DD``), só para log.
        dry_run: ``True`` não grava nada.

    Returns:
        Chaves das partições gravadas; lista vazia se desligado, em dry-run,
        sem registros ou se a gravação falhou (o import não morre por isso).
    """
    if dry_run:
        logger.info(f"[DRY-RUN] {collection_date} — histórico frio não gravado.")
        return []
    if not records:
        return []
    # Mesmo interruptor da coleta, para desligar os dois de uma vez.
    if os.getenv("RAC_HISTORY", "on").strip().lower() in ("off", "0", "false"):
        logger.info("[Histórico] desligado por RAC_HISTORY — pulando.")
        return []

    try:
        from utils.history import DATASET_PRICETRACK
        from utils.history import write_records as _write_history

        keys = _write_history(
            records,
            run_id="import",
            dataset=DATASET_PRICETRACK,
            already_mapped=True,      # já vêm no schema da tabela
            date_column="collection_date",
        )
        if not keys:
            logger.warning(
                f"{collection_date} — histórico frio NÃO gravou partição; o dia "
                f"depende do Supabase e do arquivo em {_offers_dest(collection_date)}."
            )
        return keys
    except Exception as exc:
        # Destino do histórico nunca pode derrubar o import: o Supabase ainda
        # pode receber o dia, e o NDJSON baixado continua em disco.
        logger.error(f"{collection_date} — histórico frio falhou: {exc}")
        return []


def _process_date(
    collection_date: str,
    dry_run: bool,
    no_upload: bool,
    download_status: str,
    categories: Optional[List[str]] = None,
) -> Tuple[str, int]:
    """
    Processa uma data cujo download já foi resolvido por ``prefetch_exports``:
      1. Confere o resultado do download (ok/cached/no_data/failed)
      2. Parse do NDJSON.gz + agregação
      3. Insere no Supabase

    Args:
        download_status: resultado do prefetch ("ok", "cached", "no_data",
            "failed" ou "dry_run").

    Retorna (status, rows_inserted).
    """
    dest_path = _offers_dest(collection_date)

    if download_status == "dry_run":
        logger.info(f"[DRY-RUN] {collection_date} — criaria export e baixaria arquivo")
        return "dry_run", 0
    if download_status == "no_data":
        return "no_data", 0
    if download_status == "failed" or not dest_path.exists():
        return "failed", 0

    if no_upload:
        return "downloaded", 0

    # ── Parse e agregação ─────────────────────────────────────────────────
    try:
        df_raw = parse_ndjson_gz(dest_path)
        if df_raw.empty:
            logger.warning(f"{collection_date} — arquivo vazio após parse")
            log_import(f"api-{collection_date}", 0, 0, 0, "SUCCESS")
            return "empty", 0

        rows_raw = len(df_raw)
        logger.info(f"{collection_date} — {rows_raw:,} ofertas brutas")

        df_agg, rejections = aggregate_offers(
            df_raw, collection_date, categories=categories
        )
        rejection_log = [
            {"reason": reason, "rows": count}
            for reason, count in sorted(rejections.items())
        ]
        if df_agg.empty:
            logger.warning(f"{collection_date} — zero linhas após filtro+agregação")
            log_import(
                f"api-{collection_date}", rows_raw, 0, rows_raw, "PARTIAL",
                rejection_log=rejection_log,
            )
            return "empty", 0

        rows_agg = len(df_agg)
        # rows_rejected = ofertas CRUAS efetivamente descartadas pelos filtros
        # (fora de categoria + sem preço + sem sku), somadas do breakdown de
        # rejeições. NÃO usamos rows_raw - rows_agg: com o split de turno o
        # df_agg tem até 3 linhas por grupo (Diário/Manhã/Tarde) e é agregado,
        # então aquela diferença confunde "colapso por agregação" com "rejeição".
        # Motivos de diagnóstico são subconjunto de outro motivo e ficam fora
        # do total — somá-los contaria a mesma oferta duas vezes.
        rows_rejected = int(
            sum(v for k, v in rejections.items() if k not in _DIAGNOSTIC_REASONS)
        )
        logger.info(f"{collection_date} — {rows_agg:,} linhas AC agregadas "
                    f"({rows_rejected:,} ofertas cruas descartadas pelos filtros)")

    except Exception as e:
        logger.error(f"{collection_date} — erro no parse: {e}")
        return "failed", 0

    # ── Inserção no Supabase ──────────────────────────────────────────────
    records = df_agg.where(pd.notnull(df_agg), None).to_dict("records")

    # Converte tipos numéricos para nativos (numpy/NaN não são JSON serializable).
    # NaN precisa virar None explicitamente: `pd.notnull` não pega NaN dentro de
    # coluna object, e um NaN chegaria ao PostgREST como o literal `NaN`.
    for r in records:
        for k in ("min_price", "avg_price", "mode_price", "max_price",
                  "last_price", "spot_min_price", "pix_min_price"):
            v = r.get(k)
            r[k] = None if v is None or pd.isna(v) else round(float(v), 2)
        for k in ("last_hour", "obs_count", "unavailable_count"):
            v = r.get(k)
            r[k] = None if v is None or pd.isna(v) else int(v)

    # ── Histórico frio (Parquet no Drive) ─────────────────────────────────
    # ANTES do Supabase e independente dele: é o que faz o dia sobreviver a um
    # banco fora do ar ou restrito por cota. Mesma ordem usada pela coleta em
    # main.py — foi a ausência dela que deixou 17 dias só no artifact.
    write_history(records, collection_date, dry_run=dry_run)

    inserted = insert_rows(records, dry_run=dry_run)
    log_import(
        source_file=f"api-{collection_date}",
        rows_total=rows_raw,
        rows_inserted=inserted,
        rows_rejected=rows_rejected,
        status="SUCCESS" if inserted > 0 else "PARTIAL",
        rejection_log=rejection_log,
        dry_run=dry_run,
    )
    logger.success(f"{collection_date} — {inserted:,} linhas inseridas")
    return "completed", inserted


# ── Orquestrador principal ──────────────────────────────────────────────────

def run(
    token: str,
    start: date,
    end: date,
    dry_run: bool = False,
    force: bool = False,
    no_upload: bool = False,
    concurrent: int = _MAX_CONCURRENT,
    categories: Optional[List[str]] = None,
    gaps_only: bool = False,
) -> None:
    _DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    done_set: Set[str] = set(progress["completed"]) | set(progress["skipped"])

    # Gera lista de datas a processar. A checagem no banco é por data
    # (limit 1), evitando o bug de paginação do select global.
    dates_to_process: List[str] = []
    skipped_existing = 0
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        # gaps_only: ignora o progress.json e confia só na verdade do banco
        # (date_exists) — preenche exatamente os buracos do intervalo.
        if force and not gaps_only:
            dates_to_process.append(ds)
        elif not gaps_only and ds in done_set:
            skipped_existing += 1
        elif date_exists(ds, dry_run=dry_run):
            skipped_existing += 1
        else:
            dates_to_process.append(ds)
        cur += timedelta(days=1)

    if gaps_only:
        logger.info(
            f"[gaps-only] {len(dates_to_process)} data(s) ausente(s) em "
            f"[{start} → {end}]: {', '.join(dates_to_process) or 'nenhuma'}"
        )

    total = len(dates_to_process)
    logger.info(
        f"Datas a importar: {total} ({start} → {end}); "
        f"{skipped_existing} já no banco/progresso"
    )
    if total == 0:
        logger.success("Nada a importar — tudo já está no banco.")
        return

    # ── Fase 1: downloads em lote (até `concurrent` exports em voo) ────────
    if dry_run:
        download_statuses = {
            ds: ("dry_run" if _should_redownload(ds, _offers_dest(ds).exists())
                 else "cached")
            for ds in dates_to_process
        }
    else:
        download_statuses = prefetch_exports(token, dates_to_process, concurrent)

    # ── Fase 2: parse + agregação + upsert, data a data ────────────────────
    stats = {"completed": 0, "failed": 0, "no_data": 0, "total_rows": 0}

    for i, ds in enumerate(dates_to_process, 1):
        logger.info(f"[{i}/{total}] Processando {ds} ...")

        result, rows = _process_date(
            ds, dry_run, no_upload,
            download_status=download_statuses.get(ds, "failed"),
            categories=categories,
        )

        if result in ("completed", "downloaded", "dry_run"):
            progress["completed"].append(ds)
            stats["completed"] += 1
            stats["total_rows"] += rows
        elif result == "no_data":
            # 409 da API: data genuinamente sem coleta — não tenta de novo
            progress["skipped"].append(ds)
            stats["no_data"] += 1
        elif result == "empty":
            # 0 linhas após filtro/parse — pode ser bug ou categoria ausente;
            # NÃO marca como skipped para permitir reprocessamento automático
            progress["failed"].append(ds)
            stats["no_data"] += 1
        elif result == "failed":
            progress["failed"].append(ds)
            stats["failed"] += 1

        save_progress(progress)

    logger.success(
        f"Importação concluída — "
        f"OK: {stats['completed']}, "
        f"Sem dados: {stats['no_data']}, "
        f"Falhas: {stats['failed']}, "
        f"Linhas inseridas: {stats['total_rows']:,}"
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa histórico de preços 2026 via PriceTrack API"
    )
    parser.add_argument(
        "--start",
        default="2026-01-01",
        help="Data inicial (YYYY-MM-DD). Padrão: 2026-01-01",
    )
    parser.add_argument(
        "--end",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Data final (YYYY-MM-DD). Padrão: ontem",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula sem baixar ou inserir dados",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reimporta datas já presentes no banco",
    )
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help="Detecta e importa SOMENTE as datas ausentes no banco no intervalo "
             "(ignora o progress.json). Use para preencher buracos do histórico.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Baixa arquivos NDJSON.gz mas não insere no Supabase",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=_MAX_CONCURRENT,
        choices=[1, 2, 3],
        help="Exports concorrentes (máx 3). Padrão: 3",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Inspeciona o schema do arquivo NDJSON.gz já baixado para a data "
             "--start (não baixa nada) e sai. Use para descobrir os nomes "
             "reais dos campos.",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=DEFAULT_CATEGORIES,
        metavar="CAT",
        help=(
            "Categorias a importar (case-insensitive, separadas por espaço). "
            f"Padrão: {DEFAULT_CATEGORIES}. "
            "Ex: --categories 'AR CONDICIONADO' CLIMATIZACAO"
        ),
    )
    args = parser.parse_args()
    categories = [c.upper().strip() for c in args.categories]

    # ── Modo inspeção: dump do schema de um arquivo já baixado ────────────
    if args.inspect:
        inspect_file(_DOWNLOAD_DIR / f"offers-{args.start}.ndjson.gz")
        return

    # ── Configura logger ──────────────────────────────────────────────────
    log_path = _PROJECT_ROOT / "logs" / f"pricetrack_api_import_{date.today()}.log"
    log_path.parent.mkdir(exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(log_path, level="DEBUG", rotation="50 MB",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}")

    # ── Valida token ──────────────────────────────────────────────────────
    token = os.getenv("PRICETRACK_API_KEY", "").strip()
    if not token and not args.dry_run:
        logger.error(
            "PRICETRACK_API_KEY não configurado no .env\n"
            "Adicione: PRICETRACK_API_KEY=<seu_token>\n"
            "Use --dry-run para testar sem token."
        )
        sys.exit(1)
    token = token or "dry-run-placeholder"

    # ── Parse datas ───────────────────────────────────────────────────────
    try:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    except ValueError as e:
        logger.error(f"Formato de data inválido: {e}")
        sys.exit(1)

    if start_date > end_date:
        logger.error("--start deve ser anterior a --end")
        sys.exit(1)

    # ── Resumo antes de iniciar ───────────────────────────────────────────
    days = (end_date - start_date).days + 1
    logger.info(f"PriceTrack API Import")
    logger.info(f"  Período: {start_date} → {end_date} ({days} dias)")
    logger.info(f"  Dry-run: {args.dry_run}")
    logger.info(f"  Force:   {args.force}")
    logger.info(f"  Upload:  {not args.no_upload}")
    logger.info(f"  Arquivos: {_DOWNLOAD_DIR}")
    logger.info(f"  Seller map: {'sim' if _HAS_SELLER_MAP else 'fallback'}")
    logger.info(f"  Categorias: {categories}")
    if not _HAS_SUPABASE and not args.no_upload:
        logger.warning("supabase-py não instalado — use --no-upload ou instale: pip install supabase")

    run(
        token=token,
        start=start_date,
        end=end_date,
        dry_run=args.dry_run,
        force=args.force,
        no_upload=args.no_upload,
        concurrent=args.concurrent,
        categories=categories,
        gaps_only=args.gaps_only,
    )


if __name__ == "__main__":
    main()
