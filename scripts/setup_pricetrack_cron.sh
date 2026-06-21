#!/usr/bin/env bash
# Instala (ou atualiza) as entradas de cron do import do PriceTrack na VM.
# Horário em UTC; Brasil = UTC-3 (sem horário de verão desde 2019).
#   06:00 BRT → 09:00 UTC  D-1 (definitivo) — espelha pricetrack_daily.yml
#   13:10 BRT → 16:10 UTC  hoje (intra-dia, após a manhã) — pricetrack_intraday.yml
#   23:10 BRT → 02:10 UTC  hoje (intra-dia, após a tarde) — pricetrack_intraday.yml
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

# D-1 (definitivo) às 06:00 BRT + intra-dia (hoje) às 13:10 e 23:10 BRT
CRON_IMPORT="0 9 * * * $IMPORT_SCRIPT $MARKER"
CRON_INTRADAY_AM="10 16 * * * $IMPORT_SCRIPT today $MARKER"
CRON_INTRADAY_PM="10 2 * * * $IMPORT_SCRIPT today $MARKER"

remove_entries() {
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "Entrada PriceTrack removida do crontab."
}

install_entries() {
    # Lê crontab atual (ignora erro se vazio)
    EXISTING="$(crontab -l 2>/dev/null || true)"

    # Remove entrada antiga (idempotente)
    CLEANED="$(echo "$EXISTING" | grep -v "$MARKER" || true)"

    # Adiciona as novas (D-1 + 2 intra-dia)
    NEW_CRONTAB="$(printf '%s\n%s\n%s\n%s\n' \
        "$CLEANED" "$CRON_IMPORT" "$CRON_INTRADAY_AM" "$CRON_INTRADAY_PM")"

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
    echo "  06:00 BRT (09:00 UTC) → D-1 definitivo (dia anterior, --force)"
    echo "  13:10 BRT (16:10 UTC) → hoje, intra-dia (após a manhã)"
    echo "  23:10 BRT (02:10 UTC) → hoje, intra-dia (após a tarde)"
    echo ""
    echo "Pré-requisito: PRICETRACK_API_KEY + SUPABASE_URL + SUPABASE_KEY no $PROJECT_DIR/.env"
    echo "Log: $PROJECT_DIR/logs/cron_pricetrack.log"
    echo ""
    echo "Para remover: bash $0 --remove"
fi
