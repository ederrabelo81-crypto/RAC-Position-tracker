#!/usr/bin/env bash
# =============================================================================
# racpricing_setup.sh — Configuração do PC local (IP residencial) Ubuntu Server
#
# Plataformas: ML + Google Shopping (precisam de IP residencial)
# Oracle VM cuida de: Magalu, Amazon, Leroy Merlin, Dealers
#
# Uso:
#   chmod +x racpricing_setup.sh
#   ./racpricing_setup.sh \
#       --supabase-url "https://xxxx.supabase.co" \
#       --supabase-key "seu_service_role_key" \
#       --telegram-token "7730291785:AAF..." \
#       --telegram-chat-id "336855871"
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

SUPABASE_URL=""
SUPABASE_KEY=""
TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""
REPO_URL="https://github.com/ederrabelo81-crypto/rac-position-tracker.git"
REPO_BRANCH="main"
INSTALL_DIR="$HOME/rac-position-tracker"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --supabase-url)    SUPABASE_URL="$2";    shift 2 ;;
        --supabase-key)    SUPABASE_KEY="$2";    shift 2 ;;
        --telegram-token)  TELEGRAM_TOKEN="$2";  shift 2 ;;
        --telegram-chat-id) TELEGRAM_CHAT_ID="$2"; shift 2 ;;
        --repo-url)        REPO_URL="$2";        shift 2 ;;
        --branch)          REPO_BRANCH="$2";     shift 2 ;;
        --install-dir)     INSTALL_DIR="$2";     shift 2 ;;
        *) error "Argumento desconhecido: $1" ;;
    esac
done

[[ -z "$SUPABASE_URL" ]] && error "Forneça --supabase-url"
[[ -z "$SUPABASE_KEY" ]] && error "Forneça --supabase-key"

# ─── 1. Atualiza o sistema ───────────────────────────────────────────────────
info "Atualizando pacotes do sistema..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# ─── 2. Instala dependências ─────────────────────────────────────────────────
info "Instalando dependências..."
sudo apt-get install -y -qq \
    git curl wget unzip \
    python3 python3-pip python3-venv python3-dev \
    build-essential libssl-dev libffi-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    fonts-liberation xdg-utils

if ! command -v python &>/dev/null; then
    sudo ln -sf "$(command -v python3)" /usr/local/bin/python
fi

info "Python: $(python3 --version)"

# ─── 3. Clona / atualiza repositório ────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Repositório já existe — atualizando..."
    git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
else
    info "Clonando repositório..."
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ─── 4. Cria virtualenv e instala dependências Python ───────────────────────
info "Criando virtualenv..."
python3 -m venv .venv
source .venv/bin/activate

info "Instalando dependências Python..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ─── 5. Instala Playwright + Chromium ────────────────────────────────────────
info "Instalando Playwright Chromium..."
playwright install chromium --with-deps
info "Playwright OK"

# ─── 6. Cria .env ────────────────────────────────────────────────────────────
info "Criando .env..."
cat > "$INSTALL_DIR/.env" <<EOF
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_KEY=${SUPABASE_KEY}
N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta
N8N_TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-336855871}
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
EOF
chmod 600 "$INSTALL_DIR/.env"

# ─── 7. Cria diretórios ──────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/output" "$INSTALL_DIR/logs"

# ─── 8. Cria scripts de coleta (IP residencial: ML + Google Shopping) ────────
info "Criando scripts de coleta para IP residencial..."

cat > "$INSTALL_DIR/scripts/collect_manha_linux.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Coleta manhã — PC residencial (racpricing) — 10:00 BRT
# Plataformas: ML + Google Shopping (precisam de IP residencial)
# Oracle VM cuida de: Magalu, Amazon, Leroy, Dealers
cd "$(dirname "$(realpath "$0")")/.."
source .venv/bin/activate
set -a; source .env; set +a
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando coleta manha (ML + Google)..." >> logs/cron.log
python main.py \
    --platforms ml google_shopping \
    --pages 2 \
    --priority alta media \
    >> logs/cron.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Coleta manha concluida." >> logs/cron.log
SCRIPT

cat > "$INSTALL_DIR/scripts/collect_noite_linux.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Coleta noite — PC residencial (racpricing) — 21:00 BRT
# Plataformas: ML + Google Shopping (precisam de IP residencial)
cd "$(dirname "$(realpath "$0")")/.."
source .venv/bin/activate
set -a; source .env; set +a
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando coleta noite (ML + Google)..." >> logs/cron.log
python main.py \
    --platforms ml google_shopping \
    --pages 1 \
    --priority alta \
    >> logs/cron.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Coleta noite concluida." >> logs/cron.log
SCRIPT

chmod +x "$INSTALL_DIR/scripts/collect_manha_linux.sh"
chmod +x "$INSTALL_DIR/scripts/collect_noite_linux.sh"

# ─── 9. Cron jobs (horário BRT — PC local com timezone correta) ──────────────
info "Configurando cron jobs (BRT)..."
crontab -l 2>/dev/null | grep -v "rac-position-tracker" > /tmp/crontab_clean || true

cat >> /tmp/crontab_clean <<EOF

# RAC Price Tracker — PC residencial (ML + Google Shopping)
# Timezone: America/Sao_Paulo (configurar com: sudo timedatectl set-timezone America/Sao_Paulo)
0 10 * * * $INSTALL_DIR/scripts/collect_manha_linux.sh
0 21 * * * $INSTALL_DIR/scripts/collect_noite_linux.sh
EOF

crontab /tmp/crontab_clean
rm -f /tmp/crontab_clean

info "Cron jobs instalados:"
crontab -l | grep "rac-position-tracker"

# ─── 10. Configura timezone para BRT ─────────────────────────────────────────
info "Configurando timezone para America/Sao_Paulo..."
sudo timedatectl set-timezone America/Sao_Paulo
timedatectl | grep "Time zone"

# ─── 11. Script de monitoramento ─────────────────────────────────────────────
cat > "$INSTALL_DIR/scripts/monitor.sh" <<'SCRIPT'
#!/usr/bin/env bash
tail -n 50 "$(dirname "$(realpath "$0")")/../logs/cron.log"
SCRIPT
chmod +x "$INSTALL_DIR/scripts/monitor.sh"

# ─── Resumo ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup racpricing concluído!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Plataformas : ML + Google Shopping (IP residencial)"
echo "  Cron        : 10:00 e 21:00 BRT"
echo "  Projeto     : $INSTALL_DIR"
echo ""
echo "  Próximos passos:"
echo "    Testar coleta : cd $INSTALL_DIR && source .venv/bin/activate"
echo "                    python main.py --platforms ml --pages 1"
echo "    Ver logs      : $INSTALL_DIR/scripts/monitor.sh"
echo ""
