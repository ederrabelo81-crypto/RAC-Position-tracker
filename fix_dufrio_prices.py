"""
Script para corrigir preços da Dufrio no Supabase.

PROBLEMA: Preços coletados como 182900.0 em vez de 1829.00
CAUSA: Concatenação incorreta de VTEX split price sem separador decimal
SOLUÇÃO: Dividir valores por 100 quando forem claramente valores concatenados

Exemplos:
  - 182900.0 → 1829.00 (R$ 1.829,00)
  - 152010.0 → 1520.10 (R$ 1.520,10)
  - 279990.0 → 2799.90 (R$ 2.799,90)
  - 9990.0   → 99.90   (R$ 99,90)
"""

import os
import math
from pathlib import Path
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from loguru import logger
from supabase import create_client, Client

# Carrega .env
load_dotenv(Path(__file__).parent / ".env")

def get_supabase_client() -> Optional[Client]:
    """Cria client Supabase ou retorna None se não configurado."""
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    
    if not url or not key:
        logger.error("[Supabase] Credenciais não encontradas. Verifique o .env")
        return None
    
    try:
        client = create_client(url, key)
        logger.info(f"[Supabase] Conectado em: {url[:40]}...")
        return client
    except Exception as exc:
        logger.error(f"[Supabase] Erro ao conectar: {exc}")
        return None


def is_dufrio_concatenated_price(value: float) -> bool:
    """
    Detecta se um preço é um valor concatenado da Dufrio.
    
    Critérios:
      - Valor > 1000 (preços reais de AC são tipicamente < 10000)
      - Valor termina em 00, 10, 90, 99, etc. (centavos comuns)
      - Valor / 100 resulta em preço razoável (50-5000)
    """
    if value is None or value <= 0:
        return False
    
    # Valores muito baixos não são concatenados
    if value < 100:
        return False
    
    # Divide por 100 para obter preço potencial
    divided = value / 100
    
    # Preço razoável após divisão? (R$ 50 a R$ 5000)
    if divided < 50 or divided > 5000:
        return False
    
    # Verifica se parece ser concatenado:
    # - Original é inteiro ou tem poucos decimais
    # - Após dividir por 100, tem decimais significativos
    original_str = f"{value:.2f}"
    divided_str = f"{divided:.2f}"
    
    # Se o valor original termina em ,00 ou ,10, ,90, etc., provavelmente é concatenado
    cents = int(round((value % 1) * 100))
    if cents == 0:
        # Termina em 00 - comum em valores concatenados
        return True
    
    # Verifica padrão: valor grande com centavos "estranhos" que fazem sentido /100
    divided_cents = int(round((divided % 1) * 100))
    if divided_cents in [0, 10, 90, 99, 50, 49, 51]:
        return True
    
    return False


def fix_dufrio_price(value: float) -> float:
    """
    Corrige um preço concatenado da Dufrio.
    
    Exemplos:
      182900.0 → 1829.00
      152010.0 → 1520.10
      9990.0   → 99.90
    """
    if value is None or value <= 0:
        return value
    
    # Só corrige se for claramente um valor concatenado
    if is_dufrio_concatenated_price(value):
        return round(value / 100, 2)
    
    return value


def scan_dufrio_prices(client: Client, dry_run: bool = True, preview_limit: int = 50) -> Dict[str, Any]:
    """
    Varre registros da Dufrio e identifica preços que precisam de correção.
    
    Args:
        client: Client Supabase
        dry_run: Se True, apenas analisa - não atualiza
        preview_limit: Máximo de exemplos para mostrar
    
    Returns:
        dict com estatísticas e preview das correções
    """
    _FETCH_BATCH = 1000
    
    scanned = 0
    needs_fix = 0
    already_ok = 0
    preview = []
    offset = 0
    
    logger.info("[Dufrio] Fase 1 - Identificando preços incorretos...")
    
    while True:
        try:
            resp = (
                client.table("coletas")
                .select("id,plataforma,produto,preco")
                .eq("plataforma", "Dufrio")
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
            preco = row.get("preco")
            
            if preco is None:
                already_ok += 1  # Sem preço, não precisa corrigir
                continue
            
            if is_dufrio_concatenated_price(preco):
                needs_fix += 1
                if len(preview) < preview_limit:
                    corrected = fix_dufrio_price(preco)
                    preview.append({
                        "id": row["id"],
                        "produto": row.get("produto", "")[:80],
                        "preco_original": preco,
                        "preco_corrigido": corrected,
                        "diferenca": f"{preco - corrected:.2f}"
                    })
            else:
                already_ok += 1
        
        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH
    
    logger.info(
        f"[Dufrio] Varredura concluída: {scanned} registros, "
        f"{needs_fix} precisam corrigir, {already_ok} já OK"
    )
    
    return {
        "scanned": scanned,
        "needs_fix": needs_fix,
        "already_ok": already_ok,
        "preview": preview,
    }


def fix_dufrio_prices_in_supabase(client: Client, dry_run: bool = True) -> Dict[str, int]:
    """
    Corrige preços da Dufrio no Supabase.
    
    Estratégia:
      1. Busca todos os registros da Dufrio em lotes
      2. Identifica preços concatenados (>1000 e divisível por 100 faz sentido)
      3. Atualiza apenas os registros com preços incorretos
    
    Args:
        client: Client Supabase
        dry_run: Se True, apenas conta - não atualiza
    
    Returns:
        dict com scanned, fixed, errors
    """
    _FETCH_BATCH = 500
    
    scanned = 0
    fixed = 0
    errors = 0
    offset = 0
    
    logger.info("[Dufrio] Iniciando correção de preços...")
    
    # Coleta IDs que precisam de atualização
    updates_needed: List[Dict[str, Any]] = []
    
    while True:
        try:
            resp = (
                client.table("coletas")
                .select("id,plataforma,produto,preco")
                .eq("plataforma", "Dufrio")
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
            preco = row.get("preco")
            
            if preco is not None and is_dufrio_concatenated_price(preco):
                corrected = fix_dufrio_price(preco)
                updates_needed.append({
                    "id": row["id"],
                    "preco_original": preco,
                    "preco_corrigido": corrected
                })
        
        if len(batch) < _FETCH_BATCH:
            break
        offset += _FETCH_BATCH
    
    logger.info(f"[Dufrio] {len(updates_needed)} registros precisam de atualização")
    
    if dry_run or not updates_needed:
        return {"scanned": scanned, "fixed": 0, "errors": 0, "to_fix": len(updates_needed)}
    
    # Atualiza em lote único usando upsert
    logger.info(f"[Dufrio] Atualizando {len(updates_needed)} registros...")
    
    try:
        # Prepara lista de atualizações
        update_rows = [{"id": u["id"], "preco": u["preco_corrigido"]} for u in updates_needed]
        
        # Upsert em lote - muito mais rápido que atualizações individuais
        client.table("coletas").upsert(
            update_rows,
            on_conflict="id",
            ignore_duplicates=False
        ).execute()
        
        fixed = len(update_rows)
        logger.info(f"[Dufrio] Lote único: {fixed} registros atualizados")
        
    except Exception as exc:
        logger.error(f"[Dufrio] Erro na atualização em lote: {exc}")
        logger.info("[Dufrio] Tentando atualizações individuais (mais lento)...")
        
        # Fallback para atualizações individuais se batch falhar
        total = len(updates_needed)
        for i, update in enumerate(updates_needed, 1):
            try:
                client.table("coletas").update({
                    "preco": update["preco_corrigido"]
                }).eq("id", update["id"]).execute()
                fixed += 1
                if i % 50 == 0:
                    logger.info(f"[Dufrio] Progresso: {i}/{total} ({100*i/total:.1f}%)")
            except Exception as row_exc:
                errors += 1
                logger.warning(f"[Dufrio] Erro no registro {update['id']}: {row_exc}")
    
    logger.info(
        f"[Dufrio] Correção concluída: {fixed} atualizados, "
        f"{errors} com erro"
    )
    
    return {"scanned": scanned, "fixed": fixed, "errors": errors, "to_fix": len(updates_needed)}


def main():
    """Função principal."""
    logger.info("=" * 60)
    logger.info("CORREÇÃO DE PREÇOS DUFRIO NO SUPABASE")
    logger.info("=" * 60)
    
    client = get_supabase_client()
    if client is None:
        logger.error("Não foi possível conectar ao Supabase. Encerrando.")
        return
    
    # Fase 1: Análise (dry run)
    logger.info("\n--- FASE 1: ANÁLISE ---")
    analysis = scan_dufrio_prices(client, dry_run=True, preview_limit=20)
    
    logger.info(f"\n📊 Resultados da análise:")
    logger.info(f"   Total scanned: {analysis['scanned']}")
    logger.info(f"   Precisa corrigir: {analysis['needs_fix']}")
    logger.info(f"   Já OK: {analysis['already_ok']}")
    
    if analysis['preview']:
        logger.info("\n📋 Exemplos de correções necessárias:")
        for item in analysis['preview'][:10]:
            logger.info(
                f"   ID {item['id']}: R$ {item['preco_original']:.2f} → "
                f"R$ {item['preco_corrigido']:.2f} ({item['produto'][:50]}...)"
            )
    
    if analysis['needs_fix'] == 0:
        logger.info("\n✅ Nenhum preço precisa de correção!")
        return
    
    # Pergunta se deve prosseguir
    logger.info("\n" + "=" * 60)
    logger.info("PARA EXECUTAR A CORREÇÃO REAL:")
    logger.info("  Execute este script com --apply flag:")
    logger.info("  python fix_dufrio_prices.py --apply")
    logger.info("=" * 60)
    
    # Verifica se deve aplicar
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        logger.info("\n--- FASE 2: CORREÇÃO ---")
        result = fix_dufrio_prices_in_supabase(client, dry_run=False)
        
        logger.info(f"\n✅ Correção concluída!")
        logger.info(f"   Registros corrigidos: {result['fixed']}")
        if result['errors'] > 0:
            logger.warning(f"   Erros: {result['errors']}")
    else:
        logger.info("\n⚠️  Modo DRY RUN - nenhuma alteração foi feita")
        logger.info("   Use --apply para aplicar as correções")


if __name__ == "__main__":
    main()
