"""
scripts/ml_oauth_setup.py — Setup OAuth do Mercado Livre (authorization_code).

Obtém um token de USUÁRIO com escopo de busca — necessário porque o token de
app (client_credentials) não tem permissão para o endpoint de search.

USO (única vez, rodar na máquina local com browser):
    python scripts/ml_oauth_setup.py

PRÉ-REQUISITOS:
    1. ML_APP_ID e ML_APP_SECRET configurados no .env
    2. No portal developers.mercadolivre.com.br, na configuração do app,
       adicione "http://localhost:8765/callback" como Redirect URI.

O QUE FAZ:
    1. Inicia um servidor HTTP local na porta 8765
    2. Abre browser na URL de autorização do ML
    3. Usuário faz login e clica em "Permitir"
    4. ML redireciona para localhost:8765/callback?code=XXXX
    5. Script captura o code, troca por access_token + refresh_token
    6. Salva em utils/sessions/mercadolivre_oauth.json

Após isso, MLAPIScraper usa o refresh_token para renovar tokens automaticamente.
"""

import json
import os
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
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
PORT       = 8765
REDIRECT   = f"http://localhost:{PORT}/callback"
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

# ---------------------------------------------------------------------------
# Servidor HTTP local para capturar o code do redirect
# ---------------------------------------------------------------------------
_captured_code: list = []   # lista mutável para comunicação entre threads


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs   = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]

        if code:
            _captured_code.append(code)
            body = b"<h2>Autorizado! Pode fechar esta janela.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            error = qs.get("error", ["desconhecido"])[0]
            body  = f"<h2>Erro: {error}. Tente novamente.</h2>".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass   # silencia logs do servidor


def _run_server(server: HTTPServer):
    while not _captured_code:
        server.handle_request()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
print("  ML OAuth Setup — authorization_code + localhost redirect")
print(f"{'='*60}")
print("\n  INSTRUÇÕES:")
print("  1. No portal developers.mercadolivre.com.br → seu app → Editar")
print(f"     Adicione como Redirect URI: {REDIRECT}")
print("  2. Salve e volte aqui, pressione ENTER para continuar")
print("  3. O browser vai abrir na página de login do ML")
print("  4. Faça login e clique em 'Permitir'")
print("  5. O script captura o token automaticamente\n")
print(f"  App ID:   {APP_ID}")
print(f"  Redirect: {REDIRECT}")

try:
    input("\n  → Redirect URI adicionado no portal? Pressione ENTER para abrir o browser: ")
except KeyboardInterrupt:
    print("\n  Cancelado.")
    sys.exit(0)

# Inicia servidor local
server = HTTPServer(("localhost", PORT), _CallbackHandler)
t = threading.Thread(target=_run_server, args=(server,), daemon=True)
t.start()

print(f"\n  Servidor local iniciado em {REDIRECT}")
print("  Abrindo browser...")
webbrowser.open(auth_url)
print("  Aguardando autorização (até 3 minutos)...")

# Aguarda até 3 minutos pelo código
import time as _time
deadline = _time.time() + 180
while not _captured_code and _time.time() < deadline:
    _time.sleep(0.5)

server.server_close()

if not _captured_code:
    print("\n❌ Timeout. Nenhuma autorização recebida em 3 minutos.")
    print("   Verifique se o Redirect URI está correto no portal do app.")
    sys.exit(1)

code = _captured_code[0]
print(f"\n  ✅ Código de autorização capturado: {code[:12]}...")
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

print(f"\n  ✅ Tokens salvos em: {out_path}")
print(f"     access_token:  {access_token[:20]}...")
print(f"     refresh_token: {str(refresh_token)[:20]}..." if refresh_token else "     refresh_token: (ausente)")
print(f"     expires_in:    {expires_in}s ({expires_in//3600}h)")
print("\n  Próximo passo — copie o arquivo para o Oracle VM:")
print(f"  scp {out_path} ubuntu@<IP-DO-VM>:~/rac-position-tracker/utils/sessions/")
print("\n  Pronto! MLAPIScraper renovará o token automaticamente a cada coleta.\n")
