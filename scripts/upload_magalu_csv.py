"""
scripts/upload_magalu_csv.py — Importa CSV gerado pelo scraper Node.js (magalu_shopee)
para a tabela `coletas` do Supabase.

Uso:
    python scripts/upload_magalu_csv.py magalu_shopee/data/rac_2026-05-05T14-02-17.csv
    python scripts/upload_magalu_csv.py magalu_shopee/data/*.csv
    python scripts/upload_magalu_csv.py magalu_shopee/data/rac_*.csv --dry-run
    python scripts/upload_magalu_csv.py magalu_shopee/data/rac_*.csv --turno Abertura
"""

import argparse
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.supabase_client import _get_client  # reutiliza client já configurado

_BRT = timedelta(hours=-3)
_BATCH_SIZE = 500

# Mapeamento keyword → categoria (espelho de magalu_shopee/src/config/queries.ts)
_CATEGORY_MAP: dict[str, str] = {
    "ar condicionado split":                    "Genérica",
    "ar condicionado inverter":                 "Genérica",
    "ar condicionado":                          "Genérica",
    "ar condicionado split inverter":           "Genérica",
    "ar condicionado 9000 btus":               "Capacidade BTU",
    "ar condicionado 12000 btus":              "Capacidade BTU",
    "ar condicionado 18000 btus":              "Capacidade BTU",
    "ar condicionado 24000 btus":              "Capacidade BTU",
    "ar condicionado 9000 btus inverter":      "Capacidade + Tipo",
    "ar condicionado 12000 btus inverter":     "Capacidade + Tipo",
    "split 12000 btus inverter":               "Capacidade + Tipo",
    "split 9000 btus inverter":                "Capacidade + Tipo",
    "ar condicionado midea":                   "Marca",
    "midea inverter":                          "Marca",
    "midea 12000 btus":                        "Marca",
    "ar condicionado midea 12000":             "Marca",
    "midea ecomaster":                         "Modelo Midea",
    "midea airvolution":                       "Modelo Midea",
    "ar condicionado lg":                      "Marca",
    "lg dual inverter":                        "Marca",
    "ar condicionado lg dual inverter 12000":  "Marca",
    "ar condicionado samsung":                 "Marca",
    "samsung windfree":                        "Marca",
    "ar condicionado gree":                    "Marca",
    "ar condicionado elgin":                   "Marca",
    "ar condicionado philco":                  "Marca",
    "ar condicionado tcl":                     "Marca",
    "melhor ar condicionado custo benefício":  "Intenção Compra",
    "melhor ar condicionado 2026":             "Intenção Compra",
    "comprar ar condicionado":                 "Intenção Compra",
    "ar condicionado em promoção":             "Preço / Promoção",
}


def _derive_run_id(csv_path: Path) -> str:
    """UUID v5 determinístico pelo nome do arquivo — re-importar é idempotente."""
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    return str(uuid.uuid5(namespace, csv_path.name))


def _parse_collected_at(raw: str) -> tuple[str, str, str]:
    """Retorna (data_brt, horario_brt, turno) a partir do timestamp ISO do Node.js."""
    try:
        dt_utc = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt_utc = datetime.now(tz=timezone.utc)

    dt_brt = dt_utc.astimezone(timezone(_BRT))
    data = dt_brt.strftime("%Y-%m-%d")
    horario = dt_brt.strftime("%H:%M")
    turno = "Abertura" if 5 <= dt_brt.hour < 18 else "Fechamento"
    return data, horario, turno


def _to_int(val) -> int | None:
    try:
        return int(float(val)) if val not in (None, "", "nan") else None
    except (ValueError, TypeError):
        return None


def _to_float(val) -> float | None:
    try:
        return float(val) if val not in (None, "", "nan") else None
    except (ValueError, TypeError):
        return None


def _to_bool(val) -> bool | None:
    if val is None or str(val).strip() == "":
        return None
    return str(val).strip().lower() in ("true", "1", "yes", "sim", "oficial")


def _map_row(row: dict, turno_override: str | None, run_id: str) -> dict:
    """Converte uma linha do CSV Node.js para o schema da tabela coletas."""
    raw_ts = str(row.get("Data Coleta", ""))
    data, horario, turno_auto = _parse_collected_at(raw_ts)
    turno = turno_override or turno_auto

    keyword = str(row.get("Query Buscada", "") or "").strip().lower()

    return {
        "data":              data,
        "turno":             turno,
        "horario":           horario,
        "plataforma":        str(row.get("Marketplace", "Magalu")).strip(),
        "tipo":              "Marketplace",
        "keyword":           str(row.get("Query Buscada", "") or "").strip(),
        "categoria":         _CATEGORY_MAP.get(keyword, "Genérica"),
        "marca":             str(row.get("Marca", "") or "Desconhecida").strip() or "Desconhecida",
        "produto":           str(row.get("Produto", "") or "").strip(),
        "posicao_organica":  _to_int(row.get("Posição")),
        "posicao_patrocinada": None,
        "posicao_geral":     _to_int(row.get("Posição")),
        "preco":             _to_float(row.get("Preço Atual (R$)")),
        "seller":            str(row.get("Seller", "") or "").strip() or None,
        "fulfillment":       _to_bool(row.get("Oficial?")),
        "avaliacao":         _to_float(row.get("Avaliação")),
        "qtd_avaliacoes":    _to_int(row.get("Qtd Avaliações")),
        "tag":               None,
        "run_id":            run_id,
    }


def upload_csv(csv_path: Path, turno_override: str | None, dry_run: bool) -> bool:
    if not csv_path.exists():
        logger.error(f"Arquivo não encontrado: {csv_path}")
        return False

    run_id = _derive_run_id(csv_path)
    logger.info(f"Lendo: {csv_path.name} | run_id={run_id}")

    try:
        df = pd.read_csv(csv_path, sep=",", encoding="utf-8-sig", dtype=str)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, sep=",", encoding="latin-1", dtype=str)

    df = df.where(pd.notna(df), None)
    records = [_map_row(row, turno_override, run_id) for row in df.to_dict(orient="records")]

    if not records:
        logger.warning("Nenhum registro encontrado no CSV.")
        return True

    # Estatísticas rápidas
    plataformas = {r["plataforma"] for r in records}
    logger.info(f"{len(records)} registros | plataformas: {plataformas}")

    if dry_run:
        logger.info(f"[DRY-RUN] Primeira linha mapeada: {records[0]}")
        logger.info("[DRY-RUN] Nenhum dado enviado.")
        return True

    client = _get_client()
    if client is None:
        logger.error("Supabase client não disponível — verifique SUPABASE_URL e SUPABASE_KEY no .env")
        return False

    inserted = 0
    for i in range(0, len(records), _BATCH_SIZE):
        batch = records[i : i + _BATCH_SIZE]
        batch_num = i // _BATCH_SIZE + 1
        resp = client.table("coletas").insert(batch).execute()
        if hasattr(resp, "data") and resp.data:
            inserted += len(resp.data)
            logger.info(f"Batch {batch_num}: {len(resp.data)} registros inseridos")
        else:
            logger.error(f"Batch {batch_num}: falha — {getattr(resp, 'error', resp)}")
            return False

    logger.success(f"Total inserido: {inserted}/{len(records)} registros de {csv_path.name}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa CSV do scraper Node.js (magalu_shopee) para coletas no Supabase."
    )
    parser.add_argument("csv_files", nargs="+", metavar="CSV")
    parser.add_argument(
        "--turno",
        choices=["Abertura", "Fechamento"],
        help="Força um turno (padrão: derivado do horário do CSV).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida e mostra primeiro registro mapeado sem enviar ao Supabase.",
    )
    args = parser.parse_args()

    paths = [Path(f) for f in args.csv_files]
    results: dict[str, bool] = {}

    for csv_path in paths:
        results[csv_path.name] = upload_csv(csv_path, args.turno, args.dry_run)

    if len(paths) > 1:
        ok_count = sum(results.values())
        logger.info(f"\nResumo: {ok_count}/{len(paths)} arquivos processados.")
        for name, ok in results.items():
            logger.info(f"  {'✓' if ok else '✗'} {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
