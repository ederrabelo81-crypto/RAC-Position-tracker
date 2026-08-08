#!/usr/bin/env bash
# Instala (ou atualiza) as entradas de cron do import do PriceTrack na VM.
# Horário em UTC; Brasil = UTC-3 (sem horário de verão desde 2019).
#   06:00 BRT → 09:00 UTC  D-1 (definitivo, --force) — espelha pricetrack_daily.yml
#
# A VM é o scheduler CONFIÁVEL: o cron agendado do GitHub Actions atrasa 2–6h em
# pico e pode importar o dia errado quando o relógio BRT vira.
#
# O refresh HORÁRIO foi APOSENTADO em 08/08/2026. Ele criava um export por hora
# e, quando o run era morto antes de terminar, o export ficava órfão segurando
# um dos 3 slots da organização. Dois zumbis assim (um PENDING que nunca
# começou, um PROCESSING parado em 0% por 23h) travaram toda a importação com
# HTTP 429 — inclusive as manuais. O modo `refresh` continua disponível para
# uso MANUAL; o que saiu foi o agendamento de hora em hora.
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

# D-1 (definitivo) às 06:00 BRT — única entrada agendada.
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

    # Adiciona a nova (só o D-1 definitivo)
    NEW_CRONTAB="$(printf '%s\n%s\n' \
        "$CLEANED" "$CRON_IMPORT")"

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
