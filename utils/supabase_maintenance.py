"""
utils/supabase_maintenance.py — Limpeza, normalização e auditoria do Supabase.

Extraído de utils/supabase_client.py (que ficou focado em upload). Estas são
operações de manutenção da tabela `coletas`, acionadas pela página Data Cleanup
do dashboard e por scripts (cleanup_supabase.py, normalize_supabase.py,
scripts/fix_turno.py). Todas aceitam dry_run para pré-visualizar antes de aplicar.

Os helpers de baixo nível (cliente, parsing de BTU, detecção de preço suspeito)
continuam em supabase_client e são importados aqui — dependência unidirecional
(maintenance → client), sem ciclo.
"""

import math
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from utils.text import is_valid_product
from utils.normalize_product import normalize_product_name, normalize_product_name_v2
from utils.supabase_client import (
    _get_client,
    _extract_btu,
    _is_price_suspicious,
    _BTU_PRICE_CEILINGS,
)

def delete_invalid_from_supabase(
    dry_run: bool = False,
    since_id: Optional[int] = None,
) -> Dict[str, int]:
    """
    Varre a tabela `coletas` e remove registros que não passam no filtro AC.

    Estratégia:
      1. Busca id + produto + preco em lotes de 1.000 linhas (paginação)
      2. Aplica is_valid_product() em cada linha client-side
      3. Deleta IDs inválidos em lotes de 100

    Args:
        dry_run:  Se True, apenas conta — não deleta nada.
        since_id: Varredura incremental — só linhas com id > since_id
                  (watermark da automação). None = histórico inteiro.

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

    logger.info(
        f"[Supabase] Iniciando varredura de registros inválidos"
        f"{f' (id > {since_id})' if since_id else ''}..."
    )

    while True:
        try:
            q = (
                client.table("coletas")
                .select("id,produto,preco")
                .order("id")
            )
            if since_id:
                q = q.gt("id", since_id)
            resp = q.range(offset, offset + _FETCH_BATCH - 1).execute()
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
    since_id: Optional[int] = None,
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
        since_id:      Varredura incremental — só linhas com id > since_id.

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
            q = (
                client.table("coletas")
                .select("id,produto,marca")
                .order("id")
            )
            if since_id:
                q = q.gt("id", since_id)
            resp = q.range(offset, offset + _FETCH_BATCH - 1).execute()
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

def scan_fix_bad_prices_in_supabase(
    dry_run: bool = False,
    since_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Varre a tabela `coletas` e identifica/remove registros com preço suspeito.

    Um preço é suspeito quando excede o teto razoável para a capacidade BTU
    detectada no nome do produto. Exemplo: 9.000 BTUs com preço R$ 18.990
    (deveria ser ~R$ 1.899) — indício do bug de parser ×10.

    Args:
        dry_run:  Se True, apenas conta e retorna exemplos — não deleta.
        since_id: Varredura incremental — só linhas com id > since_id.

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

    logger.info(
        f"[Supabase] Iniciando varredura de preços suspeitos"
        f"{f' (id > {since_id})' if since_id else ''}..."
    )

    while True:
        try:
            q = (
                client.table("coletas")
                .select("id,produto,preco,plataforma")
                .not_.is_("preco", "null")
                .order("id")
            )
            if since_id:
                q = q.gt("id", since_id)
            resp = q.range(offset, offset + _FETCH_BATCH - 1).execute()
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

def recalculate_unknown_brands_in_supabase(
    dry_run: bool = False,
    since_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Varre registros com marca='Desconhecida' e re-aplica extract_brand() sobre
    o campo `produto`. Atualiza os que agora são identificados com a lista atual
    de config.BRANDS.

    Útil após adicionar novas marcas em config.py para recuperar registros
    históricos que foram gravados como "Desconhecida".

    Args:
        dry_run:  Se True, apenas conta/lista — não grava nada.
        since_id: Varredura incremental — só linhas com id > since_id.
                  Após mudar config.BRANDS, rode sem since_id (full scan)
                  para recuperar o histórico.

    Returns:
        dict: scanned, updated, unchanged, errors, preview
    """
    from utils.brands import extract_brand  # importação local para evitar circular

    client = _get_client()
    if client is None:
        return {"scanned": 0, "updated": 0, "unchanged": 0, "errors": 0, "preview": []}

    _FETCH_BATCH = 1_000
    _UPDATE_BATCH = 200

    changes: Dict[int, str] = {}   # {id: nova_marca}
    preview: List[Dict[str, Any]] = []
    scanned = 0
    unchanged = 0
    offset = 0

    logger.info("[Supabase] recalculate_unknown_brands — Fase 1: identificando…")

    while True:
        try:
            q = (
                client.table("coletas")
                .select("id,produto")
                .eq("marca", "Desconhecida")
                .order("id")
            )
            if since_id:
                q = q.gt("id", since_id)
            resp = q.range(offset, offset + _FETCH_BATCH - 1).execute()
        except Exception as exc:
            logger.error(f"[Supabase] Erro ao buscar lote (offset={offset}): {exc}")
            break

        batch = resp.data or []
        if not batch:
            break

        for row in batch:
            scanned += 1
            produto = row.get("produto") or ""
            nova_marca = extract_brand(produto)
            if nova_marca and nova_marca != "Desconhecida":
                changes[row["id"]] = nova_marca
                if len(preview) < 30:
                    preview.append({"id": row["id"], "produto": produto, "nova_marca": nova_marca})
            else:
                unchanged += 1

        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH

    logger.info(
        f"[Supabase] Fase 1: {scanned} registros 'Desconhecida', "
        f"{len(changes)} identificados, {unchanged} continuam desconhecidos."
    )

    if dry_run or not changes:
        return {
            "scanned": scanned,
            "updated": 0,
            "unchanged": unchanged,
            "errors": 0,
            "preview": preview,
        }

    # Fase 2 — atualizar em lotes por marca para eficiência
    from collections import defaultdict
    by_marca: Dict[str, List[int]] = defaultdict(list)
    for rid, marca in changes.items():
        by_marca[marca].append(rid)

    updated = 0
    errors = 0

    for marca, ids in by_marca.items():
        for i in range(0, len(ids), _UPDATE_BATCH):
            batch_ids = ids[i : i + _UPDATE_BATCH]
            try:
                client.table("coletas").update({"marca": marca}).in_("id", batch_ids).execute()
                updated += len(batch_ids)
                logger.debug(f"[Supabase] {marca}: {len(batch_ids)} registros atualizados")
            except Exception as exc:
                errors += len(batch_ids)
                logger.warning(f"[Supabase] Erro ao atualizar marca={marca!r}: {exc}")

    logger.info(
        f"[Supabase] recalculate_unknown_brands concluída: "
        f"{updated} atualizados, {errors} erros."
    )
    return {
        "scanned": scanned,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "preview": preview,
    }

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
