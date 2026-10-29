"""
scripts/clima_collect.py — Radar meteorológico: coleta, histórico e clima × venda.

Fonte: Open-Meteo (gratuito, sem chave). ``archive-api`` (ERA5) para o
histórico; ``api`` para os últimos dias e a previsão de 16 dias. Lógica pura em
`utils/clima.py`; tabela `clima_diario` (migração 021).

Uso::

    # Diário (workflow clima_daily.yml): últimos 10 dias + previsão de 16
    python scripts/clima_collect.py --notificar

    # Histórico: uma vez, para a normal climatológica e a correlação longa
    python scripts/clima_collect.py --backfill --desde 2016-01-01

    # Clima × venda: proxy de Mais Vendidos (diário) e, opcional, série
    # mensal externa (GfK/sell-out: CSV com colunas mes;vendas)
    python scripts/clima_collect.py --correlacao
    python scripts/clima_collect.py --correlacao --vendas-csv gfk_mensal.csv

Escreve no Supabase (precisa da migração 021 e de ``SUPABASE_KEY``
service_role) E em ``data/clima/clima_diario.csv``: banco fora do ar ou sem a
tabela não perde a coleta.

Exit codes: 0 ok · 1 nenhuma cidade respondeu · 2 argumento inválido.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.clima import (  # noqa: E402
    BRASIL,
    CIDADES,
    COLUNAS,
    OBSERVADO,
    PREVISAO,
    VARIAVEIS_DIARIAS,
    Cidade,
    com_anomalia,
    consolidar,
    correlacao_defasada,
    correlacao_mensal,
    indice_regional,
    ler_veredito,
    parse_open_meteo,
    proxy_vendas_diario,
    sem_previsao_vencida,
)
from utils.text import now_brt  # noqa: E402

_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_CSV_PADRAO = Path(__file__).resolve().parent.parent / "data" / "clima" / "clima_diario.csv"
_TABELA = "clima_diario"
_LOTE = 500


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

def _get(url: str, params: dict, tentativas: int = 3) -> Optional[dict]:
    """GET com nova tentativa (429/5xx/rede). Nunca levanta: None = falhou."""
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[Clima] HTTP {resp.status_code}: {resp.text[:160]}")
            if resp.status_code < 500 and resp.status_code != 429:
                return None
        except requests.RequestException as exc:
            logger.warning(f"[Clima] Rede ({tentativa}/{tentativas}): {exc}")
        time.sleep(2 ** tentativa)
    return None


def _params(cidade: Cidade, **extra) -> dict:
    return {
        "latitude": cidade.lat,
        "longitude": cidade.lon,
        "daily": ",".join(VARIAVEIS_DIARIAS),
        "timezone": "America/Sao_Paulo",
        **extra,
    }


def coletar(backfill: bool, desde: date, ate: date, dias_passados: int, hoje: date) -> pd.DataFrame:
    """
    Baixa o clima de todas as cidades.

    Args:
        backfill: True usa o archive (ERA5) entre ``desde`` e ``ate``.
        desde/ate: janela do backfill.
        dias_passados: no modo diário, quantos dias passados pedir à API de
            previsão (cobre o atraso de ~5 dias do archive).
        hoje: dia corrente BRT (separa observado de previsão).

    Returns:
        Linhas consolidadas de todas as cidades que responderam.
    """
    partes: List[pd.DataFrame] = []
    for cidade in CIDADES:
        if backfill:
            resposta = _get(_ARCHIVE, _params(
                cidade, start_date=desde.isoformat(), end_date=ate.isoformat()))
            fonte = "archive"
        else:
            resposta = _get(_FORECAST, _params(
                cidade, past_days=dias_passados, forecast_days=16))
            fonte = "forecast"
        if resposta is None:
            logger.error(f"[Clima] {cidade.nome}: sem resposta")
            continue
        df = parse_open_meteo(resposta, cidade, hoje, fonte)
        logger.info(f"[Clima] {cidade.nome}: {len(df)} dia(s) ({fonte})")
        partes.append(df)
        time.sleep(0.3)  # gentileza com a API gratuita
    return consolidar(*partes)


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def ler_csv(caminho: Path) -> pd.DataFrame:
    """Histórico local (vazio se não existir)."""
    if not caminho.exists():
        return pd.DataFrame(columns=list(COLUNAS))
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
    df["data"] = pd.to_datetime(df["data"]).dt.date
    return df


def gravar_csv(novo: pd.DataFrame, caminho: Path) -> pd.DataFrame:
    """Mescla com o histórico local e regrava. Devolve o histórico inteiro."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tudo = consolidar(ler_csv(caminho), novo)
    tudo.to_csv(caminho, sep=";", index=False, encoding="utf-8-sig")
    logger.success(f"[Clima] CSV: {caminho} ({len(tudo):,} linhas)")
    return tudo


def _client():
    from utils.supabase_client import _get_client

    return _get_client()


def _sem_sobrescrever_archive(client, df: pd.DataFrame) -> pd.DataFrame:
    """
    Tira do lote observado-via-forecast as chaves que o banco já tem do archive.

    A chave é ``(data, cidade, tipo)``: a rodada diária pede 10 dias passados à
    API de previsão e os grava como observado — sem este filtro, o upsert
    trocaria a reanálise ERA5 (revisada) pelo dado do modelo de previsão.
    """
    alvo = df[(df["fonte"] != "archive") & (df["tipo"] == OBSERVADO)]
    if not len(alvo):
        return df
    resp = (
        client.table(_TABELA).select("data,cidade")
        .eq("fonte", "archive").eq("tipo", OBSERVADO)
        .gte("data", min(alvo["data"]).isoformat())
        .lte("data", max(alvo["data"]).isoformat())
        .limit(5000).execute()
    )
    protegidas = {(str(r["data"])[:10], r["cidade"]) for r in (resp.data or [])}
    if not protegidas:
        return df
    chave = df["data"].astype(str) + "|" + df["cidade"]
    bloquear = (
        (df["fonte"] != "archive") & (df["tipo"] == OBSERVADO)
        & chave.isin({f"{d}|{c}" for d, c in protegidas})
    )
    if bloquear.any():
        logger.info(f"[Clima] {int(bloquear.sum())} linha(s) já do archive preservadas.")
    return df[~bloquear]


def gravar_supabase(df: pd.DataFrame) -> int:
    """Upsert em `clima_diario`. Falha vira aviso: o CSV já guardou a coleta."""
    if not len(df):
        return 0
    client = _client()
    if client is None:
        logger.warning("[Clima] Supabase não configurado — só CSV.")
        return 0
    try:
        df = _sem_sobrescrever_archive(client, df)
    except Exception as exc:
        # Sem saber o que é archive, não arrisca: grava só a previsão.
        logger.warning(f"[Clima] Checagem do archive falhou ({exc}) — gravando só a previsão.")
        df = df[(df["fonte"] == "archive") | (df["tipo"] == PREVISAO)]
    # `atualizado_em` vai explícito: no upsert, coluna omitida mantém o valor
    # antigo, e o supervisor (pipeline_watch) conta a rodada por este carimbo.
    carimbo = datetime.now(timezone.utc).isoformat()
    registros = [
        {**{k: (None if pd.isna(v) else (v.isoformat() if isinstance(v, date) else v))
            for k, v in linha.items()},
         "atualizado_em": carimbo}
        for linha in df[list(COLUNAS)].to_dict("records")
    ]
    gravadas = 0
    try:
        for i in range(0, len(registros), _LOTE):
            lote = registros[i:i + _LOTE]
            client.table(_TABELA).upsert(lote, on_conflict="data,cidade,tipo").execute()
            gravadas += len(lote)
    except Exception as exc:
        logger.warning(
            f"[Clima] Supabase recusou ({gravadas} gravadas): {exc} — a tabela "
            "existe? docs/migrations/021_clima_diario.sql; chave service_role?"
        )
    else:
        logger.success(f"[Clima] Supabase: {gravadas:,} linha(s) em {_TABELA}")
    return gravadas


def ler_supabase(desde: date) -> pd.DataFrame:
    """Lê `clima_diario` desde ``desde`` (vazio em qualquer falha)."""
    client = _client()
    if client is None:
        return pd.DataFrame(columns=list(COLUNAS))
    linhas: List[dict] = []
    try:
        for offset in range(0, 500_000, 1000):
            resp = (
                client.table(_TABELA).select(",".join(COLUNAS))
                .gte("data", desde.isoformat()).order("data")
                .range(offset, offset + 999).execute()
            )
            lote = resp.data or []
            linhas.extend(lote)
            if len(lote) < 1000:
                break
    except Exception as exc:
        logger.warning(f"[Clima] Leitura do Supabase falhou: {exc}")
        return pd.DataFrame(columns=list(COLUNAS))
    df = pd.DataFrame(linhas, columns=list(COLUNAS))
    if len(df):
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for coluna in ("tmax", "tmin", "tmed", "sensacao_max", "precip_mm"):
            df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Leitura para o negócio
# ---------------------------------------------------------------------------

def resumo(historico: pd.DataFrame, hoje: date) -> str:
    """
    Texto curto: como foi a última semana e o que vem nos próximos 7 dias,
    por região, em anomalia contra a normal (quando há histórico para ela).
    """
    idx = indice_regional(com_anomalia(sem_previsao_vencida(historico, hoje)))
    if not len(idx):
        return "🌡️ Radar de clima: sem dado."
    idx["data"] = pd.to_datetime(idx["data"]).dt.date
    passado = idx[(idx["tipo"] == OBSERVADO) & (idx["data"] >= hoje - timedelta(days=7))]
    futuro = idx[(idx["tipo"] == PREVISAO) & (idx["data"] <= hoje + timedelta(days=7))]

    def _linha(df: pd.DataFrame, regiao: str) -> str:
        r = df[df["regiao"] == regiao]
        if not len(r):
            return "—"
        texto = f"máx {r['tmax'].mean():.1f}°C"
        if "anomalia_tmax" in r and r["anomalia_tmax"].notna().any():
            texto += f" ({r['anomalia_tmax'].mean():+.1f}°C vs normal)"
        return texto

    linhas = [f"🌡️ Radar de clima · {hoje:%d/%m/%Y}", "", "Região — últimos 7 dias | próximos 7 dias"]
    for regiao in (BRASIL, "Sudeste", "Sul", "Centro-Oeste", "Nordeste", "Norte"):
        linhas.append(f"• {regiao}: {_linha(passado, regiao)} | {_linha(futuro, regiao)}")
    tem_normal = "anomalia_tmax" in idx and idx["anomalia_tmax"].notna().any()
    if not tem_normal:
        linhas.append("")
        linhas.append(
            "Sem normal climatológica ainda — rode o backfill: "
            "python scripts/clima_collect.py --backfill --desde 2016-01-01"
        )
    return "\n".join(linhas)


def _ler_bestsellers(desde: date) -> pd.DataFrame:
    """Leituras de Mais Vendidos com contador de avaliações (para o proxy)."""
    client = _client()
    if client is None:
        return pd.DataFrame()
    linhas: List[dict] = []
    for offset in range(0, 500_000, 1000):
        resp = (
            client.table("bestsellers")
            .select("data,plataforma,sku_plataforma,url_produto,reviews,referencia")
            .gte("data", desde.isoformat()).order("data")
            .range(offset, offset + 999).execute()
        )
        lote = resp.data or []
        linhas.extend(lote)
        if len(lote) < 1000:
            break
    return pd.DataFrame(linhas)


def executar_correlacao(historico: pd.DataFrame, vendas_csv: Optional[str]) -> str:
    """Clima × venda: diária com o proxy de Mais Vendidos e mensal com série externa."""
    idx = indice_regional(com_anomalia(historico))
    saida = ["🔬 Clima × venda", ""]
    if not len(idx):
        return "\n".join(saida + ["Sem clima no histórico."])
    obs = idx[(idx["tipo"] == OBSERVADO)].copy()

    bs = _ler_bestsellers(min(obs["data"]))
    proxy = proxy_vendas_diario(bs)
    if len(proxy):
        vendas = proxy.set_index("data")["avaliacoes_dia"]
        for regiao in (BRASIL, "Sudeste"):
            r = obs[obs["regiao"] == regiao].set_index("data")
            coluna = "anomalia_tmax" if r.get("anomalia_tmax", pd.Series(dtype=float)).notna().any() else "tmax"
            corr = correlacao_defasada(r[coluna], vendas)
            saida.append(f"• {regiao} ({coluna}) × avaliações/dia nos Mais Vendidos:")
            saida.append(f"  {ler_veredito(corr)}")
    else:
        saida.append("• Proxy de Mais Vendidos indisponível (sem leituras com avaliações).")

    if vendas_csv:
        vendas_m = pd.read_csv(vendas_csv, sep=None, engine="python")
        corr_m = correlacao_mensal(idx, vendas_m)
        saida.append(f"• Mensal × {Path(vendas_csv).name}: {ler_veredito([corr_m] if corr_m else [])}")
    return "\n".join(saida)


def _notificar(texto: str) -> None:
    try:
        from utils.n8n_notify import _send_direct_telegram

        if not _send_direct_telegram(html.escape(texto)):
            logger.warning("[Clima] Telegram não enviado (token/chat ausente?).")
    except Exception as exc:
        logger.warning(f"[Clima] Falha ao notificar: {exc}")


def _bater(status: str, linhas: Optional[int] = None, detalhe: str = "") -> None:
    """Batida no livro-razão quando ``RAC_JOB_ID`` está definido. Nunca levanta."""
    job_id = os.getenv("RAC_JOB_ID", "").strip()
    if not job_id:
        return
    try:
        from utils.heartbeat import bater

        bater(job_id, status, rows=linhas, detail=detalhe)
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug(f"[Heartbeat] '{status}' não registrada: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _data(valor: str) -> date:
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"data inválida: {valor} (AAAA-MM-DD)") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Radar meteorológico (Open-Meteo).")
    parser.add_argument("--backfill", action="store_true", help="histórico ERA5 (archive)")
    parser.add_argument("--desde", type=_data, default=date(2016, 1, 1))
    parser.add_argument("--ate", type=_data, default=None, help="fim do backfill (padrão: ontem)")
    parser.add_argument("--dias-passados", type=int, default=10)
    parser.add_argument("--csv", default=str(_CSV_PADRAO))
    parser.add_argument("--sem-supabase", action="store_true")
    parser.add_argument("--notificar", action="store_true", help="resumo no Telegram")
    parser.add_argument("--correlacao", action="store_true", help="clima × venda (não coleta)")
    parser.add_argument("--vendas-csv", default=None, help="série mensal externa: mes;vendas")
    args = parser.parse_args(argv)

    hoje = now_brt().date()
    caminho = Path(args.csv)

    if args.correlacao:
        historico = consolidar(ler_csv(caminho), ler_supabase(date(2016, 1, 1)))
        texto = executar_correlacao(historico, args.vendas_csv)
        print("\n" + texto)
        return 0

    _bater("STARTED", detalhe="backfill" if args.backfill else "diário")
    ate = args.ate or (hoje - timedelta(days=1))
    if args.backfill and args.desde > ate:
        logger.error("--desde depois de --ate")
        return 2

    novo = coletar(args.backfill, args.desde, ate, args.dias_passados, hoje)
    if not len(novo):
        _bater("FAILED", 0, "nenhuma cidade respondeu")
        logger.error("[Clima] Nenhuma cidade respondeu.")
        return 1

    historico = gravar_csv(novo, caminho)
    gravadas = 0 if args.sem_supabase else gravar_supabase(novo)

    # A normal precisa do histórico: o CSV local pode ser só desta máquina
    # (runner do Actions começa vazio), então o banco completa.
    if not args.sem_supabase:
        historico = consolidar(ler_supabase(hoje - timedelta(days=366 * 11)), historico)
    texto = resumo(historico, hoje)
    print("\n" + texto)
    if args.notificar:
        _notificar(texto)

    cidades = novo["cidade"].nunique()
    _bater(
        "SUCCESS" if cidades == len(CIDADES) else "PARTIAL",
        gravadas or len(novo),
        f"{cidades}/{len(CIDADES)} cidades, {len(novo)} linhas",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
