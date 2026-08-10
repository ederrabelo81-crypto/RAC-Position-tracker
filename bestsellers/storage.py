"""
bestsellers/storage.py — Persistência da série de mais vendidos.

Três destinos, em ordem de importância:

  1. **CSV do dia** (`output/bestsellers/`) — evidência bruta da coleta,
     gravada ANTES de qualquer upload. Se o banco cair, o dado não se perde.
  2. **Master histórico** (`data/bestsellers/master_bestsellers.csv`) — a série
     acumulada. É ela que alimenta as agregações semanal e mensal.
  3. **Supabase** (tabela `bestsellers`) — opcional; falha de upload não
     derruba a coleta, apenas registra o erro.

O master é reescrito de forma idempotente: recoletar o mesmo dia substitui as
linhas daquele (data, plataforma) em vez de duplicá-las. Sem isso, uma
re-execução depois de conserto de parser inflaria a contagem de posições e o
KPI do dia sairia errado.
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from bestsellers.config import HISTORICO_PATH, OUTPUT_DIR
from bestsellers.models import COLUNAS

_TABELA_SUPABASE = "bestsellers"
_TAMANHO_LOTE = 500

# Colunas que são IDENTIFICADOR, nunca número — mesmo quando parecem um.
# Sem forçar o dtype, um `sku_plataforma` só de dígitos (itemid da Shopee,
# productId da VTEX) volta do CSV como float ("200000002.0"); o pareamento
# entre dias então não encontra item nenhum e a tabela de estabilidade fica
# vazia sem erro nenhum no log.
_DTYPES_TEXTO = {
    "sku_plataforma": "string",
    "run_id": "string",
    "data": "string",
    "horario": "string",
}


# ---------------------------------------------------------------------------
# DataFrame
# ---------------------------------------------------------------------------

def to_dataframe(linhas: List[Dict[str, Any]]) -> pd.DataFrame:
    """Constrói o DataFrame na ordem canônica, com todas as colunas presentes."""
    df = pd.DataFrame(linhas)
    for coluna in COLUNAS:
        if coluna not in df.columns:
            df[coluna] = None
    return df[COLUNAS]


# ---------------------------------------------------------------------------
# CSV do dia
# ---------------------------------------------------------------------------

def exportar_csv(df: pd.DataFrame, data: str, output_dir: str = OUTPUT_DIR) -> Path:
    """
    Grava o CSV bruto da coleta do dia.

    UTF-8 com BOM e separador `;` — mesma convenção do CSV da coleta
    principal, para abrir direto no Excel PT-BR.

    O arquivo do dia é MESCLADO por (data, plataforma), como o histórico:
    reprocessar só a Shopee não pode apagar as outras cinco plataformas da
    evidência bruta daquele dia.

    Returns:
        Caminho do arquivo gravado.
    """
    destino = Path(output_dir)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"bestsellers_{data}.csv"

    final = _mesclar_por_plataforma(_ler_csv(caminho), df)
    final.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")
    logger.success(f"[Bestsellers] CSV exportado: {caminho} ({len(final)} linhas)")
    return caminho


def _ler_csv(caminho: Path) -> pd.DataFrame:
    """CSV da série no formato canônico, ou vazio tipado quando não existe."""
    if not caminho.exists():
        return pd.DataFrame(columns=COLUNAS)
    try:
        lido = pd.read_csv(
            caminho, sep=";", encoding="utf-8-sig", dtype=_DTYPES_TEXTO
        )
    except Exception as exc:
        logger.error(f"[Bestsellers] Falha ao ler {caminho}: {exc}")
        return pd.DataFrame(columns=COLUNAS)
    for coluna in COLUNAS:
        if coluna not in lido.columns:
            lido[coluna] = None
    return lido[COLUNAS]


def _mesclar_por_plataforma(
    existente: pd.DataFrame, novo: pd.DataFrame
) -> pd.DataFrame:
    """
    Substitui em `existente` as linhas de cada (data, plataforma) de `novo`.

    A substituição é por par, e não pela data inteira, porque recoletar uma
    plataforma não pode apagar as outras do mesmo dia.
    """
    if len(existente) and len(novo):
        chaves = set(zip(novo["data"].astype(str), novo["plataforma"].astype(str)))
        manter = [
            chave not in chaves
            for chave in zip(
                existente["data"].astype(str), existente["plataforma"].astype(str)
            )
        ]
        substituidas = len(manter) - sum(manter)
        if substituidas:
            logger.info(
                f"[Bestsellers] {substituidas} linha(s) da mesma data/plataforma "
                "substituída(s) (recoleta)."
            )
        existente = existente[manter]

    return pd.concat([existente, novo], ignore_index=True).sort_values(
        ["data", "plataforma", "rank"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Master histórico
# ---------------------------------------------------------------------------

def carregar_historico(caminho: str = HISTORICO_PATH) -> pd.DataFrame:
    """
    Lê a série histórica acumulada.

    Returns:
        DataFrame com as colunas canônicas — vazio (mas tipado) quando o
        arquivo ainda não existe, para o caller não precisar checar None.
    """
    return _ler_csv(Path(caminho))


def atualizar_historico(
    df: pd.DataFrame, caminho: str = HISTORICO_PATH
) -> pd.DataFrame:
    """
    Acrescenta a coleta do dia ao master, substituindo o que já existia.

    A substituição é por (data, plataforma) e não pela data inteira: coletar
    de novo só a Shopee não pode apagar a Amazon do mesmo dia.

    Returns:
        O master atualizado.
    """
    master = _mesclar_por_plataforma(carregar_historico(caminho), df)

    arquivo = Path(caminho)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(arquivo, index=False, encoding="utf-8-sig", sep=";")
    logger.success(
        f"[Bestsellers] Histórico atualizado: {arquivo} "
        f"({len(master)} linhas, {master['data'].nunique()} datas)"
    )
    return master


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def _linha_supabase(registro: Dict[str, Any]) -> Dict[str, Any]:
    """Converte uma linha do DataFrame para o formato da tabela."""
    linha: Dict[str, Any] = {}
    for coluna in COLUNAS:
        valor = registro.get(coluna)
        # NaN do pandas e escalares numpy não sobrevivem à serialização JSON.
        if isinstance(valor, float) and math.isnan(valor):
            valor = None
        elif hasattr(valor, "item"):
            valor = valor.item()
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            valor = None
        linha[coluna] = valor

    for inteiro in ("rank", "btu", "reviews"):
        if linha.get(inteiro) is not None:
            try:
                linha[inteiro] = int(float(linha[inteiro]))
            except (TypeError, ValueError):
                linha[inteiro] = None
    for decimal in ("preco", "preco_de", "rating", "vendidos"):
        if linha.get(decimal) is not None:
            try:
                linha[decimal] = round(float(linha[decimal]), 2)
            except (TypeError, ValueError):
                linha[decimal] = None
    for booleano in ("grupo_midea", "no_escopo", "patrocinado"):
        if linha.get(booleano) is not None:
            linha[booleano] = bool(linha[booleano])
    # Título longo estoura o índice único do Postgres (~2700 bytes).
    if isinstance(linha.get("titulo"), str):
        linha["titulo"] = linha["titulo"][:500]
    return linha


def upload_supabase(df: pd.DataFrame) -> bool:
    """
    Envia a coleta do dia para a tabela `bestsellers`.

    Usa `upsert` na chave (data, plataforma, rank): uma recoleta do mesmo dia
    corrige as linhas em vez de duplicar o ranking.

    Returns:
        True em sucesso (ou nada a enviar); False se o upload falhou — o CSV
        e o histórico já foram gravados nesse ponto, então a coleta não se
        perde.
    """
    if not len(df):
        logger.info("[Bestsellers] Nenhum registro para enviar ao Supabase.")
        return True

    try:
        from utils.supabase_client import _get_client, is_quota_restricted_error
    except Exception as exc:
        logger.error(f"[Bestsellers] Client Supabase indisponível: {exc}")
        return False

    cliente = _get_client()
    if cliente is None:
        return False

    linhas = [_linha_supabase(r) for r in df.to_dict(orient="records")]
    total_lotes = math.ceil(len(linhas) / _TAMANHO_LOTE)
    enviadas = 0

    for indice in range(total_lotes):
        lote = linhas[indice * _TAMANHO_LOTE:(indice + 1) * _TAMANHO_LOTE]
        try:
            cliente.table(_TABELA_SUPABASE).upsert(
                lote, on_conflict="data,plataforma,rank"
            ).execute()
            enviadas += len(lote)
        except Exception as exc:
            if is_quota_restricted_error(exc):
                logger.error(
                    "[Bestsellers] Projeto Supabase restrito por cota de disco "
                    "— nenhum lote passará. Libere espaço ou faça upgrade."
                )
                return False
            if "does not exist" in str(exc).lower() or "PGRST205" in str(exc):
                logger.error(
                    f"[Bestsellers] Tabela `{_TABELA_SUPABASE}` não existe. "
                    "Aplique docs/migrations/011_bestsellers_diario.sql."
                )
                return False
            logger.error(f"[Bestsellers] Lote {indice + 1}/{total_lotes} falhou: {exc}")

    if enviadas:
        logger.success(
            f"[Bestsellers] Supabase: {enviadas}/{len(linhas)} linhas enviadas."
        )
    return enviadas == len(linhas)


def salvar_relatorio(texto: str, data: str, output_dir: str = OUTPUT_DIR) -> Path:
    """Grava o brief markdown ao lado do CSV do dia."""
    destino = Path(output_dir)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"brief_bestsellers_{data}.md"
    caminho.write_text(texto, encoding="utf-8")
    logger.success(f"[Bestsellers] Relatório: {caminho}")
    return caminho


def caminho_relatorio(nome: str, output_dir: str = OUTPUT_DIR) -> Optional[Path]:
    """Caminho de um relatório agregado (semanal/mensal), criando o diretório."""
    destino = Path(output_dir)
    destino.mkdir(parents=True, exist_ok=True)
    return destino / nome
