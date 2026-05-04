#!/usr/bin/env bash
# Coleta manhã — 10:00 BRT (13:00 UTC)
# Plataformas: TODAS ativas | Prioridade: alta + media | Páginas: 2

set -u  # erro em variável não definida (sem set -e — git pull não-fatal)

cd "$(dirname "$(realpath "$0")")/.."
source .venv/bin/activate
set -a; source .env; set +a

LOG=logs/cron.log
mkdir -p logs

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

# 2. Executa coleta
python main.py \
    --platforms ml magalu amazon google_shopping leroy dealers \
    --pages 2 \
    --priority alta media \
    >> "$LOG" 2>&1
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã concluída (exit=$EXIT_CODE) ===" >> "$LOG"
exit $EXIT_CODE
