#!/usr/bin/env bash
# =============================================================================
# oracle_setup.sh — Configuração inicial da VM Oracle Cloud Free Tier (Ubuntu)
#
# Uso:
#   1. Conecte via SSH na VM:
#        ssh ubuntu@<IP_DA_VM>
#   2. Copie este script para a VM:
#        scp scripts/oracle_setup.sh ubuntu@<IP_DA_VM>:~/
#   3. Execute com suas credenciais:
#        chmod +x oracle_setup.sh
#        ./oracle_setup.sh \
#            --supabase-url "https://xxxx.supabase.co" \
#            --supabase-key "seu_service_role_key" \
#            --repo-url    "https://github.com/ederrabelo81-crypto/rac-position-tracker.git"
#
# O script assume Ubuntu 22.04 LTS (padrão Oracle Cloud Free Tier x86_64).
# =============================================================================

set -euo pipefail

# ─── Cores ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ─── Defaults ───────────────────────────────────────────────────────────────
SUPABASE_URL=""
SUPABASE_KEY=""
REPO_URL="https://github.com/ederrabelo81-crypto/rac-position-tracker.git"
REPO_BRANCH="main"
INSTALL_DIR="$HOME/rac-position-tracker"
PYTHON_MIN="3.11"

# ─── Parse argumentos ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --supabase-url)  SUPABASE_URL="$2";  shift 2 ;;
        --supabase-key)  SUPABASE_KEY="$2";  shift 2 ;;
        --repo-url)      REPO_URL="$2";      shift 2 ;;
        --branch)        REPO_BRANCH="$2";   shift 2 ;;
        --install-dir)   INSTALL_DIR="$2";   shift 2 ;;
        *) error "Argumento desconhecido: $1" ;;
    esac
done

[[ -z "$SUPABASE_URL" ]] && error "Forneça --supabase-url"
[[ -z "$SUPABASE_KEY" ]] && error "Forneça --supabase-key"

# ─── 1. Atualiza o sistema ───────────────────────────────────────────────────
info "Atualizando pacotes do sistema..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# ─── 2. Instala dependências básicas ────────────────────────────────────────
info "Instalando dependências básicas..."
sudo apt-get install -y -qq \
    git curl wget unzip \
    python3 python3-pip python3-venv python3-dev \
    build-essential libssl-dev libffi-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    fonts-liberation libappindicator3-1 xdg-utils

# Cria link python3 → python se não existir
if ! command -v python &>/dev/null; then
    sudo ln -sf "$(command -v python3)" /usr/local/bin/python
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
info "Python: $PYTHON_VERSION"

# ─── 2b. Configura swap de 2 GB (essencial para VMs com 1 GB RAM) ────────────
TOTAL_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
info "RAM disponível: ${TOTAL_RAM_MB} MB"
if [[ $TOTAL_RAM_MB -le 1200 && ! -f /swapfile ]]; then
    info "RAM ≤ 1.2 GB detectado — criando swapfile de 2 GB..."
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    # Persiste o swap após reboot
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    # Reduz swappiness para não usar swap desnecessariamente
    echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
    sudo sysctl vm.swappiness=10
    info "Swap de 2 GB ativado."
elif [[ -f /swapfile ]]; then
    info "Swapfile já existe — pulando."
fi

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

# ─── 4. Cria e ativa ambiente virtual ───────────────────────────────────────
info "Criando virtualenv em $INSTALL_DIR/.venv ..."
python3 -m venv .venv
source .venv/bin/activate

# ─── 5. Instala dependências Python ─────────────────────────────────────────
info "Instalando dependências Python (requirements.txt)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ─── 6. Instala Playwright + Chromium ────────────────────────────────────────
info "Instalando Playwright Chromium com dependências de sistema..."
playwright install chromium --with-deps

CHROMIUM_PATH=$(playwright install chromium 2>&1 | grep -oP '(?<=Chromium )\S+' || true)
info "Playwright Chromium OK"

# ─── 7. Cria .env com credenciais ───────────────────────────────────────────
info "Criando arquivo .env ..."
cat > "$INSTALL_DIR/.env" <<EOF
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_KEY=${SUPABASE_KEY}
EOF
chmod 600 "$INSTALL_DIR/.env"
info ".env criado com permissão restrita (600)"

# ─── 8. Cria diretórios de trabalho ─────────────────────────────────────────
mkdir -p "$INSTALL_DIR/output" "$INSTALL_DIR/logs"

# ─── 9. Cria wrappers de coleta ─────────────────────────────────────────────
info "Criando scripts de coleta..."

# Script de coleta manhã (10:00 BRT = 13:00 UTC)
cat > "$INSTALL_DIR/scripts/collect_manha_linux.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Coleta manhã — 10:00 BRT (13:00 UTC)
# Plataformas: ML + Google Shopping + Dealers | Prioridade: alta + media | Páginas: 2
cd "$(dirname "$(realpath "$0")")/.."
source .venv/bin/activate
set -a; source .env; set +a
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando coleta manha..." >> logs/cron.log
python main.py \
    --platforms ml google_shopping dealers \
    --pages 2 \
    --priority alta media \
    >> logs/cron.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Coleta manha concluida." >> logs/cron.log
SCRIPT

# Script de coleta noite (21:00 BRT = 00:00 UTC)
cat > "$INSTALL_DIR/scripts/collect_noite_linux.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Coleta noite — 21:00 BRT (00:00 UTC)
# Plataformas: ML + Google Shopping + Dealers | Prioridade: alta | Páginas: 1
cd "$(dirname "$(realpath "$0")")/.."
source .venv/bin/activate
set -a; source .env; set +a
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando coleta noite..." >> logs/cron.log
python main.py \
    --platforms ml google_shopping dealers \
    --pages 1 \
    --priority alta \
    >> logs/cron.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Coleta noite concluida." >> logs/cron.log
SCRIPT

chmod +x "$INSTALL_DIR/scripts/collect_manha_linux.sh"
chmod +x "$INSTALL_DIR/scripts/collect_noite_linux.sh"

# ─── 10. Instala cron jobs ───────────────────────────────────────────────────
info "Configurando cron jobs (horário UTC)..."

# Remove entradas antigas do RAC caso existam
crontab -l 2>/dev/null | grep -v "rac-position-tracker" > /tmp/crontab_clean || true

# Adiciona as duas coletas
cat >> /tmp/crontab_clean <<EOF

# RAC Price Tracker — coleta manhã  (10:00 BRT = 13:00 UTC)
0 13 * * * $INSTALL_DIR/scripts/collect_manha_linux.sh

# RAC Price Tracker — coleta noite  (21:00 BRT = 00:00 UTC)
0 0  * * * $INSTALL_DIR/scripts/collect_noite_linux.sh
EOF

crontab /tmp/crontab_clean
rm -f /tmp/crontab_clean

info "Cron jobs instalados:"
crontab -l | grep "rac-position-tracker"

# ─── 11. Script de monitoramento ────────────────────────────────────────────
cat > "$INSTALL_DIR/scripts/monitor.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Mostra as últimas 50 linhas do log de cron
tail -n 50 "$(dirname "$(realpath "$0")")/../logs/cron.log"
SCRIPT
chmod +x "$INSTALL_DIR/scripts/monitor.sh"

# ─── 12. Teste rápido de sanidade ───────────────────────────────────────────
info "Executando teste de sanidade (python -c import)..."
source .venv/bin/activate
python - <<'PYEOF'
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
page = b.new_page()
page.goto("https://example.com", timeout=15000)
title = page.title()
b.close(); p.stop()
assert "Example" in title, f"Título inesperado: {title}"
print("  Playwright OK — título:", title)
PYEOF

# ─── Resumo final ────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup concluído com sucesso!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Projeto instalado em : $INSTALL_DIR"
echo "  Cron jobs ativos     : 13:00 UTC (manhã) e 00:00 UTC (noite)"
echo ""
echo "  Comandos úteis:"
echo "    Ver logs            : $INSTALL_DIR/scripts/monitor.sh"
echo "    Testar coleta       : cd $INSTALL_DIR && source .venv/bin/activate"
echo "                          python main.py --platforms dealers --pages 1 --priority alta"
echo "    Cron jobs atuais    : crontab -l"
echo "    Atualizar projeto   : cd $INSTALL_DIR && git pull origin main"
echo ""
