"""
scripts/ml_oauth_setup.py — Setup OAuth do Mercado Livre (authorization_code).

Obtém um token de USUÁRIO com escopo de busca — necessário porque o token de
app (client_credentials) não tem permissão para o endpoint de search.

USO (única vez, rodar na máquina local com browser):
    python scripts/ml_oauth_setup.py

PRÉ-REQUISITOS:
    1. ML_APP_ID e ML_APP_SECRET configurados no .env
    2. No portal developers.mercadolivre.com.br, na configuração do app,
       adicione "https://www.mercadolivre.com.br" como Redirect URI permitida.

O QUE FAZ:
    1. Abre browser na URL de autorização do ML
    2. Usuário faz login e autoriza o app
    3. ML redireciona para mercadolivre.com.br?code=XXXX
    4. Script extrai o code da URL, troca por access_token + refresh_token
    5. Salva em utils/sessions/mercadolivre_oauth.json

Após isso, MLAPIScraper usa o refresh_token para renovar tokens automaticamente.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERRO: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERRO: pip install requests")
    sys.exit(1)

# Carrega .env se existir
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

APP_ID     = os.environ.get("ML_APP_ID", "").strip()
APP_SECRET = os.environ.get("ML_APP_SECRET", "").strip()
REDIRECT   = "https://www.mercadolivre.com.br"
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
print("  ML OAuth Setup — authorization_code")
print(f"{'='*60}")
print("\n  INSTRUÇÕES:")
print("  1. O browser vai abrir na página de login do Mercado Livre")
print("  2. Faça login com sua conta ML")
print("  3. Clique em 'Permitir' para autorizar o app")
print("  4. Você será redirecionado para mercadolivre.com.br")
print("  5. O script detecta automaticamente e salva o token")
print(f"\n  App ID: {APP_ID}")
print(f"  Redirect: {REDIRECT}")


def exchange_code(code: str) -> dict:
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
    return resp.json()


with sync_playwright() as p:
    browser = None
    for channel in ["chrome", "msedge", None]:
        try:
            browser = p.chromium.launch(
                headless=False,
                channel=channel,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            print(f"\n  Browser: {channel or 'chromium'}")
            break
        except Exception:
            continue

    if not browser:
        print("ERRO: Nenhum browser disponível. Execute: python -m playwright install chromium")
        sys.exit(1)

    context = browser.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo")
    page    = context.new_page()

    print(f"\n  Abrindo: {auth_url[:80]}...")
    page.goto(auth_url, wait_until="domcontentloaded", timeout=30_000)

    print("\n  Aguardando autorização do usuário...")
    print("  (O script detecta automaticamente o redirect)")

    # Aguarda até a URL mudar para mercadolivre.com.br?code=...
    code = None
    try:
        page.wait_for_url(
            lambda url: "mercadolivre.com.br" in url and "code=" in url,
            timeout=180_000,  # 3 minutos para o usuário autorizar
        )
        final_url = page.url
        qs   = parse_qs(urlparse(final_url).query)
        code = qs.get("code", [None])[0]
    except Exception as e:
        print(f"\n  AVISO: Timeout ou erro ao aguardar redirect ({e})")
        print("  Verifique se o Redirect URI está configurado no portal do app.")

    browser.close()

if not code:
    print("\n❌ Código de autorização não obtido. Verifique:")
    print("   - O app tem 'https://www.mercadolivre.com.br' como Redirect URI?")
    print("   - O usuário autorizou o app?")
    sys.exit(1)

print(f"\n  Código obtido: {code[:12]}...")
print("  Trocando por access_token + refresh_token...")

try:
    tokens = exchange_code(code)
except Exception as e:
    print(f"\n❌ Falha ao trocar código por tokens: {e}")
    sys.exit(1)

access_token  = tokens.get("access_token")
refresh_token = tokens.get("refresh_token")
expires_in    = tokens.get("expires_in", 21600)

if not access_token:
    print(f"\n❌ Resposta inesperada da API: {tokens}")
    sys.exit(1)

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
session_data = {
    "saved_at":     datetime.now().isoformat(),
    "access_token": access_token,
    "refresh_token": refresh_token,
    "expires_in":   expires_in,
    "user_id":      tokens.get("user_id"),
}
out_path = SESSIONS_DIR / "mercadolivre_oauth.json"
out_path.write_text(json.dumps(session_data, indent=2))

print(f"\n✅ Tokens salvos em: {out_path}")
print(f"   access_token:  {access_token[:20]}...")
print(f"   refresh_token: {str(refresh_token)[:20]}..." if refresh_token else "   refresh_token: (ausente)")
print(f"   expires_in:    {expires_in}s ({expires_in//3600}h)")
print("\n  Pronto! MLAPIScraper vai usar e renovar este token automaticamente.")
print("  Copie utils/sessions/mercadolivre_oauth.json para o Oracle VM.")
