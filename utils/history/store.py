"""
utils/history/store.py — Histórico frio em Parquet particionado por dia.

Modelo de dados
---------------
Uma partição = um arquivo Parquet imutável::

    coletas/data=2026-07-25__run-36abc8e6.parquet
    pricetrack/data=2026-07-25__run-manual.parquet

Imutável é a propriedade que faz o resto funcionar: o cache local nunca
invalida, a leitura nunca precisa travar nada e reprocessar um dia é escrever
um arquivo novo (não editar um existente).

Por que Parquet e não CSV/Sheets
--------------------------------
As colunas do RAC são altamente repetitivas (plataforma, keyword, marca,
seller). Com dicionário + compressão, o mesmo dia que ocupa ~9 MB no Postgres
cabe em centenas de KB — é o que torna "histórico ilimitado no Drive" viável
dentro dos 15 GB gratuitos.

Uso
---
    from utils.history import write_records, read_coletas

    write_records(all_records, run_id=RUN_ID)          # após a coleta
    df = read_coletas(date(2026, 1, 1), date(2026, 7, 25))
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
from loguru import logger

from utils.history.backends import (
    GoogleDriveBackend,
    HistoryBackend,
    HistoryBackendError,
    LocalBackend,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATASET_COLETAS = "coletas"
DATASET_PRICETRACK = "pricetrack"

#: Janela quente padrão: dias mantidos no Supabase antes de migrarem ao Drive.
DEFAULT_HOT_WINDOW_DAYS = 15

#: Tipos das colunas conhecidas de `coletas`. Colunas extras (id,
#: estado_match, sku_resolvido…) passam com o tipo inferido — o schema é
#: unificado na leitura.
_COLETAS_TYPES: Dict[str, str] = {
    "data": "string",
    "turno": "string",
    "horario": "string",
    "plataforma": "string",
    "tipo": "string",
    "keyword": "string",
    "categoria": "string",
    "marca": "string",
    "produto": "string",
    "produto_normalizado": "string",
    "posicao_organica": "Int64",
    "posicao_patrocinada": "Int64",
    "posicao_geral": "Int64",
    "patrocinado": "boolean",
    "buy_box_seller": "string",
    "qtd_sellers": "Int64",
    "tipo_seller": "string",
    "reputacao_seller": "string",
    "preco": "float64",
    "seller": "string",
    "fulfillment": "boolean",
    "avaliacao": "float64",
    "qtd_avaliacoes": "Int64",
    "tag": "string",
    "url_produto": "string",
    "screenshot_busca": "string",
    "screenshot_produto": "string",
    "run_id": "string",
}

_KEY_RE = re.compile(
    r"^(?P<dataset>[a-z_]+)/data=(?P<day>\d{4}-\d{2}-\d{2})__run-(?P<run>[^/]+)\.parquet$"
)


class HistoryStoreError(RuntimeError):
    """Falha de leitura/escrita do histórico (schema, backend, dependência)."""


def _require_pyarrow():
    """Importa pyarrow com mensagem acionável quando ausente."""
    try:
        import pyarrow  # noqa: F401
        return pyarrow
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise HistoryStoreError(
            "pyarrow não instalado — o histórico em Parquet precisa dele. "
            "Execute: pip install pyarrow"
        ) from exc


def hot_window_days() -> int:
    """Dias mantidos no Supabase antes da migração (``RAC_HOT_WINDOW_DAYS``)."""
    raw = os.getenv("RAC_HOT_WINDOW_DAYS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_HOT_WINDOW_DAYS


def partition_key(dataset: str, day: date, run_id: Optional[str] = None) -> str:
    """Monta a chave da partição.

    Args:
        dataset: ``coletas`` ou ``pricetrack``.
        day: Dia dos dados (não o dia da gravação).
        run_id: Identificador da execução; ``None`` vira ``manual``.

    Returns:
        Chave no formato ``<dataset>/data=<YYYY-MM-DD>__run-<run>.parquet``.

    Example:
        >>> partition_key("coletas", date(2026, 7, 25), "36abc8e6-ea19")
        'coletas/data=2026-07-25__run-36abc8e6.parquet'
    """
    run = (run_id or "manual").strip() or "manual"
    # Só o prefixo do UUID: o nome fica legível no Drive e continua único por run.
    run = re.sub(r"[^A-Za-z0-9_-]", "", run)[:8] or "manual"
    return f"{dataset}/data={day.isoformat()}__run-{run}.parquet"


def parse_key(key: str) -> Optional[Dict[str, Any]]:
    """Decompõe uma chave de partição, ou ``None`` se o formato não bate."""
    match = _KEY_RE.match(key)
    if not match:
        return None
    parts = match.groupdict()
    try:
        parts["day"] = datetime.strptime(parts["day"], "%Y-%m-%d").date()
    except ValueError:
        return None
    return parts


class HistoryStore:
    """Leitura e escrita do histórico frio.

    Args:
        backend: Destino dos bytes (``LocalBackend`` ou ``GoogleDriveBackend``).
        cache: Cache local para partições vindas de backend remoto. ``None``
            desliga o cache (o backend local dispensa).
    """

    def __init__(
        self,
        backend: HistoryBackend,
        cache: Optional[LocalBackend] = None,
    ) -> None:
        self.backend = backend
        self.cache = cache

    # -- escrita -----------------------------------------------------------
    def write_day(
        self,
        dataset: str,
        day: date,
        rows: Sequence[Dict[str, Any]],
        run_id: Optional[str] = None,
    ) -> Optional[str]:
        """Grava as linhas de um dia como uma partição Parquet.

        Args:
            dataset: ``coletas`` ou ``pricetrack``.
            day: Dia dos dados.
            rows: Linhas já no schema do banco (snake_case).
            run_id: Identificador da execução.

        Returns:
            A chave gravada, ou ``None`` se ``rows`` estava vazio.
        """
        if not rows:
            return None
        pa = _require_pyarrow()
        import pyarrow.parquet as pq

        df = _coerce_types(pd.DataFrame(list(rows)))
        table = pa.Table.from_pandas(df, preserve_index=False)

        import io
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd", use_dictionary=True)
        payload = buf.getvalue()

        key = partition_key(dataset, day, run_id)
        self.backend.put(key, payload)
        if self.cache is not None:
            self.cache.put(key, payload)
        logger.info(
            f"[Histórico] {key} — {len(df)} linhas, "
            f"{len(payload) / 1024:.0f} KB → {self.backend.describe}"
        )
        return key

    def write_records(
        self,
        rows: Iterable[Dict[str, Any]],
        dataset: str = DATASET_COLETAS,
        run_id: Optional[str] = None,
        date_column: str = "data",
    ) -> List[str]:
        """Agrupa linhas por dia e grava uma partição para cada.

        Args:
            rows: Linhas no schema do banco.
            dataset: Dataset de destino.
            run_id: Identificador da execução.
            date_column: Coluna que carrega o dia.

        Returns:
            Lista das chaves gravadas.
        """
        by_day: Dict[date, List[Dict[str, Any]]] = {}
        for row in rows:
            day = _as_date(row.get(date_column))
            if day is None:
                logger.warning(
                    f"[Histórico] linha sem '{date_column}' válido — ignorada."
                )
                continue
            by_day.setdefault(day, []).append(row)

        written: List[str] = []
        for day in sorted(by_day):
            key = self.write_day(dataset, day, by_day[day], run_id=run_id)
            if key:
                written.append(key)
        return written

    # -- leitura -----------------------------------------------------------
    def days(self, dataset: str = DATASET_COLETAS) -> List[date]:
        """Dias distintos presentes no histórico, ordenados."""
        days = set()
        for key in self.backend.list(dataset):
            parsed = parse_key(key)
            if parsed:
                days.add(parsed["day"])
        return sorted(days)

    def keys_in_range(
        self,
        dataset: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[str]:
        """Chaves cujo dia cai em ``[start, end]`` (limites inclusivos)."""
        selected = []
        for key in self.backend.list(dataset):
            parsed = parse_key(key)
            if not parsed:
                continue
            day = parsed["day"]
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            selected.append(key)
        return sorted(selected)

    def _local_path(self, key: str) -> Path:
        """Caminho local da partição, baixando do backend remoto se preciso.

        Partições são normalmente imutáveis, então o cache quase nunca é
        re-baixado. A exceção é **reprocessar a mesma run**: o arquivo remoto
        é sobrescrito e o cache de OUTRO host ficaria eternamente velho. Para
        cobrir isso, o MD5 remoto (que a listagem já traz de graça) é comparado
        com o do cache — divergiu, baixa de novo.
        """
        if isinstance(self.backend, LocalBackend):
            return self.backend.root / key
        if self.cache is None:
            raise HistoryStoreError(
                "Backend remoto sem cache local — defina RAC_HISTORY_DIR."
            )

        if self.cache.exists(key):
            remoto = self.backend.fingerprint(key)
            if remoto is None or remoto == self.cache.fingerprint(key):
                return self.cache.root / key
            logger.info(f"[Histórico] cache de {key} desatualizado — rebaixando.")

        self.cache.put(key, self.backend.get(key))
        return self.cache.root / key

    def read(
        self,
        dataset: str = DATASET_COLETAS,
        start: Optional[date] = None,
        end: Optional[date] = None,
        columns: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """Lê o histórico de um intervalo de dias.

        Args:
            dataset: Dataset a ler.
            start: Primeiro dia (inclusivo). ``None`` = desde o começo.
            end: Último dia (inclusivo). ``None`` = até o fim.
            columns: Subconjunto de colunas (projeção). ``None`` = todas.

        Returns:
            DataFrame com as linhas do período; vazio se não há partição.
            A coluna ``data`` volta como ``datetime.date``, igual ao que o
            dashboard recebe do Supabase.
        """
        keys = self.keys_in_range(dataset, start, end)
        if not keys:
            return pd.DataFrame()

        _require_pyarrow()
        import pyarrow.parquet as pq

        frames: List[pd.DataFrame] = []
        for key in keys:
            try:
                path = self._local_path(key)
                table = pq.read_table(path, columns=list(columns) if columns else None)
                frames.append(table.to_pandas())
            except HistoryBackendError as exc:
                # Uma partição ilegível não pode derrubar o relatório inteiro —
                # o dia entra faltando e o log diz qual.
                logger.error(f"[Histórico] partição ilegível {key}: {exc}")
            except Exception as exc:  # pyarrow levanta ArrowInvalid e afins
                logger.error(f"[Histórico] falha lendo {key}: {exc}")

        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)
        if "data" in df.columns:
            df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
        return df

    def drop_day(
        self,
        dataset: str,
        day: date,
        exceto: Optional[str] = None,
    ) -> int:
        """Remove as partições de um dia. Devolve quantas apagou.

        Args:
            dataset: Dataset alvo.
            day: Dia a limpar.
            exceto: Chave a preservar. Usado pela migração: a partição vinda do
                Supabase já resolvido **substitui** as que a coleta gravou
                naquele dia — sem isso as duas seriam lidas e o dia apareceria
                em dobro.

        Returns:
            Quantidade de partições removidas.
        """
        removed = 0
        for key in self.keys_in_range(dataset, day, day):
            if exceto is not None and key == exceto:
                continue
            self.backend.delete(key)
            if self.cache is not None:
                self.cache.delete(key)
            removed += 1
        return removed


# ---------------------------------------------------------------------------
# Helpers de tipo
# ---------------------------------------------------------------------------
def _as_date(value: Any) -> Optional[date]:
    """Converte str/date/datetime/Timestamp em ``date``; ``None`` se inválido."""
    if value is None or value != value:  # None ou NaN
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica os tipos canônicos de `coletas` às colunas conhecidas.

    Colunas desconhecidas passam intactas — o histórico aceita colunas que o
    schema do banco ganhou depois (estado_match, sku_resolvido…) sem precisar
    de migração aqui.
    """
    out = df.copy()
    for col, dtype in _COLETAS_TYPES.items():
        if col not in out.columns:
            continue
        try:
            if dtype == "string":
                # `data` vira texto ISO para o Parquet ficar estável entre runs.
                out[col] = out[col].map(
                    lambda v: None if v is None or v != v else str(v)
                ).astype("string")
            elif dtype in ("Int64", "float64"):
                out[col] = pd.to_numeric(out[col], errors="coerce")
                if dtype == "Int64":
                    out[col] = out[col].astype("Int64")
            elif dtype == "boolean":
                out[col] = out[col].astype("boolean")
        except (TypeError, ValueError) as exc:
            logger.warning(f"[Histórico] coluna '{col}' não coagiu para {dtype}: {exc}")
    # Colunas object remanescentes viram string — Parquet não aceita object misto.
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(
                lambda v: None if v is None or v != v else str(v)
            ).astype("string")
    return out


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------
def history_dir() -> Path:
    """Diretório do histórico local / cache do Drive (``RAC_HISTORY_DIR``)."""
    raw = os.getenv("RAC_HISTORY_DIR", "").strip()
    return Path(raw) if raw else _PROJECT_ROOT / "data" / "history"


def resolve_backend_name() -> str:
    """Backend efetivo: ``drive`` ou ``local``.

    ``RAC_HISTORY_BACKEND`` decide explicitamente. No padrão (``auto``) usa o
    Drive quando há ``GDRIVE_FOLDER_ID`` configurado, senão cai no disco — de
    modo que a coleta nunca falha por falta de credencial.
    """
    choice = os.getenv("RAC_HISTORY_BACKEND", "auto").strip().lower()
    if choice in ("drive", "gdrive", "google_drive"):
        return "drive"
    if choice in ("local", "disk", "fs"):
        return "local"
    return "drive" if os.getenv("GDRIVE_FOLDER_ID", "").strip() else "local"


def get_store(backend_name: Optional[str] = None) -> HistoryStore:
    """Constrói o ``HistoryStore`` conforme o ambiente.

    Args:
        backend_name: Força ``local`` ou ``drive``. ``None`` = resolve do env.

    Returns:
        Store pronto para uso. Se o Drive foi pedido mas falha ao inicializar,
        cai no backend local e registra o motivo — perder o dado é pior que
        gravá-lo no lugar errado.
    """
    name = (backend_name or resolve_backend_name()).lower()
    cache_root = history_dir()

    if name == "drive":
        try:
            backend = GoogleDriveBackend(os.getenv("GDRIVE_FOLDER_ID", "").strip())
            return HistoryStore(backend, cache=LocalBackend(cache_root / "_cache"))
        except HistoryBackendError as exc:
            logger.error(
                f"[Histórico] Drive indisponível ({exc}) — gravando em disco "
                f"({cache_root}). Sincronize depois com scripts/history_sync.py."
            )

    return HistoryStore(LocalBackend(cache_root))
