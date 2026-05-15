#!/usr/bin/env bash
# Coleta manhã LOCAL — 10:00 BRT (13:00 UTC)
# Python : magalu + amazon + leroy + dealers | prioridade: alta + media | páginas: 2
# Magalu voltou ao Python (curl_cffi) — substitui o scraper Node.js bloqueado por Akamai.

set -u

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

LOG="$PROJECT_DIR/logs/cron_local.log"
mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE=/tmp/rac_local_manha.lock

exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: coleta manhã local já em execução (lock ativo). Abortando." >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Iniciando coleta manhã LOCAL ===" >> "$LOG"

# ── Python: magalu + amazon + leroy + dealers ──────────────────────────────
cd "$PROJECT_DIR"
source .venv/bin/activate
set -a; source .env; set +a
python main.py \
    --platforms magalu amazon leroy dealers \
    --pages 2 \
    --priority alta media \
    >> "$LOG" 2>&1
EXIT_PYTHON=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$EXIT_PYTHON)" >> "$LOG"

if [ $EXIT_PYTHON -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã LOCAL concluída com erros (python=$EXIT_PYTHON) ===" >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã LOCAL concluída com sucesso ===" >> "$LOG"
