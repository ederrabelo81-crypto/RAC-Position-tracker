#!/usr/bin/env bash
# Instala (ou atualiza) as entradas de cron do import do PriceTrack na VM.
# Horário em UTC; Brasil = UTC-3 (sem horário de verão desde 2019).
#   06:00 BRT → 09:00 UTC  D-1 (definitivo, --force) — espelha pricetrack_daily.yml
#   :30 toda hora          refresh — hoje (--force) + cura buracos recentes
#                          (--gaps-only). Espelha pricetrack_intraday.yml (horário).
#
# A VM é o scheduler CONFIÁVEL: o cron agendado do GitHub Actions atrasa 2–6h em
# pico e pode importar o dia errado quando o relógio BRT vira. O refresh de hora
# em hora aqui garante Manhã/Tarde do PriceTrack frescos e tampa um D-1 perdido
# (ex.: 21/06) em até ~1h, sem depender do GH. O :30 evita coincidir com o D-1
# das 09:00 UTC (o flock abortaria um dos dois).
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

# D-1 (definitivo) às 06:00 BRT + refresh de hora em hora (no minuto :30)
CRON_IMPORT="0 9 * * * $IMPORT_SCRIPT $MARKER"
CRON_REFRESH="30 * * * * $IMPORT_SCRIPT refresh $MARKER"

remove_entries() {
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "Entrada PriceTrack removida do crontab."
}

install_entries() {
    # Lê crontab atual (ignora erro se vazio)
    EXISTING="$(crontab -l 2>/dev/null || true)"

    # Remove entrada antiga (idempotente)
    CLEANED="$(echo "$EXISTING" | grep -v "$MARKER" || true)"

    # Adiciona as novas (D-1 definitivo + refresh horário)
    NEW_CRONTAB="$(printf '%s\n%s\n%s\n' \
        "$CLEANED" "$CRON_IMPORT" "$CRON_REFRESH")"

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
    echo "  :30 de hora em hora   → refresh: hoje (--force) + cura buracos (--gaps-only)"
    echo ""
    echo "Pré-requisito: PRICETRACK_API_KEY + SUPABASE_URL + SUPABASE_KEY no $PROJECT_DIR/.env"
    echo "Log: $PROJECT_DIR/logs/cron_pricetrack.log"
    echo ""
    echo "Para remover: bash $0 --remove"
fi
