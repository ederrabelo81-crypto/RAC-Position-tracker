#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# RAC Position Tracker — Setup Tailscale no Linux Server
# Instala e ativa o Tailscale para acesso remoto de qualquer rede.
#
# Após rodar este script uma vez (em casa, conectado localmente),
# o servidor ficará acessível via PuTTY de qualquer lugar pelo IP Tailscale.
#
# Uso:
#   bash scripts/setup_tailscale_linux.sh
#
# Requisitos: acesso root (sudo), conexão com internet
# ════════════════════════════════════════════════════════════════════════════

set -euo pipefail

echo ""
echo "════════════════════════════════════════"
echo "  RAC — Setup Tailscale (Linux Server)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"
echo ""

# ── 1. Instala Tailscale ─────────────────────────────────────────────────────
echo "[1/4] Instalando Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh
echo "    ✓ Tailscale instalado"

# ── 2. Habilita inicialização automática no boot ─────────────────────────────
echo ""
echo "[2/4] Habilitando serviço no boot..."
sudo systemctl enable tailscaled
sudo systemctl start tailscaled
echo "    ✓ tailscaled ativo e habilitado"

# ── 3. Autentica e ativa (gera link para abrir no navegador) ──────────────────
echo ""
echo "[3/4] Autenticando com Tailscale..."
echo "      Um link vai aparecer abaixo — abra no navegador para fazer login."
echo "      Use a mesma conta (Google/GitHub) que usou no Windows."
echo ""
sudo tailscale up
echo ""
echo "    ✓ Tailscale autenticado"

# ── 4. Exibe IP Tailscale ─────────────────────────────────────────────────────
echo ""
echo "[4/4] IP Tailscale deste servidor:"
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "aguardando...")
echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │  IP: $TAILSCALE_IP"
echo "  └─────────────────────────────────────┘"
echo ""
echo "════════════════════════════════════════"
echo "  PRONTO!"
echo "════════════════════════════════════════"
echo ""
echo "  Anote o IP acima (100.x.x.x)"
echo ""
echo "  No PuTTY (de qualquer rede/WiFi):"
echo "    Host Name: $TAILSCALE_IP"
echo "    Port:      22"
echo ""
echo "  No Windows:"
echo "    1. Instale: https://tailscale.com/download/windows"
echo "    2. Login com a mesma conta usada aqui"
echo "    3. Conecte no PuTTY com o IP acima"
echo ""
