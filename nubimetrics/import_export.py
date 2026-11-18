"""Importa os exports XLSX da Nubimetrics (sell-out DIÁRIO do ML).

Uso::

    python -m nubimetrics.import_export                       # uploads/nubimetrics/
    python -m nubimetrics.import_export caminho/ outro.xlsx   # pastas e/ou arquivos
    python -m nubimetrics.import_export --dry-run             # lê, confere e resume; não grava
    python -m nubimetrics.import_export --atributos atrib.xlsx

O que faz, nesta ordem (falha ALTO antes de gravar qualquer coisa):

1. lê cada XLSX (``export_pivot.ler_export``) e confere folhas × "Total geral";
2. separa o export fino (título → marca → seller, template "RAC | Top anuncios
   por dia") dos agregados (por marca, por seller…) e dos de atributos;
3. confere o fino contra cada agregado do mesmo período (mesmo relatório);
4. classifica (``diario.montar_linhas``: override → atributo → título);
5. grava o histórico inteiro no Parquet (dataset ``nubi_vendas_diarias``, uma
   partição por dia, regravada a cada import);
6. troca os dias do export no banco numa transação (RPC
   ``nubi_substituir_diario``, migração 024) e poda a janela móvel
   (agregado 100 dias, anúncio 35 dias).

Escrita só com service_role (``SUPABASE_KEY``). Não é agendado: a Nubimetrics
não tem API para este relatório, o export é feito na mão (README do módulo).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from loguru import logger

from nubimetrics import diario as D
from nubimetrics.export_pivot import ATRIBUTOS, ExportLayoutError, PivotExport, ler_atributos, ler_export, reconciliar

DATASET_PARQUET = "nubi_vendas_diarias"
PASTA_PADRAO = Path("uploads/nubimetrics")
RETENCAO_AGG_DIAS = 100
RETENCAO_ANUNCIO_DIAS = 35
_NIVEIS_FINO = ["titulo", "marca", "seller"]


@dataclass
class Lote:
    """Tudo o que um import leu e conferiu."""

    fino: List[PivotExport] = field(default_factory=list)
    agregados: List[PivotExport] = field(default_factory=list)
    atributos: List[pd.DataFrame] = field(default_factory=list)
    problemas: List[str] = field(default_factory=list)
    mtimes: Dict[str, float] = field(default_factory=dict)


def _arquivos(entradas: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for e in entradas or [str(PASTA_PADRAO)]:
        p = Path(e)
        if p.is_dir():
            out.extend(sorted(x for x in p.iterdir() if x.suffix.lower() in (".xlsx", ".csv")))
        elif p.exists():
            out.append(p)
        else:
            raise FileNotFoundError(f"{p} não existe")
    return out


def ler_lote(arquivos: Sequence[Path], atributos: Sequence[Path] = ()) -> Lote:
    """Lê e confere os exports. Problema de layout ou total = entra em ``problemas``."""
    lote = Lote()
    for a in arquivos:
        try:
            exp = ler_export(a)
        except ExportLayoutError as exc:
            lote.problemas.append(str(exc))
            continue
        lote.mtimes[exp.arquivo] = a.stat().st_mtime
        if any(n in ATRIBUTOS for n in exp.niveis):
            lote.atributos.append(ler_atributos(a))
            continue
        filtro = exp.filtros.get("Categoria_Nivel_3")
        if filtro and filtro != "Ares Condicionados":
            lote.problemas.append(f"{exp.arquivo}: filtro Categoria_Nivel_3 = '{filtro}' (esperado "
                                  "'Ares Condicionados')")
            continue
        lote.problemas.extend(reconciliar(exp))
        (lote.fino if exp.niveis == _NIVEIS_FINO else lote.agregados).append(exp)
    for a in atributos:
        try:
            lote.atributos.append(ler_atributos(a))
        except ExportLayoutError as exc:
            lote.problemas.append(str(exc))
    if not lote.fino:
        lote.problemas.append("nenhum export no nível título → marca → seller (template "
                              "'RAC | Top anuncios por dia') — é dele que sai o painel diário")
    return lote


def folhas_unificadas(lote: Lote) -> pd.DataFrame:
    """Une os exports finos; o mesmo dia em dois arquivos fica com o mais recente.

    A Nubimetrics revisa a estimativa dos últimos dias: o export mais novo é o
    que vale para o dia inteiro (nunca soma dois arquivos).
    """
    partes = []
    for exp in sorted(lote.fino, key=lambda e: lote.mtimes.get(e.arquivo, 0.0)):
        partes.append(exp.linhas.assign(_arquivo=exp.arquivo, _mtime=lote.mtimes.get(exp.arquivo, 0.0)))
    df = pd.concat(partes, ignore_index=True)
    dono = df.groupby("data")["_mtime"].transform("max")
    df = df[df["_mtime"] == dono]
    # Mesmo mtime (cópias do mesmo arquivo): fica um arquivo por dia.
    primeiro = df.groupby("data")["_arquivo"].transform("first")
    return df[df["_arquivo"] == primeiro].drop(columns=["_arquivo", "_mtime"]).reset_index(drop=True)


def conferir(lote: Lote, linhas: pd.DataFrame) -> List[str]:
    """Fino × cada export agregado, nos dias em comum."""
    erros: List[str] = []
    dias = set(linhas["data"])
    for exp in lote.agregados:
        ref = exp.linhas[exp.linhas["data"].isin(dias)]
        if ref.empty:
            continue
        for e in D.conferir_agregados(linhas[linhas["data"].isin(set(ref["data"]))], ref, exp.niveis):
            erros.append(f"{exp.arquivo}: {e}")
    return erros


def _registros(df: pd.DataFrame, colunas: Sequence[str], agora: str) -> List[Dict[str, Any]]:
    x = df[list(colunas)].copy()
    x["data"] = x["data"].map(lambda d: d.isoformat())
    x["atualizado_em"] = agora
    return x.to_dict("records")


def _ler_tabela(client: Any, tabela: str) -> pd.DataFrame:
    try:
        return pd.DataFrame(client.table(tabela).select("*").limit(20000).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — migração 024 ausente: segue sem
        logger.warning(f"[Nubi diário] {tabela} indisponível ({exc}) — seguindo sem")
        return pd.DataFrame()


def gravar_banco(client: Any, linhas: pd.DataFrame, agg: pd.DataFrame,
                 atributos: Optional[pd.DataFrame], hoje: dt.date) -> Dict[str, Any]:
    """Troca os dias importados (RPC atômica) e poda a janela móvel."""
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    corte_agg = hoje - dt.timedelta(days=RETENCAO_AGG_DIAS)
    corte_anu = hoje - dt.timedelta(days=RETENCAO_ANUNCIO_DIAS)
    agg = agg[agg["data"] >= corte_agg]
    anu = linhas[linhas["data"] >= corte_anu]
    if agg.empty:
        logger.warning("[Nubi diário] todos os dias do export estão fora da janela do banco — só Parquet")
        return {}
    desde, ate = min(agg["data"]), max(agg["data"])
    p_agg = _registros(agg, ["data", "marca", "seller", "segmento", "faixa_btu", "unidades", "gmv_brl",
                             "anuncios"], agora)
    p_anu = _registros(anu, ["data", "anuncio_key", "titulo", "marca", "seller", "segmento", "faixa_btu",
                             "origem", "unidades", "gmv_brl"], agora)
    args: Dict[str, Any] = {"p_desde": desde.isoformat(), "p_ate": ate.isoformat(),
                            "p_agg": p_agg, "p_anuncio": p_anu,
                            "p_corte_agg": corte_agg.isoformat(), "p_corte_anuncio": corte_anu.isoformat()}
    if type(client).__name__ == "PostgresClient":
        from psycopg2.extras import Json  # utils/db.py: lista viraria ARRAY, não jsonb

        args["p_agg"], args["p_anuncio"] = Json(p_agg), Json(p_anu)
    resp = client.rpc("nubi_substituir_diario", args).execute()
    res = resp.data if isinstance(resp.data, dict) else {}
    logger.success(f"[Nubi diário] banco: {desde:%d/%m}–{ate:%d/%m} trocados {res}")

    if atributos is not None and not atributos.empty:
        regs = atributos.assign(marca=atributos.get("marca", "").fillna("") if "marca" in atributos else "",
                                snapshot_month=hoje.replace(day=1).isoformat(), atualizado_em=agora)
        cols = ["titulo", "marca", *[a for a in ATRIBUTOS if a in regs], "snapshot_month", "atualizado_em"]
        recs = regs[cols].where(regs[cols].notna(), None).to_dict("records")
        for i in range(0, len(recs), 500):
            client.table("nubi_listing_attributes").upsert(recs[i:i + 500], on_conflict="titulo,marca").execute()
        logger.success(f"[Nubi diário] nubi_listing_attributes: {len(recs)} título(s)")

    for tabela in ("nubi_vendas_diarias_agg", "nubi_vendas_diarias_anuncio"):
        try:
            client.rpc("compactar_indices", {"p_tabela": tabela}).execute()
        except Exception as exc:  # noqa: BLE001 — compactação é manutenção, não derruba o import
            logger.warning(f"[Nubi diário] compactar_indices({tabela}) falhou: {exc} — reaplique a 022")
    return res


def gravar_parquet(linhas: pd.DataFrame) -> int:
    """Uma partição por dia (run ``export`` → reimport sobrescreve o dia)."""
    from utils.history import get_store

    store = get_store()
    n = 0
    for dia, g in linhas.groupby("data"):
        regs = g.drop(columns=[c for c in g.columns if c.startswith("_")]).to_dict("records")
        store.write_day(DATASET_PARQUET, dia, regs, run_id="export")
        n += 1
    return n


def resumo(linhas: pd.DataFrame) -> str:
    """Texto curto para o log: dias, unidades e HW Inverter por dia."""
    tot = linhas.groupby("data")["unidades"].sum()
    hw = linhas[linhas["segmento"] == D.SEG_HW_INVERTER].groupby("data")["unidades"].sum()
    orig = D.origem_pct(linhas)
    partes = [f"{len(tot)} dia(s) {min(tot.index):%d/%m}–{max(tot.index):%d/%m}, "
              f"{tot.sum():,.0f} unid., GMV R$ {linhas['gmv_brl'].sum() / 1e6:,.2f} mi",
              f"HW Inverter: {hw.sum():,.0f} unid. ({hw.sum() / tot.sum():.0%})",
              f"classificação: {', '.join(f'{k} {v:.0%}' for k, v in orig.items())}"]
    return " | ".join(partes)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entradas", nargs="*", help=f"arquivos/pastas (padrão: {PASTA_PADRAO})")
    ap.add_argument("--atributos", nargs="*", default=[], help="export(s) do relatório de atributos")
    ap.add_argument("--dry-run", action="store_true", help="lê, confere e resume; não grava")
    ap.add_argument("--sem-banco", action="store_true", help="grava só o Parquet")
    ap.add_argument("--forcar", action="store_true", help="grava mesmo com divergência na conferência")
    args = ap.parse_args(argv)

    lote = ler_lote(_arquivos(args.entradas), [Path(a) for a in args.atributos])
    if not lote.fino:
        for p in lote.problemas:
            logger.error(f"[Nubi diário] {p}")
        return 1
    folhas = folhas_unificadas(lote)
    problemas = lote.problemas + conferir(lote, folhas)

    client = None
    overrides: Dict[str, str] = {}
    attr_banco = pd.DataFrame()
    if not args.dry_run and not args.sem_banco:
        from nubimetrics.load import cliente

        client = cliente()
        ov = _ler_tabela(client, "nubi_segment_override")
        if not ov.empty:
            overrides = dict(zip(ov["chave"], ov["segmento"]))
        attr_banco = _ler_tabela(client, "nubi_listing_attributes")
    attrs = [a for a in [attr_banco, *lote.atributos] if a is not None and not a.empty]
    atributos = pd.concat(attrs, ignore_index=True).drop_duplicates(["titulo", "marca"] if all(
        "marca" in a for a in attrs) else ["titulo"], keep="last") if attrs else None

    linhas = D.montar_linhas(folhas, atributos, overrides)
    agg = D.agregar(linhas)
    logger.info(f"[Nubi diário] {resumo(linhas)}")
    if problemas:
        for p in problemas:
            logger.error(f"[Nubi diário] conferência: {p}")
        if not args.forcar:
            logger.error("[Nubi diário] nada gravado (use --forcar só se a divergência for entendida)")
            return 1
    if args.dry_run:
        logger.info("[Nubi diário] --dry-run: nada gravado")
        return 0

    from utils.text import now_brt

    n = gravar_parquet(linhas)
    logger.success(f"[Nubi diário] Parquet: {n} dia(s) em {DATASET_PARQUET}")
    if client is not None:
        gravar_banco(client, linhas, agg, atributos, now_brt().date())
    return 0


if __name__ == "__main__":
    sys.exit(main())
