"""
utils/history/csv_mirror.py — Espelha o CSV da coleta no Google Drive.

Por que existe
--------------
O histórico frio (``utils/history/store.py``) já leva **os dados** ao Drive em
Parquet, mas o Parquet não se abre no Excel e não serve para reprocessar via
``scripts/upload_csv.py``. O CSV que a coleta gera continuava **só no disco da
máquina que coletou** — e é exatamente essa máquina que o pipeline não controla
(notebook do analista, VM efêmera, runner do GitHub Actions).

Quando o Supabase está restrito por cota (HTTP 402, o caso de 31/07/2026), o
CSV local passa a ser a única cópia crua do dia. Espelhá-lo no Drive fecha esse
buraco: o arquivo sai da máquina no mesmo run que o produziu.

Onde vai
--------
Uma subpasta ``csv_coletas/`` ao lado dos datasets Parquet, dentro da mesma
pasta raiz (``GDRIVE_FOLDER_ID``)::

    RAC Position Tracker - Historico/
    ├── coletas/                              ← Parquet (leitura do dashboard)
    └── csv_coletas/
        └── rac_monitoramento_20260731_1713.csv   ← cru, abre no Excel

Contrato
--------
Nunca levanta: falha de rede, credencial ou dependência é logada e a coleta
segue. O CSV local já está salvo — o espelho é redundância, não etapa crítica.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

from utils.history.backends import GoogleDriveBackend, HistoryBackendError
from utils.history.store import resolve_backend_name

#: Subpasta dos CSVs crus no Drive. Nome distinto dos datasets Parquet
#: (``coletas``/``pricetrack``) para que a leitura do histórico nunca tente
#: interpretar um CSV como partição.
DATASET_CSV = "csv_coletas"

#: Teto de tamanho do espelho. Um CSV de coleta cheia tem ~2 MB; ordens de
#: magnitude acima disso indica arquivo errado, não coleta gorda.
_MAX_MB = 50


def csv_mirror_enabled() -> bool:
    """``True`` quando o espelho está ligado (``RAC_DRIVE_CSV``, default on)."""
    raw = os.getenv("RAC_DRIVE_CSV", "on").strip().lower()
    return raw not in ("off", "0", "false", "no")


def mirror_csv_to_drive(
    csv_path: Path | str,
    dataset: str = DATASET_CSV,
) -> Optional[str]:
    """Sobe uma cópia do CSV da coleta para o Drive.

    Args:
        csv_path: Arquivo gerado por ``_export_csv`` em ``main.py``.
        dataset: Subpasta de destino no Drive (default: ``csv_coletas``).

    Returns:
        A chave gravada (``csv_coletas/arquivo.csv``), ou ``None`` quando o
        espelho está desligado, o destino não é o Drive, ou o upload falhou.

    Note:
        Não levanta exceção — o CSV local é a fonte, o Drive é a redundância.
    """
    path = Path(csv_path)

    if not csv_mirror_enabled():
        logger.debug("[Drive] Espelho do CSV desligado (RAC_DRIVE_CSV=off).")
        return None

    if not path.is_file():
        logger.warning(f"[Drive] CSV inexistente, nada a espelhar: {path}")
        return None

    # Backend local = sem credencial do Drive neste host. Avisar é o ponto:
    # a coleta acaba de rodar e o dado está inteiro numa máquina só.
    if resolve_backend_name() != "drive":
        logger.warning(
            f"[Drive] CSV NÃO espelhado — o histórico deste host está em modo "
            f"local, então {path.name} existe só nesta máquina. Configure o "
            f"Drive com: python scripts/gdrive_setup.py --client-secrets <json>"
        )
        return None

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > _MAX_MB:
        logger.error(
            f"[Drive] {path.name} tem {size_mb:.1f} MB (teto: {_MAX_MB} MB) — "
            "espelho abortado. Confirme se é mesmo o CSV da coleta."
        )
        return None

    key = f"{dataset}/{path.name}"
    try:
        backend = GoogleDriveBackend(os.getenv("GDRIVE_FOLDER_ID", "").strip())
        backend.put(key, path.read_bytes())
    except HistoryBackendError as exc:
        logger.error(
            f"[Drive] Falha espelhando {path.name}: {exc} — o CSV local segue "
            f"intacto em {path}. Reenvie depois com: "
            f"python scripts/history_cli.py import-csv {path}"
        )
        return None
    except Exception as exc:  # dependência ausente, credencial corrompida…
        logger.error(f"[Drive] Espelho do CSV falhou ({type(exc).__name__}): {exc}")
        return None

    logger.success(
        f"[Drive] CSV espelhado: {key} ({size_mb:.1f} MB) — cópia crua fora "
        "da máquina que coletou."
    )
    return key
