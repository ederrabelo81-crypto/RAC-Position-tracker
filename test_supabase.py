"""
test_supabase.py — Diagnóstico direto da integração Supabase.
Execute: python test_supabase.py
"""

import os
import sys
from pathlib import Path

print("=" * 60)
print("  DIAGNÓSTICO SUPABASE")
print("=" * 60)

# 1. Verifica python-dotenv
print("\n[1] python-dotenv...")
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    loaded = load_dotenv(env_path)
    print(f"    ✓ dotenv carregado | .env encontrado: {loaded} | caminho: {env_path}")
    print(f"    .env existe no disco: {env_path.exists()}")
except ImportError:
    print("    ✗ python-dotenv NÃO instalado: pip install python-dotenv")
    sys.exit(1)

# 2. Verifica variáveis de ambiente
print("\n[2] Variáveis de ambiente...")
url = os.getenv("SUPABASE_URL", "")
key = os.getenv("SUPABASE_KEY", "")
print(f"    SUPABASE_URL : {'✓ ' + url[:40] + '...' if url else '✗ NÃO ENCONTRADA'}")
print(f"    SUPABASE_KEY : {'✓ ' + key[:20] + '...' if key else '✗ NÃO ENCONTRADA'}")

if not url or not key:
    print("\n    AÇÃO: Verifique o arquivo .env — deve conter:")
    print("        SUPABASE_URL=https://xxxx.supabase.co")
    print("        SUPABASE_KEY=eyJ...")
    print("\n    Conteúdo atual do .env:")
    try:
        print("    " + env_path.read_text(encoding="utf-8").replace("\n", "\n    "))
    except Exception as e:
        print(f"    Erro ao ler .env: {e}")
    sys.exit(1)

# 3. Verifica pacote supabase
print("\n[3] Pacote supabase...")
try:
    from supabase import create_client
    print("    ✓ supabase importado com sucesso")
except ImportError as e:
    print(f"    ✗ Erro ao importar supabase: {e}")
    print("    AÇÃO: python -m pip install supabase")
    sys.exit(1)

# 4. Testa conexão
print("\n[4] Conexão com Supabase...")
try:
    client = create_client(url, key)
    print("    ✓ Client criado com sucesso")
except Exception as e:
    print(f"    ✗ Falha ao criar client: {e}")
    sys.exit(1)

# 5. Testa acesso à tabela coletas
print("\n[5] Acesso à tabela 'coletas'...")
try:
    resp = client.table("coletas").select("id").limit(1).execute()
    print(f"    ✓ Tabela acessível | Linhas existentes: {len(resp.data)}")
except Exception as e:
    print(f"    ✗ Erro ao acessar tabela: {e}")
    print("    Possíveis causas:")
    print("      - Tabela 'coletas' não existe (verifique SQL no Supabase Dashboard)")
    print("      - RLS (Row Level Security) bloqueando acesso anon")
    print("      - SUPABASE_KEY é a service_role key, não a anon key")
    sys.exit(1)

# 6. Testa insert de 1 linha de teste
print("\n[6] Insert de linha de teste...")
test_row = {
    "data": "2026-01-01",
    "turno": "Manhã",
    "plataforma": "TESTE_DIAGNOSTICO",
    "produto": "Produto Teste Diagnóstico",
    "preco": 999.99,
    "marca": "Teste",
}
try:
    resp = client.table("coletas").insert(test_row).execute()
    inserted_id = resp.data[0]["id"] if resp.data else "?"
    print(f"    ✓ Linha inserida com sucesso! id={inserted_id}")

    # Remove a linha de teste
    client.table("coletas").delete().eq("id", inserted_id).execute()
    print(f"    ✓ Linha de teste removida (id={inserted_id})")
except Exception as e:
    print(f"    ✗ Erro ao inserir: {e}")
    print("\n    CAUSA PROVÁVEL: RLS (Row Level Security) está ativo.")
    print("    SOLUÇÃO: No Supabase Dashboard:")
    print("      1. Table Editor → coletas → clique no cadeado (RLS)")
    print("      2. OU execute no SQL Editor:")
    print("         ALTER TABLE coletas DISABLE ROW LEVEL SECURITY;")
    print("      OU adicione uma policy que permite INSERT anon:")
    print("         CREATE POLICY allow_all ON coletas FOR ALL USING (true) WITH CHECK (true);")
    sys.exit(1)

print("\n" + "=" * 60)
print("  ✓ TUDO OK — Supabase configurado corretamente!")
print("  Rode: python main.py --platforms leroy --pages 1")
print("=" * 60)
