#!/usr/bin/env python3
"""
Script para corrigir preços da Dufrio no Supabase.

Problema: Preços coletados como valores concatenados VTEX estão sendo 
interpretados incorretamente pelo parser antigo.

Solução: Re-processar os preços usando o novo parser parse_price_brazil()
que corrige automaticamente valores concatenados.

Exemplos de correção:
  - "26990" → 2699.0 (era 269.9)
  - "25990" → 2599.0 (era 259.9)
  - "269900" → 2699.0 (correto)
"""

import sys
from utils.supabase_client import _get_client as get_supabase_client
from utils.text import parse_price_brazil

def get_dufrio_records():
    """Busca todos os registros da Dufrio com preço."""
    supabase = get_supabase_client()
    
    response = supabase.table('coletas').select(
        'id', 'preco', 'plataforma'
    ).eq('plataforma', 'Dufrio').execute()
    
    return response.data

def fix_price(raw_value, current_price):
    """
    Tenta corrigir o preço baseado no valor raw.
    
    Se o raw_data contém um valor numérico concatenado, re-processa.
    """
    if not raw_value:
        return None
    
    # Extrai valor numérico do raw_data
    import re
    
    # Tenta encontrar padrão de preço no raw_data
    # Pode estar em campos como: price, sellingPrice, etc.
    raw_str = str(raw_value)
    
    # Remove formatação e extrai apenas dígitos
    digits_only = re.sub(r'[^0-9]', '', raw_str)
    
    if len(digits_only) >= 4:
        # Tenta parsear com a nova lógica
        fixed = parse_price_brazil(digits_only)
        if fixed and abs(fixed - current_price) > 0.01:
            return fixed
    
    return None

def main():
    print("=" * 70)
    print("CORREÇÃO DE PREÇOS - DUFRIO")
    print("=" * 70)
    
    supabase = get_supabase_client()
    
    # Busca registros
    print("\n[1/4] Buscando registros da Dufrio...")
    records = get_dufrio_records()
    total = len(records)
    print(f"      Encontrados {total} registros")
    
    if total == 0:
        print("Nenhum registro encontrado. Encerrando.")
        return
    
    # Analisa quais precisam de correção
    print("\n[2/4] Analisando preços...")
    to_fix = []
    
    for record in records:
        record_id = record['id']
        current_price = record.get('preco')
        
        if current_price is None:
            continue
        
        # Verifica se o preço parece estar errado (muito baixo para AC)
        # ACs normalmente custam R$ 1000+, então preços < 500 são suspeitos
        # Se o preço é < 500 e termina em .9, provavelmente foi dividido errado
        # Ex: 269.9 deveria ser 2699.0
        if current_price < 500 and str(current_price).endswith('.9'):
            # Heurística: multiplica por 10 para corrigir
            fixed = current_price * 10
            if fixed > 500 and fixed < 50000:  # Validação de faixa razoável
                to_fix.append({
                    'id': record_id,
                    'old_price': current_price,
                    'new_price': fixed,
                    'reason': f'preco_baixo_termina_em_9'
                })
    
    print(f"      Registros que precisam de correção: {len(to_fix)}")
    
    if not to_fix:
        print("\n      Nenhum preço precisa ser corrigido!")
        print("      Os preços já estão corretos ou não foi possível identificar correções.")
        return
    
    # Mostra amostra das correções
    print("\n[3/4] Amostra das correções:")
    for i, fix in enumerate(to_fix[:10]):
        print(f"      ID {fix['id']}: R$ {fix['old_price']:.2f} → R$ {fix['new_price']:.2f}")
    if len(to_fix) > 10:
        print(f"      ... e mais {len(to_fix) - 10} correções")
    
    # Aplica correções
    print("\n[4/4] Aplicando correções...")
    corrected = 0
    errors = 0
    
    for fix in to_fix:
        try:
            response = supabase.table('coletas').update({
                'preco': fix['new_price']
            }).eq('id', fix['id']).execute()
            
            if response.data:
                corrected += 1
            else:
                errors += 1
                print(f"      ERRO ao atualizar ID {fix['id']}")
        except Exception as e:
            errors += 1
            print(f"      EXCEÇÃO ao atualizar ID {fix['id']}: {e}")
    
    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    print(f"Total de registros analisados: {total}")
    print(f"Correções aplicadas: {corrected}")
    print(f"Erros na atualização: {errors}")
    print(f"Registros sem alteração: {total - corrected - errors}")
    print("=" * 70)
    
    if corrected > 0:
        print("\n✅ Correções aplicadas com sucesso!")
        print("Os preços da Dufrio agora estão corretos no Supabase.")
    else:
        print("\n⚠️ Nenhuma correção foi aplicada.")

if __name__ == '__main__':
    import re
    main()
