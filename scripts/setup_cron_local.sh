#!/usr/bin/env bash
# Instala (ou atualiza) as entradas de cron para coleta local.
# Horários em UTC; Brasil = UTC-3 (sem horário de verão desde 2019).
#   10:00 BRT → 13:00 UTC
#   21:00 BRT → 00:00 UTC (meia-noite)
#
# Uso:
#   bash scripts/setup_cron_local.sh          # instala
#   bash scripts/setup_cron_local.sh --remove # remove

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MANHA_SCRIPT="$SCRIPT_DIR/collect_local_manha.sh"
NOITE_SCRIPT="$SCRIPT_DIR/collect_local_noite.sh"

# Garante permissão de execução
chmod +x "$MANHA_SCRIPT" "$NOITE_SCRIPT"

# Marcador para identificar as linhas gerenciadas por este script
MARKER="# RAC-local-cron"

CRON_MANHA="0 13 * * * $MANHA_SCRIPT $MARKER"
CRON_NOITE="0 0  * * * $NOITE_SCRIPT $MARKER"

remove_entries() {
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "Entradas RAC removidas do crontab."
}

install_entries() {
    # Lê crontab atual (ignora erro se vazio)
    EXISTING="$(crontab -l 2>/dev/null || true)"

    # Remove entradas antigas (idempotente)
    CLEANED="$(echo "$EXISTING" | grep -v "$MARKER" || true)"

    # Adiciona as novas
    NEW_CRONTAB="$(printf '%s\n%s\n%s\n' "$CLEANED" "$CRON_MANHA" "$CRON_NOITE")"

    echo "$NEW_CRONTAB" | crontab -

    echo "Crontab atualizado:"
    crontab -l | grep "$MARKER"
}

if [ "${1:-}" = "--remove" ]; then
    remove_entries
else
    install_entries
    echo ""
    echo "Coleta LOCAL agendada:"
    echo "  Manhã : 10:00 BRT (13:00 UTC) → amazon + leroy + dealers + magalu | 2 páginas"
    echo "  Noite : 21:00 BRT (00:00 UTC) → amazon + leroy + dealers + magalu | 1 página"
    echo ""
    echo "Log unificado: $PROJECT_DIR/logs/cron_local.log"
    echo ""
    echo "Para remover: bash $0 --remove"
fi
