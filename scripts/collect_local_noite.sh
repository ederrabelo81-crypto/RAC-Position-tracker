#!/usr/bin/env bash
# Coleta noite LOCAL — 21:00 BRT (00:00 UTC)
# Python : magalu + amazon + leroy + dealers | prioridade: alta | páginas: 1
# Magalu voltou ao Python (curl_cffi) — substitui o scraper Node.js bloqueado por Akamai.

set -u

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

LOG="$PROJECT_DIR/logs/cron_local.log"
mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE=/tmp/rac_local_noite.lock

exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: coleta noite local já em execução (lock ativo). Abortando." >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Iniciando coleta noite LOCAL ===" >> "$LOG"

# ── Python: magalu + amazon + leroy + dealers + casasbahia (+ shopee) ──────
cd "$PROJECT_DIR"
source .venv/bin/activate
set -a; source .env; set +a
# Casas Bahia (curl_cffi + warm-up Akamai) expõe buy box via sellers[].
# Shopee só roda se houver sessão capturada (cookies SPC_*/csrftoken expiram).
PLATFORMS="magalu amazon leroy dealers casasbahia"
if [ -f "$PROJECT_DIR/utils/sessions/shopee.json" ]; then
    PLATFORMS="$PLATFORMS shopee"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Shopee: sessão encontrada — incluída na coleta" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Shopee: sem sessão — pulando (python utils/session_grabber.py --site shopee)" >> "$LOG"
fi
python main.py \
    --platforms $PLATFORMS \
    --pages 1 \
    --priority alta \
    >> "$LOG" 2>&1
EXIT_PYTHON=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$EXIT_PYTHON)" >> "$LOG"

if [ $EXIT_PYTHON -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta noite LOCAL concluída com erros (python=$EXIT_PYTHON) ===" >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta noite LOCAL concluída com sucesso ===" >> "$LOG"
