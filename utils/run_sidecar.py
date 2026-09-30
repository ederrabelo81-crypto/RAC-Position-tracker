"""
utils/run_sidecar.py — O run_id de um CSV de coleta viaja junto com ele.

Por que existe (30/09/2026)
---------------------------
Todo turno do PC coletor chegava DUAS vezes em `coletas`: o `main.py` grava
com ``run_id`` UUID4 e, logo depois, `scripts/collect_local_authenticated.bat`
roda `scripts/upload_csv.py` como "reforço" — que derivava OUTRO run_id (UUID5
do nome do arquivo). Como ``run_id`` faz parte da chave única
``(data, turno, plataforma, keyword, produto, run_id)``, o upsert com
``ignore_duplicates`` não reconhecia a linha e inseria uma cópia. Conferido no
banco em 28–29/09/2026: dois run_ids por turno com o mesmo horário, e toda
plataforma coletada no PC pesando o dobro da Amazon (que vem do Actions, uma
run só) em qualquer contagem.

A correção é o CSV carregar a identidade da run que o gerou: o `main.py`
grava ``<csv>.run_id`` ao lado do CSV, e quem reenvia o arquivo reusa esse
id. O reforço vira o que sempre pretendeu ser — completa o que faltou, sem
duplicar o que já entrou.

Uso:
    from utils.run_sidecar import write_run_id, resolve_run_id
    write_run_id(csv_path, RUN_ID)            # main.py, logo após o CSV
    run_id, origem = resolve_run_id(csv_path) # upload_csv.py / history_cli.py
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

__all__ = [
    "sidecar_path",
    "write_run_id",
    "read_run_id",
    "derive_run_id",
    "resolve_run_id",
]

#: Namespace do UUID5 derivado do nome do arquivo (o mesmo que `upload_csv.py`
#: e `history_cli.py` sempre usaram — mudar invalidaria reimportações antigas).
_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def sidecar_path(csv_path: Path) -> Path:
    """Caminho do arquivo irmão: ``rac_monitoramento_X.csv`` → ``.run_id``.

    A extensão diferente mantém o arquivo fora dos curingas
    ``rac_monitoramento_*.csv`` dos scripts de upload e do espelho no Drive.
    """
    return Path(csv_path).with_suffix(".run_id")


def write_run_id(csv_path: Path, run_id: str) -> Optional[Path]:
    """Grava o run_id ao lado do CSV. Falha é absorvida (nunca derruba a coleta).

    Args:
        csv_path: CSV recém-exportado.
        run_id: UUID da execução que gerou o CSV.

    Returns:
        Caminho gravado, ou None se não deu para gravar.
    """
    destino = sidecar_path(csv_path)
    try:
        destino.write_text(f"{run_id}\n", encoding="utf-8")
        return destino
    except OSError as exc:
        logger.warning(
            f"[run_id] Não gravei {destino.name} ({exc}) — um reenvio deste CSV "
            "vai derivar outro run_id e DUPLICAR as linhas no banco."
        )
        return None


def read_run_id(csv_path: Path) -> Optional[str]:
    """Lê o run_id gravado ao lado do CSV; None se ausente ou inválido."""
    origem = sidecar_path(csv_path)
    if not origem.exists():
        return None
    try:
        bruto = origem.read_text(encoding="utf-8").strip()
        return str(uuid.UUID(bruto))
    except (OSError, ValueError) as exc:
        logger.warning(f"[run_id] {origem.name} ilegível ({exc}) — ignorado.")
        return None


def derive_run_id(csv_path: Path) -> str:
    """UUID5 determinístico pelo nome do arquivo (CSV sem arquivo irmão)."""
    return str(uuid.uuid5(_NAMESPACE, Path(csv_path).name))


def resolve_run_id(csv_path: Path) -> Tuple[str, str]:
    """run_id a usar ao (re)enviar um CSV.

    Returns:
        ``(run_id, origem)`` — origem ``"coleta"`` quando veio do arquivo irmão
        (mesma run do `main.py`, reenvio idempotente) ou ``"nome do arquivo"``
        quando foi derivado (CSV antigo, de artifact, ou sem o irmão).
    """
    gravado = read_run_id(csv_path)
    if gravado:
        return gravado, "coleta"
    return derive_run_id(csv_path), "nome do arquivo"
