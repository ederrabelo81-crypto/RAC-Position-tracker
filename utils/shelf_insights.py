"""
utils/shelf_insights.py — Leitura de prateleira para o trade online (Cockpit).

Funções PURAS de pandas sobre linhas de `coletas`: nada aqui fala com banco nem
com Streamlit, para que cada número do Cockpit do Trade (`app.py`, página
Overview) seja testável em `tests/test_shelf_insights.py`.

Perguntas que o especialista de trade faz no começo do dia, e a função que
responde cada uma:

* "Quanto da prateleira neutra é nosso, e mudou?"   → `share_of_shelf`,
  `midea_share_by`, `share_trend`
* "Em qual busca genérica estamos perdendo, e para quem?" → `keyword_battle`
* "Quem ganha a buy box das NOSSAS ofertas?"        → `buybox_on_brand`
* "Nosso preço na vitrine está acima do rival no mesmo BTU?" → `shelf_price_by_btu`
* "Posso confiar na comparação de hoje?"            → `coverage_by_turno`
* "O que mudou e exige ação?"                       → `build_alerts`

Três regras que valem para todas elas:

1. **Uma observação por posição.** Cada turno do PC coletor chega DUAS vezes
   ao banco (o `main.py` grava com run_id UUID4 e o reforço
   `upload_csv.py` regrava o mesmo CSV com run_id UUID5 — conferido em
   28–29/09/2026). `dedup_snapshot` fica com a última run de cada
   (data, turno, plataforma) e uma linha por posição. Sem isso toda plataforma
   coletada no PC pesa o dobro da Amazon.
2. **Share só se mede em busca neutra de marca** (`config.BRAND_NEUTRAL_CATEGORIES`).
   Busca "ar condicionado midea" devolve SERP da Midea — somá-la infla quem tem
   mais keywords dirigidas a si. As funções recebem o recorte já feito;
   `app.py` passa só as genéricas para o KPI principal.
3. **Silêncio não é mudança de mercado** (mesma regra do `seller_coverage_daily`).
   Plataforma com coleta parcial num dos dias não gera alerta de share — um
   bloqueio do Mercado Livre à tarde não é a Midea "perdendo" a prateleira.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

__all__ = [
    "GRUPO_MIDEA",
    "is_grupo_midea",
    "dedup_snapshot",
    "top_n",
    "share_of_shelf",
    "midea_share_by",
    "share_trend",
    "comparable_days",
    "keyword_battle",
    "buybox_on_brand",
    "extract_btu",
    "is_fora_hiwall",
    "shelf_price_by_btu",
    "coverage_by_turno",
    "build_alerts",
]

#: Marcas canônicas do grupo Midea Carrier como o painel as grava em
#: `coletas.marca` (Springer e "Midea Carrier" já colapsam em "Midea"). Mesmo
#: corte de grupo do GfK que `bestsellers.config.GRUPO_MIDEA` usa.
GRUPO_MIDEA: frozenset = frozenset({"Midea", "Carrier", "Comfee"})

#: Rótulo do grupo nas tabelas agregadas.
ROTULO_GRUPO = "Midea Carrier"

_ORDEM_TURNO = {"Abertura": 0, "Tarde": 1, "Fechamento": 2}

# Faixa plausível de preço de split residencial (R$). Fora dela é acessório,
# kit, erro de parser ×10 ou produto comercial — ruído para índice de preço.
_PRECO_MIN, _PRECO_MAX = 600.0, 25_000.0

_BTU_RE = re.compile(r"(\d{1,2})[.,](\d{3})\s*btu|(\d{4,6})\s*btu", re.IGNORECASE)
_BTUS_VITRINE = (9000, 12000, 18000, 24000)

# Formatos que não competem com o split hi-wall no preço (a linha principal da
# Midea). Usado só quando a linha não tem `estado_match` (histórico sem de-para).
_RE_FORA_HIWALL = re.compile(
    r"port[aá]til|janela|janeleiro|cassete|cassette|piso[\s-]?teto|multi[\s-]?split",
    re.IGNORECASE,
)


def is_grupo_midea(marca: object) -> bool:
    """True se a marca (canônica) é do grupo Midea Carrier."""
    return isinstance(marca, str) and marca in GRUPO_MIDEA


def _marca_grupo(marca: object) -> object:
    """Colapsa as marcas do grupo no rótulo único para agregação."""
    return ROTULO_GRUPO if is_grupo_midea(marca) else marca


# ---------------------------------------------------------------------------
# 1. Base limpa: uma observação por posição
# ---------------------------------------------------------------------------

def dedup_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Última run por (data, turno, plataforma) e uma linha por posição.

    Args:
        df: linhas de `coletas` (qualquer subconjunto de colunas com, no mínimo,
            data/turno/plataforma).

    Returns:
        Cópia deduplicada. Sem `run_id`, só a deduplicação por posição vale.

    Note:
        "Última" pelo maior `created_at` da run; sem `created_at`, pelo maior
        `id`; sem nenhum dos dois, a run fica arbitrária — as duplicatas
        conhecidas são cópias idênticas, então qualquer uma serve.
    """
    if df.empty:
        return df.copy()
    out = df.copy()
    chave_turno = [c for c in ("data", "turno", "plataforma") if c in out.columns]

    if "run_id" in out.columns and chave_turno:
        rid = out["run_id"].astype("string").fillna("∅")
        ordem_col = next((c for c in ("created_at", "id") if c in out.columns), None)
        if ordem_col is not None:
            # Chave de ordem com tipo único: `created_at` chega como texto ISO do
            # Supabase e como timestamp do Parquet — misturados, o sort quebra.
            if ordem_col == "created_at":
                ordem = pd.to_datetime(out["created_at"], errors="coerce", utc=True)
            else:
                ordem = pd.to_numeric(out["id"], errors="coerce")
            ordem_col = "_ordem"
            marca_tempo = out.assign(_rid=rid, _ordem=ordem) \
                .groupby(chave_turno + ["_rid"], dropna=False)[ordem_col].max()
            vencedora = (
                marca_tempo.reset_index()
                .sort_values(ordem_col, ascending=False, kind="stable")
                .drop_duplicates(subset=chave_turno, keep="first")
            )
        else:
            vencedora = (
                out.assign(_rid=rid)[chave_turno + ["_rid"]]
                .drop_duplicates()
                .sort_values("_rid", ascending=False, kind="stable")
                .drop_duplicates(subset=chave_turno, keep="first")
            )
        chaves_ok = set(map(tuple, vencedora[chave_turno + ["_rid"]].astype(str).values))
        mask = [
            tuple(map(str, t)) in chaves_ok
            for t in zip(*(out[c] for c in chave_turno), rid)
        ]
        out = out[pd.Series(mask, index=out.index)]

    chave_pos = [c for c in ("data", "turno", "plataforma", "keyword", "posicao_organica")
                 if c in out.columns]
    if "posicao_organica" in chave_pos:
        tem_pos = out["posicao_organica"].notna()
        com_pos = out[tem_pos].drop_duplicates(subset=chave_pos, keep="first")
        out = pd.concat([com_pos, out[~tem_pos]], ignore_index=False).sort_index()
    return out.reset_index(drop=True)


def top_n(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Linhas nas `n` primeiras posições orgânicas."""
    if df.empty or "posicao_organica" not in df.columns:
        return df.iloc[0:0].copy()
    pos = pd.to_numeric(df["posicao_organica"], errors="coerce")
    return df[(pos >= 1) & (pos <= n)].copy()


def _prateleira(df: pd.DataFrame, n: int, excluir_nao_ac: bool) -> pd.DataFrame:
    base = top_n(df, n)
    if excluir_nao_ac and "estado_match" in base.columns:
        # Acessório/peça/ruído de busca ocupa posição mas não é ar-condicionado:
        # entra no denominador de prateleira como nada — fica de fora.
        base = base[base["estado_match"].astype("string").fillna("") != "NAO_AC"]
    if "marca" in base.columns:
        base = base.assign(marca=base["marca"].fillna("Desconhecida"))
    return base


# ---------------------------------------------------------------------------
# 2. Share de prateleira
# ---------------------------------------------------------------------------

def share_of_shelf(
    df: pd.DataFrame,
    by: Sequence[str] = ("plataforma",),
    n: int = 10,
    agrupar_grupo: bool = True,
    excluir_nao_ac: bool = True,
) -> pd.DataFrame:
    """Fração das `n` primeiras posições orgânicas ocupada por cada marca.

    Args:
        df: linhas já deduplicadas (`dedup_snapshot`) e já no recorte de busca.
        by: dimensões do agrupamento (ex.: ``("plataforma",)``, ``("keyword",)``).
        n: tamanho da prateleira (10 = primeira dobra na maioria das SERPs).
        agrupar_grupo: soma Midea/Carrier/Comfee em "Midea Carrier".
        excluir_nao_ac: tira do denominador o que não é ar-condicionado.

    Returns:
        Longo: ``by + [marca, slots, total, share]`` com share em 0–1.
    """
    by = [c for c in by if c in df.columns]
    base = _prateleira(df, n, excluir_nao_ac)
    colunas = by + ["marca", "slots", "total", "share"]
    if base.empty or "marca" not in base.columns:
        return pd.DataFrame(columns=colunas)
    if agrupar_grupo:
        base = base.assign(marca=base["marca"].map(_marca_grupo))
    slots = base.groupby(by + ["marca"], dropna=False).size().rename("slots").reset_index()
    if by:
        total = base.groupby(by, dropna=False).size().rename("total").reset_index()
        out = slots.merge(total, on=by, how="left")
    else:
        out = slots.assign(total=len(base))
    out["share"] = out["slots"] / out["total"]
    return out.sort_values(by + ["share"], ascending=[True] * len(by) + [False]).reset_index(drop=True)[colunas]


def midea_share_by(
    df: pd.DataFrame,
    by: Sequence[str] = ("plataforma",),
    n: int = 10,
) -> pd.DataFrame:
    """Uma linha por grupo: share do grupo Midea, líder e distância ao líder.

    Returns:
        ``by + [share_midea, slots_midea, total, lider, share_lider,
        gap_lider, melhor_pos_midea]``. `lider` é a maior marca que NÃO é do
        grupo (o rival a bater); `gap_lider` = share_lider − share_midea.
    """
    by = [c for c in by if c in df.columns]
    colunas = by + ["share_midea", "slots_midea", "total", "lider",
                    "share_lider", "gap_lider", "melhor_pos_midea"]
    longo = share_of_shelf(df, by=by, n=n)
    if longo.empty:
        return pd.DataFrame(columns=colunas)
    if not by:
        # Sem dimensão: agrega tudo num grupo só com uma chave técnica.
        longo = longo.assign(_todos=0)
        chave = ["_todos"]
    else:
        chave = list(by)

    total = longo.groupby(chave, dropna=False)["total"].first().rename("total")
    midea = (longo[longo["marca"] == ROTULO_GRUPO]
             .groupby(chave, dropna=False)["slots"].sum().rename("slots_midea"))
    rivais = (longo[longo["marca"] != ROTULO_GRUPO]
              .sort_values("share", ascending=False, kind="stable")
              .drop_duplicates(subset=chave, keep="first")
              .set_index(chave)[["marca", "share"]]
              .rename(columns={"marca": "lider", "share": "share_lider"}))

    base = _prateleira(df, n, True)
    if not by:
        base = base.assign(_todos=0)
    so_midea = base[base["marca"].map(is_grupo_midea)].copy()
    so_midea["_pos"] = pd.to_numeric(so_midea["posicao_organica"], errors="coerce")
    melhor = so_midea.groupby(chave, dropna=False)["_pos"].min().rename("melhor_pos_midea")

    out = pd.concat([total, midea, rivais, melhor], axis=1)
    out["slots_midea"] = out["slots_midea"].fillna(0).astype(int)
    out["total"] = out["total"].astype(int)
    out["share_midea"] = out["slots_midea"] / out["total"]
    out["share_lider"] = out["share_lider"].fillna(0.0).astype(float)
    out["gap_lider"] = out["share_lider"] - out["share_midea"]
    out["melhor_pos_midea"] = out["melhor_pos_midea"].map(
        lambda v: int(v) if pd.notna(v) else None
    ).astype(object)
    out = out.reset_index()
    if not by:
        out = out.drop(columns=["_todos"])
    return out[colunas]


def share_trend(
    df: pd.DataFrame,
    n: int = 10,
    marcas: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Share diário por marca, com peso igual por plataforma.

    Pesar pela contagem crua deixaria a série refém da cobertura: o dia em que
    o Mercado Livre coleta 1.900 linhas e o seguinte em que coleta 160, o mix
    muda e o share "se move" sem o mercado mudar. Média simples entre as
    plataformas observadas no dia neutraliza isso.

    Returns:
        ``[data, marca, share, plataformas]`` — `plataformas` é quantas entraram
        na média daquele dia.
    """
    if df.empty or "data" not in df.columns:
        return pd.DataFrame(columns=["data", "marca", "share", "plataformas"])
    longo = share_of_shelf(df, by=("data", "plataforma"), n=n)
    if longo.empty:
        return pd.DataFrame(columns=["data", "marca", "share", "plataformas"])
    # Completa zeros: marca ausente numa plataforma tem share 0 ali, não "sem dado".
    grade = longo.pivot_table(index=["data", "plataforma"], columns="marca",
                              values="share", aggfunc="sum", fill_value=0.0)
    media = grade.groupby(level="data").mean()
    n_plat = grade.groupby(level="data").size().rename("plataformas")
    out = media.stack().rename("share").reset_index()
    out.columns = ["data", "marca", "share"]
    out = out.merge(n_plat.reset_index(), on="data", how="left")
    if marcas is not None:
        alvo = set(marcas) | {ROTULO_GRUPO}
        out = out[out["marca"].isin(alvo)]
    return out.sort_values(["data", "share"], ascending=[True, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Comparação justa entre dias
# ---------------------------------------------------------------------------

def comparable_days(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    """Último dia do recorte vs. o dia anterior, nos MESMOS turnos por plataforma.

    O último dia costuma estar incompleto (às 10h só existe a Abertura). Comparar
    a Abertura de hoje com o dia inteiro de ontem mistura mix de turno com
    mudança de mercado. Aqui, cada plataforma entra só com os turnos que os dois
    dias têm em comum — e, dentro deles, só com as buscas que os dois dias
    coletaram.

    Returns:
        ``(df_ultimo, df_anterior, meta)`` — meta traz ``ultimo``, ``anterior``,
        ``turnos`` ({plataforma: [turnos comparados]}) e ``buscas_descartadas``
        (pares plataforma × turno × busca vistos em só um dos dias). Sem dois
        dias, o anterior volta vazio.
    """
    meta: Dict[str, object] = {"ultimo": None, "anterior": None, "turnos": {}}
    if df.empty or "data" not in df.columns:
        return df.iloc[0:0], df.iloc[0:0], meta
    dias = sorted(pd.Series(df["data"]).dropna().unique())
    if not dias:
        return df.iloc[0:0], df.iloc[0:0], meta
    ultimo = dias[-1]
    meta["ultimo"] = ultimo
    d_ult = df[df["data"] == ultimo]
    if len(dias) < 2:
        return d_ult, df.iloc[0:0], meta
    anterior = dias[-2]
    meta["anterior"] = anterior
    d_ant = df[df["data"] == anterior]
    if "turno" not in df.columns or "plataforma" not in df.columns:
        return d_ult, d_ant, meta

    turnos_ult = d_ult.groupby("plataforma")["turno"].agg(lambda s: set(s.dropna()))
    turnos_ant = d_ant.groupby("plataforma")["turno"].agg(lambda s: set(s.dropna()))
    comuns: Dict[str, List[str]] = {}
    for plat in set(turnos_ult.index) & set(turnos_ant.index):
        inter = turnos_ult[plat] & turnos_ant[plat]
        if inter:
            comuns[plat] = sorted(inter, key=lambda t: _ORDEM_TURNO.get(t, 9))
    meta["turnos"] = comuns

    def _recorta(d: pd.DataFrame) -> pd.DataFrame:
        mask = [t in comuns.get(p, ()) for p, t in zip(d["plataforma"], d["turno"])]
        return d[pd.Series(mask, index=d.index, dtype=bool)]

    d_ult, d_ant = _recorta(d_ult), _recorta(d_ant)

    # Mesmas BUSCAS também: se o Mercado Livre coletou 14 buscas ontem e 10
    # hoje (caso real, 28→29/09/2026), o share muda só porque o conjunto de
    # SERPs mudou. Cada (plataforma, turno) entra só com as keywords que os
    # dois dias observaram.
    meta["buscas_descartadas"] = 0
    if "keyword" in df.columns:
        chave = ["plataforma", "turno", "keyword"]
        k_ult = set(map(tuple, d_ult[chave].drop_duplicates().values))
        k_ant = set(map(tuple, d_ant[chave].drop_duplicates().values))
        comuns_kw = k_ult & k_ant
        meta["buscas_descartadas"] = len(k_ult - comuns_kw) + len(k_ant - comuns_kw)

        def _recorta_kw(d: pd.DataFrame) -> pd.DataFrame:
            mask = [tuple(r) in comuns_kw for r in d[chave].values]
            return d[pd.Series(mask, index=d.index, dtype=bool)]

        d_ult, d_ant = _recorta_kw(d_ult), _recorta_kw(d_ant)

    return d_ult, d_ant, meta


# ---------------------------------------------------------------------------
# 4. Batalha por keyword
# ---------------------------------------------------------------------------

def keyword_battle(
    df_ultimo: pd.DataFrame,
    df_anterior: Optional[pd.DataFrame] = None,
    n: int = 10,
) -> pd.DataFrame:
    """Share da Midea por keyword (todas as plataformas somadas), com delta.

    Returns:
        ``[keyword, share_midea, delta_pp, melhor_pos_midea, lider,
        share_lider, gap_lider, plataformas]``, ordenado pela maior distância ao
        líder — o topo da tabela é onde a prateleira mais escapa.
    """
    colunas = ["keyword", "share_midea", "delta_pp", "melhor_pos_midea",
               "lider", "share_lider", "gap_lider", "plataformas"]
    if df_ultimo.empty or "keyword" not in df_ultimo.columns:
        return pd.DataFrame(columns=colunas)
    atual = midea_share_by(df_ultimo, by=("keyword",), n=n)
    if atual.empty:
        return pd.DataFrame(columns=colunas)
    plats = df_ultimo.groupby("keyword")["plataforma"].nunique().rename("plataformas") \
        if "plataforma" in df_ultimo.columns else pd.Series(dtype=int, name="plataformas")
    atual = atual.merge(plats.reset_index(), on="keyword", how="left")
    atual["delta_pp"] = None
    if df_anterior is not None and not df_anterior.empty:
        antes = midea_share_by(df_anterior, by=("keyword",), n=n)[["keyword", "share_midea"]]
        antes = antes.rename(columns={"share_midea": "_antes"})
        atual = atual.merge(antes, on="keyword", how="left")
        atual["delta_pp"] = (atual["share_midea"] - atual["_antes"]) * 100
        atual = atual.drop(columns=["_antes"])
    return atual.sort_values(["gap_lider", "share_midea"], ascending=[False, True]) \
        .reset_index(drop=True)[colunas]


# ---------------------------------------------------------------------------
# 5. Buy box das ofertas do grupo
# ---------------------------------------------------------------------------

def buybox_on_brand(
    df: pd.DataFrame,
    plataformas: Optional[Iterable[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Quem vende as ofertas do grupo Midea quando a buy box foi observada.

    Usa SÓ `buy_box_seller`: o fallback em `seller` é enganoso aqui — na Amazon
    ele é sempre "Amazon" e na Casas Bahia é "Casas Bahia" quando a buy box não
    veio. Oferta sem buy box observada entra na cobertura, não no ranking.

    Args:
        df: linhas deduplicadas.
        plataformas: restringe às superfícies com buy box disputada
            (marketplaces). ``None`` = todas as do recorte.

    Returns:
        ``(ranking, meta)``. Ranking: ``[seller, ofertas, share, plataformas]``;
        meta: ``ofertas_midea``, ``com_buybox`` e ``cobertura`` (0–1).
    """
    meta = {"ofertas_midea": 0, "com_buybox": 0, "cobertura": 0.0}
    colunas = ["seller", "ofertas", "share", "plataformas"]
    if df.empty or "marca" not in df.columns:
        return pd.DataFrame(columns=colunas), meta
    base = df[df["marca"].map(is_grupo_midea)]
    if plataformas is not None and "plataforma" in base.columns:
        base = base[base["plataforma"].isin(set(plataformas))]
    # Uma oferta = (plataforma, produto) no dia/turno — a mesma oferta aparece em
    # várias keywords e não pode pesar N vezes.
    chave = [c for c in ("data", "turno", "plataforma", "produto") if c in base.columns]
    if chave:
        base = base.drop_duplicates(subset=chave)
    meta["ofertas_midea"] = int(len(base))
    if base.empty or "buy_box_seller" not in base.columns:
        return pd.DataFrame(columns=colunas), meta
    com_bb = base[base["buy_box_seller"].notna() & (base["buy_box_seller"].astype("string").str.strip() != "")]
    meta["com_buybox"] = int(len(com_bb))
    meta["cobertura"] = len(com_bb) / len(base) if len(base) else 0.0
    if com_bb.empty:
        return pd.DataFrame(columns=colunas), meta
    rank = (
        com_bb.groupby("buy_box_seller")
        .agg(ofertas=("buy_box_seller", "size"),
             plataformas=("plataforma", lambda s: ", ".join(sorted(set(s.dropna())))))
        .reset_index()
        .rename(columns={"buy_box_seller": "seller"})
    )
    rank["share"] = rank["ofertas"] / rank["ofertas"].sum()
    return rank.sort_values("ofertas", ascending=False).reset_index(drop=True)[colunas], meta


# ---------------------------------------------------------------------------
# 6. Preço na vitrine por capacidade
# ---------------------------------------------------------------------------

def extract_btu(produto: object) -> Optional[int]:
    """Capacidade em BTU lida do nome ("12.000 BTUs", "9000btu")."""
    if not isinstance(produto, str):
        return None
    m = _BTU_RE.search(produto)
    if not m:
        return None
    if m.group(3):
        return int(m.group(3))
    return int(m.group(1)) * 1000 + int(m.group(2))


def is_fora_hiwall(produto: object) -> bool:
    """True se o nome indica um formato que não compete com o split hi-wall.

    Portátil, janela, cassete, piso-teto e multi-split no mesmo BTU custam
    outra coisa: misturá-los numa mediana/moda de preço por marca muda o número
    sem o mercado mudar.
    """
    return isinstance(produto, str) and bool(_RE_FORA_HIWALL.search(produto))


def shelf_price_by_btu(df: pd.DataFrame, btus: Sequence[int] = _BTUS_VITRINE) -> pd.DataFrame:
    """Mediana de preço na vitrine: grupo Midea vs. concorrentes, por BTU.

    É o preço que o consumidor VÊ na SERP (coleta de posição), não o preço de
    referência por SKU do PriceTrack — pergunta diferente, e por isso a tabela
    diz a fonte. Só split hi-wall: portátil/janela/cassete no mesmo BTU custa
    outra coisa e distorceria o índice.

    Returns:
        ``[btu, mediana_midea, mediana_rivais, indice, ofertas_midea,
        ofertas_rivais]`` — índice = Midea / rivais × 100 (100 = paridade).
    """
    colunas = ["btu", "mediana_midea", "mediana_rivais", "indice",
               "ofertas_midea", "ofertas_rivais"]
    if df.empty or not {"produto", "preco", "marca"}.issubset(df.columns):
        return pd.DataFrame(columns=colunas)
    base = df.copy()
    base["preco"] = pd.to_numeric(base["preco"], errors="coerce")
    base = base[(base["preco"] >= _PRECO_MIN) & (base["preco"] <= _PRECO_MAX)]
    if "estado_match" in base.columns:
        est = base["estado_match"].astype("string")
        sem_estado = est.isna()
        fora = base["produto"].astype("string").fillna("").str.contains(_RE_FORA_HIWALL)
        base = base[(est == "MAPEADO") | (sem_estado & ~fora)]
    else:
        base = base[~base["produto"].astype("string").fillna("").str.contains(_RE_FORA_HIWALL)]
    base = base.assign(btu=base["produto"].map(extract_btu))
    base = base[base["btu"].isin(list(btus))]
    # Uma oferta conta uma vez por dia/turno/plataforma, qualquer que seja a keyword.
    chave = [c for c in ("data", "turno", "plataforma", "produto", "preco") if c in base.columns]
    base = base.drop_duplicates(subset=chave)
    if base.empty:
        return pd.DataFrame(columns=colunas)
    base = base.assign(_midea=base["marca"].map(is_grupo_midea))
    linhas = []
    for btu in btus:
        b = base[base["btu"] == btu]
        m = b.loc[b["_midea"], "preco"]
        r = b.loc[~b["_midea"] & (b["marca"] != "Desconhecida"), "preco"]
        if m.empty and r.empty:
            continue
        med_m = float(m.median()) if not m.empty else None
        med_r = float(r.median()) if not r.empty else None
        linhas.append({
            "btu": btu,
            "mediana_midea": med_m,
            "mediana_rivais": med_r,
            "indice": (med_m / med_r * 100) if (med_m and med_r) else None,
            "ofertas_midea": int(len(m)),
            "ofertas_rivais": int(len(r)),
        })
    return pd.DataFrame(linhas, columns=colunas)


# ---------------------------------------------------------------------------
# 7. Cobertura — dá para confiar na comparação?
# ---------------------------------------------------------------------------

def coverage_by_turno(df: pd.DataFrame, limite: float = 0.3) -> pd.DataFrame:
    """Linhas por (data, turno, plataforma) e o flag de coleta parcial.

    Parcial = menos de `limite` × o maior volume que a plataforma teve num turno
    da janela. É o caso real do Mercado Livre em 28–29/09/2026: 1.938 linhas
    na Abertura e 32–166 na Tarde/Fechamento (login gate), não uma queda de
    mercado.

    Returns:
        ``[data, turno, plataforma, linhas, referencia, parcial]``.
    """
    colunas = ["data", "turno", "plataforma", "linhas", "referencia", "parcial"]
    if df.empty or not {"data", "turno", "plataforma"}.issubset(df.columns):
        return pd.DataFrame(columns=colunas)
    cont = df.groupby(["data", "turno", "plataforma"]).size().rename("linhas").reset_index()
    ref = cont.groupby("plataforma")["linhas"].max().rename("referencia")
    cont = cont.merge(ref.reset_index(), on="plataforma", how="left")
    cont["parcial"] = cont["linhas"] < (cont["referencia"] * limite)
    cont["_o"] = cont["turno"].map(lambda t: _ORDEM_TURNO.get(t, 9))
    return cont.sort_values(["data", "_o", "plataforma"]).drop(columns="_o") \
        .reset_index(drop=True)[colunas]


# ---------------------------------------------------------------------------
# 8. Alertas do dia
# ---------------------------------------------------------------------------

def build_alerts(
    df_ultimo: pd.DataFrame,
    df_anterior: pd.DataFrame,
    cobertura: Optional[pd.DataFrame] = None,
    limiar_pp: float = 5.0,
    n: int = 10,
) -> List[Dict[str, str]]:
    """Mudanças que pedem ação, em linguagem de trade.

    Regras (cada uma só dispara com os dois dias comparáveis):

    * share do grupo numa plataforma variou ≥ `limiar_pp` → perda/ganho;
    * rival ganhou ≥ `limiar_pp` numa plataforma → ataque;
    * a Midea tinha top 3 numa keyword e saiu (ou entrou) → posição;
    * plataforma com coleta parcial no último dia → ressalva de dado.

    Returns:
        Lista de ``{"nivel": "alta"|"media"|"info"|"positivo", "texto": str}``,
        mais graves primeiro.
    """
    alertas: List[Dict[str, str]] = []
    parciais: set = set()
    if cobertura is not None and not cobertura.empty and "data" in df_ultimo.columns and not df_ultimo.empty:
        ultimo = df_ultimo["data"].max()
        p = cobertura[(cobertura["data"] == ultimo) & cobertura["parcial"]]
        for plat, g in p.groupby("plataforma"):
            parciais.add(plat)
            turnos = ", ".join(sorted(set(g["turno"]), key=lambda t: _ORDEM_TURNO.get(t, 9)))
            alertas.append({
                "nivel": "info",
                "texto": f"{plat}: coleta parcial em {turnos} — share dessa plataforma "
                         "nesses turnos fica fora dos alertas (silêncio não é mercado).",
            })

    if df_anterior is None or df_anterior.empty or df_ultimo.empty:
        return _ordena(alertas)

    atual = midea_share_by(df_ultimo, by=("plataforma",), n=n).set_index("plataforma")
    antes = midea_share_by(df_anterior, by=("plataforma",), n=n).set_index("plataforma")
    for plat in atual.index.intersection(antes.index):
        if plat in parciais:
            continue
        d = (atual.at[plat, "share_midea"] - antes.at[plat, "share_midea"]) * 100
        if d <= -limiar_pp:
            alertas.append({"nivel": "alta", "texto":
                f"{plat}: Midea perdeu {abs(d):.1f} pp de prateleira no top {n} "
                f"({antes.at[plat, 'share_midea']:.0%} → {atual.at[plat, 'share_midea']:.0%})."})
        elif d >= limiar_pp:
            alertas.append({"nivel": "positivo", "texto":
                f"{plat}: Midea ganhou {d:.1f} pp de prateleira no top {n} "
                f"({antes.at[plat, 'share_midea']:.0%} → {atual.at[plat, 'share_midea']:.0%})."})

    rivais_atual = share_of_shelf(df_ultimo, by=("plataforma",), n=n)
    rivais_antes = share_of_shelf(df_anterior, by=("plataforma",), n=n)
    if not rivais_atual.empty and not rivais_antes.empty:
        m = rivais_atual.merge(rivais_antes, on=["plataforma", "marca"], how="left",
                               suffixes=("", "_antes")).fillna({"share_antes": 0.0})
        m = m[(m["marca"] != ROTULO_GRUPO) & ~m["plataforma"].isin(parciais)
              & m["plataforma"].isin(rivais_antes["plataforma"].unique())]
        m["d"] = (m["share"] - m["share_antes"]) * 100
        for _, r in m[m["d"] >= limiar_pp].sort_values("d", ascending=False).head(5).iterrows():
            alertas.append({"nivel": "media", "texto":
                f"{r['plataforma']}: {r['marca']} avançou {r['d']:.1f} pp no top {n} "
                f"({r['share_antes']:.0%} → {r['share']:.0%})."})

    if "keyword" in df_ultimo.columns:
        pos_a = midea_share_by(df_ultimo, by=("plataforma", "keyword"), n=n)
        pos_b = midea_share_by(df_anterior, by=("plataforma", "keyword"), n=n)
        if not pos_a.empty and not pos_b.empty:
            j = pos_a.merge(pos_b, on=["plataforma", "keyword"], suffixes=("", "_antes"))
            j = j[~j["plataforma"].isin(parciais)]
            perdeu = j[(j["melhor_pos_midea_antes"].fillna(99) <= 3) & (j["melhor_pos_midea"].fillna(99) > 3)]
            ganhou = j[(j["melhor_pos_midea_antes"].fillna(99) > 3) & (j["melhor_pos_midea"].fillna(99) <= 3)]
            # Uma linha por plataforma, não por busca: 15 alertas "saiu do top 3"
            # soltos escondem o padrão (é a plataforma inteira que caiu?).
            for plat, g in perdeu.groupby("plataforma"):
                itens = [
                    f"\"{r['keyword']}\" (#{int(r['melhor_pos_midea_antes'])} → "
                    + ("fora do top 10" if pd.isna(r["melhor_pos_midea"]) else f"#{int(r['melhor_pos_midea'])}")
                    + (f", líder {r['lider']}" if r["lider"] else "") + ")"
                    for _, r in g.sort_values("melhor_pos_midea_antes").iterrows()
                ]
                mais = f" e mais {len(itens) - 3}" if len(itens) > 3 else ""
                alertas.append({"nivel": "alta", "texto":
                    f"{plat}: Midea saiu do top 3 em {len(itens)} busca(s) — "
                    f"{'; '.join(itens[:3])}{mais}."})
            for plat, g in ganhou.groupby("plataforma"):
                kws = [f"\"{k}\"" for k in g["keyword"]]
                mais = f" e mais {len(kws) - 3}" if len(kws) > 3 else ""
                alertas.append({"nivel": "positivo", "texto":
                    f"{plat}: Midea entrou no top 3 em {len(kws)} busca(s) — "
                    f"{', '.join(kws[:3])}{mais}."})
    return _ordena(alertas)


def _ordena(alertas: List[Dict[str, str]]) -> List[Dict[str, str]]:
    peso = {"alta": 0, "media": 1, "positivo": 2, "info": 3}
    return sorted(alertas, key=lambda a: peso.get(a["nivel"], 9))
