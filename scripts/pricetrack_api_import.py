#!/usr/bin/env python3
"""
pricetrack_api_import.py — Importação histórica 2026 via PriceTrack API.

Usa o endpoint bulk export (NDJSON.gz) para baixar ofertas dia a dia.
Gerencia até 3 exports concorrentes, agrega preços (min/avg/mode/max)
por (data, brand, sku, marketplace, seller) e persiste em `pricetrack_daily`.

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
import time
import uuid
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
import requests
from loguru import logger

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
_BASE_URL = "https://api.pricetrack.com.br"
_MAX_CONCURRENT = 3
_POLL_INTERVAL = 30       # segundos entre polls de status
_POLL_TIMEOUT = 7200      # 2 horas por export
_DOWNLOAD_DIR = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "raw"
_PROGRESS_FILE = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "progress.json"
_BATCH_SIZE = 500
_TABLE = "pricetrack_daily"
_LOG_TABLE = "pricetrack_import_log"


# ── Helpers de API ──────────────────────────────────────────────────────────

def _headers(token: str) -> Dict[str, str]:
    return {"token": token, "Content-Type": "application/json"}


def create_export(token: str, collection_date: str) -> Dict:
    """POST /exports-external/collects-offers → retorna {exportId, status, statusUrl}."""
    resp = requests.post(
        f"{_BASE_URL}/exports-external/collects-offers",
        headers=_headers(token),
        json={"collectionDate": collection_date},
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("Limite de 3 exports concorrentes atingido")
    resp.raise_for_status()
    return resp.json()


def get_export_status(token: str, export_id: str) -> Dict:
    """GET /exports-external/{exportId} → retorna status atual."""
    resp = requests.get(
        f"{_BASE_URL}/exports-external/{export_id}",
        headers=_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def download_file(url: str, dest: Path) -> None:
    """Baixa arquivo de URL pré-assinada (sem autenticação)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)


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
_PRICE_FIELDS = (
    "spot_price", "spotprice",
    "pix_price", "pixprice",
    "price", "forward_price", "forwardprice",
    "sale_price", "saleprice",
    "preco", "preco_avista", "valor",
)
_BRAND_FIELDS = ("brand", "productbrand", "product_brand", "marca")
_SKU_FIELDS = ("sku", "productsku", "product_sku", "sku_code", "codigo", "cod")
_TITLE_FIELDS = ("product_name", "productname", "title", "name", "produto", "titulo")
_MARKETPLACE_FIELDS = ("marketplace", "market", "loja_marketplace")
_SELLER_FIELDS = ("seller", "vendedor", "store", "loja")
_CATEGORY_FIELDS = ("category", "categoria", "product_category", "productcategory")

# Categorias de ar condicionado aceitas (uppercase, comparação exata).
# Configurável via --categories na CLI.
DEFAULT_CATEGORIES: List[str] = ["AR CONDICIONADO"]


def _mode(series: pd.Series) -> float:
    m = series.dropna().mode()
    if len(m) > 0:
        return float(m.iloc[0])
    v = series.dropna()
    return float(v.mean()) if len(v) > 0 else 0.0


def _pick_text(df: pd.DataFrame, lookup: Dict[str, str],
               candidates: Tuple[str, ...], default: str = "") -> pd.Series:
    """Devolve a primeira coluna textual encontrada (case-insensitive)."""
    for cand in candidates:
        if cand in lookup:
            return df[lookup[cand]].fillna(default).astype(str).str.strip()
    return pd.Series([default] * len(df), index=df.index, dtype="object")


def _pick_price(df: pd.DataFrame, lookup: Dict[str, str]) -> pd.Series:
    """Combina os candidatos de preço, preenchendo nulos com o próximo campo."""
    price = pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    for cand in _PRICE_FIELDS:
        if cand in lookup:
            col = pd.to_numeric(df[lookup[cand]], errors="coerce")
            price = price.fillna(col)
    return price


def aggregate_offers(
    df: pd.DataFrame,
    collection_date: str,
    categories: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Filtra por categoria e agrega ofertas para o formato daily.

    Grupo: (collection_date, brand, sku, title, marketplace, seller).
    Resolve nomes de campo de forma case-insensitive para lidar com
    snake_case do NDJSON vs camelCase do schema OpenAPI.

    Filtros aplicados em ordem:
      1. Categoria (campo `category`) — default: AR CONDICIONADO
      2. Preço válido (> 0)

    brand/sku/title vazios são mantidos — melhor importar com identificador
    parcial do que perder a oferta.
    """
    if df.empty:
        return pd.DataFrame()

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
        return pd.DataFrame()

    # Reconstrói lookup após filtro
    lookup = {c.lower(): c for c in df.columns}

    # ── 2. Preço ─────────────────────────────────────────────────────────
    price = _pick_price(df, lookup)
    n_with_price = int(price.notna().sum())

    work = pd.DataFrame({
        "_price": price,
        "brand": _pick_text(df, lookup, _BRAND_FIELDS).str.upper(),
        "sku": _pick_text(df, lookup, _SKU_FIELDS),
        "title": _pick_text(df, lookup, _TITLE_FIELDS),
        "marketplace": _pick_text(df, lookup, _MARKETPLACE_FIELDS),
        "seller": _pick_text(df, lookup, _SELLER_FIELDS),
    })

    before = len(work)
    work = work.dropna(subset=["_price"])
    work = work[work["_price"] > 0]
    dropped_no_price = before - len(work)

    if work.empty:
        logger.warning(
            f"{collection_date} — 0 linhas válidas após filtro de preço.\n"
            f"  Preço presente em {n_with_price:,}/{before:,} ofertas de AC.\n"
            f"  Colunas no arquivo ({len(df.columns)}): {list(df.columns)}"
        )
        sample = df.iloc[0].to_dict()
        logger.warning(
            f"{collection_date} — registro de exemplo:\n"
            f"{json.dumps(sample, ensure_ascii=False, default=str)[:2000]}"
        )
        return pd.DataFrame()

    logger.debug(
        f"{collection_date} — preço presente em {n_with_price:,}/{before:,} ofertas AC; "
        f"{dropped_no_price:,} descartadas sem preço"
    )

    agg = (
        work.groupby(["brand", "sku", "title", "marketplace", "seller"])["_price"]
        .agg(
            min_price="min",
            avg_price="mean",
            max_price="max",
            mode_price=_mode,
        )
        .reset_index()
    )

    agg["collection_date"] = collection_date
    agg["seller_canonical"] = agg["seller"].apply(normalize_seller)
    agg["source_file"] = f"api-{collection_date}"

    return agg[[
        "collection_date", "brand", "sku", "title",
        "marketplace", "seller", "seller_canonical",
        "min_price", "avg_price", "mode_price", "max_price",
        "source_file",
    ]]


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
            client.table(_TABLE).insert(batch).execute()
            inserted += len(batch)
        except Exception as e:
            logger.error(f"Erro ao inserir lote {i//500 + 1}: {e}")
    return inserted


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


# ── Export Manager ──────────────────────────────────────────────────────────

class ExportJob:
    """Representa um job de export em andamento."""
    __slots__ = ("export_id", "collection_date", "created_at", "started_at")

    def __init__(self, export_id: str, collection_date: str):
        self.export_id = export_id
        self.collection_date = collection_date
        self.created_at = time.time()
        self.started_at: Optional[float] = None


def _process_date(
    token: str,
    collection_date: str,
    dry_run: bool,
    no_upload: bool,
    progress: Dict,
    categories: Optional[List[str]] = None,
) -> Tuple[str, int]:
    """
    Executa o ciclo completo para uma data:
      1. Cria export
      2. Polling até DONE
      3. Baixa NDJSON.gz
      4. Agrega e insere

    Retorna (status, rows_inserted).
    """
    dest_path = _DOWNLOAD_DIR / f"offers-{collection_date}.ndjson.gz"

    # Se arquivo já foi baixado, pula o download
    if not dest_path.exists():
        if dry_run:
            logger.info(f"[DRY-RUN] {collection_date} — criaria export e baixaria arquivo")
            return "dry_run", 0

        # ── Cria export ──────────────────────────────────────────────────
        try:
            resp = create_export(token, collection_date)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                logger.warning(f"{collection_date} — sem dados na API (409)")
                return "no_data", 0
            logger.error(f"{collection_date} — erro ao criar export: {e}")
            return "failed", 0

        export_id = resp["exportId"]
        logger.info(f"{collection_date} — export criado: {export_id[:8]}...")

        # ── Polling ──────────────────────────────────────────────────────
        deadline = time.time() + _POLL_TIMEOUT
        download_url: Optional[str] = None

        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            try:
                status_resp = get_export_status(token, export_id)
            except Exception as e:
                logger.warning(f"{collection_date} — poll falhou: {e}, aguardando...")
                continue

            status = status_resp.get("status", "")
            progress_pct = status_resp.get("progress", 0)
            logger.debug(f"{collection_date} — status: {status} ({progress_pct}%)")

            if status == "DONE":
                download_url = status_resp.get("downloadUrl")
                break
            elif status == "FAILED":
                logger.error(f"{collection_date} — export FAILED")
                return "failed", 0

        if not download_url:
            logger.error(f"{collection_date} — timeout aguardando export")
            return "failed", 0

        # ── Download ─────────────────────────────────────────────────────
        logger.info(f"{collection_date} — baixando arquivo...")
        try:
            download_file(download_url, dest_path)
            size_kb = dest_path.stat().st_size // 1024
            logger.success(f"{collection_date} — download OK ({size_kb} KB)")
        except Exception as e:
            logger.error(f"{collection_date} — falha no download: {e}")
            if dest_path.exists():
                dest_path.unlink()
            return "failed", 0

    else:
        logger.info(f"{collection_date} — arquivo já existe, pulando download")

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

        df_agg = aggregate_offers(df_raw, collection_date, categories=categories)
        if df_agg.empty:
            logger.warning(f"{collection_date} — zero linhas após filtro+agregação")
            log_import(f"api-{collection_date}", rows_raw, 0, rows_raw, "PARTIAL")
            return "empty", 0

        rows_agg = len(df_agg)
        rows_rejected = rows_raw - rows_agg
        logger.info(f"{collection_date} — {rows_agg:,} linhas AC agregadas "
                    f"({rows_rejected:,} descartadas: outras categorias ou sem preço)")

    except Exception as e:
        logger.error(f"{collection_date} — erro no parse: {e}")
        return "failed", 0

    # ── Inserção no Supabase ──────────────────────────────────────────────
    records = df_agg.where(pd.notnull(df_agg), None).to_dict("records")

    # Converte tipos numéricos para float nativo (JSON serializable)
    for r in records:
        for k in ("min_price", "avg_price", "mode_price", "max_price"):
            if r[k] is not None:
                r[k] = round(float(r[k]), 2)

    inserted = insert_rows(records, dry_run=dry_run)
    log_import(
        source_file=f"api-{collection_date}",
        rows_total=rows_raw,
        rows_inserted=inserted,
        rows_rejected=rows_rejected,
        status="SUCCESS" if inserted > 0 else "PARTIAL",
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
        if force:
            dates_to_process.append(ds)
        elif ds in done_set:
            skipped_existing += 1
        elif date_exists(ds, dry_run=dry_run):
            skipped_existing += 1
        else:
            dates_to_process.append(ds)
        cur += timedelta(days=1)

    total = len(dates_to_process)
    logger.info(
        f"Datas a importar: {total} ({start} → {end}); "
        f"{skipped_existing} já no banco/progresso"
    )
    if total == 0:
        logger.success("Nada a importar — tudo já está no banco.")
        return

    stats = {"completed": 0, "failed": 0, "no_data": 0, "total_rows": 0}

    for i, ds in enumerate(dates_to_process, 1):
        logger.info(f"[{i}/{total}] Processando {ds} ...")

        result, rows = _process_date(
            token, ds, dry_run, no_upload, progress, categories=categories
        )

        if result in ("completed", "downloaded", "dry_run"):
            progress["completed"].append(ds)
            stats["completed"] += 1
            stats["total_rows"] += rows
        elif result in ("no_data", "empty"):
            progress["skipped"].append(ds)
            stats["no_data"] += 1
        elif result == "failed":
            progress["failed"].append(ds)
            stats["failed"] += 1

        save_progress(progress)

        # Pequena pausa entre requisições para não sobrecarregar a API
        if i < total and not dry_run:
            time.sleep(5)

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
    )


if __name__ == "__main__":
    main()
