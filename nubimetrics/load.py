"""Gravação: Supabase (tabelas pequenas da 023) + Parquet no Drive (histórico).

Regras:
- escrita SÓ com a chave ``service_role`` (`SUPABASE_KEY`) — a ``anon`` é
  recusada pela RLS e o ETL falha ALTO (a coleta de bestsellers já perdeu dias
  gravando com anon em silêncio);
- upsert idempotente pela chave natural: rodar 2× não duplica;
- a foto de anúncios troca inteira pela RPC ``nubi_substituir_anuncios``
  (TRUNCATE + INSERT numa transação — nada de DELETE, que incha o banco);
- o bruto do explorador vai para o Parquet (`dataset=nubimetrics`), nunca
  para o banco.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

DATASET_PARQUET = "nubimetrics"
_LOTE = 500

_CHAVES = {
    "nubi_mercado_mensal": "categoria_id,mes,tipo",
    "nubi_ranking_mensal": "categoria_id,mes,dimensao,posicao",
    "nubi_concorrente_diario": "data,concorrente",
}


class LoadError(RuntimeError):
    """O banco não aceitou a gravação (tabela ausente, chave anon, rede)."""


def cliente() -> Any:
    """Cliente do banco pelo adaptador do projeto (Supabase ou `RAC_DB_DSN`)."""
    from utils.supabase_client import _get_client

    c = _get_client()
    if c is None:
        raise LoadError("Banco não configurado: SUPABASE_URL/SUPABASE_KEY (service_role) ausentes.")
    return c


def upsert(client: Any, tabela: str, linhas: Sequence[Dict[str, Any]]) -> int:
    """Upsert em lotes pela chave natural da tabela."""
    if not linhas:
        return 0
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    # Todas as linhas com as MESMAS chaves: o PostgREST (e o adaptador
    # utils/db.py) montam o INSERT pelas colunas da 1ª linha do lote — um termo
    # de busca (sem receita) no início do lote zerava a receita das marcas.
    colunas = sorted({k for linha in linhas for k in linha})
    linhas = [{c: linha.get(c) for c in colunas} for linha in linhas]
    feitas = 0
    for i in range(0, len(linhas), _LOTE):
        lote = [{**linha, "atualizado_em": agora} for linha in linhas[i:i + _LOTE]]
        try:
            client.table(tabela).upsert(lote, on_conflict=_CHAVES[tabela]).execute()
        except Exception as exc:
            raise LoadError(
                f"{tabela}: banco recusou após {feitas} linha(s): {exc} — migração 023 "
                "aplicada? chave service_role no SUPABASE_KEY?"
            ) from exc
        feitas += len(lote)
    logger.success(f"[Nubimetrics] {tabela}: {feitas} linha(s)")
    return feitas


def mercado_sem_rebaixar_real(linhas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Previsão nunca ocupa o lugar do real: é outra chave (`tipo`), então os dois
    convivem; o painel prefere o real. Aqui só garante que não há duplicata no lote."""
    vistos: Dict[tuple, Dict[str, Any]] = {}
    for linha in linhas:
        vistos[(linha["categoria_id"], linha["mes"], linha["tipo"])] = linha
    return list(vistos.values())


def substituir_anuncios(client: Any, linhas: List[Dict[str, Any]]) -> int:
    """Troca a foto de anúncios inteira (RPC atômica da migração 023)."""
    if not linhas:
        logger.warning("[Nubimetrics] explorador vazio — foto anterior mantida.")
        return 0
    lote: Any = linhas
    if type(client).__name__ == "PostgresClient":
        # Adaptador psycopg2 (utils/db.py): lista Python viraria ARRAY, não jsonb.
        from psycopg2.extras import Json

        lote = Json(linhas)
    try:
        resp = client.rpc("nubi_substituir_anuncios", {"p_linhas": lote}).execute()
    except Exception as exc:
        raise LoadError(f"nubi_substituir_anuncios recusou: {exc} — migração 023? service_role?") from exc
    n = resp.data if isinstance(resp.data, int) else len(linhas)
    logger.success(f"[Nubimetrics] nubi_anuncios: foto trocada ({n} anúncios)")
    return int(n)


def parquet(linhas: List[Dict[str, Any]], dia: dt.date, run_id: Optional[str] = None) -> Optional[str]:
    """Histórico frio do explorador (Drive se configurado; senão disco local).

    Best effort: falha aqui não derruba o ETL (o banco já tem a foto do dia).
    """
    if not linhas:
        return None
    try:
        from utils.history import get_store

        return get_store().write_day(DATASET_PARQUET, dia, linhas, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — histórico é complementar
        logger.warning(f"[Nubimetrics] Parquet não gravado: {exc}")
        return None
