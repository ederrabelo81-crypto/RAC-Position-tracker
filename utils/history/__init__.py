"""
utils/history — Histórico frio em Parquet (Google Drive ou disco).

Arquitetura híbrida (decidida em Jul/2026, após o incidente de cota):

    coleta ──┬─► Supabase  (janela QUENTE, ~15 dias — dashboard operacional)
             └─► Histórico (Parquet no Drive — TODO o histórico, para sempre)

A gravação é **dupla e independente**: se o Supabase estiver fora do ar ou
restrito por cota, o histórico ainda recebe o dia. Foi exatamente essa
independência que faltou entre 16/07 e 25/07, quando 9 dias de coleta ficaram
só nos artifacts do GitHub.

A leitura é **unificada**: `read_coletas()` costura a janela quente do Supabase
com o histórico frio e devolve um DataFrame só, no schema que o dashboard já
espera.

API pública::

    from utils.history import write_records, read_coletas, get_store

    write_records(all_records, run_id=RUN_ID)     # após a coleta
    df = read_coletas(date(2026, 1, 1), date.today())
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
from loguru import logger

from utils.history.backends import (
    GoogleDriveBackend,
    HistoryBackend,
    HistoryBackendError,
    LocalBackend,
)
from utils.history.store import (
    DATASET_COLETAS,
    DATASET_PRICETRACK,
    DEFAULT_HOT_WINDOW_DAYS,
    HistoryStore,
    HistoryStoreError,
    get_store,
    history_dir,
    hot_window_days,
    parse_key,
    partition_key,
    resolve_backend_name,
)

__all__ = [
    "DATASET_COLETAS",
    "DATASET_PRICETRACK",
    "DEFAULT_HOT_WINDOW_DAYS",
    "GoogleDriveBackend",
    "HistoryBackend",
    "HistoryBackendError",
    "HistoryStore",
    "HistoryStoreError",
    "LocalBackend",
    "get_store",
    "history_dir",
    "hot_window_days",
    "hot_window_start",
    "parse_key",
    "partition_key",
    "read_coletas",
    "resolve_backend_name",
    "write_records",
]


def hot_window_start(today: Optional[date] = None) -> date:
    """Primeiro dia que ainda pertence à janela quente do Supabase.

    Args:
        today: Data de referência (default: hoje).

    Returns:
        ``today - (hot_window_days() - 1)`` — de modo que uma janela de 15 dias
        inclua hoje e os 14 anteriores.

    Example:
        >>> hot_window_start(date(2026, 7, 25))   # com janela de 15 dias
        datetime.date(2026, 7, 11)
    """
    ref = today or date.today()
    return ref - timedelta(days=hot_window_days() - 1)


def write_records(
    records: Iterable[Dict[str, Any]],
    run_id: Optional[str] = None,
    dataset: str = DATASET_COLETAS,
    store: Optional[HistoryStore] = None,
    already_mapped: bool = False,
) -> List[str]:
    """Grava registros de coleta no histórico frio.

    Aplica o mesmo mapeamento usado no upload ao Supabase
    (`utils.supabase_client.map_record`), então as linhas do Parquet e as do
    banco são idênticas coluna a coluna.

    Args:
        records: Registros no formato interno do bot (ou já mapeados, ver
            `already_mapped`).
        run_id: Identificador da execução, gravado em cada linha.
        dataset: Dataset de destino.
        store: Store a usar. ``None`` = resolve do ambiente.
        already_mapped: ``True`` quando os registros já estão no schema do
            banco (caso da migração vinda do Supabase).

    Returns:
        Chaves das partições gravadas — lista vazia se não havia o que gravar.

    Note:
        Não levanta em falha de backend: registra o erro e devolve o que
        conseguiu gravar. Quem chama (a coleta) não deve morrer por causa do
        destino do histórico.
    """
    rows: List[Dict[str, Any]] = []
    if already_mapped:
        rows = [dict(r) for r in records]
    else:
        from utils.supabase_client import is_ac_row, map_record

        mapeadas = [map_record(r) for r in records]
        # Mesmo corte que o upload aplica antes de gravar no banco. Sem ele, o
        # frio guardaria linhas que o quente rejeita e os dois lados da união
        # divergiriam em conteúdo (o CSV bruto segue com tudo).
        rows = [r for r in mapeadas if is_ac_row(r)]
        descartadas = len(mapeadas) - len(rows)
        if descartadas:
            logger.info(
                f"[Histórico] filtro AC: {descartadas} registro(s) descartado(s) "
                f"— mesma regra do upload ao Supabase."
            )

    if run_id:
        for row in rows:
            row.setdefault("run_id", run_id)

    if not rows:
        logger.info("[Histórico] nada a gravar (0 registros).")
        return []

    target = store or get_store()
    try:
        keys = target.write_records(rows, dataset=dataset, run_id=run_id)
        logger.success(
            f"[Histórico] {len(rows)} linhas em {len(keys)} partição(ões) "
            f"→ {target.backend.describe}"
        )
        return keys
    except (HistoryStoreError, HistoryBackendError) as exc:
        logger.error(f"[Histórico] falha ao gravar em {target.backend.describe}: {exc}")

    # Fallback local. As credenciais do Drive só são exercitadas na PRIMEIRA
    # chamada de verdade, então uma falha de credencial/API aparece aqui — não
    # na construção do store. Sem esta rede de segurança, o dia se perderia
    # justamente no cenário que o histórico existe para cobrir.
    if store is not None or isinstance(target.backend, LocalBackend):
        return []
    try:
        local = get_store("local")
        keys = local.write_records(rows, dataset=dataset, run_id=run_id)
        logger.warning(
            f"[Histórico] gravado em DISCO como fallback ({local.backend.describe}) "
            f"— {len(rows)} linhas em {len(keys)} partição(ões). Sincronize com o "
            f"Drive depois de corrigir a credencial."
        )
        return keys
    except (HistoryStoreError, HistoryBackendError) as exc:
        logger.error(f"[Histórico] fallback local também falhou: {exc}")
        return []


def read_coletas(
    start: date,
    end: date,
    store: Optional[HistoryStore] = None,
    supabase_client: Optional[Any] = None,
    columns: Optional[Sequence[str]] = None,
    include_hot: bool = True,
) -> pd.DataFrame:
    """Lê `coletas` costurando histórico frio + janela quente do Supabase.

    O intervalo pedido é dividido em duas partes:

    * **frio** — do histórico em Parquet (Drive/disco), sempre consultado;
    * **quente** — dos últimos `hot_window_days()` dias, lido do Supabase
      quando um cliente é fornecido e a leitura funciona.

    Linhas duplicadas (um dia presente nos dois lados, típico durante a
    migração) são resolvidas com precedência do **Supabase**, que é o lado
    onde a automação Admin aplica normalização e de-para.

    Args:
        start: Primeiro dia (inclusivo).
        end: Último dia (inclusivo).
        store: Store do histórico. ``None`` = resolve do ambiente.
        supabase_client: Cliente Supabase já autenticado. ``None`` = só frio.
        columns: Projeção de colunas.
        include_hot: ``False`` lê apenas o histórico frio (útil em teste e
            quando o banco está restrito).

    Returns:
        DataFrame ordenado por (``data``, ``plataforma``); vazio se não há dado.
    """
    if start > end:
        start, end = end, start

    # `data` é a chave da precedência quente-sobre-frio. Se a projeção do
    # chamador não a inclui, os dois lados voltariam sem ela, a desduplicação
    # seria pulada em silêncio e os dias sobrepostos apareceriam duplicados.
    # Então ela entra na consulta e sai do resultado no fim.
    cols_efetivas: Optional[List[str]] = None
    remover_data = False
    if columns is not None:
        cols_efetivas = list(columns)
        if "data" not in cols_efetivas:
            cols_efetivas.append("data")
            remover_data = True

    target = store or get_store()
    try:
        cold = target.read(DATASET_COLETAS, start=start, end=end, columns=cols_efetivas)
    except (HistoryStoreError, HistoryBackendError) as exc:
        logger.error(f"[Histórico] leitura do frio falhou: {exc}")
        cold = pd.DataFrame()

    hot = pd.DataFrame()
    if include_hot and supabase_client is not None:
        # Janela relativa a `end`, não a hoje: durante a transição um período
        # antigo ainda pode estar no Supabase (a migração é manual/mensal), e
        # ancorar em hoje deixaria esses dias invisíveis.
        hot_start = max(start, hot_window_start(end))
        if hot_start <= end:
            hot = _read_hot(supabase_client, hot_start, end, cols_efetivas)

    if cold.empty:
        return _finalize(hot, drop_data=remover_data)
    if hot.empty:
        return _finalize(cold, drop_data=remover_data)

    # Precedência do quente: descarta do frio os dias que o Supabase devolveu.
    hot_days = set(hot["data"].dropna().unique()) if "data" in hot.columns else set()
    if hot_days and "data" in cold.columns:
        cold = cold[~cold["data"].isin(hot_days)]

    return _finalize(pd.concat([cold, hot], ignore_index=True), drop_data=remover_data)


def _read_hot(
    client: Any,
    start: date,
    end: date,
    columns: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Lê a janela quente do Supabase, paginando o teto de 1000 linhas do PostgREST.

    Erros são degradados para DataFrame vazio: com o projeto restrito por cota
    (HTTP 402) o relatório deve sair só com o histórico, não falhar.
    """
    select = ",".join(columns) if columns else "*"
    page = 1000
    rows: List[Dict[str, Any]] = []
    offset = 0
    try:
        while True:
            resp = (
                client.table("coletas")
                .select(select)
                .gte("data", start.isoformat())
                .lte("data", end.isoformat())
                .order("data", desc=False)
                .order("id", desc=False)
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as exc:
        from utils.supabase_client import is_quota_restricted_error

        if is_quota_restricted_error(exc):
            logger.warning(
                "[Histórico] Supabase restrito por cota — relatório sai só com "
                "o histórico frio."
            )
        else:
            logger.error(f"[Histórico] leitura da janela quente falhou: {exc}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
    return df


def _finalize(df: pd.DataFrame, drop_data: bool = False) -> pd.DataFrame:
    """Ordena e normaliza tipos numéricos do resultado unificado.

    Args:
        df: Resultado da união.
        drop_data: Remove a coluna ``data`` no fim — ela foi acrescentada só
            para permitir a desduplicação e não fazia parte da projeção pedida.
    """
    if df.empty:
        return df
    for col in ("posicao_organica", "posicao_patrocinada", "posicao_geral",
                "qtd_avaliacoes", "qtd_sellers"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("preco", "avaliacao"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    sort_cols = [c for c in ("data", "plataforma") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    if drop_data and "data" in df.columns:
        df = df.drop(columns=["data"])
    return df
