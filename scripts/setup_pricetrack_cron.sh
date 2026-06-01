#!/usr/bin/env bash
# Instala (ou atualiza) a entrada de cron do import diário do PriceTrack na VM.
# Horário em UTC; Brasil = UTC-3 (sem horário de verão desde 2019).
#   06:00 BRT → 09:00 UTC  (espelha o GitHub Actions pricetrack_daily.yml)
#
# Uso:
#   bash scripts/setup_pricetrack_cron.sh          # instala
#   bash scripts/setup_pricetrack_cron.sh --remove # remove

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

IMPORT_SCRIPT="$SCRIPT_DIR/pricetrack_import_linux.sh"

# Garante permissão de execução
chmod +x "$IMPORT_SCRIPT"

# Marcador para identificar a linha gerenciada por este script
MARKER="# RAC-pricetrack-cron"

CRON_IMPORT="0 9 * * * $IMPORT_SCRIPT $MARKER"

remove_entries() {
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "Entrada PriceTrack removida do crontab."
}

install_entries() {
    # Lê crontab atual (ignora erro se vazio)
    EXISTING="$(crontab -l 2>/dev/null || true)"

    # Remove entrada antiga (idempotente)
    CLEANED="$(echo "$EXISTING" | grep -v "$MARKER" || true)"

    # Adiciona a nova
    NEW_CRONTAB="$(printf '%s\n%s\n' "$CLEANED" "$CRON_IMPORT")"

    echo "$NEW_CRONTAB" | crontab -

    echo "Crontab atualizado:"
    crontab -l | grep "$MARKER"
}

if [ "${1:-}" = "--remove" ]; then
    remove_entries
else
    install_entries
    echo ""
    echo "Import PriceTrack agendado:"
    echo "  Diário: 06:00 BRT (09:00 UTC) → importa a coleta do dia anterior para pricetrack_daily"
    echo ""
    echo "Pré-requisito: PRICETRACK_API_KEY + SUPABASE_URL + SUPABASE_KEY no $PROJECT_DIR/.env"
    echo "Log: $PROJECT_DIR/logs/cron_pricetrack.log"
    echo ""
    echo "Para remover: bash $0 --remove"
fi
