"""
utils/price_series.py — uma série de preço, uma fonte.

Funções PURAS de pandas (sem banco, sem Streamlit) para os gráficos de preço de
`app.py`, testadas em `tests/test_price_series.py`.

O problema que isto resolve (validado contra o banco em 30/09/2026): a série
"Tendência de Preço por Marca" mostrava a Philco caindo de ~R$ 13 mil para
~R$ 2 mil no último dia. Não houve mercado nenhum por trás: o
`query_price_evolution_data` aplicava a precedência do PriceTrack por
``(data, SKU)`` e deixava as coletas preencherem as datas que o PriceTrack não
cobre. Como `pricetrack_daily` não tinha 25/09, 26/09 e 29/09/2026, esses dias
vieram das coletas — outra medida (oferta na SERP, não moda por seller) e outro
mix de produtos. A linha trocava de fonte no meio e "se mexia" sozinha.

Regras que valem para toda série de preço do dashboard:

1. **Uma série = uma fonte.** Cada série (SKU, marca, plataforma…) usa uma
   fonte só, na janela inteira: as linhas da outra fonte saem de TODOS os
   dias, não só dos dias em que as duas existem. O PriceTrack (a verdade de
   preço do projeto) fica com a série enquanto cobrir ao menos METADE dos dias
   que a Coletas cobre; abaixo disso a série passa para a fonte que cobre mais
   dias. Por que não "mais dias vence" puro: o último dia de toda janela que
   termina hoje não tem PriceTrack até o import D-1 (06:00 BRT do dia
   seguinte), e as coletas têm — a regra pura passaria TODAS as séries para
   as coletas durante o dia e de volta ao PriceTrack na manhã seguinte, o
   mesmo degrau que isto existe para matar.
2. **A outra fonte nunca tapa buraco.** Dia sem a fonte escolhida fica em
   branco no gráfico e é listado na legenda de cobertura: silêncio da fonte
   não é mudança de mercado.
3. **Preço por marca só se compara no mesmo BTU e no mesmo formato.** A moda
   de uma marca entre 9K e 60K, com portátil e cassete no meio, não é um
   número comparável — muda com o mix, não com o preço. `capacity_btu` e
   `fora_hiwall_mask` dão o recorte.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from utils.shelf_insights import extract_btu, is_fora_hiwall

__all__ = [
    "SOURCE_PRICETRACK",
    "SOURCE_COLETAS",
    "SOURCE_LABELS",
    "SOURCE_SYMBOLS",
    "source_label",
    "pick_series_sources",
    "single_source_per_series",
    "days_by_source",
    "format_day_list",
    "coverage_caption",
    "capacity_btu",
    "fora_hiwall_mask",
    "format_btu",
]

SOURCE_PRICETRACK = "pricetrack"
SOURCE_COLETAS = "coletas"

#: Rótulo de exibição de cada fonte (legenda, hover, tabelas).
SOURCE_LABELS: Mapping[str, str] = {
    SOURCE_PRICETRACK: "PriceTrack",
    SOURCE_COLETAS: "Coletas",
}

#: Marcador do ponto no gráfico — a fonte fica visível sem abrir o hover.
SOURCE_SYMBOLS: Mapping[str, str] = {
    SOURCE_PRICETRACK: "circle",
    SOURCE_COLETAS: "diamond-open",
}

# Linha sem `source` (não deveria existir) vira fonte própria, nunca é
# atribuída em silêncio a PriceTrack ou Coletas.
_SEM_FONTE = "desconhecida"

REPORT_COLUMNS: Tuple[str, ...] = (
    "fonte",
    "dias_fonte",
    "dias_descartados",
    "datas_descartadas",
    "linhas_descartadas",
    "fontes_descartadas",
)


def source_label(source: object) -> str:
    """Nome de exibição da fonte ("pricetrack" → "PriceTrack")."""
    if source is None or (isinstance(source, float) and pd.isna(source)):
        return _SEM_FONTE
    return SOURCE_LABELS.get(str(source), str(source))


def _series_ids(df: pd.DataFrame, series_cols: Sequence[str]) -> pd.Series:
    """Id inteiro por série; NaN na chave é uma série própria, não descartada."""
    if not series_cols:
        return pd.Series(0, index=df.index)
    return df.groupby(list(series_cols), dropna=False, sort=True).ngroup()


def pick_series_sources(
    df: pd.DataFrame,
    series_cols: Sequence[str] = (),
    *,
    date_col: str = "data",
    source_col: str = "source",
    prefer: str = SOURCE_PRICETRACK,
    min_prefer_share: float = 0.5,
) -> pd.DataFrame:
    """Qual fonte cada série usa, e o que a outra fonte deixa de preencher.

    Args:
        df: linhas de preço com coluna de data e de fonte.
        series_cols: colunas que definem a série (``()`` = o recorte inteiro é
            uma série só — ex.: um histograma diário).
        date_col: coluna de data (a cobertura conta dias distintos).
        source_col: coluna de fonte (``"pricetrack"`` / ``"coletas"``).
        prefer: fonte preferida (PriceTrack).
        min_prefer_share: a preferida fica com a série se cobrir ao menos esta
            fração dos dias da melhor outra fonte (``0.5`` = metade; ``1.0`` =
            "mais dias vence", empate → preferida; ``0`` = preferida sempre
            que aparecer).

    Returns:
        Uma linha por série: ``series_cols`` + ``fonte`` (escolhida),
        ``dias_fonte``, ``dias_descartados`` (dias que só a outra fonte tinha —
        viram lacuna), ``datas_descartadas`` (tupla ordenada desses dias),
        ``linhas_descartadas`` e ``fontes_descartadas`` (tupla).
    """
    return _resolve(df, series_cols, date_col, source_col, prefer,
                    min_prefer_share)[1]


def single_source_per_series(
    df: pd.DataFrame,
    series_cols: Sequence[str] = (),
    *,
    date_col: str = "data",
    source_col: str = "source",
    prefer: str = SOURCE_PRICETRACK,
    min_prefer_share: float = 0.5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Mantém, em cada série, só as linhas de UMA fonte.

    A escolha segue `pick_series_sources` (PriceTrack enquanto cobrir ao menos
    `min_prefer_share` dos dias da outra fonte; senão, a que cobre mais dias)
    e vale para a série inteira: a outra fonte sai de TODOS os dias, inclusive
    dos que ela cobriria sozinha, senão a linha voltaria a trocar de fonte no
    meio.

    Returns:
        ``(linhas mantidas, relatório)`` — o relatório é o de
        `pick_series_sources`. Sem coluna de fonte ou de data, devolve o
        próprio df (cópia) e relatório vazio: não há o que escolher.
    """
    return _resolve(df, series_cols, date_col, source_col, prefer,
                    min_prefer_share)


def _choose(stats: pd.DataFrame, prefer: str, min_prefer_share: float) -> pd.Series:
    """Fonte de cada série, dado ``[_sid, _src, dias]``.

    A preferida fica com a série se aparecer e cobrir ao menos
    `min_prefer_share` dos dias da melhor outra fonte; senão vence a fonte com
    mais dias (empate entre as outras → ordem alfabética, determinística).
    Linha sem fonte (`_SEM_FONTE`) nunca vence uma série que tenha fonte
    conhecida — só fica com a série quando não há outra.
    """
    piv = stats.pivot(index="_sid", columns="_src", values="dias").fillna(0)
    known = sorted(c for c in piv.columns if c != _SEM_FONTE)
    if not known:
        return pd.Series(_SEM_FONTE, index=piv.index, dtype="object")
    piv = piv[known]
    best = piv.idxmax(axis=1)  # 1ª coluna no empate → alfabética
    best = best.where(piv.max(axis=1) > 0, _SEM_FONTE)
    if prefer not in piv.columns:
        return best
    pref_days = piv[prefer]
    others = piv.drop(columns=prefer)
    others_best = others.max(axis=1) if not others.empty else pd.Series(0, index=piv.index)
    keep_pref = (pref_days > 0) & (pref_days >= min_prefer_share * others_best)
    return best.where(~keep_pref, prefer)


def _resolve(
    df: pd.DataFrame,
    series_cols: Sequence[str],
    date_col: str,
    source_col: str,
    prefer: str,
    min_prefer_share: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cols = list(series_cols)
    empty_report = pd.DataFrame(columns=cols + list(REPORT_COLUMNS))
    if df.empty or source_col not in df.columns or date_col not in df.columns:
        return df.copy(), empty_report

    sid = _series_ids(df, cols).to_numpy()
    src = df[source_col].astype("string").fillna(_SEM_FONTE).to_numpy()
    work = pd.DataFrame({"_sid": sid, "_src": src, "_d": df[date_col].to_numpy()})

    stats = work.groupby(["_sid", "_src"])["_d"].nunique().rename("dias").reset_index()
    choice = _choose(stats, prefer, min_prefer_share)

    keep = (work["_src"] == work["_sid"].map(choice)).to_numpy()
    kept = df[keep]

    # Relatório vetorizado: janela longa com centenas de SKUs e dezenas de
    # milhares de linhas não pode custar um filtro por série.
    kept_w, drop_w = work[keep], work[~keep]
    kd = kept_w[["_sid", "_d"]].dropna().drop_duplicates()
    dd = drop_w[["_sid", "_d"]].dropna().drop_duplicates()
    only = dd.merge(kd, on=["_sid", "_d"], how="left", indicator=True)
    only = only[only["_merge"] == "left_only"].sort_values(["_sid", "_d"])
    datas = only.groupby("_sid")["_d"].agg(tuple)

    report = pd.DataFrame({"_sid": choice.index, "fonte": choice.to_numpy()})
    report["dias_fonte"] = report["_sid"].map(kd.groupby("_sid").size()).fillna(0).astype(int)
    report["datas_descartadas"] = report["_sid"].map(datas).map(
        lambda t: t if isinstance(t, tuple) else ())
    report["dias_descartados"] = report["datas_descartadas"].map(len)
    report["linhas_descartadas"] = (
        report["_sid"].map(drop_w.groupby("_sid").size()).fillna(0).astype(int))
    fontes = drop_w.groupby("_sid")["_src"].agg(lambda x: tuple(sorted(set(x))))
    report["fontes_descartadas"] = report["_sid"].map(fontes).map(
        lambda t: t if isinstance(t, tuple) else ())
    if cols:
        keys = df[cols].groupby(sid, sort=True).first()
        keys.index.name = "_sid"
        report = report.merge(keys.reset_index(), on="_sid", how="left")
    report = report.drop(columns="_sid")
    return kept, report[cols + list(REPORT_COLUMNS)]


def days_by_source(
    df: pd.DataFrame,
    *,
    date_col: str = "data",
    source_col: str = "source",
) -> dict:
    """``{fonte: [dias ordenados]}`` — o que cada fonte trouxe na janela."""
    if df.empty or source_col not in df.columns or date_col not in df.columns:
        return {}
    src = df[source_col].astype("string").fillna(_SEM_FONTE)
    out: dict = {}
    for fonte, dias in df[date_col].groupby(src.to_numpy()):
        out[str(fonte)] = sorted(set(dias.dropna()))
    return out


def _as_date(value: object) -> Optional[date]:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(ts) else ts.date()


def format_day_list(dates: Iterable[object], max_runs: int = 8) -> str:
    """Dias como faixas curtas: ``"25–26/09, 29/09"``.

    Dias consecutivos viram uma faixa; mais de `max_runs` faixas são cortadas
    com ``"+N"`` para a legenda caber numa linha.
    """
    ds = sorted({d for d in (_as_date(v) for v in dates) if d is not None})
    if not ds:
        return "—"
    runs: List[Tuple[date, date]] = []
    start = prev = ds[0]
    for d in ds[1:]:
        if (d - prev).days == 1:
            prev = d
            continue
        runs.append((start, prev))
        start = prev = d
    runs.append((start, prev))

    def _fmt(a: date, b: date) -> str:
        if a == b:
            return a.strftime("%d/%m")
        if (a.year, a.month) == (b.year, b.month):
            return f"{a.day:02d}–{b.strftime('%d/%m')}"
        return f"{a.strftime('%d/%m')}–{b.strftime('%d/%m')}"

    parts = [_fmt(a, b) for a, b in runs]
    if len(parts) > max_runs:
        parts = parts[:max_runs] + [f"+{len(parts) - max_runs}"]
    return ", ".join(parts)


def coverage_caption(
    report: pd.DataFrame,
    label_col: Optional[str] = None,
    max_items: int = 6,
) -> str:
    """Frase curta com as lacunas que a regra "uma série, uma fonte" deixou.

    Args:
        report: saída de `pick_series_sources` / `single_source_per_series`.
        label_col: coluna do relatório com o nome da série (``None`` = série
            única, sem nome).
        max_items: quantas séries listar antes de resumir em "+N".

    Returns:
        ``""`` quando nenhuma série perdeu dia; senão, por exemplo,
        ``"Philco (PriceTrack) sem 25–26/09, 29/09"``.
    """
    if report is None or report.empty or "dias_descartados" not in report.columns:
        return ""
    gaps = report[report["dias_descartados"] > 0]
    if gaps.empty:
        return ""
    gaps = gaps.sort_values("dias_descartados", ascending=False)
    parts = []
    for _, r in gaps.head(max_items).iterrows():
        fonte = source_label(r["fonte"])
        dias = format_day_list(r["datas_descartadas"])
        if label_col and pd.notna(r.get(label_col)):
            parts.append(f"{r[label_col]} ({fonte}) sem {dias}")
        else:
            parts.append(f"{fonte} sem {dias}")
    extra = len(gaps) - max_items
    if extra > 0:
        parts.append(f"+{extra} série(s)")
    return "; ".join(parts)


_BTU_MILHAR_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


def _btu_value(value: object) -> Optional[int]:
    """BTU do catálogo como inteiro: ``12000``, ``12000.0``, ``"12000"`` e
    ``"12.000"`` (milhar brasileiro) → 12000; vazio/ilegível → None.

    `pd.to_numeric("12.000")` daria 12.0 — o produto sairia como "12 BTU" e
    sumiria do recorte de 12.000 BTU sem aviso nenhum.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        s = value.strip()
        if _BTU_MILHAR_RE.fullmatch(s):
            return int(re.sub(r"[.,]", "", s))
        value = s
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(num)) if math.isfinite(num) else None


def capacity_btu(
    df: pd.DataFrame,
    sku_to_btu: Optional[Mapping[str, object]] = None,
    *,
    sku_col: str = "sku",
    text_cols: Sequence[str] = ("produto", "title"),
) -> pd.Series:
    """Capacidade (BTU) de cada linha: catálogo pelo SKU, senão o título.

    O catálogo (`produtos_catalogo.capacidade_btu`) é a fonte de primeira mão
    para SKU resolvido; o nome do anúncio só entra onde o SKU não resolve —
    mesmo parser do Cockpit (`utils.shelf_insights.extract_btu`).

    Returns:
        Série ``Int64`` alinhada ao índice de `df` (``<NA>`` = desconhecida).
    """
    out = pd.Series(pd.NA, index=df.index, dtype="Int64")
    if df.empty:
        return out
    if sku_to_btu and sku_col in df.columns:
        sk = df[sku_col].astype("string").str.strip()
        mapped = sk.map(
            lambda s: _btu_value(sku_to_btu.get(s)) if isinstance(s, str) else None)
        out = pd.Series(pd.array(mapped.tolist(), dtype="Int64"), index=df.index)
    for col in text_cols:
        missing = out.isna()
        if not missing.any():
            break
        if col not in df.columns:
            continue
        parsed = df.loc[missing, col].map(extract_btu)
        out.loc[missing] = pd.array(parsed.tolist(), dtype="Int64")
    return out


def fora_hiwall_mask(
    df: pd.DataFrame,
    *,
    text_cols: Sequence[str] = ("produto", "title"),
    hiwall_skus: Optional[Iterable[str]] = None,
    sku_col: str = "sku",
) -> pd.Series:
    """True nas linhas de portátil/janela/cassete/piso-teto/multi-split.

    SKU presente em `hiwall_skus` (o catálogo é só RAC High Wall) é hi-wall
    por construção, qualquer que seja o título do anúncio.
    """
    mask = pd.Series(False, index=df.index)
    if df.empty:
        return mask
    for col in text_cols:
        if col in df.columns:
            mask |= df[col].map(is_fora_hiwall).astype(bool)
    if hiwall_skus is not None and sku_col in df.columns:
        known = set(str(s) for s in hiwall_skus)
        mask &= ~df[sku_col].astype("string").str.strip().isin(known).fillna(False)
    return mask


def format_btu(btu: object) -> str:
    """``12000`` → ``"12.000 BTU"``; desconhecido → ``"BTU ?"``."""
    try:
        return f"{int(btu):,} BTU".replace(",", ".")
    except (TypeError, ValueError):
        return "BTU ?"
