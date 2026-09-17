#!/usr/bin/env python3
"""
scripts/upload_bestsellers_csv.py — Reenvia CSVs de Mais Vendidos para o banco.

Existe para o caso em que a coleta rodou e gravou CSV normalmente, mas o
upload ao banco falhou (rede fora, banco em somente-leitura por disco cheio no
Aiven, cota estourada no Supabase etc.) — `bestsellers/storage.upload_supabase`
já deixa claro nesses casos que "o CSV do dia e o histórico master JÁ estão
gravados", e este script é o "agora reenvia" correspondente.

Não escolhe o banco sozinho: chama `storage.upload_supabase`, que por sua vez
usa `utils.supabase_client._get_client()` — a MESMA regra de roteamento da
coleta (`RAC_DB_DSN` preenchido → Postgres novo/Aiven; vazio → Supabase). Rodar
este script sem `RAC_DB_DSN` configurado reenvia para o Supabase, não para o
Aiven — confira o log "destino:" abaixo antes de reenviar um volume grande.

Uso:
    # Tudo que tem em output/bestsellers/ (o comportamento padrão)
    python scripts/upload_bestsellers_csv.py

    # Só uma janela de datas
    python scripts/upload_bestsellers_csv.py --desde 2026-09-15 --ate 2026-09-17

    # Arquivo(s) específico(s)
    python scripts/upload_bestsellers_csv.py output/bestsellers/bestsellers_2026-09-17.csv

    # Também (ou só) o histórico master acumulado
    python scripts/upload_bestsellers_csv.py --historico --desde 2026-09-01

    # Só conferir o que seria enviado, sem gravar
    python scripts/upload_bestsellers_csv.py --dry-run

Códigos de saída:
    0  tudo enviado (ou nada a enviar)
    1  pelo menos um arquivo falhou
    2  erro de configuração/argumento (nenhum CSV encontrado)
"""

import argparse
import glob
import sys
from pathlib import Path
from typing import List

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bestsellers import storage  # noqa: E402
from bestsellers.config import HISTORICO_PATH, OUTPUT_DIR  # noqa: E402


def _configurar_log(nivel: str) -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=nivel)
    logger.add(
        "logs/upload_bestsellers_csv_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="1 day",
        retention="30 days",
        encoding="utf-8",
    )


def _anunciar_destino() -> None:
    """Loga PARA ONDE o upload vai antes de gravar — é a pergunta que motivou este script."""
    try:
        from utils.db import dsn_from_env, resolve_backend_name

        backend = resolve_backend_name()
    except Exception as exc:  # utils.db pode faltar em ambiente antigo
        logger.warning(f"[Upload] Não deu para resolver o backend via utils.db: {exc}")
        return

    if backend == "postgres":
        dsn = dsn_from_env()
        # Não loga o DSN inteiro (carrega senha) — só o host:porta, que basta
        # para confirmar "é o Aiven mesmo" sem vazar credencial no log. DSN em
        # URI (`postgresql://user:pass@host:port/db`) é o formato que os
        # provedores gerenciados entregam; fora isso, não arrisca parsear
        # formato keyword=value (a senha viria solta em `password=...`).
        if "@" in dsn and "://" in dsn:
            host = dsn.split("@", 1)[-1].split("/", 1)[0]
        else:
            host = "(DSN em formato não-URI — host omitido do log por segurança)"
        logger.info(f"[Upload] destino: Postgres direto (RAC_DB_DSN → {host})")
    else:
        logger.info(
            "[Upload] destino: Supabase (RAC_DB_DSN vazio nesta máquina — "
            "para reenviar ao Aiven, defina RAC_DB_DSN no .env antes de rodar)"
        )


def _coletar_arquivos(
    explicitos: List[str], diretorio: str, incluir_historico: bool
) -> List[Path]:
    """Resolve a lista de CSVs a enviar, sem repetir o mesmo caminho duas vezes."""
    vistos: List[Path] = []

    def _add(caminho: Path) -> None:
        resolvido = caminho.resolve()
        if resolvido not in [v.resolve() for v in vistos]:
            vistos.append(caminho)

    if explicitos:
        for bruto in explicitos:
            caminho = Path(bruto)
            if not caminho.exists():
                logger.error(f"[Upload] Arquivo não encontrado: {caminho}")
                continue
            _add(caminho)
    else:
        # Nenhum arquivo passado explicitamente: "tudo que tem no diretório" é
        # o comportamento padrão — é o que "todos os CSVs coletados aqui" pede.
        for bruto in sorted(glob.glob(str(Path(diretorio) / "bestsellers_*.csv"))):
            _add(Path(bruto))

    if incluir_historico:
        caminho_hist = Path(HISTORICO_PATH)
        if caminho_hist.exists():
            _add(caminho_hist)
        else:
            logger.warning(f"[Upload] --historico pedido mas {caminho_hist} não existe.")

    return vistos


def _filtrar_por_data(df: pd.DataFrame, desde: str, ate: str) -> pd.DataFrame:
    if not len(df) or (not desde and not ate):
        return df
    serie = df["data"].astype(str)
    mascara = pd.Series(True, index=df.index)
    if desde:
        mascara &= serie >= desde
    if ate:
        mascara &= serie <= ate
    return df[mascara]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Reenvia CSVs de Mais Vendidos já coletados para o banco ativo (Aiven ou Supabase).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "arquivos", nargs="*", default=[],
        help="CSV(s) específico(s). Sem isso, usa todos de --dir.",
    )
    parser.add_argument(
        "--dir", default=OUTPUT_DIR,
        help=f"Diretório com os CSVs diários (padrão: {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--historico", action="store_true",
        help=f"Também envia o histórico master acumulado ({HISTORICO_PATH}).",
    )
    parser.add_argument("--desde", default=None, metavar="YYYY-MM-DD", help="Filtra data >= .")
    parser.add_argument("--ate", default=None, metavar="YYYY-MM-DD", help="Filtra data <= .")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Só mostra o que seria enviado — não grava nada.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    _configurar_log(args.log_level)
    _anunciar_destino()

    arquivos = _coletar_arquivos(args.arquivos, args.dir, args.historico)
    if not arquivos:
        logger.error(
            f"[Upload] Nenhum CSV encontrado em {args.dir} "
            "(nem arquivo explícito, nem --historico)."
        )
        return 2

    logger.info(f"[Upload] {len(arquivos)} arquivo(s) na fila.")

    total_linhas = 0
    falhas: List[str] = []

    for caminho in arquivos:
        df = storage.carregar_historico(caminho=str(caminho))
        df = _filtrar_por_data(df, args.desde, args.ate)
        if not len(df):
            logger.info(f"[Upload] {caminho}: 0 linha(s) na janela — pulado.")
            continue

        datas = sorted(df["data"].astype(str).unique())
        logger.info(
            f"[Upload] {caminho}: {len(df)} linha(s), "
            f"{len(datas)} data(s) ({datas[0]}..{datas[-1]})."
        )

        if args.dry_run:
            total_linhas += len(df)
            continue

        if storage.upload_supabase(df):
            total_linhas += len(df)
        else:
            falhas.append(str(caminho))

    if args.dry_run:
        logger.success(f"[Upload] dry-run: {total_linhas} linha(s) seriam enviadas.")
        return 0

    if falhas:
        logger.error(
            f"[Upload] {len(falhas)}/{len(arquivos)} arquivo(s) falharam: "
            f"{', '.join(falhas)}"
        )
        return 1

    logger.success(f"[Upload] Concluído: {total_linhas} linha(s) enviadas de {len(arquivos)} arquivo(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
