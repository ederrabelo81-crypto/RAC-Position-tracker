"""
utils/supabase_client.py — Upload de registros para Supabase.

Carrega credenciais do .env (SUPABASE_URL + SUPABASE_KEY).
Mapeia colunas do formato interno do bot para a tabela `coletas`.
Faz upsert em lotes de 500 linhas para evitar timeout na free tier.

USO:
    from utils.supabase_client import upload_to_supabase
    upload_to_supabase(all_records)   # lista de dicts do scraper

REQUISITOS:
    pip install supabase python-dotenv
    .env na raiz do projeto com:
        SUPABASE_URL=https://xxxx.supabase.co
        SUPABASE_KEY=eyJ...
"""

import os
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from utils.text import is_valid_product
from utils.normalize_product import normalize_product_name

# Carrega .env da raiz do projeto (pai de utils/)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv opcional — variáveis podem vir do ambiente

try:
    from supabase import create_client, Client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False

# Tamanho do lote para upsert — free tier Supabase suporta bem até 500 linhas
_BATCH_SIZE = 500

# Mapeamento: coluna interna do bot → coluna na tabela `coletas` do Supabase
_COLUMN_MAP = {
    "Data":                 "data",
    "Turno":                "turno",
    "Horário":              "horario",
    "Plataforma":           "plataforma",
    "Tipo Plataforma":      "tipo",
    "Keyword Buscada":      "keyword",
    "Categoria Keyword":    "categoria",
    "Marca Monitorada":     "marca",
    "Produto / SKU":        "produto",
    "Posição Orgânica":     "posicao_organica",
    "Posição Patrocinada":  "posicao_patrocinada",
    "Posição Geral":        "posicao_geral",
    "Preço (R$)":           "preco",
    "Seller / Vendedor":    "seller",
    "Fulfillment?":         "fulfillment",
    "Avaliação":            "avaliacao",
    "Qtd Avaliações":       "qtd_avaliacoes",
    "Tag Destaque":         "tag",
}

# Colunas numéricas — None em vez de NaN para o Postgres
_INT_COLS   = {"posicao_organica", "posicao_patrocinada", "posicao_geral", "qtd_avaliacoes"}
_FLOAT_COLS = {"preco", "avaliacao"}
_BOOL_COLS  = {"fulfillment"}


def _get_client() -> Optional["Client"]:
    """Cria e retorna o client Supabase, ou None se não configurado."""
    if not _HAS_SUPABASE:
        logger.error(
            "[Supabase] ❌ Pacote 'supabase' NÃO instalado. "
            "Execute no terminal: pip install supabase"
        )
        return None

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()

    if not url:
        logger.error(
            "[Supabase] ❌ SUPABASE_URL não encontrada. "
            f"Verifique o arquivo .env em: {Path(__file__).parent.parent / '.env'}"
        )
        return None
    if not key:
        logger.error(
            "[Supabase] ❌ SUPABASE_KEY não encontrada. "
            f"Verifique o arquivo .env em: {Path(__file__).parent.parent / '.env'}"
        )
        return None

    logger.info(f"[Supabase] Conectando em: {url[:40]}...")
    try:
        client = create_client(url, key)
        logger.info("[Supabase] ✓ Conexão estabelecida.")
        return client
    except Exception as exc:
        logger.error(f"[Supabase] ❌ Falha ao criar client: {exc}")
        return None


def _map_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converte um registro do formato interno do bot para o formato da tabela.
    Trata tipos: int/float/bool/None. Trunca produto a 500 chars (limite índice PG).
    """
    row: Dict[str, Any] = {}
    for src_col, dest_col in _COLUMN_MAP.items():
        val = record.get(src_col)

        # Converte NaN/pandas NA para None
        if val != val:  # NaN check (NaN != NaN em float)
            val = None
        elif hasattr(val, "item"):  # numpy scalar → Python nativo
            val = val.item()

        if val is None or val == "" or val == "nan":
            val = None
        elif dest_col in _INT_COLS:
            try:
                val = int(float(val))
            except (ValueError, TypeError):
                val = None
        elif dest_col in _FLOAT_COLS:
            try:
                val = round(float(val), 2)
            except (ValueError, TypeError):
                val = None
        elif dest_col in _BOOL_COLS:
            if isinstance(val, bool):
                pass
            elif isinstance(val, str):
                val = val.strip().lower() in ("sim", "yes", "true", "1", "s")
            else:
                val = bool(val) if val is not None else None

        # Trunca produto — índice único PG tem limite de ~2700 bytes
        if dest_col == "produto" and isinstance(val, str) and len(val) > 500:
            val = val[:500]

        row[dest_col] = val

    # Normalize product name for CSV imports (live scraping normalizes in _build_record)
    if row.get("produto"):
        normalized = normalize_product_name(row["produto"], row.get("marca"))
        if normalized:
            row["produto"] = normalized[:500]

    return row


def _is_ac_row(row: Dict[str, Any]) -> bool:
    """
    Retorna True se o registro mapeado deve ser mantido.

    Permite registros sem nome de produto (dados parciais com posição/preço).
    Rejeita apenas quando o nome do produto está presente e claramente não é AC.
    """
    produto = row.get("produto") or ""
    if not produto:
        return True  # Sem nome → não é possível validar → mantém
    preco = row.get("preco")  # None se não capturado
    return is_valid_product(produto, preco)


def delete_invalid_from_supabase(dry_run: bool = False) -> Dict[str, int]:
    """
    Varre a tabela `coletas` e remove registros que não passam no filtro AC.

    Estratégia:
      1. Busca id + produto + preco em lotes de 1.000 linhas (paginação)
      2. Aplica is_valid_product() em cada linha client-side
      3. Deleta IDs inválidos em lotes de 100

    Args:
        dry_run: Se True, apenas conta — não deleta nada.

    Returns:
        dict com chaves: scanned, invalid, deleted, errors
    """
    client = _get_client()
    if client is None:
        return {"scanned": 0, "invalid": 0, "deleted": 0, "errors": 1}

    _FETCH_BATCH = 1_000
    _DELETE_BATCH = 100

    invalid_ids: List[int] = []
    scanned = 0
    offset = 0

    logger.info("[Supabase] Iniciando varredura de registros inválidos...")

    while True:
        try:
            resp = (
                client.table("coletas")
                .select("id,produto,preco")
                .range(offset, offset + _FETCH_BATCH - 1)
                .execute()
            )
        except Exception as exc:
            logger.error(f"[Supabase] Erro ao buscar registros (offset={offset}): {exc}")
            break

        batch = resp.data or []
        if not batch:
            break

        for row in batch:
            scanned += 1
            produto = row.get("produto") or ""
            preco   = row.get("preco")
            if produto and not is_valid_product(produto, preco):
                invalid_ids.append(row["id"])

        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH

    logger.info(
        f"[Supabase] Varredura concluída: {scanned} registros analisados, "
        f"{len(invalid_ids)} inválidos encontrados."
    )

    if dry_run or not invalid_ids:
        return {"scanned": scanned, "invalid": len(invalid_ids), "deleted": 0, "errors": 0}

    # Deleta em lotes
    deleted = 0
    errors  = 0
    total_batches = math.ceil(len(invalid_ids) / _DELETE_BATCH)

    for i in range(total_batches):
        batch_ids = invalid_ids[i * _DELETE_BATCH : (i + 1) * _DELETE_BATCH]
        try:
            client.table("coletas").delete().in_("id", batch_ids).execute()
            deleted += len(batch_ids)
            logger.debug(f"[Supabase] Delete lote {i+1}/{total_batches}: {len(batch_ids)} IDs")
        except Exception as exc:
            errors += len(batch_ids)
            logger.warning(f"[Supabase] Erro ao deletar lote {i+1}: {exc}")

    logger.info(f"[Supabase] Limpeza concluída: {deleted} deletados, {errors} com erro.")
    return {"scanned": scanned, "invalid": len(invalid_ids), "deleted": deleted, "errors": errors}


def upload_to_supabase(records: List[Dict[str, Any]]) -> bool:
    """
    Faz upload de uma lista de registros para a tabela `coletas` no Supabase.

    Usa upsert com ignore_duplicates=True para evitar erros em reexecuções.
    A deduplicação usa a constraint UNIQUE (data, turno, plataforma, produto).

    Args:
        records: lista de dicts no formato interno do bot (mesmo formato do CSV)

    Returns:
        True se upload bem-sucedido, False se falhou (CSV já foi salvo antes).
    """
    if not records:
        logger.info("[Supabase] Nenhum registro para enviar.")
        return True

    logger.info(f"[Supabase] Iniciando upload de {len(records)} registros...")
    client = _get_client()
    if client is None:
        return False

    rows = [_map_record(r) for r in records]

    # Remove linhas sem plataforma (requerida pela constraint NOT NULL)
    rows = [r for r in rows if r.get("plataforma")]

    # Filtra produtos não relacionados a ar-condicionado
    before_filter = len(rows)
    rows = [r for r in rows if _is_ac_row(r)]
    filtered_out = before_filter - len(rows)
    if filtered_out > 0:
        logger.info(
            f"[Supabase] Filtro AC: {filtered_out} registro(s) removido(s) "
            f"(não relacionados a ar-condicionado)."
        )

    total   = len(rows)
    batches = math.ceil(total / _BATCH_SIZE)
    sent    = 0
    errors  = 0

    logger.info(f"[Supabase] Enviando {total} registros em {batches} lote(s)...")

    for i in range(batches):
        batch = rows[i * _BATCH_SIZE : (i + 1) * _BATCH_SIZE]
        try:
            client.table("coletas").upsert(
                batch,
                on_conflict="data,turno,plataforma,produto",
                ignore_duplicates=True,
            ).execute()
            sent += len(batch)
            logger.debug(f"[Supabase] Lote {i+1}/{batches}: {len(batch)} linhas OK")
        except Exception as exc:
            errors += len(batch)
            logger.warning(f"[Supabase] Lote {i+1}/{batches} falhou: {exc}")

    if errors == 0:
        logger.success(f"[Supabase] {sent} registros enviados com sucesso.")
        return True
    elif sent > 0:
        logger.warning(f"[Supabase] Upload parcial: {sent} OK, {errors} com erro.")
        return False
    else:
        logger.error(f"[Supabase] Upload falhou para todos os {total} registros.")
        return False
