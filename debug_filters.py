"""Script de diagnóstico para identificar o bug dos filtros."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from supabase import create_client

url = os.getenv("SUPABASE_URL", "").strip()
key = os.getenv("SUPABASE_KEY", "").strip()

if not url or not key:
    print("❌ SUPABASE_URL ou SUPABASE_KEY não configurados")
    exit(1)

client = create_client(url, key)
print("✅ Conectado ao Supabase\n")

# Teste 1: Query sem filtro de plataforma (simula "todas as plataformas")
print("=" * 60)
print("TESTE 1: Todas as plataformas (sem filtro de plataforma)")
print("=" * 60)

q1 = (
    client.table("coletas")
    .select("*", count="exact")
    .gte("data", "2025-01-01")
    .lte("data", "2025-12-31")
)

# Adiciona filtros BTU como no código original
or_patterns = []
for btu in ["12000"]:
    or_patterns.append(f"produto.ilike.%{btu}%")
    or_patterns.append(f"produto.ilike.%12.000%")

if or_patterns:
    q1 = q1.or_(",".join(or_patterns))
    print(f"Padrões OR aplicados: {or_patterns}")

try:
    resp1 = q1.execute()
    count1 = len(resp1.data) if resp1.data else 0
    print(f"Resultado (com filtro BTU): {count1} registros\n")
except Exception as e:
    print(f"Erro: {e}\n")

# Teste 2: Query COM filtro de plataforma específica
print("=" * 60)
print("TESTE 2: Apenas 'ferreira_costa' (uma plataforma)")
print("=" * 60)

q2 = (
    client.table("coletas")
    .select("*", count="exact")
    .gte("data", "2025-01-01")
    .lte("data", "2025-12-31")
    .in_("plataforma", ["ferreira_costa"])
)

# Mesmos filtros BTU
or_patterns2 = []
for btu in ["12000"]:
    or_patterns2.append(f"produto.ilike.%{btu}%")
    or_patterns2.append(f"produto.ilike.%12.000%")

if or_patterns2:
    q2 = q2.or_(",".join(or_patterns2))
    print(f"Padrões OR aplicados: {or_patterns2}")

try:
    resp2 = q2.execute()
    count2 = len(resp2.data) if resp2.data else 0
    print(f"Resultado (com filtro BTU + plataforma): {count2} registros\n")
except Exception as e:
    print(f"Erro: {e}\n")

# Teste 3: Query sem filtro BTU - apenas plataforma
print("=" * 60)
print("TESTE 3: Apenas 'ferreira_costa' SEM filtro BTU")
print("=" * 60)

q3 = (
    client.table("coletas")
    .select("*", count="exact")
    .gte("data", "2025-01-01")
    .lte("data", "2025-12-31")
    .in_("plataforma", ["ferreira_costa"])
)

try:
    resp3 = q3.execute()
    count3 = len(resp3.data) if resp3.data else 0
    print(f"Resultado (apenas filtro plataforma): {count3} registros\n")
except Exception as e:
    print(f"Erro: {e}\n")

# Teste 4: Query sem nenhum filtro
print("=" * 60)
print("TESTE 4: Sem filtros (apenas data)")
print("=" * 60)

q4 = (
    client.table("coletas")
    .select("*", count="exact")
    .gte("data", "2025-01-01")
    .lte("data", "2025-12-31")
)

try:
    resp4 = q4.execute()
    count4 = len(resp4.data) if resp4.data else 0
    print(f"Resultado (sem filtros): {count4} registros\n")
except Exception as e:
    print(f"Erro: {e}\n")

# Análise
print("=" * 60)
print("ANÁLISE DO PROBLEMA")
print("=" * 60)
print(f"Teste 1 (todas plataformas + BTU): {count1}")
print(f"Teste 2 (ferreira_costa + BTU):    {count2}")
print(f"Teste 3 (ferreira_costa s/ BTU):   {count3}")
print(f"Teste 4 (sem filtros):             {count4}")
print()

if count1 < count2:
    print("❌ BUG CONFIRMADO: 'Todas as plataformas' retorna MENOS registros")
    print("   que uma plataforma específica!")
    print()
    print("ISSO ACONTECE PORQUE:")
    print("  - O filtro BTU usa .or_() com ILIKE")
    print("  - Registros que NÃO têm '12000' ou '12.000' no nome são EXCLUÍDOS")
    print("  - Quando filtra por plataforma, pode ser que TODOS os registros")
    print("    daquela plataforma já tenham BTU no nome, mascarando o problema")
    print()
    print("SOLUÇÃO:")
    print("  - Os filtros BTU/product_types devem ser OPCIONAIS")
    print("  - Se o usuário NÃO selecionou BTU, não aplicar o filtro")
    print("  - OU usar lógica diferente: AND entre filtros, OR dentro de cada grupo")
else:
    print("✅ Comportamento esperado")
