#!/usr/bin/env bash
# RAC Price Collector — Teste manual Magalu (Playwright browser persistente)
#
# Uso:
#   ./scripts/test_magalu.sh              → 1 página, browser visível (recomendado local)
#   ./scripts/test_magalu.sh 2            → 2 páginas, browser visível
#   ./scripts/test_magalu.sh 1 alta       → 1 página, priority alta
#   ./scripts/test_magalu.sh 1 "" headless → headless (Oracle VM)
#
# Dica: browser visível passa muito mais fácil pelo sensor.js do Akamai.
# Em headless, sensor.js detecta automação e bloqueia /busca/.

set -u

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PAGES="${1:-1}"
PRIORITY="${2:-}"
MODE="${3:-}"

# Default: browser visível
if [ "$MODE" = "headless" ]; then
    export MAGALU_HEADLESS="true"
    MODE_LABEL="headless"
else
    export MAGALU_HEADLESS="false"
    MODE_LABEL="visible"
fi

cd "$PROJECT_DIR"

# Ativa venv
if [ ! -f ".venv/bin/activate" ]; then
    echo "[ERRO] .venv não encontrado. Crie com: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Garante deps instaladas
if ! python -c "import curl_cffi" 2>/dev/null; then
    echo "[INFO] Instalando curl-cffi..."
    pip install 'curl-cffi>=0.6.0'
fi
if ! python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    echo "[INFO] Instalando playwright..."
    pip install playwright
    python -m playwright install chromium
fi

# Carrega .env (Supabase credentials etc.)
if [ -f .env ]; then
    set -a; source .env; set +a
fi

# Limpa cache de sessão antiga
rm -f data/magalu_session.json 2>/dev/null

echo "=== Teste Magalu (browser $MODE_LABEL) — ${PAGES} página(s) ==="
echo "MAGALU_HEADLESS=$MAGALU_HEADLESS"
echo

if [ -z "$PRIORITY" ]; then
    python main.py --platforms magalu --pages "$PAGES"
else
    python main.py --platforms magalu --pages "$PAGES" --priority "$PRIORITY"
fi

EXIT=$?
echo
echo "=== Teste concluído (exit=$EXIT) ==="
echo "CSV salvo em: $PROJECT_DIR/output/"
echo "Logs em:      $PROJECT_DIR/logs/"
exit $EXIT
