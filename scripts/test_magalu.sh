#!/usr/bin/env bash
# RAC Price Collector — Teste manual Magalu (curl_cffi, sem browser)
# Roda 1 página do scraper Python novo pra validar bypass do Akamai.
#
# Uso:
#   ./scripts/test_magalu.sh              → 1 página, sem priority filter
#   ./scripts/test_magalu.sh 2            → 2 páginas
#   ./scripts/test_magalu.sh 1 alta       → 1 página, só prioridade alta

set -u

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PAGES="${1:-1}"
PRIORITY="${2:-}"

cd "$PROJECT_DIR"

# Ativa venv
if [ ! -f ".venv/bin/activate" ]; then
    echo "[ERRO] .venv não encontrado. Crie com: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Garante curl_cffi instalado
if ! python -c "import curl_cffi" 2>/dev/null; then
    echo "[INFO] Instalando curl-cffi..."
    pip install 'curl-cffi>=0.6.0'
fi

# Carrega .env (Supabase credentials etc.)
if [ -f .env ]; then
    set -a; source .env; set +a
fi

echo "=== Teste Magalu Python (curl_cffi) — ${PAGES} página(s) ==="
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
