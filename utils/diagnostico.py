"""
diagnostico.py — métricas do painel Diagnóstico (reprodução da aba do PriceTrack).

Fórmulas documentadas em ``docs/diagnostico.md``. Tudo aqui é pandas puro sobre
um *frame de anúncios-dia*: uma linha por (dia, marketplace, sku, seller) com o
menor preço à vista do dia (``min_price``, base ``best_cash`` do import).

Fonte: histórico Parquet do PriceTrack no Drive (o Supabase guarda só 2 dias —
decisão de 04/11/2026). ``load_frame`` lê e prepara; as funções de métrica não
sabem de onde o dado veio, e é assim que os testes as exercitam.

Regra dura de escopo (``docs/diagnostico_fase0.md`` §8): só ar-condicionado;
padrão Split Hi-Wall Inverter até 30.000 BTU (``utils.ac_attributes``).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable, List, Optional, Sequence

import pandas as pd

from utils import ac_attributes as aa

#: Colunas lidas do Parquet de ``pricetrack_daily``.
COLUNAS_LEITURA = (
    "collection_date", "turno", "brand", "sku", "title", "subcategory",
    "marketplace", "seller", "seller_canonical", "min_price", "imported_at",
)
#: Chave única de ``pricetrack_daily`` sem o turno (o frame é só "Diário").
CHAVE = ["dia", "brand", "sku", "marketplace", "seller_raw"]
TODAS = "TODAS"
#: Coluna com a chave da partição Parquet de origem de cada linha.
COL_PARTICAO = "_particao"
#: Partição gravada pelo import (``write_history``, run_id estável "import").
#: A poda (``history_cli.py tier``) apaga esta partição ao migrar o dia; se as
#: duas coexistem, a de import veio DEPOIS (reimportação) e é a mais nova.
_RUN_IMPORT = "__run-import"
#: Dias recentes lidos para montar o de-para SKU → subcategoria.
DIAS_DEPARA = 14
#: Palavras do NOME que identificam a subcategoria sem ambiguidade. Último
#: recurso para SKU sem de-para: "SPLIT" sozinho NÃO entra (o Ecomaster é
#: Hi-Wall e o nome não diz) — na dúvida, fica fora.
_SUB_PELO_NOME = (
    ("HI-WALL", r"HI[\s-]?WALL"),
    ("CASSETE", r"CASSETE"),
    ("TETO", r"PISO[\s-]?TETO|\bTETO\b"),
    ("JANELA", r"\bJANELA\b"),
    ("PORTÁTIL", r"PORT[AÁ]TIL"),
    ("DUTO", r"\bDUTO\b"),
)


# ── Carregamento ────────────────────────────────────────────────────────────

def subcategoria_pelo_nome(titulo: Optional[str]) -> str:
    """Subcategoria quando o nome a declara sem ambiguidade; senão ``""``."""
    import re
    texto = str(titulo or "").upper()
    for sub, padrao in _SUB_PELO_NOME:
        if re.search(padrao, texto):
            return sub
    return ""


def prepare_frame(raw: pd.DataFrame, mapping: Optional[dict] = None) -> pd.DataFrame:
    """Normaliza linhas cruas de ``pricetrack_daily`` no frame de anúncios-dia.

    * só turno ``Diário`` (o dia inteiro; Manhã/Tarde são recortes do mesmo dado);
    * deduplica pela chave única, ficando com a importação mais recente — o
      Parquet pode ter o mesmo dia em duas partições (import + poda);
    * seller = ``seller_canonical`` quando houver, senão ``seller``;
    * subcategoria vazia preenchida pelo de-para SKU → subcategoria (o do
      próprio frame somado a ``mapping``, lido de dias recentes) e, por último,
      pelo nome quando ele declara a subcategoria;
    * atributos (BTU, tecnologia, ciclo, voltagem) lidos do título;
    * descarta linha sem preço (não compete em preço nem conta como anúncio
      com preço — o PriceTrack só exporta oferta à venda).
    """
    if raw.empty:
        return _frame_vazio()
    df = raw.copy()
    if "turno" in df.columns:
        df = df[df["turno"].fillna("Diário") == "Diário"]
    df["dia"] = pd.to_datetime(df["collection_date"]).dt.date
    df["brand"] = df["brand"].fillna("").astype(str).str.strip().str.upper()
    df["marketplace"] = df["marketplace"].fillna("").astype(str).str.strip().str.upper()
    df["seller_raw"] = df["seller"].fillna("").astype(str).str.strip()
    canon = df["seller_canonical"] if "seller_canonical" in df.columns else df["seller"]
    df["seller"] = canon.fillna("").astype(str).str.strip()
    df.loc[df["seller"] == "", "seller"] = df["seller_raw"]
    df["preco"] = pd.to_numeric(df["min_price"], errors="coerce")

    # Dedup ANTES do filtro de preço: a versão mais nova decide, mesmo que
    # ela diga "sem preço". Recência: partição do import (reimportação) acima
    # da partição da poda; dentro dela, ``imported_at``.
    df["_rank_particao"] = (
        df[COL_PARTICAO].astype(str).str.contains(_RUN_IMPORT, regex=False).astype(int)
        if COL_PARTICAO in df.columns else 0
    )
    ordem = ["_rank_particao"] + (["imported_at"] if "imported_at" in df.columns else [])
    df = df.sort_values(ordem, kind="mergesort", na_position="first")
    df = df.drop_duplicates(CHAVE, keep="last")
    df = df[df["preco"].notna() & (df["preco"] > 0)]
    # Grão do frame = seller CANÔNICO: duas grafias do mesmo lojista viram uma
    # linha, com o menor preço do dia.
    df = df.sort_values("preco", kind="mergesort").drop_duplicates(
        ["dia", "marketplace", "sku", "seller"], keep="first"
    )

    if "subcategory" not in df.columns:
        df["subcategory"] = None
    depara = dict(mapping or {})
    depara.update(aa.subcategory_map(df))
    df = aa.fill_subcategory(df, depara)
    falta = df["subcategory"] == ""
    if falta.any():
        titulos = df.loc[falta, "title"]
        unicos = pd.Series(titulos.dropna().unique())
        por_titulo = dict(zip(unicos, unicos.map(subcategoria_pelo_nome)))
        df.loc[falta, "subcategory"] = titulos.map(por_titulo).fillna("")
    df = aa.add_attributes(df, "title")
    return df[[
        "dia", "marketplace", "sku", "seller", "brand", "title", "subcategory",
        "btu", "tecnologia", "ciclo", "voltagem", "preco",
    ]].reset_index(drop=True)


def load_subcategory_map(store: Any, ate: date, dias: int = DIAS_DEPARA,
                         dataset: str = "pricetrack") -> dict:
    """De-para SKU → subcategoria dos ``dias`` mais recentes até ``ate``.

    Lido à parte do período do painel: um período inteiro anterior a
    05/11/2026 (migração 007) não tem nenhuma subcategoria gravada, e o de-para
    só do período sairia vazio — o filtro Hi-Wall esvaziaria a página.
    """
    partes: List[pd.DataFrame] = []
    inicio = ate - timedelta(days=dias)
    for _dia, frame in store.read_days(dataset, start=inicio, end=ate, columns=["sku", "subcategory"]):
        if "subcategory" in frame.columns:
            partes.append(frame[["sku", "subcategory"]])
    if not partes:
        return {}
    return aa.subcategory_map(pd.concat(partes, ignore_index=True))


def load_frame(store: Any, inicio: date, fim: date, dataset: str = "pricetrack",
               mapping: Optional[dict] = None) -> pd.DataFrame:
    """Lê o histórico [inicio, fim] dia a dia e devolve o frame preparado.

    ``store`` é um ``utils.history.HistoryStore`` (ou objeto com ``read_days``).
    Cada dia é filtrado ao turno Diário antes de juntar, para não segurar os
    três turnos na memória do Streamlit Cloud. ``mapping``: de-para SKU →
    subcategoria de fora do período (``load_subcategory_map``).
    """
    partes: List[pd.DataFrame] = []
    for _dia, frame in store.read_days(dataset, start=inicio, end=fim,
                                       columns=list(COLUNAS_LEITURA),
                                       partition_column=COL_PARTICAO):
        if "turno" in frame.columns:
            frame = frame[frame["turno"].fillna("Diário") == "Diário"]
        if not frame.empty:
            partes.append(frame)
    if not partes:
        return _frame_vazio()
    return prepare_frame(pd.concat(partes, ignore_index=True), mapping)


def _frame_vazio() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "dia", "marketplace", "sku", "seller", "brand", "title", "subcategory",
        "btu", "tecnologia", "ciclo", "voltagem", "preco",
    ])


def apply_filters(
    df: pd.DataFrame,
    subcategorias: Optional[Sequence[str]] = (aa.SUBCATEGORIA_HIWALL,),
    tecnologias: Optional[Sequence[str]] = (aa.TECNOLOGIA_INVERTER,),
    btus: Optional[Sequence[int]] = None,
    ciclos: Optional[Sequence[str]] = None,
    skus: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Recorte do painel. Padrão = Hi-Wall Inverter; ``None`` = sem filtro.

    O teto de 30.000 BTU vale sempre — acima disso não é RAC residencial.
    """
    btu = pd.to_numeric(df["btu"], errors="coerce")
    m = btu.le(aa.BTU_MAXIMO_ESCOPO).fillna(False)
    if subcategorias:
        m &= df["subcategory"].isin([s.upper() for s in subcategorias])
    if tecnologias:
        m &= df["tecnologia"].isin(list(tecnologias))
    if btus:
        m &= btu.isin(list(btus))
    if ciclos:
        m &= df["ciclo"].isin(list(ciclos))
    if skus:
        m &= df["sku"].isin(list(skus))
    return df[m]


# ── Bloco 1 — quantidade de ofertas, foco vs. mercado ───────────────────────

def ofertas_por_marketplace(df: pd.DataFrame, foco: str, top: int = 10) -> pd.DataFrame:
    """``(dia, marketplace, ofertas)``: anúncios ativos = SKU × seller distintos.

    Top ``top`` marketplaces pelo total do período, **sempre** incluindo o foco.
    """
    if df.empty:
        return pd.DataFrame(columns=["dia", "marketplace", "ofertas"])
    contagem = (
        df.assign(oferta=df["sku"] + "|" + df["seller"])
        .groupby(["dia", "marketplace"])["oferta"].nunique()
        .rename("ofertas").reset_index()
    )
    ranking = contagem.groupby("marketplace")["ofertas"].sum().sort_values(ascending=False)
    escolhidos = list(ranking.index[:top])
    if foco in ranking.index and foco not in escolhidos:
        escolhidos = escolhidos[: top - 1] + [foco]
    return contagem[contagem["marketplace"].isin(escolhidos)].reset_index(drop=True)


# ── Blocos 2–4 — anúncios no foco por dimensão ──────────────────────────────

def anuncios_por_dimensao(df: pd.DataFrame, foco: str, dimensao: str) -> pd.DataFrame:
    """``(dia, grupo, qtd, pct)`` no marketplace foco.

    ``dimensao``: ``subcategoria`` ou ``marca``. Em ``marca`` entra a linha
    ``TODAS`` (total do dia, 100%). Só há uma categoria (ar-condicionado), por
    isso não há dimensão "categoria".
    """
    coluna = {"subcategoria": "subcategory", "marca": "brand"}[dimensao]
    f = df[df["marketplace"] == foco]
    if f.empty:
        return pd.DataFrame(columns=["dia", "grupo", "qtd", "pct"])
    f = f.assign(oferta=f["sku"] + "|" + f["seller"])
    por_grupo = f.groupby(["dia", coluna])["oferta"].nunique().rename("qtd").reset_index()
    por_grupo = por_grupo.rename(columns={coluna: "grupo"})
    total = f.groupby("dia")["oferta"].nunique().rename("total")
    por_grupo = por_grupo.join(total, on="dia")
    por_grupo["pct"] = 100.0 * por_grupo["qtd"] / por_grupo["total"]
    if dimensao == "marca":
        todas = total.rename("qtd").reset_index().assign(grupo=TODAS, pct=100.0)
        por_grupo = pd.concat([por_grupo.drop(columns="total"), todas], ignore_index=True)
    else:
        por_grupo = por_grupo.drop(columns="total")
    return por_grupo[["dia", "grupo", "qtd", "pct"]].sort_values(["dia", "grupo"]).reset_index(drop=True)


# ── Bloco 5 — preço mínimo praticado por marca no foco ──────────────────────

def preco_minimo_por_marca(df: pd.DataFrame, foco: str) -> pd.DataFrame:
    """``(dia, marca, preco_min, sku, seller, title)`` — o anúncio mais barato da marca."""
    f = df[df["marketplace"] == foco]
    if f.empty:
        return pd.DataFrame(columns=["dia", "marca", "preco_min", "sku", "seller", "title"])
    idx = f.sort_values(["preco", "sku", "seller"]).groupby(["dia", "brand"]).head(1)
    return (
        idx.rename(columns={"brand": "marca", "preco": "preco_min"})
        [["dia", "marca", "preco_min", "sku", "seller", "title"]]
        .sort_values(["dia", "marca"]).reset_index(drop=True)
    )


# ── Blocos 6–7 — mediana de mercado, profundidade, agressivos ───────────────

def mediana_mercado(
    df: pd.DataFrame,
    foco: str,
    incluir_foco: bool = False,
    excluir_lojas_marca: bool = True,
) -> pd.DataFrame:
    """``(dia, sku, mediana_mercado, n_marketplaces)``.

    Por (SKU, dia): menor preço em CADA marketplace, depois a mediana entre
    marketplaces. Por padrão sem o foco (o que se compara não entra na régua)
    e sem lojas próprias de fabricante (decisão de 05/11/2026).
    """
    m = df
    if not incluir_foco:
        m = m[m["marketplace"] != foco]
    if excluir_lojas_marca and not m.empty:
        lojas = aa.brand_store_marketplaces(m["marketplace"].unique(), df["brand"].unique())
        m = m[~m["marketplace"].isin(lojas)]
    if m.empty:
        return pd.DataFrame(columns=["dia", "sku", "mediana_mercado", "n_marketplaces"])
    por_mkt = m.groupby(["dia", "sku", "marketplace"])["preco"].min().reset_index()
    return (
        por_mkt.groupby(["dia", "sku"])["preco"]
        .agg(mediana_mercado="median", n_marketplaces="size")
        .reset_index()
    )


def _foco_vs_mercado(df: pd.DataFrame, foco: str, **kw: Any) -> pd.DataFrame:
    """Por (dia, sku) no foco: menor preço, seller dele, nº de sellers e o pct.

    ``pct = (mediana_mercado − preço_foco) / mediana_mercado`` — positivo =
    foco mais barato que o mercado.
    """
    f = df[df["marketplace"] == foco]
    if f.empty:
        return pd.DataFrame(columns=[
            "dia", "sku", "brand", "title", "preco_foco", "seller", "n_sellers",
            "mediana_mercado", "pct",
        ])
    melhor = f.sort_values(["preco", "seller"]).groupby(["dia", "sku"]).head(1)
    n_sellers = f.groupby(["dia", "sku"])["seller"].nunique().rename("n_sellers")
    base = (
        melhor.rename(columns={"preco": "preco_foco"})
        [["dia", "sku", "brand", "title", "preco_foco", "seller"]]
        .join(n_sellers, on=["dia", "sku"])
    )
    med = mediana_mercado(df, foco, **kw)
    base = base.merge(med[["dia", "sku", "mediana_mercado"]], on=["dia", "sku"], how="left")
    base["pct"] = 100.0 * (base["mediana_mercado"] - base["preco_foco"]) / base["mediana_mercado"]
    return base


def profundidade_por_marca(df: pd.DataFrame, foco: str, **kw: Any) -> pd.DataFrame:
    """``(dia, marca, profundidade_pct, n_skus)`` — mediana dos pct dos SKUs da marca."""
    base = _foco_vs_mercado(df, foco, **kw).dropna(subset=["pct"])
    if base.empty:
        return pd.DataFrame(columns=["dia", "marca", "profundidade_pct", "n_skus"])
    return (
        base.groupby(["dia", "brand"])["pct"]
        .agg(profundidade_pct="median", n_skus="size")
        .reset_index().rename(columns={"brand": "marca"})
    )


def produtos_agressivos(df: pd.DataFrame, foco: str, limite: int = 10, **kw: Any) -> pd.DataFrame:
    """Produtos do foco mais abaixo da mediana de mercado na média do período.

    Exige presença (dia com preço no foco E mediana de mercado) em pelo menos
    metade dos dias com coleta no foco, arredondando para cima.

    Colunas: ``sku, produto, marca, menor_preco, seller, n_sellers_foco,
    mediana_mercado, pct_abaixo_medio, dias_presenca, serie_precos``.
    ``menor_preco``/``seller``/``n_sellers_foco`` são do dia do menor preço;
    ``mediana_mercado`` é a mediana das medianas diárias; ``serie_precos`` é o
    preço do foco dia a dia (lista, na ordem dos dias).
    """
    cols = [
        "sku", "produto", "marca", "menor_preco", "seller", "n_sellers_foco",
        "mediana_mercado", "pct_abaixo_medio", "dias_presenca", "serie_precos",
    ]
    base = _foco_vs_mercado(df, foco, **kw)
    dias_coleta = df.loc[df["marketplace"] == foco, "dia"].nunique()
    base = base.dropna(subset=["pct"])
    if base.empty or not dias_coleta:
        return pd.DataFrame(columns=cols)
    minimo = math.ceil(dias_coleta / 2.0)

    linhas = []
    for sku, g in base.sort_values("dia").groupby("sku"):
        if g["dia"].nunique() < minimo:
            continue
        menor = g.sort_values(["preco_foco", "dia"]).iloc[0]
        linhas.append({
            "sku": sku,
            "produto": menor["title"],
            "marca": menor["brand"],
            "menor_preco": float(menor["preco_foco"]),
            "seller": menor["seller"],
            "n_sellers_foco": int(menor["n_sellers"]),
            "mediana_mercado": float(g["mediana_mercado"].median()),
            "pct_abaixo_medio": float(g["pct"].mean()),
            "dias_presenca": int(g["dia"].nunique()),
            "serie_precos": [float(p) for p in g["preco_foco"]],
        })
    if not linhas:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(linhas, columns=cols)
    return out.sort_values(["pct_abaixo_medio", "sku"], ascending=[False, True]).head(limite).reset_index(drop=True)


# ── Bloco 8 — comparador ────────────────────────────────────────────────────

def historico_produtos(df: pd.DataFrame, skus: Iterable[str], foco: Optional[str] = None) -> pd.DataFrame:
    """``(dia, sku, marketplace, preco_min)``; ``foco`` restringe a um marketplace."""
    h = df[df["sku"].isin(list(skus))]
    if foco:
        h = h[h["marketplace"] == foco]
    if h.empty:
        return pd.DataFrame(columns=["dia", "sku", "marketplace", "preco_min"])
    return (
        h.groupby(["dia", "sku", "marketplace"])["preco"].min()
        .rename("preco_min").reset_index()
    )


# ── Bloco 9 — exportar ──────────────────────────────────────────────────────

def anuncios_dia(df: pd.DataFrame) -> pd.DataFrame:
    """Linhas do recorte para exportar (XLSX/CSV), datas ISO 8601."""
    out = df.rename(columns={"dia": "data", "preco": "menor_preco_a_vista"}).copy()
    out["data"] = pd.to_datetime(out["data"]).dt.strftime("%Y-%m-%d")
    return out.sort_values(["data", "marketplace", "brand", "sku", "seller"]).reset_index(drop=True)
