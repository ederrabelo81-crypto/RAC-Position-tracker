#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# RAC Position Tracker — Sync Linux / Oracle Cloud
# Sincroniza o repositório com o GitHub e atualiza TODAS as dependências
# (Python + Node.js). Execute após clonar e sempre que o repo mudar.
#
# Uso:
#   bash scripts/sync_linux.sh            # execução normal
#   bash scripts/sync_linux.sh --full     # reinstala browsers Playwright
#
# Requisitos: git, python3, node (18+), npm
# ════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_DIR/logs/sync.log"
FULL_INSTALL="${1:-}"

mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
ok()  { echo "    ✓ $*"; }
warn(){ echo "    ⚠ $*"; }
err() { echo "    ✗ $*"; exit 1; }

echo ""
echo "════════════════════════════════════════"
echo "  RAC — Sync Linux / Oracle Cloud"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"
echo ""

log "=== Iniciando sync Linux ==="

# ── 1. Git pull ─────────────────────────────────────────────────────────────
echo "[1/5] Atualizando repositório via git pull..."
if git pull --ff-only origin main >> "$LOG" 2>&1; then
    COMMIT=$(git rev-parse --short HEAD)
    ok "git pull OK — commit: $COMMIT"
    log "git pull OK — commit: $COMMIT"
else
    warn "git pull falhou (sem internet ou conflito). Usando código local: $(git rev-parse --short HEAD)"
    log "WARN: git pull falhou — usando código local"
fi

# ── 2. Ambiente virtual Python ───────────────────────────────────────────────
echo ""
echo "[2/5] Verificando ambiente virtual Python (.venv)..."
if [ ! -f ".venv/bin/activate" ]; then
    echo "      Criando novo ambiente virtual..."
    python3 -m venv .venv >> "$LOG" 2>&1
    ok ".venv criado"
    log ".venv criado"
else
    ok ".venv já existe"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── 3. Dependências Python ───────────────────────────────────────────────────
echo ""
echo "[3/5] Instalando/atualizando dependências Python..."
pip install --upgrade pip --quiet >> "$LOG" 2>&1
pip install -r requirements.txt --quiet >> "$LOG" 2>&1
ok "pip install OK"
log "pip install OK"

# Instala Playwright (--full reinstala browsers, padrão só instala se ausentes)
if [ "$FULL_INSTALL" = "--full" ] || ! python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); p.stop()" 2>/dev/null; then
    echo "      Instalando Playwright Chromium (pode demorar)..."
    python -m playwright install chromium >> "$LOG" 2>&1
    python -m playwright install-deps chromium >> "$LOG" 2>&1
    ok "Playwright chromium instalado"
    log "playwright install chromium OK"
else
    ok "Playwright já instalado"
fi

# ── 4. Dependências Node.js (magalu_shopee) ──────────────────────────────────
echo ""
echo "[4/5] Instalando/atualizando dependências Node.js (magalu_shopee)..."
if [ ! -f "magalu_shopee/package.json" ]; then
    warn "magalu_shopee/package.json não encontrado. Pulando Node.js."
else
    if ! command -v node &>/dev/null; then
        warn "Node.js não instalado. Instale Node 18+ e reexecute."
        log "WARN: node não encontrado"
    else
        NODE_VER=$(node --version)
        cd magalu_shopee
        npm install --silent >> "$LOG" 2>&1
        ok "npm install OK (Node $NODE_VER)"
        log "npm install OK — node $NODE_VER"
        cd "$PROJECT_DIR"
    fi
fi

# ── 5. Verificação final ─────────────────────────────────────────────────────
echo ""
echo "[5/5] Verificação final..."

# Python imports
if python -c "import playwright, pandas, supabase; print('OK')" >> "$LOG" 2>&1; then
    ok "Python imports OK"
else
    warn "Alguns imports Python falharam. Veja $LOG"
fi

# Node ts-node
if [ -f "magalu_shopee/node_modules/.bin/ts-node" ]; then
    ok "Node.js ts-node OK"
else
    warn "ts-node não encontrado — npm install pode ter falhado"
fi

# .env
if [ -f ".env" ]; then
    ok ".env presente"
else
    warn ".env não encontrado — copie .env.example e preencha as chaves"
fi

echo ""
echo "════════════════════════════════════════"
echo "  Sync concluído! $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"
echo ""
echo "  Próximo passo: ./scripts/collect_manha_linux.sh"
echo "  Log completo:  $LOG"
echo ""

log "=== Sync Linux concluído ==="
