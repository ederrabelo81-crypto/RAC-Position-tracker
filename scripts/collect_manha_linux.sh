#!/usr/bin/env bash
# Coleta manhã — 10:00 BRT (13:00 UTC)
# Plataformas: TODAS ativas | Prioridade: alta + media | Páginas: 2

set -u  # erro em variável não definida (sem set -e — git pull não-fatal)

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
source .venv/bin/activate
set -a; source .env; set +a

LOG="$PROJECT_DIR/logs/cron.log"
mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE=/tmp/rac_coleta_manha.lock

# Adquire lock exclusivo não-bloqueante (fd 9)
# Se outra instância estiver rodando, aborta sem duplicar dados no Supabase
exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: coleta manhã já em execução (lock ativo). Abortando." >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Iniciando coleta manhã ===" >> "$LOG"

# 1. Atualiza o repo (não-fatal — se falhar, segue com código local)
if git pull --ff-only origin main >> "$LOG" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] git pull OK — commit: $(git rev-parse --short HEAD)" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: git pull falhou — usando código local: $(git rev-parse --short HEAD)" >> "$LOG"
fi

# 2. Python: ML + Google Shopping + Amazon + Leroy + Dealers
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python: ml google_shopping amazon leroy dealers (2 páginas)..." >> "$LOG"
python main.py \
    --platforms ml google_shopping amazon leroy dealers \
    --pages 2 \
    --priority alta media \
    >> "$LOG" 2>&1
PYTHON_EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$PYTHON_EXIT)" >> "$LOG"

# 3. Node.js: Magalu (Puppeteer-stealth — bypass Akamai Bot Manager)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Node.js: magalu (2 páginas)..." >> "$LOG"
if command -v node &>/dev/null && [ -f "$PROJECT_DIR/magalu_shopee/node_modules/.bin/ts-node" ]; then
    cd "$PROJECT_DIR/magalu_shopee"
    node node_modules/.bin/ts-node src/index.ts --platforms magalu --pages 2 >> "$LOG" 2>&1
    NODE_EXIT=$?
    cd "$PROJECT_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Node.js Magalu concluído (exit=$NODE_EXIT)" >> "$LOG"
else
    NODE_EXIT=0
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: Node.js ou ts-node não encontrado — Magalu pulado" >> "$LOG"
fi

# Considera falha se Python falhou (Node.js é best-effort)
EXIT_CODE=$PYTHON_EXIT
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã concluída (python=$PYTHON_EXIT node=$NODE_EXIT) ===" >> "$LOG"
exit $EXIT_CODE
