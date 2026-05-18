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

# 2. Python: Amazon + Leroy + Dealers + Google Shopping + Magalu
# ML removido deste VM — IP de datacenter Oracle é bloqueado pelo ML.
# ML roda apenas no PC local Windows do analista (IP residencial).
# Ordem: scrapers estáveis primeiro (Amazon, Leroy, Dealers); scrapers
# baseados em browser persistente por último (Google Shopping, Magalu).
# Se Magalu/Google travar ou estourar timeout, Amazon/Leroy/Dealers já
# foram coletados — não bloqueiam a pipeline inteira como antes.
# (Bug Mai/13-17: Magalu travado deixou Amazon sem coletar por 5 dias.)
#
# xvfb-run cria display X virtual pro Chromium. Sem isso, sensor.js do
# Akamai (Magalu) detecta automação em headless e bloqueia /busca/ → 0
# produtos. MAGALU_HEADLESS=false força browser "visível" no display
# virtual — mesma estratégia já usada pro ML (commit ecd9b49).
# Instalar uma vez: sudo apt-get install -y xvfb
export MAGALU_HEADLESS=false
if command -v xvfb-run >/dev/null 2>&1; then
    XVFB_CMD=(xvfb-run -a --server-args="-screen 0 1366x768x24")
    XVFB_LABEL="on"
else
    # Fallback no-op: `env` sem args só executa o que vem depois.
    # Evita problemas com array vazio sob set -u em bash antigo.
    XVFB_CMD=(env)
    XVFB_LABEL="MISSING"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: xvfb-run não encontrado — Magalu provavelmente falhará (sensor.js Akamai). Instale: sudo apt-get install -y xvfb" >> "$LOG"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python: amazon leroy dealers google_shopping magalu (2 páginas, xvfb=$XVFB_LABEL)..." >> "$LOG"
"${XVFB_CMD[@]}" python main.py \
    --platforms amazon leroy dealers google_shopping magalu \
    --pages 2 \
    --priority alta media \
    >> "$LOG" 2>&1
PYTHON_EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python concluído (exit=$PYTHON_EXIT)" >> "$LOG"

# (Removido) Node.js Magalu — Puppeteer não bypassa o Akamai novo (JA3/JA4).
# Magalu agora roda como Python no comando acima. O sub-projeto magalu_shopee/
# permanece apenas para Shopee (requer sessão autenticada).

EXIT_CODE=$PYTHON_EXIT

# 3. Validação de status — consulta Supabase, envia status PASS/FAIL no Telegram
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Validação diária..." >> "$LOG"
python scripts/daily_status_check.py --turno Abertura >> "$LOG" 2>&1
DAILY_STATUS_EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Validação concluída (exit=$DAILY_STATUS_EXIT)" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Coleta manhã concluída (python=$PYTHON_EXIT, status=$DAILY_STATUS_EXIT) ===" >> "$LOG"
exit $EXIT_CODE
