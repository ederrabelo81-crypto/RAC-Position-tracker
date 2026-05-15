#!/usr/bin/env bash
# Coleta manhã — 10:00 BRT (13:00 UTC)
# Plataformas: TODAS ativas | Prioridade: alta + media | Páginas: 2

set -u  # erro em variável não definida (sem set -e — git pull não-fatal)

# Caminhos hardcoded — evita ambiguidade no ambiente minimalista do cron
PROJECT_DIR="/home/ubuntu/rac-position-tracker"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/.venv/bin/activate"
set -a; source "$PROJECT_DIR/.env"; set +a

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

# 2. Python: Google Shopping + Amazon + Leroy + Dealers + Magalu
# ML removido deste VM — IP de datacenter Oracle é bloqueado pelo ML.
# ML roda apenas no PC local Windows do analista (IP residencial).
# Magalu voltou para Python via curl_cffi (TLS chrome impersonation — bypassa Akamai).
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python: magalu google_shopping amazon leroy dealers (2 páginas)..." >> "$LOG"
python main.py \
    --platforms magalu google_shopping amazon leroy dealers \
    --pages 2 \
    --priority alta media \
    >> "$LOG" 2>&1
PYTHON_EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$PYTHON_EXIT)" >> "$LOG"

# (Removido) Node.js Magalu — Puppeteer não bypassa o Akamai novo (JA3/JA4).
# Magalu agora roda como Python no comando acima. O sub-projeto magalu_shopee/
# permanece apenas para Shopee (requer sessão autenticada).

EXIT_CODE=$PYTHON_EXIT
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã concluída (python=$PYTHON_EXIT) ===" >> "$LOG"
exit $EXIT_CODE
