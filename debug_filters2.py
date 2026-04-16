"""Script para verificar dados no banco e entender o problema."""

import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import date, timedelta

load_dotenv(Path(__file__).parent / ".env")

from supabase import create_client

url = os.getenv("SUPABASE_URL", "").strip()
key = os.getenv("SUPABASE_KEY", "").strip()

if not url or not key:
    print("❌ SUPABASE_URL ou SUPABASE_KEY não configurados")
    exit(1)

client = create_client(url, key)
print("✅ Conectado ao Supabase\n")

# Verifica a data mais recente
resp_max = client.table("coletas").select("data").order("data", desc=True).limit(1).execute()
if resp_max.data:
    max_date = resp_max.data[0].get("data")
    print(f"Data mais recente no banco: {max_date}")
    
    # Usa últimos 90 dias a partir da data máxima
    try:
        max_date_obj = date.fromisoformat(max_date[:10]) if max_date else date.today()
    except:
        max_date_obj = date.today()
    
    start_date = str(max_date_obj - timedelta(days=90))
    end_date = str(max_date_obj)
else:
    print("❌ Sem dados no banco")
    exit(1)

print(f"Período de teste: {start_date} até {end_date}\n")

# Teste A: Todas as plataformas com filtros BTU vazios (simula usuário sem selecionar BTU)
print("=" * 60)
print("TESTE A: Todas as plataformas, SEM filtro BTU/product_types")
print("=" * 60)

qA = (
    client.table("coletas")
    .select("*")
    .gte("data", start_date)
    .lte("data", end_date)
    .limit(10000)
)

respA = qA.execute()
countA = len(respA.data) if respA.data else 0
print(f"Total de registros: {countA}\n")

# Teste B: Apenas ferreira_costa, SEM filtro BTU
print("=" * 60)
print("TESTE B: Apenas 'ferreira_costa', SEM filtro BTU")
print("=" * 60)

qB = (
    client.table("coletas")
    .select("*")
    .gte("data", start_date)
    .lte("data", end_date)
    .in_("plataforma", ["ferreira_costa"])
    .limit(10000)
)

respB = qB.execute()
countB = len(respB.data) if respB.data else 0
print(f"Total de registros: {countB}\n")

# Teste C: Todas as plataformas COM filtro BTU (12000)
print("=" * 60)
print("TESTE C: Todas as plataformas, COM filtro BTU=12000")
print("=" * 60)

or_patterns_C = [
    "produto.ilike.%12000%",
    "produto.ilike.%12.000%",
]

qC = (
    client.table("coletas")
    .select("*")
    .gte("data", start_date)
    .lte("data", end_date)
    .or_(",".join(or_patterns_C))
    .limit(10000)
)

respC = qC.execute()
countC = len(respC.data) if respC.data else 0
print(f"Total de registros: {countC}\n")

# Teste D: ferreira_costa COM filtro BTU
print("=" * 60)
print("TESTE D: 'ferreira_costa', COM filtro BTU=12000")
print("=" * 60)

qD = (
    client.table("coletas")
    .select("*")
    .gte("data", start_date)
    .lte("data", end_date)
    .in_("plataforma", ["ferreira_costa"])
    .or_(",".join(or_patterns_C))
    .limit(10000)
)

respD = qD.execute()
countD = len(respD.data) if respD.data else 0
print(f"Total de registros: {countD}\n")

# Análise
print("=" * 60)
print("ANÁLISE")
print("=" * 60)
print(f"A (todas, s/ BTU):     {countA}")
print(f"B (ferreira, s/ BTU):  {countB}")
print(f"C (todas, c/ BTU):     {countC}")
print(f"D (ferreira, c/ BTU):  {countD}")
print()

if countA > 0 and countC < countA:
    print(f"⚠️  O filtro BTU está excluindo {countA - countC} registros!")
    print("   Isso pode causar o bug se o usuário selecionar BTU.")
    
if countB > 0 and countD < countB:
    print(f"⚠️  No ferreira_costa, o filtro BTU excluiu {countB - countD} registros!")

# Verifica quantos registros têm posicao_geral
print()
print("=" * 60)
print("VERIFICANDO CAMPO posicao_geral")
print("=" * 60)

qE = (
    client.table("coletas")
    .select("posicao_geral")
    .gte("data", start_date)
    .lte("data", end_date)
    .not_.is_("posicao_geral", "null")
    .limit(10000)
)

respE = qE.execute()
countE = len(respE.data) if respE.data else 0
print(f"Registros com posicao_geral preenchido: {countE}")

# Verifica plataformas disponíveis
print()
print("=" * 60)
print("PLATAFORMAS DISPONÍVEIS")
print("=" * 60)

qF = (
    client.table("coletas")
    .select("plataforma")
    .gte("data", start_date)
    .lte("data", end_date)
    .limit(50000)
)

respF = qF.execute()
if respF.data:
    plataformas = {}
    for r in respF.data:
        p = r.get("plataforma", "unknown")
        plataformas[p] = plataformas.get(p, 0) + 1
    
    for plat, cnt in sorted(plataformas.items(), key=lambda x: -x[1])[:20]:
        print(f"  {plat}: {cnt} registros")
