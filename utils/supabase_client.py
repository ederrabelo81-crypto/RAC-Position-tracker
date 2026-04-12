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
from typing import Any, Dict, List, Optional

from loguru import logger

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
        logger.warning(
            "[Supabase] Pacote 'supabase' não instalado. "
            "Execute: pip install supabase python-dotenv"
        )
        return None

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()

    if not url or not key:
        logger.warning(
            "[Supabase] Credenciais não encontradas. "
            "Verifique SUPABASE_URL e SUPABASE_KEY no arquivo .env"
        )
        return None

    try:
        return create_client(url, key)
    except Exception as exc:
        logger.error(f"[Supabase] Falha ao criar client: {exc}")
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

    return row


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
        logger.debug("[Supabase] Nenhum registro para enviar.")
        return True

    client = _get_client()
    if client is None:
        return False

    rows = [_map_record(r) for r in records]
    # Remove linhas sem plataforma (requerida pela constraint NOT NULL)
    rows = [r for r in rows if r.get("plataforma")]

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
