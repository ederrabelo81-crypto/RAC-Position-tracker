#!/usr/bin/env bash
# Import do PriceTrack → Supabase (backup da VM Oracle do GitHub Actions).
#
# Espelha .github/workflows/pricetrack_daily.yml + pricetrack_intraday.yml.
#
# Modos (1º argumento):
#   (vazio) | yesterday  → importa o DIA ANTERIOR (D-1), já fechado — definitivo.
#   today                → importa o DIA CORRENTE (provisório, intra-dia), para
#                          que o dashboard mostre os turnos Manhã/Tarde de hoje
#                          já vindos do PriceTrack em vez do fallback de Coletas.
#
# Ambos rodam com --force: o D-1 sobrescreve (upsert) qualquer linha provisória
# gravada antes pelo run intra-dia, e o intra-dia re-baixa o export do dia (que
# ainda cresce). pricetrack_api_import.py é idempotente (upsert ON CONFLICT),
# então rodar VM + GitHub Actions no mesmo dia não duplica dados.
#
# Agendar via: bash scripts/setup_pricetrack_cron.sh

set -u  # erro em variável não definida (sem set -e — git pull não-fatal)

MODE="${1:-yesterday}"

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

# 2. Resolve a data-alvo em BRT (independe da timezone do SO da VM; o run da
#    tarde roda às 23:10 BRT = 02:10 UTC do dia seguinte, então forçamos BRT).
if [ "$MODE" = "today" ]; then
    TARGET=$(TZ='America/Sao_Paulo' date +%F)
    LABEL="intra-dia (hoje)"
else
    TARGET=$(TZ='America/Sao_Paulo' date -d 'yesterday' +%F)
    LABEL="D-1 (ontem)"
fi

# Import com --force: D-1 finaliza/sobrescreve o provisório; intra-dia re-baixa
# o export do dia corrente (que ainda cresce). PRICETRACK_API_KEY + SUPABASE_*
# vêm do .env.
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import PriceTrack $LABEL para $TARGET ..." >> "$LOG"
python scripts/pricetrack_api_import.py --start "$TARGET" --end "$TARGET" --force >> "$LOG" 2>&1
EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import concluído (exit=$EXIT_CODE)" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Import PriceTrack concluído (exit=$EXIT_CODE) ===" >> "$LOG"
exit $EXIT_CODE
