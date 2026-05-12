"""
scripts/ml_oauth_setup.py — Setup OAuth do Mercado Livre (authorization_code).

Obtém um token de USUÁRIO com escopo de busca — necessário porque o token de
app (client_credentials) não tem permissão para o endpoint de search.

USO (única vez, rodar na máquina local):
    python scripts/ml_oauth_setup.py

PRÉ-REQUISITOS:
    1. ML_APP_ID e ML_APP_SECRET configurados no .env
    2. No portal developers.mercadolivre.com.br → seu app → Editar:
       Adicione como Redirect URI: https://localhost

FLUXO (sem servidor, sem HTTPS real):
    1. Script abre o browser na URL de autorização do ML
    2. Usuário faz login e clica em "Permitir"
    3. Browser tenta redirecionar para https://localhost?code=XXXX
       (página não carrega — normal; o código está na barra de endereço)
    4. Usuário copia a URL completa da barra de endereço e cola no terminal
    5. Script extrai o code, troca por access_token + refresh_token
    6. Salva em utils/sessions/mercadolivre_oauth.json
"""

import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
except ImportError:
    print("ERRO: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Carrega .env se existir
# ---------------------------------------------------------------------------
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

APP_ID     = os.environ.get("ML_APP_ID", "").strip()
APP_SECRET = os.environ.get("ML_APP_SECRET", "").strip()
REDIRECT   = "https://localhost"
API_BASE   = "https://api.mercadolibre.com"
SESSIONS_DIR = Path(__file__).parent.parent / "utils" / "sessions"

if not APP_ID or not APP_SECRET:
    print("\nERRO: ML_APP_ID e ML_APP_SECRET não encontrados no .env")
    print("  Adicione ao .env:")
    print("    ML_APP_ID=seu_app_id")
    print("    ML_APP_SECRET=seu_app_secret")
    sys.exit(1)

auth_url = (
    "https://auth.mercadolivre.com.br/authorization?"
    + urlencode({
        "response_type": "code",
        "client_id":     APP_ID,
        "redirect_uri":  REDIRECT,
    })
)

print(f"\n{'='*60}")
print("  ML OAuth Setup")
print(f"{'='*60}")
print("\n  PASSO 1 — Configure o Redirect URI no portal (uma vez):")
print("  → developers.mercadolivre.com.br → seu app → Editar")
print("  → Redirect URI: https://localhost")
print("  → Salve")

try:
    input("\n  Redirect URI já configurado? Pressione ENTER para abrir o browser: ")
except KeyboardInterrupt:
    print("\n  Cancelado.")
    sys.exit(0)

print("\n  PASSO 2 — Autorizando no browser...")
print("  → Faça login com sua conta Mercado Livre")
print("  → Clique em 'Permitir'")
print("  → O browser vai tentar abrir 'https://localhost' (página não carrega — normal)")
print("  → Copie a URL COMPLETA da barra de endereço do browser")
print("    (começa com: https://localhost?code=...  ou  https://localhost/?code=...)")

webbrowser.open(auth_url)

print()
try:
    pasted = input("  → Cole a URL aqui e pressione ENTER: ").strip()
except KeyboardInterrupt:
    print("\n  Cancelado.")
    sys.exit(0)

# Extrai o code da URL colada
if "code=" not in pasted:
    # Talvez o usuário colou só o code, não a URL inteira
    if len(pasted) > 10 and " " not in pasted and "?" not in pasted:
        code = pasted
    else:
        print("\n❌ Código não encontrado na URL. Certifique-se de copiar a URL completa.")
        print("   Exemplo: https://localhost?code=TG-XXXXXXXX-XXXXXXXXXXXX")
        sys.exit(1)
else:
    qs   = parse_qs(urlparse(pasted).query)
    code = qs.get("code", [None])[0]
    if not code:
        print("\n❌ Não foi possível extrair o código da URL fornecida.")
        sys.exit(1)

print(f"\n  Código capturado: {code[:15]}...")
print("  Trocando por access_token + refresh_token...")

try:
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "client_id":     APP_ID,
            "client_secret": APP_SECRET,
            "code":          code,
            "redirect_uri":  REDIRECT,
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
except Exception as e:
    print(f"\n❌ Falha ao trocar código por tokens: {e}")
    if hasattr(e, "response") and e.response is not None:
        print(f"   Resposta: {e.response.text}")
    sys.exit(1)

access_token  = tokens.get("access_token")
refresh_token = tokens.get("refresh_token")
expires_in    = tokens.get("expires_in", 21600)

if not access_token:
    print(f"\n❌ Resposta inesperada: {tokens}")
    sys.exit(1)

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
session_data = {
    "saved_at":      datetime.now().isoformat(),
    "access_token":  access_token,
    "refresh_token": refresh_token,
    "expires_in":    expires_in,
    "user_id":       tokens.get("user_id"),
}
out_path = SESSIONS_DIR / "mercadolivre_oauth.json"
out_path.write_text(json.dumps(session_data, indent=2))

print(f"\n  ✅ Tokens salvos: {out_path}")
print(f"     access_token:  {access_token[:20]}...")
if refresh_token:
    print(f"     refresh_token: {refresh_token[:20]}...")
print(f"     expires_in:    {expires_in//3600}h")
print("\n  Copie para o Oracle VM:")
print(f"  scp {out_path} ubuntu@<IP>:~/rac-position-tracker/utils/sessions/")
print("\n  Pronto! MLAPIScraper renovará o token automaticamente.\n")
