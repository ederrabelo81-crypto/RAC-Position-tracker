#!/usr/bin/env bash
# Coleta manhã LOCAL — 10:00 BRT (13:00 UTC)
# Python  : amazon + leroy + dealers | prioridade: alta + media | páginas: 2
# Node.js : magalu                   | páginas: 2
# Ambos rodam em paralelo; script aguarda os dois antes de sair.

set -u

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NODE_DIR="$PROJECT_DIR/magalu_shopee"

LOG="$PROJECT_DIR/logs/cron_local.log"
mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE=/tmp/rac_local_manha.lock

exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: coleta manhã local já em execução (lock ativo). Abortando." >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Iniciando coleta manhã LOCAL ===" >> "$LOG"

# ── Python: amazon + leroy + dealers ────────────────────────────────────────
(
    cd "$PROJECT_DIR"
    source .venv/bin/activate
    set -a; source .env; set +a
    python main.py \
        --platforms amazon leroy dealers \
        --pages 2 \
        --priority alta media \
        >> "$LOG" 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$?)" >> "$LOG"
) &
PID_PYTHON=$!

# ── Node.js: Magalu ──────────────────────────────────────────────────────────
(
    cd "$NODE_DIR"
    export PATH="/opt/node22/bin:$PATH"
    set -a; source "$PROJECT_DIR/.env"; set +a
    node_modules/.bin/ts-node src/index.ts --platforms magalu --pages 2 \
        >> "$LOG" 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Node Magalu concluído (exit=$?)" >> "$LOG"
) &
PID_NODE=$!

# Aguarda ambos
wait $PID_PYTHON
EXIT_PYTHON=$?
wait $PID_NODE
EXIT_NODE=$?

if [ $EXIT_PYTHON -ne 0 ] || [ $EXIT_NODE -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã LOCAL concluída com erros (python=$EXIT_PYTHON node=$EXIT_NODE) ===" >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã LOCAL concluída com sucesso ===" >> "$LOG"
