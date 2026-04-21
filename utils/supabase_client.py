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
import re
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


def normalize_brands_in_supabase(dry_run: bool = False) -> Dict[str, Any]:
    """
    Consolida variantes de marca no campo `marca` da tabela `coletas`.

    Mapeamento aplicado (mesmo que _BRAND_ALIASES em normalize_product.py):
      "Springer Midea"  → "Midea"
      "Midea Carrier"   → "Midea"
      "Springer"        → "Midea"
      "Britania"        → "Britânia"

    Para cada variante faz um UPDATE direto (.eq("marca", source)) — sem risco
    de conflito de UNIQUE pois `marca` não faz parte da constraint da tabela.

    Args:
        dry_run: Se True, apenas conta registros afetados — não atualiza.

    Returns:
        dict com: total_updated, errors, by_brand
          by_brand = {source: {"count": N, "target": canonical}}
    """
    client = _get_client()
    if client is None:
        return {"total_updated": 0, "errors": 0, "by_brand": {}}

    # Variants to consolidate — same mapping as normalize_product._BRAND_ALIASES
    _BRAND_MAP: Dict[str, str] = {
        "Springer Midea": "Midea",
        "Midea Carrier":  "Midea",
        "Springer":       "Midea",
        "Britania":       "Britânia",
    }

    by_brand: Dict[str, Any] = {}
    total_updated = 0
    errors = 0

    for source, target in _BRAND_MAP.items():
        # Count how many records have this variant
        try:
            count_resp = (
                client.table("coletas")
                .select("id", count="exact")
                .eq("marca", source)
                .execute()
            )
            count = count_resp.count or 0
        except Exception as exc:
            logger.warning(f"[Supabase] Erro ao contar marca={source!r}: {exc}")
            count = -1

        by_brand[source] = {"count": count, "target": target}

        if dry_run or count <= 0:
            continue

        # Update in-place — safe because marca is not in the UNIQUE constraint
        try:
            client.table("coletas").update({"marca": target}).eq("marca", source).execute()
            total_updated += count
            logger.info(
                f"[Supabase] Marca normalizada: {source!r} → {target!r} ({count} registros)"
            )
        except Exception as exc:
            errors += 1
            logger.warning(f"[Supabase] Erro ao normalizar marca {source!r}: {exc}")

    if not dry_run:
        logger.info(
            f"[Supabase] Normalização de marcas concluída: "
            f"{total_updated} registros atualizados, {errors} erros."
        )
    return {"total_updated": total_updated, "errors": errors, "by_brand": by_brand}


def normalize_platforms_sellers_in_supabase(dry_run: bool = False) -> Dict[str, Any]:
    """
    Corrige variantes de plataforma e seller nos campos `plataforma` e `seller`
    da tabela `coletas`.

    Mapeamento aplicado:
      "FerreiraCoasta"  → "FerreiraCosta"   (typo no nome do dealer)
      "Webcontinental"  → "WebContinental"  (capitalização incorreta)

    Aplica em ambas as colunas `plataforma` e `seller`.

    Args:
        dry_run: Se True, apenas conta registros afetados — não atualiza.

    Returns:
        dict com: total_updated, errors, by_mapping
    """
    client = _get_client()
    if client is None:
        return {"total_updated": 0, "errors": 0, "by_mapping": {}}

    _PLATFORM_MAP: Dict[str, str] = {
        "FerreiraCoasta": "FerreiraCosta",
        "Webcontinental": "WebContinental",
    }
    _COLUMNS = ["plataforma", "seller"]

    by_mapping: Dict[str, Any] = {}
    total_updated = 0
    errors = 0

    for source, target in _PLATFORM_MAP.items():
        entry: Dict[str, Any] = {"target": target}

        for col in _COLUMNS:
            try:
                count_resp = (
                    client.table("coletas")
                    .select("id", count="exact")
                    .eq(col, source)
                    .execute()
                )
                count = count_resp.count or 0
            except Exception as exc:
                logger.warning(f"[Supabase] Erro ao contar {col}={source!r}: {exc}")
                count = -1

            entry[col] = count

            if dry_run or count <= 0:
                continue

            try:
                client.table("coletas").update({col: target}).eq(col, source).execute()
                total_updated += count
                logger.info(
                    f"[Supabase] {col} normalizado: {source!r} → {target!r} ({count} registros)"
                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    f"[Supabase] Erro ao normalizar {col} {source!r}: {exc}"
                )

        by_mapping[source] = entry

    if not dry_run:
        logger.info(
            f"[Supabase] Normalização de plataformas/sellers concluída: "
            f"{total_updated} registros atualizados, {errors} erros."
        )
    return {"total_updated": total_updated, "errors": errors, "by_mapping": by_mapping}


def normalize_all_products_in_supabase(
    dry_run: bool = False,
    preview_limit: int = 20,
) -> Dict[str, Any]:
    """
    Varrer a tabela `coletas` e re-normaliza o campo `produto` usando
    normalize_product_name(), atualizando apenas as linhas que mudaram.

    Estratégia em 2 fases:
      Fase 1 — Identificação (lotes de 1.000 linhas):
        Busca id + produto + marca, aplica normalize_product_name() e
        registra quais IDs precisam atualizar e qual o novo nome.

      Fase 2 — Atualização (lotes de 200 linhas):
        Para cada lote de IDs alterados, busca TODAS as colunas da linha
        (necessário porque upsert faz INSERT…ON CONFLICT, e colunas NOT NULL
        não podem estar ausentes), substitui produto e faz upsert por id.

    Args:
        dry_run:       Se True, conta e exibe preview — não grava nada.
        preview_limit: Máximo de exemplos de mudança retornados no dry-run.

    Returns:
        dict com chaves:
          scanned, changed, unchanged, updated, errors, preview
          (preview = lista de dicts {id, before, after} para dry-run)
    """
    client = _get_client()
    if client is None:
        return {"scanned": 0, "changed": 0, "unchanged": 0,
                "updated": 0, "errors": 1, "preview": []}

    _FETCH_BATCH  = 1_000   # linhas por página na varredura
    _UPDATE_BATCH = 200     # IDs por lote na fase de atualização

    # {id: novo_produto} — mapa de todas as linhas que precisam atualizar
    changes: Dict[int, str] = {}
    preview: List[Dict[str, Any]] = []
    scanned   = 0
    unchanged = 0
    offset    = 0

    logger.info("[Supabase] Fase 1 — identificando produtos a normalizar…")

    # ── Fase 1: identificar mudanças ──
    while True:
        try:
            resp = (
                client.table("coletas")
                .select("id,produto,marca")
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
            raw   = row.get("produto") or ""
            marca = row.get("marca")  or None
            if not raw:
                unchanged += 1
                continue

            normalized = normalize_product_name(raw, marca)
            if normalized and normalized != raw:
                changes[row["id"]] = normalized[:500]
                if len(preview) < preview_limit:
                    preview.append({"id": row["id"], "before": raw, "after": normalized[:500]})
            else:
                unchanged += 1

        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH

    logger.info(
        f"[Supabase] Fase 1 concluída: {scanned} registros, "
        f"{len(changes)} precisam atualizar, {unchanged} já normalizados."
    )

    if dry_run or not changes:
        return {
            "scanned":   scanned,
            "changed":   len(changes),
            "unchanged": unchanged,
            "updated":   0,
            "deduped":   0,
            "errors":    0,
            "preview":   preview,
        }

    # ── Fase 2: atualizar ou desduplicar ──
    #
    # Cenário A (sem conflito): o nome normalizado ainda não existe para esse
    #   (data, turno, plataforma) → upsert com linha completa renomeia o produto.
    #
    # Cenário B (conflito 23505): já existe outra linha com o nome normalizado
    #   para o mesmo (data, turno, plataforma) — o bot gravou versões normalizadas
    #   em runs posteriores. A linha antiga é uma duplicata → DELETE.
    #
    # Fluxo por lote:
    #   1. Tenta upsert em lote (rápido para Cenário A)
    #   2. Se o lote falha com 23505: tenta cada linha individualmente via PATCH
    #      (.update().eq("id", …)) — PATCH gera UPDATE SQL direto, sem INSERT
    #   3. Linhas que ainda falham com 23505: DELETE em lote (duplicatas confirmadas)
    changed_ids   = list(changes.keys())
    updated = 0
    deduped = 0   # linhas deletadas por já existir versão normalizada
    errors  = 0
    total_batches = math.ceil(len(changed_ids) / _UPDATE_BATCH)

    logger.info(
        f"[Supabase] Fase 2 — {len(changed_ids)} registros em "
        f"{total_batches} lote(s) (atualização ou deduplicação)…"
    )

    for i in range(total_batches):
        batch_ids = changed_ids[i * _UPDATE_BATCH : (i + 1) * _UPDATE_BATCH]

        # 2a — buscar linha completa para os IDs do lote
        try:
            full_resp = (
                client.table("coletas")
                .select("*")
                .in_("id", batch_ids)
                .execute()
            )
            full_rows = full_resp.data or []
        except Exception as exc:
            errors += len(batch_ids)
            logger.warning(f"[Supabase] Erro ao buscar lote {i+1}/{total_batches}: {exc}")
            continue

        if not full_rows:
            continue

        # 2b — aplicar nome normalizado
        for row in full_rows:
            row["produto"] = changes[row["id"]]

        # 2c — caminho rápido: upsert em lote (funciona quando não há duplicatas)
        try:
            client.table("coletas").upsert(full_rows, on_conflict="id").execute()
            updated += len(full_rows)
            logger.debug(f"[Supabase] Lote {i+1}/{total_batches}: {len(full_rows)} OK (batch)")
            continue
        except Exception as batch_exc:
            if "23505" not in str(batch_exc):
                # Erro inesperado — não é duplicata, registra e segue
                errors += len(batch_ids)
                logger.warning(f"[Supabase] Lote {i+1}/{total_batches} falhou: {batch_exc}")
                continue
            # Pelo menos uma linha conflita — tratar individualmente

        # 2d — caminho lento: PATCH individual (gera UPDATE SQL, sem INSERT)
        to_delete: List[int] = []
        for row in full_rows:
            try:
                client.table("coletas").update(
                    {"produto": row["produto"]}
                ).eq("id", row["id"]).execute()
                updated += 1
            except Exception as row_exc:
                if "23505" in str(row_exc):
                    # Nome normalizado já existe → linha atual é duplicata
                    to_delete.append(row["id"])
                else:
                    errors += 1
                    logger.warning(
                        f"[Supabase] Erro ao atualizar id={row['id']}: {row_exc}"
                    )

        # 2e — deletar duplicatas em lote
        if to_delete:
            try:
                client.table("coletas").delete().in_("id", to_delete).execute()
                deduped += len(to_delete)
            except Exception as del_exc:
                errors += len(to_delete)
                logger.warning(
                    f"[Supabase] Erro ao deletar duplicatas lote {i+1}: {del_exc}"
                )

        logger.debug(
            f"[Supabase] Lote {i+1}/{total_batches}: "
            f"{len(full_rows) - len(to_delete)} atualizados, "
            f"{len(to_delete)} duplicatas removidas"
        )

    logger.info(
        f"[Supabase] Normalização concluída: {updated} renomeados, "
        f"{deduped} duplicatas removidas, {errors} com erro."
    )
    return {
        "scanned":   scanned,
        "changed":   len(changes),
        "unchanged": unchanged,
        "updated":   updated,
        "deduped":   deduped,
        "errors":    errors,
        "preview":   preview,
    }


# ---------------------------------------------------------------------------
# Price validation — detect ×10 parser errors stored in DB
# ---------------------------------------------------------------------------

# Reasonable price ceilings per BTU capacity (R$) for residential/light-commercial ACs.
# Prices above these are almost certainly ×10 parse errors (e.g., R$ 1.899 stored as 18990).
_BTU_PRICE_CEILINGS: Dict[int, float] = {
    7000:  4_500,
    9000:  5_500,
    12000: 7_000,
    18000: 12_000,
    22000: 15_000,
    24000: 16_000,
    30000: 22_000,
    36000: 28_000,
    48000: 40_000,
    60000: 55_000,
}

# Matches BTU values like "9.000 BTU", "12000 BTUs", "9,000 BTU"
_BTU_RE = re.compile(
    r'(\d{1,2})[.,](\d{3})\s*BTU|(\d{4,6})\s*BTU',
    re.IGNORECASE,
)


def _extract_btu(produto: str) -> Optional[int]:
    """Extract BTU capacity (int) from a product name, or None if not found."""
    m = _BTU_RE.search(produto or "")
    if not m:
        return None
    if m.group(3):          # raw: "9000 BTU"
        return int(m.group(3))
    return int(m.group(1)) * 1000 + int(m.group(2))   # dotted: "9.000 BTU"


def _is_price_suspicious(produto: str, preco: float) -> bool:
    """
    Returns True if preco exceeds the reasonable ceiling for the BTU detected in produto.
    When no BTU is detected, uses the global R$ 80,000 ceiling from is_valid_product().
    """
    if not preco or preco <= 0:
        return False
    btu = _extract_btu(produto)
    if btu is None:
        return preco > 80_000
    closest_btu = min(_BTU_PRICE_CEILINGS, key=lambda k: abs(k - btu))
    return preco > _BTU_PRICE_CEILINGS[closest_btu]


def scan_fix_bad_prices_in_supabase(dry_run: bool = False) -> Dict[str, Any]:
    """
    Varre a tabela `coletas` e identifica/remove registros com preço suspeito.

    Um preço é suspeito quando excede o teto razoável para a capacidade BTU
    detectada no nome do produto. Exemplo: 9.000 BTUs com preço R$ 18.990
    (deveria ser ~R$ 1.899) — indício do bug de parser ×10.

    Args:
        dry_run: Se True, apenas conta e retorna exemplos — não deleta.

    Returns:
        dict com chaves: scanned, suspicious, deleted, errors, examples
    """
    client = _get_client()
    if client is None:
        return {"scanned": 0, "suspicious": 0, "deleted": 0, "errors": 0, "examples": []}

    _FETCH_BATCH  = 1_000
    _DELETE_BATCH = 100

    suspicious_ids: List[int] = []
    examples: List[Dict[str, Any]] = []
    scanned = 0
    offset  = 0

    logger.info("[Supabase] Iniciando varredura de preços suspeitos...")

    while True:
        try:
            resp = (
                client.table("coletas")
                .select("id,produto,preco,plataforma")
                .not_.is_("preco", "null")
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
            try:
                preco_f = float(preco) if preco is not None else None
            except (ValueError, TypeError):
                preco_f = None

            if preco_f and _is_price_suspicious(produto, preco_f):
                suspicious_ids.append(row["id"])
                if len(examples) < 30:
                    btu = _extract_btu(produto)
                    examples.append({
                        "id":            row["id"],
                        "produto":       produto[:80],
                        "preco":         preco_f,
                        "plataforma":    row.get("plataforma", ""),
                        "btu_detectado": btu,
                        "teto_btu":      _BTU_PRICE_CEILINGS.get(
                            min(_BTU_PRICE_CEILINGS, key=lambda k: abs(k - btu)) if btu else 0,
                            80_000,
                        ),
                    })

        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH

    logger.info(
        f"[Supabase] Varredura de preços: {scanned} registros, "
        f"{len(suspicious_ids)} com preço suspeito."
    )

    if dry_run or not suspicious_ids:
        return {
            "scanned":    scanned,
            "suspicious": len(suspicious_ids),
            "deleted":    0,
            "errors":     0,
            "examples":   examples,
        }

    # Deleta em lotes
    deleted = 0
    errors  = 0
    total_batches = math.ceil(len(suspicious_ids) / _DELETE_BATCH)

    for i in range(total_batches):
        batch_ids = suspicious_ids[i * _DELETE_BATCH : (i + 1) * _DELETE_BATCH]
        try:
            client.table("coletas").delete().in_("id", batch_ids).execute()
            deleted += len(batch_ids)
            logger.debug(f"[Supabase] Delete preços lote {i+1}/{total_batches}: {len(batch_ids)} IDs")
        except Exception as exc:
            errors += len(batch_ids)
            logger.warning(f"[Supabase] Erro ao deletar lote de preços {i+1}: {exc}")

    logger.info(f"[Supabase] Limpeza de preços: {deleted} deletados, {errors} com erro.")
    return {
        "scanned":    scanned,
        "suspicious": len(suspicious_ids),
        "deleted":    deleted,
        "errors":     errors,
        "examples":   examples,
    }


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


def fix_inverted_turno_in_supabase(dry_run: bool = True) -> Dict[str, Any]:
    """
    Remove registros com turno invertido gravados antes do fix de timezone (TZ=UTC).

    Antes do fix, o GitHub Actions usava UTC:
      - Coleta manhã (13:00 UTC) → turno="Fechamento" ❌  (horario entre 12:30-14:59)
      - Coleta noite  (00:00 UTC) → turno="Abertura"  ❌  (horario entre 00:00-01:30)

    Esses registros coexistem com os novos corretos (mesma data/plataforma/produto,
    turno diferente → constraint UNIQUE não deduplica), causando duplicatas no dashboard.

    Estratégia: identificar pelos horarios fora do contexto e deletar.
      - turno="Fechamento" AND horario BETWEEN '12:30' AND '14:59' → eram coletas manhã UTC
      - turno="Abertura"  AND horario BETWEEN '00:00' AND '01:30' → eram coletas noite UTC

    Args:
        dry_run: Se True (padrão), apenas conta — não deleta. Passe False para deletar.

    Returns:
        dict com: dry_run, fechamento_wrong, abertura_wrong, deleted, errors
    """
    client = _get_client()
    if client is None:
        return {"dry_run": dry_run, "fechamento_wrong": 0, "abertura_wrong": 0,
                "deleted": 0, "errors": 1}

    results: Dict[str, Any] = {"dry_run": dry_run, "deleted": 0, "errors": 0}

    # Caso 1: turno="Fechamento" mas horario indica coleta de manhã (UTC 13h)
    try:
        r = (client.table("coletas")
             .select("id", count="exact")
             .eq("turno", "Fechamento")
             .gte("horario", "12:30")
             .lte("horario", "14:59")
             .execute())
        results["fechamento_wrong"] = r.count or 0
        logger.info(
            f"[Supabase] Turno invertido — Fechamento com horario 12:30-14:59: "
            f"{results['fechamento_wrong']} registros"
        )
    except Exception as exc:
        logger.warning(f"[Supabase] Erro ao contar Fechamento errados: {exc}")
        results["fechamento_wrong"] = -1

    # Caso 2: turno="Abertura" mas horario indica coleta noturna (UTC 00h)
    try:
        r = (client.table("coletas")
             .select("id", count="exact")
             .eq("turno", "Abertura")
             .gte("horario", "00:00")
             .lte("horario", "01:30")
             .execute())
        results["abertura_wrong"] = r.count or 0
        logger.info(
            f"[Supabase] Turno invertido — Abertura com horario 00:00-01:30: "
            f"{results['abertura_wrong']} registros"
        )
    except Exception as exc:
        logger.warning(f"[Supabase] Erro ao contar Abertura erradas: {exc}")
        results["abertura_wrong"] = -1

    if dry_run:
        logger.info(
            f"[Supabase] DRY-RUN — nenhum registro deletado. "
            f"Execute com dry_run=False para deletar."
        )
        return results

    # Deleta turno="Fechamento" com horario de manhã
    try:
        client.table("coletas").delete() \
            .eq("turno", "Fechamento") \
            .gte("horario", "12:30") \
            .lte("horario", "14:59") \
            .execute()
        results["deleted"] += results.get("fechamento_wrong", 0)
        logger.info(
            f"[Supabase] Deletados {results.get('fechamento_wrong', 0)} registros "
            "Fechamento com horario de manhã."
        )
    except Exception as exc:
        results["errors"] += 1
        logger.error(f"[Supabase] Erro ao deletar Fechamento errados: {exc}")

    # Deleta turno="Abertura" com horario de madrugada
    try:
        client.table("coletas").delete() \
            .eq("turno", "Abertura") \
            .gte("horario", "00:00") \
            .lte("horario", "01:30") \
            .execute()
        results["deleted"] += results.get("abertura_wrong", 0)
        logger.info(
            f"[Supabase] Deletados {results.get('abertura_wrong', 0)} registros "
            "Abertura com horario de madrugada."
        )
    except Exception as exc:
        results["errors"] += 1
        logger.error(f"[Supabase] Erro ao deletar Abertura erradas: {exc}")

    logger.success(
        f"[Supabase] Limpeza de turno invertido concluída: "
        f"{results['deleted']} registros removidos, {results['errors']} erros."
    )
    return results
