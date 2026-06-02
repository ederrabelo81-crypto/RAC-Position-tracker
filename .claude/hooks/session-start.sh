#!/bin/bash
# SessionStart hook — Claude Code na web.
# Instala as dependências leves para importar os módulos do dashboard e rodar a
# suíte de testes (pytest). NÃO instala playwright/curl-cffi/bs4 (pesados, com
# download de browser) — esses são só do coletor (main.py/scrapers). Para mexer
# nos scrapers, rode `pip install -r requirements.txt` manualmente na sessão.
set -euo pipefail

# Só roda no ambiente remoto (Claude Code na web). Em dev local, use seu venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Idempotente: pip install pode rodar várias vezes sem efeito colateral.
python -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true

# Container Debian: PyJWT/cryptography vêm do apt e quebram quando o supabase
# puxa versões novas — o cryptography do sistema espera um _cffi_backend que não
# existe no Python do pip (pyo3 panic). Sombreia o trio com cópias gerenciadas
# pelo pip, consistentes entre si, antes do install principal.
python -m pip install --quiet --ignore-installed PyJWT cffi cryptography >/dev/null 2>&1 || true

python -m pip install --quiet -r requirements_app.txt pytest >/dev/null 2>&1

echo "✅ session-start: deps do dashboard + pytest instaladas (requirements_app.txt)"
