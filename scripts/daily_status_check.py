"""
scripts/daily_status_check.py — Validação diária do status de cada plataforma.

Consulta o Supabase pelos registros do dia (ou turno específico) e gera um
relatório PASS/WARN/FAIL por plataforma comparado aos thresholds mínimos.
Envia o resumo via Telegram (N8N webhook ou Bot API direto).

Uso:
    # Status do dia atual (ambos os turnos)
    python scripts/daily_status_check.py

    # Status de um turno específico
    python scripts/daily_status_check.py --turno Abertura
    python scripts/daily_status_check.py --turno Fechamento

    # Sem envio de notificação (só imprime no terminal)
    python scripts/daily_status_check.py --no-notify

    # Dia retroativo
    python scripts/daily_status_check.py --data 2026-05-14

Exit code:
    0 — todas as plataformas críticas PASS
    1 — pelo menos uma plataforma crítica WARN/FAIL
    2 — erro de configuração (Supabase indisponível, etc)
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config import ACTIVE_PLATFORMS
from utils.supabase_client import _get_client
from utils.text import now_brt


def _load_dealer_configs() -> Dict[str, Dict]:
    """Importa DEALER_CONFIGS sem triggar imports pesados (playwright, etc).

    scrapers/__init__.py importa eagerly todos os scrapers, e cada um traz
    Playwright via BaseScraper. Pulamos isso lendo o módulo direto pelo path.
    """
    import importlib.util
    dealers_path = Path(__file__).resolve().parent.parent / "scrapers" / "dealers.py"
    spec = importlib.util.spec_from_file_location("_dealers_isolated", dealers_path)
    if spec is None or spec.loader is None:
        return {}
    # NOTE: dealers.py também importa BaseScraper. Pra evitar isso, parseamos
    # manualmente o módulo procurando só o DEALER_CONFIGS dict.
    try:
        import ast
        tree = ast.parse(dealers_path.read_text(encoding="utf-8"))
        for node in tree.body:
            # `DEALER_CONFIGS: Dict[...] = {...}` é AnnAssign;
            # `DEALER_CONFIGS = {...}` é Assign.
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "DEALER_CONFIGS"
                    and node.value is not None
                ):
                    return ast.literal_eval(node.value)
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "DEALER_CONFIGS":
                        return ast.literal_eval(node.value)
    except Exception as exc:
        logger.warning(f"Falha ao extrair DEALER_CONFIGS via AST: {exc}")
    return {}


DEALER_CONFIGS = _load_dealer_configs()


# ---------------------------------------------------------------------------
# Thresholds — mínimo esperado de registros por plataforma+turno
# ---------------------------------------------------------------------------
# Valores conservadores baseados nos dias de coleta saudável (Mai/2026):
#   Abertura (2 páginas, prioridade alta+media): mais registros
#   Fechamento (1 página, prioridade alta):        menos registros
# Plataformas críticas (`critical=True`) entram no exit code 1 se falharem.

PLATFORM_NAME_MAP: Dict[str, str] = {
    "ml":              "Mercado Livre",
    "amazon":          "Amazon",
    "magalu":          "Magalu",
    "google_shopping": "Google Shopping",
    "leroy":           "Leroy Merlin",
}

# Thresholds: (min_abertura, min_fechamento, critical)
PLATFORM_THRESHOLDS: Dict[str, Tuple[int, int, bool]] = {
    "Mercado Livre":    (800, 500,  True),
    "Amazon":           (800, 400,  True),
    "Magalu":           (400, 300,  True),
    "Google Shopping":  (200, 200,  False),  # roda só Fechamento normalmente
    "Leroy Merlin":     (200, 200,  True),
}

# Dealers: cada um tem uma keyword (o nome do site) e poucos itens por turno.
# Threshold uniforme baixo — apenas valida que coletou algo.
DEALER_THRESHOLD: Tuple[int, int, bool] = (3, 3, False)


# ---------------------------------------------------------------------------
# Coleta dos dados
# ---------------------------------------------------------------------------

def _expected_platforms() -> List[str]:
    """Lista nomes de plataformas esperadas hoje (ativas no config)."""
    expected: List[str] = []
    for key, active in ACTIVE_PLATFORMS.items():
        if not active:
            continue
        if key == "dealers":
            # dealers ativos = todos não-on_hold
            expected.extend(
                name for name, cfg in DEALER_CONFIGS.items()
                if not cfg.get("on_hold")
            )
        elif key in PLATFORM_NAME_MAP:
            expected.append(PLATFORM_NAME_MAP[key])
    return expected


def _fetch_counts(
    data_str: str, turno: Optional[str]
) -> Dict[Tuple[str, str], int]:
    """
    Busca contagens de registros no Supabase.

    Returns:
        Dict[(plataforma, turno), count]
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("Supabase indisponível — verifique SUPABASE_URL/KEY no .env")

    query = client.table("coletas").select(
        "plataforma, turno"
    ).eq("data", data_str)

    if turno:
        query = query.eq("turno", turno)

    try:
        # Paginação manual: PostgREST limita a 1000 rows por default.
        # Aqui não importa o limite porque agregamos em Python depois.
        all_rows: List[Dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = query.range(offset, offset + page_size - 1).execute()
            rows = resp.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as exc:
        raise RuntimeError(f"Erro ao consultar Supabase: {exc}")

    counts: Dict[Tuple[str, str], int] = {}
    for row in all_rows:
        key = (row.get("plataforma") or "?", row.get("turno") or "?")
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Avaliação PASS/WARN/FAIL
# ---------------------------------------------------------------------------

def _evaluate(
    platform: str, turno: str, count: int
) -> Tuple[str, str, bool]:
    """
    Retorna (status, descrição_curta, é_crítica).
    status ∈ {"PASS", "WARN", "FAIL"}.
    """
    if platform in PLATFORM_THRESHOLDS:
        min_ab, min_fe, critical = PLATFORM_THRESHOLDS[platform]
    elif platform in DEALER_CONFIGS:
        min_ab, min_fe, critical = DEALER_THRESHOLD
    else:
        return "INFO", "fora do registry", False

    threshold = min_ab if turno == "Abertura" else min_fe

    if count == 0:
        return "FAIL", f"0 registros (esperado ≥{threshold})", critical
    if count < threshold:
        return "WARN", f"{count} <{threshold}", critical
    return "PASS", f"{count} ≥{threshold}", critical


def _build_report(
    data_str: str,
    turno_filter: Optional[str],
    counts: Dict[Tuple[str, str], int],
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Monta tabela de status por (plataforma, turno).

    Returns:
        (rows, summary) onde rows é lista de dicts com platform/turno/status/desc,
        e summary é dict com totais {pass, warn, fail, critical_fail}.
    """
    expected = _expected_platforms()
    turnos = [turno_filter] if turno_filter else ["Abertura", "Fechamento"]

    rows: List[Dict] = []
    summary = {"pass": 0, "warn": 0, "fail": 0, "critical_fail": 0}

    for turno in turnos:
        for platform in expected:
            count = counts.get((platform, turno), 0)
            status, desc, critical = _evaluate(platform, turno, count)
            rows.append({
                "platform": platform,
                "turno":    turno,
                "count":    count,
                "status":   status,
                "desc":     desc,
                "critical": critical,
            })
            key = status.lower()
            if key in summary:
                summary[key] += 1
            if status == "FAIL" and critical:
                summary["critical_fail"] += 1

    # Plataformas que coletaram dados mas NÃO estão no expected (ex: dealer
    # novo ou typo) — entram como INFO no relatório
    seen = {(r["platform"], r["turno"]) for r in rows}
    for (plat, turno), count in counts.items():
        if turno_filter and turno != turno_filter:
            continue
        if (plat, turno) not in seen:
            rows.append({
                "platform": plat,
                "turno":    turno,
                "count":    count,
                "status":   "INFO",
                "desc":     "não está em ACTIVE_PLATFORMS",
                "critical": False,
            })

    return rows, summary


# ---------------------------------------------------------------------------
# Formatação — terminal + Telegram HTML
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "PASS": "✅",
    "WARN": "⚠️",
    "FAIL": "❌",
    "INFO": "ℹ️",
}


def _print_terminal(
    data_str: str,
    turno_filter: Optional[str],
    rows: List[Dict],
    summary: Dict[str, int],
) -> None:
    title = f"STATUS COLETA {data_str}"
    if turno_filter:
        title += f" ({turno_filter})"
    print("\n" + "=" * 78)
    print(f"{title:^78}")
    print("=" * 78)
    fmt = "{:<22} {:<12} {:>7} {:<6} {:<30}"
    print(fmt.format("Plataforma", "Turno", "Reg", "St", "Detalhe"))
    print("-" * 78)

    # Ordena: FAIL críticos primeiro, depois WARN, depois PASS
    order_key = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
    sorted_rows = sorted(
        rows,
        key=lambda r: (order_key.get(r["status"], 9), not r["critical"], r["platform"]),
    )
    for r in sorted_rows:
        icon = _STATUS_ICON.get(r["status"], "?")
        flag = "★" if r["critical"] and r["status"] != "PASS" else " "
        print(fmt.format(
            r["platform"][:21] + flag,
            r["turno"],
            r["count"],
            f"{icon} {r['status']}",
            r["desc"][:30],
        ))
    print("-" * 78)
    print(
        f"Resumo: ✅ {summary['pass']} PASS | ⚠️ {summary['warn']} WARN | "
        f"❌ {summary['fail']} FAIL | crítico: {summary['critical_fail']}"
    )
    print("=" * 78 + "\n")


def _is_dealer(platform: str) -> bool:
    """True se a plataforma é dealer (não marketplace nacional)."""
    return platform in DEALER_CONFIGS


def _format_telegram(
    data_str: str,
    turno_filter: Optional[str],
    rows: List[Dict],
    summary: Dict[str, int],
) -> str:
    """Formata mensagem HTML pra Telegram.

    Estratégia:
      - Marketplaces (Amazon, ML, Magalu...): cada um aparece com status próprio
      - Dealers: agrupados — FAILs viram uma linha compacta com a lista dos nomes
        (evita spam de 20 linhas quando vários dealers menores falham)
    """
    import html as _html
    esc = _html.escape

    # Cabeçalho — emoji indica saúde geral
    if summary["critical_fail"] > 0:
        header_emoji = "🔴"
    elif summary["fail"] > 0 or summary["warn"] > 0:
        header_emoji = "🟡"
    else:
        header_emoji = "🟢"

    title = f"{header_emoji} <b>Status Coleta {esc(data_str)}</b>"
    if turno_filter:
        title += f" — {esc(turno_filter)}"

    lines: List[str] = [title, ""]

    # Agrupa por turno
    by_turno: Dict[str, List[Dict]] = {}
    for r in rows:
        by_turno.setdefault(r["turno"], []).append(r)

    for turno in sorted(by_turno.keys()):
        lines.append(f"<b>📅 {esc(turno)}</b>")

        # Separa marketplaces (alta visibilidade) de dealers (agrupados)
        marketplaces = [r for r in by_turno[turno] if not _is_dealer(r["platform"])]
        dealers      = [r for r in by_turno[turno] if _is_dealer(r["platform"])]

        # --- Marketplaces: linha por linha ---
        mk_order = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
        marketplaces.sort(key=lambda r: (mk_order.get(r["status"], 9), r["platform"]))
        for r in marketplaces:
            if r["status"] == "INFO":
                continue  # não pertence ao registry — silencioso
            icon = _STATUS_ICON[r["status"]]
            crit = " <b>[CRÍTICO]</b>" if r["critical"] and r["status"] != "PASS" else ""
            lines.append(
                f"  {icon} <code>{esc(r['platform'])}</code>: "
                f"{r['count']} reg — {esc(r['desc'])}{crit}"
            )

        # --- Dealers: agrupados por status ---
        dealer_by_status: Dict[str, List[Dict]] = {}
        for r in dealers:
            dealer_by_status.setdefault(r["status"], []).append(r)

        d_fail = dealer_by_status.get("FAIL", [])
        d_warn = dealer_by_status.get("WARN", [])
        d_pass = dealer_by_status.get("PASS", [])

        if d_fail:
            names = ", ".join(sorted(r["platform"] for r in d_fail))
            lines.append(
                f"  ❌ <i>Dealers sem dados ({len(d_fail)}):</i> {esc(names)}"
            )
        if d_warn:
            names = ", ".join(
                f"{r['platform']}({r['count']})"
                for r in sorted(d_warn, key=lambda r: r["platform"])
            )
            lines.append(
                f"  ⚠️ <i>Dealers abaixo do mínimo ({len(d_warn)}):</i> {esc(names)}"
            )
        if d_pass:
            lines.append(
                f"  ✅ <i>Dealers OK ({len(d_pass)})</i>"
            )

        lines.append("")

    lines.append(
        f"<b>Resumo:</b> ✅ {summary['pass']} | ⚠️ {summary['warn']} | "
        f"❌ {summary['fail']} | crítico: {summary['critical_fail']}"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Notificação Telegram (reusa infraestrutura do n8n_notify)
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> bool:
    """Envia via N8N webhook se configurado, fallback Bot API direto."""
    try:
        from utils.n8n_notify import _send  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning(f"Não foi possível importar utils.n8n_notify: {exc}")
        return False

    payload = {
        "event":   "daily_status",
        "message": message,
    }
    try:
        return _send(payload)
    except Exception as exc:
        logger.warning(f"Envio Telegram falhou: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida status diário das plataformas no Supabase",
    )
    parser.add_argument(
        "--data",
        help="Data YYYY-MM-DD (padrão: hoje BRT)",
        default=None,
    )
    parser.add_argument(
        "--turno",
        choices=["Abertura", "Fechamento"],
        default=None,
        help="Filtra por turno (padrão: ambos)",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Não envia Telegram, só imprime no terminal",
    )
    args = parser.parse_args()

    if args.data:
        data_str = args.data
    else:
        data_str = now_brt().strftime("%Y-%m-%d")

    logger.info(f"[daily_status] Validando coleta de {data_str} "
                f"(turno={args.turno or 'todos'})")

    try:
        counts = _fetch_counts(data_str, args.turno)
    except RuntimeError as exc:
        logger.error(f"[daily_status] {exc}")
        return 2

    rows, summary = _build_report(data_str, args.turno, counts)
    _print_terminal(data_str, args.turno, rows, summary)

    if not args.no_notify:
        msg = _format_telegram(data_str, args.turno, rows, summary)
        sent = _send_telegram(msg)
        if sent:
            logger.success("[daily_status] Notificação enviada ao Telegram.")
        else:
            logger.warning("[daily_status] Notificação Telegram não enviada "
                           "(N8N_WEBHOOK_URL / TELEGRAM_BOT_TOKEN ausentes?).")

    return 0 if summary["critical_fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
