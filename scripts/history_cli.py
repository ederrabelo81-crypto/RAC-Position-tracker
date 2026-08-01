"""
scripts/history_cli.py — CLI do histórico frio (Parquet no Drive/disco).

Subcomandos
-----------
    stats       Dias, linhas e tamanho do histórico
    import-csv  Carrega CSVs de coleta no histórico (recuperação de artifacts)
    tier        Migra do Supabase para o histórico o que saiu da janela quente
    export      Gera um CSV/relatório a partir do histórico (frio + quente)

Exemplos
--------
    # Onde está o histórico e o que ele já tem
    python scripts/history_cli.py stats

    # Recupera os dias que o Supabase recusou (CSVs baixados dos artifacts).
    # O curinga funciona em qualquer shell: quem expande é o próprio CLI,
    # porque PowerShell/cmd entregam o padrão literal ao Python.
    python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv

    # Migração: grava no histórico tudo com mais de 15 dias (sem apagar ainda)
    python scripts/history_cli.py tier
    # ... e agora apaga do Supabase o que já foi verificado no histórico
    python scripts/history_cli.py tier --confirm

    # Relatório de um período qualquer, atravessando frio + quente
    python scripts/history_cli.py export --start 2026-01-01 --end 2026-07-25 \\
        -o reports/historico.csv
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.history import (  # noqa: E402
    DATASET_COLETAS,
    HistoryBackendError,
    HistoryStoreError,
    get_store,
    history_dir,
    hot_window_days,
    hot_window_start,
    parse_key,
    read_coletas,
    write_records,
)

_SUPABASE_PAGE = 1000


def _parse_day(raw: str) -> date:
    """Converte ``YYYY-MM-DD`` em ``date`` (erro de CLI se inválido)."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"data inválida: {raw!r} — use o formato YYYY-MM-DD"
        ) from exc


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def cmd_stats(args: argparse.Namespace) -> int:
    """Imprime um panorama do histórico: backend, dias e volume."""
    store = get_store()
    logger.info(f"Backend: {store.backend.describe}")
    logger.info(f"Cache/dir local: {history_dir()}")
    logger.info(f"Janela quente (Supabase): {hot_window_days()} dias")

    keys = store.backend.list(DATASET_COLETAS)
    if not keys:
        logger.warning("Histórico vazio — nenhuma partição em 'coletas'.")
        return 0

    days = sorted({p["day"] for p in map(parse_key, keys) if p})
    logger.success(
        f"{len(keys)} partição(ões) · {len(days)} dia(s) · "
        f"{days[0].isoformat()} → {days[-1].isoformat()}"
    )

    if args.detail:
        df = store.read(DATASET_COLETAS)
        if not df.empty:
            por_dia = df.groupby("data").size()
            logger.info(f"Total de linhas: {len(df):,}")
            logger.info(f"Média por dia:   {por_dia.mean():,.0f}")
            if "plataforma" in df.columns:
                for plat, qtd in df["plataforma"].value_counts().items():
                    logger.info(f"  {plat}: {qtd:,}")

    # Buracos na série ajudam a decidir o que reimportar dos artifacts.
    faltando = [
        (days[0] + timedelta(days=i)).isoformat()
        for i in range((days[-1] - days[0]).days + 1)
        if (days[0] + timedelta(days=i)) not in set(days)
    ]
    if faltando:
        logger.warning(f"Dias sem partição no intervalo: {', '.join(faltando)}")
    return 0


# ---------------------------------------------------------------------------
# import-csv
# ---------------------------------------------------------------------------
def _derive_run_id(csv_path: Path) -> str:
    """run_id determinístico pelo nome do arquivo (reimportar é idempotente)."""
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    return str(uuid.uuid5(namespace, csv_path.name))


def cmd_import_csv(args: argparse.Namespace) -> int:
    """Carrega CSVs de coleta no histórico frio.

    É o caminho de recuperação quando o Supabase recusou a gravação: os CSVs
    dos artifacts do GitHub Actions entram no histórico sem passar pelo banco.
    """
    from scripts.upload_csv import _load_csv  # reaproveita aliases de coluna
    from utils.cli_args import expand_paths

    store = get_store()
    total = 0
    falhas = 0

    # PowerShell/cmd não expandem curingas — sem isto, `import-csv
    # output\rac_*.csv` no PC coletor morria com "Arquivo não encontrado".
    arquivos, sem_match = expand_paths(args.csv_files)
    for padrao in sem_match:
        logger.error(f"Nenhum arquivo casou com o padrão: {padrao}")
        falhas += 1

    for path in arquivos:
        if not path.exists():
            logger.error(f"Arquivo não encontrado: {path}")
            falhas += 1
            continue

        records = _load_csv(path)
        if not records:
            logger.warning(f"{path.name}: 0 registros — pulado.")
            continue

        run_id = args.run_id or _derive_run_id(path)
        if args.dry_run:
            logger.info(
                f"[DRY-RUN] {path.name}: {len(records)} registros, "
                f"run_id={run_id[:8]}"
                + (" (+ espelho do CSV no Drive)" if args.mirror else "")
            )
            total += len(records)
            continue

        keys = write_records(records, run_id=run_id, store=store)
        if not keys:
            logger.error(f"{path.name}: nenhuma partição gravada.")
            falhas += 1
            continue
        logger.success(f"{path.name}: {len(records)} linhas → {len(keys)} partição(ões)")
        total += len(records)

        # Backfill do CSV cru: o Parquet não abre no Excel nem volta por
        # upload_csv.py. Falha no espelho não invalida a partição já gravada.
        if args.mirror:
            from utils.history.csv_mirror import mirror_csv_to_drive
            if not mirror_csv_to_drive(path):
                logger.warning(f"{path.name}: partição gravada, espelho do CSV não.")

    logger.success(f"Total: {total:,} registros · {falhas} arquivo(s) com falha.")
    return 1 if falhas else 0


# ---------------------------------------------------------------------------
# tier
# ---------------------------------------------------------------------------
def _fetch_day(client: Any, day: date) -> List[Dict[str, Any]]:
    """Lê todas as linhas de `coletas` de um dia, paginando o teto do PostgREST."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        resp = (
            client.table("coletas")
            .select("*")
            .eq("data", day.isoformat())
            .order("id", desc=False)
            .range(offset, offset + _SUPABASE_PAGE - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < _SUPABASE_PAGE:
            return rows
        offset += _SUPABASE_PAGE


def _distinct_days_before(client: Any, cutoff: date) -> List[date]:
    """Dias com linhas em `coletas` anteriores a ``cutoff``.

    O PostgREST não tem DISTINCT, então partimos do dia mais antigo e
    contamos por dia — barato (`head=True` não traz linhas).
    """
    resp = (
        client.table("coletas")
        .select("data")
        .order("data", desc=False)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return []
    first_raw = resp.data[0].get("data")
    first = datetime.strptime(str(first_raw)[:10], "%Y-%m-%d").date()

    days: List[date] = []
    cursor = first
    while cursor < cutoff:
        count = (
            client.table("coletas")
            .select("id", count="exact", head=True)
            .eq("data", cursor.isoformat())
            .execute()
        ).count or 0
        if count:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def cmd_tier(args: argparse.Namespace) -> int:
    """Migra do Supabase para o histórico o que saiu da janela quente.

    Fluxo por dia: lê do banco → grava a partição → **verifica** relendo o
    Parquet → só então apaga do banco (e apenas com ``--confirm``).
    """
    from utils.supabase_client import _get_client, is_quota_restricted_error

    client = _get_client()
    if client is None:
        logger.error("Supabase não configurado — defina SUPABASE_URL/SUPABASE_KEY.")
        return 1

    cutoff = args.cutoff or hot_window_start()
    logger.info(
        f"Janela quente: {hot_window_days()} dias — migrando tudo anterior a "
        f"{cutoff.isoformat()}."
    )

    try:
        days = _distinct_days_before(client, cutoff)
    except Exception as exc:
        if is_quota_restricted_error(exc):
            logger.error(
                "Supabase RESTRITO por cota (HTTP 402): a API REST recusa até "
                "leitura, então a migração não pode rodar por aqui. Libere "
                "espaço primeiro pelo SQL Editor (scripts/retention_cleanup.sql) "
                "e rode este comando depois."
            )
            return 2
        logger.error(f"Falha listando dias a migrar: {exc}")
        return 1

    if not days:
        logger.success("Nada a migrar — o banco só tem dias da janela quente.")
        return 0

    logger.info(f"{len(days)} dia(s) a migrar: {days[0]} → {days[-1]}")
    store = get_store()
    migrados = 0
    apagados = 0
    falhas: List[str] = []

    for day in days:
        try:
            rows = _fetch_day(client, day)
        except Exception as exc:
            logger.error(f"{day}: falha lendo do Supabase: {exc}")
            falhas.append(f"{day} (leitura)")
            continue
        if not rows:
            continue

        if args.dry_run:
            logger.info(f"[DRY-RUN] {day}: {len(rows):,} linhas migrariam.")
            continue

        # Falha de escrita é isolada ao dia: uma indisponibilidade transitória
        # do Drive não pode abortar a migração dos dias seguintes.
        try:
            key = store.write_day(DATASET_COLETAS, day, rows, run_id=f"tier{day:%m%d}")
        except (HistoryStoreError, HistoryBackendError) as exc:
            logger.error(f"{day}: gravação falhou ({exc}) — banco intacto.")
            falhas.append(f"{day} (escrita)")
            continue
        if not key:
            logger.error(f"{day}: gravação da partição falhou — banco intacto.")
            falhas.append(f"{day} (escrita)")
            continue

        # Verificação antes de apagar: releia o que foi gravado.
        try:
            conferido = store.read(DATASET_COLETAS, start=day, end=day)
        except (HistoryStoreError, HistoryBackendError) as exc:
            logger.error(f"{day}: verificação falhou ({exc}) — banco intacto.")
            falhas.append(f"{day} (verificação)")
            continue
        if len(conferido) < len(rows):
            logger.error(
                f"{day}: verificação falhou ({len(conferido)} de {len(rows)} "
                f"linhas relidas) — NÃO vou apagar do Supabase."
            )
            falhas.append(f"{day} (verificação)")
            continue

        # A partição da migração vem do banco JÁ resolvido (estado_match,
        # família, SKU) e cobre todos os runs do dia — ela substitui as que a
        # coleta gravou. Sem esta limpeza o dia seria lido em dobro.
        try:
            supersedidas = store.drop_day(DATASET_COLETAS, day, exceto=key)
            if supersedidas:
                logger.info(
                    f"{day}: {supersedidas} partição(ões) da coleta substituída(s) "
                    f"pela versão resolvida."
                )
        except (HistoryStoreError, HistoryBackendError) as exc:
            logger.error(
                f"{day}: falha limpando partições antigas ({exc}) — o dia pode "
                f"ficar duplicado na leitura. NÃO vou apagar do Supabase."
            )
            falhas.append(f"{day} (limpeza)")
            continue

        migrados += 1
        logger.success(f"{day}: {len(rows):,} linhas no histórico ✓")

        if not args.confirm:
            continue
        try:
            client.table("coletas").delete().eq("data", day.isoformat()).execute()
            apagados += 1
            logger.success(f"{day}: removido do Supabase (espaço liberado).")
        except Exception as exc:
            logger.error(f"{day}: falha apagando do Supabase: {exc}")
            falhas.append(f"{day} (remoção)")

    logger.success(f"Migrados: {migrados} dia(s) · Apagados do banco: {apagados}")
    if migrados and not args.confirm and not args.dry_run:
        logger.warning(
            "Rode de novo com --confirm para apagar do Supabase os dias já "
            "verificados no histórico (é o que libera cota)."
        )
    if falhas:
        # Sai com código != 0 para que um cron/agendador não trate migração
        # parcial como sucesso.
        logger.error(
            f"{len(falhas)} dia(s) com falha: {', '.join(falhas)} — "
            f"esses dias continuam no Supabase."
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def cmd_export(args: argparse.Namespace) -> int:
    """Exporta um período do histórico (frio + janela quente) para CSV."""
    client = None
    if not args.cold_only:
        try:
            from utils.supabase_client import _get_client
            client = _get_client()
        except Exception as exc:
            logger.warning(f"Supabase indisponível ({exc}) — exportando só o frio.")

    df = read_coletas(args.start, args.end, supabase_client=client)
    if df.empty:
        logger.warning("Nenhuma linha no período pedido.")
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig", sep=";")
    dias = df["data"].nunique() if "data" in df.columns else 0
    logger.success(f"{len(df):,} linhas · {dias} dia(s) → {out}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Monta o parser com os quatro subcomandos."""
    parser = argparse.ArgumentParser(
        description="Histórico frio do RAC Position Tracker (Parquet no Drive/disco).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="Panorama do histórico.")
    p_stats.add_argument(
        "--detail", action="store_true",
        help="Lê as partições para contar linhas por dia e plataforma (mais lento).",
    )
    p_stats.set_defaults(func=cmd_stats)

    p_imp = sub.add_parser("import-csv", help="Carrega CSVs de coleta no histórico.")
    p_imp.add_argument("csv_files", nargs="+", metavar="CSV")
    p_imp.add_argument("--run-id", metavar="UUID", help="run_id fixo (default: pelo nome do arquivo).")
    p_imp.add_argument("--dry-run", action="store_true", help="Só relata; não grava.")
    p_imp.add_argument(
        "--mirror", action="store_true",
        help="Também sobe o CSV cru para o Drive (csv_coletas/) — backfill de "
             "coletas antigas que ficaram só no disco.",
    )
    p_imp.set_defaults(func=cmd_import_csv)

    p_tier = sub.add_parser("tier", help="Supabase → histórico (fora da janela quente).")
    p_tier.add_argument(
        "--cutoff", type=_parse_day, metavar="YYYY-MM-DD",
        help="Migra tudo ANTES desta data (default: início da janela quente).",
    )
    p_tier.add_argument(
        "--confirm", action="store_true",
        help="Apaga do Supabase os dias já verificados no histórico.",
    )
    p_tier.add_argument("--dry-run", action="store_true", help="Só relata; não grava nem apaga.")
    p_tier.set_defaults(func=cmd_tier)

    p_exp = sub.add_parser("export", help="Exporta um período para CSV.")
    p_exp.add_argument("--start", type=_parse_day, required=True, metavar="YYYY-MM-DD")
    p_exp.add_argument("--end", type=_parse_day, required=True, metavar="YYYY-MM-DD")
    p_exp.add_argument("-o", "--output", required=True, metavar="ARQUIVO.csv")
    p_exp.add_argument(
        "--cold-only", action="store_true",
        help="Ignora o Supabase e lê apenas o histórico frio.",
    )
    p_exp.set_defaults(func=cmd_export)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
