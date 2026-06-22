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
#   refresh              → modo DE HORA EM HORA (recomendado p/ o cron horário):
#                          importa HOJE com --force (frescor) + cura buracos dos
#                          últimos 3 dias com --gaps-only (barato — só baixa o
#                          que estiver faltando, ex.: um D-1 perdido como 21/06).
#
# Todos rodam com --force/idempotência: o D-1 sobrescreve (upsert) qualquer
# linha provisória gravada antes pelo run intra-dia, e o intra-dia/refresh
# re-baixa o export do dia (que ainda cresce). pricetrack_api_import.py é
# idempotente (upsert ON CONFLICT), então rodar VM + GitHub Actions no mesmo
# dia não duplica dados.
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

# 2. Resolve datas em BRT (independe da timezone do SO da VM). Todas as datas
#    são calculadas em America/Sao_Paulo para não virarem o dia errado quando o
#    SO da VM estiver em UTC. PRICETRACK_API_KEY + SUPABASE_* vêm do .env.
TODAY=$(TZ='America/Sao_Paulo' date +%F)
YESTERDAY=$(TZ='America/Sao_Paulo' date -d 'yesterday' +%F)
HEAL_START=$(TZ='America/Sao_Paulo' date -d '3 days ago' +%F)

if [ "$MODE" = "refresh" ]; then
    # Hora em hora: frescor de hoje + cura barata de buracos recentes.
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Refresh PriceTrack — hoje=$TODAY (--force) ..." >> "$LOG"
    python scripts/pricetrack_api_import.py --start "$TODAY" --end "$TODAY" --force >> "$LOG" 2>&1
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Heal PriceTrack — $HEAL_START → $YESTERDAY (--gaps-only) ..." >> "$LOG"
    python scripts/pricetrack_api_import.py --start "$HEAL_START" --end "$YESTERDAY" --gaps-only >> "$LOG" 2>&1
    HEAL_EXIT=$?
    # Mantém o pior exit-code dos dois passos para o cron registrar falha real.
    if [ "$HEAL_EXIT" -ne 0 ]; then EXIT_CODE=$HEAL_EXIT; fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Refresh concluído (exit=$EXIT_CODE)" >> "$LOG"
else
    if [ "$MODE" = "today" ]; then
        TARGET="$TODAY"
        LABEL="intra-dia (hoje)"
    else
        TARGET="$YESTERDAY"
        LABEL="D-1 (ontem)"
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import PriceTrack $LABEL para $TARGET ..." >> "$LOG"
    python scripts/pricetrack_api_import.py --start "$TARGET" --end "$TARGET" --force >> "$LOG" 2>&1
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Import concluído (exit=$EXIT_CODE)" >> "$LOG"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Import PriceTrack concluído (exit=$EXIT_CODE) ===" >> "$LOG"
exit $EXIT_CODE
