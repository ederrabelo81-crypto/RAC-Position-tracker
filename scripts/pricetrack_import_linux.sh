#!/usr/bin/env bash
# Import diário do PriceTrack → Supabase (backup da VM Oracle do GitHub Actions).
#
# Espelha o workflow .github/workflows/pricetrack_daily.yml: importa a coleta
# do DIA ANTERIOR para a tabela pricetrack_daily, que o dashboard Streamlit lê
# com precedência por data. O script pricetrack_api_import.py é incremental e
# idempotente (date_exists pula datas já presentes; upsert ON CONFLICT), então
# rodar VM + GitHub Actions no mesmo dia não duplica dados.
#
# Agendar via: bash scripts/setup_pricetrack_cron.sh

set -u  # erro em variável não definida (sem set -e — git pull não-fatal)

# Caminhos hardcoded — evita ambiguidade no ambiente minimalista do cron
PROJECT_DIR="/home/ubuntu/rac-position-tracker"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/.venv/bin/activate"
set -a; source "$PROJECT_DIR/.env"; set +a

LOG="$PROJECT_DIR/logs/cron_pricetrack.log"
mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE=/tmp/rac_pricetrack_import.lock

# Adquire lock exclusivo não-bloqueante (fd 9)
# Se outra instância estiver rodando, aborta sem duplicar trabalho
exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: import PriceTrack já em execução (lock ativo). Abortando." >> "$LOG"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Iniciando import PriceTrack ===" >> "$LOG"

# 1. Atualiza o repo (não-fatal — se falhar, segue com código local)
if git pull --ff-only origin main >> "$LOG" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] git pull OK — commit: $(git rev-parse --short HEAD)" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: git pull falhou — usando código local: $(git rev-parse --short HEAD)" >> "$LOG"
fi

# 2. Import do dia anterior (PRICETRACK_API_KEY + SUPABASE_* vêm do .env)
YESTERDAY=$(date -d 'yesterday' +%F)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import PriceTrack para $YESTERDAY ..." >> "$LOG"
python scripts/pricetrack_api_import.py --start "$YESTERDAY" --end "$YESTERDAY" >> "$LOG" 2>&1
EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import concluído (exit=$EXIT_CODE)" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Import PriceTrack concluído (exit=$EXIT_CODE) ===" >> "$LOG"
exit $EXIT_CODE
