"""Sell-out DIÁRIO do Mercado Livre a partir dos exports da Nubimetrics.

Puro (sem rede, sem Streamlit): classificação, chaves, agregação e as contas do
painel 📅 Diário. ``tests/test_nubimetrics_diario.py``.

Fonte: os templates salvos em *Relatórios dinâmicos por publicações → Análise
mensal* (``/company/itemreport``), lidos por ``export_pivot.ler_export``. O mais
fino é "RAC | Top anuncios por dia" (título → marca → seller × dia); os outros
quatro são agregados do mesmo relatório e servem só para conferência.

Limitações que o painel mostra (não esconde):
- **sem código do anúncio**: o template agrupa por TÍTULO; anúncios com o
  mesmo título, marca e seller viram uma linha (``anuncio_key``);
- **título truncado em 40 caracteres** em ~37% das unidades — sem o export de
  atributos, o segmento HW Inverter fica subestimado (ver ``origem``);
- são ESTIMATIVAS da Nubimetrics, com defasagem de ~2 dias.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from nubimetrics.filters import SEG_HW_INVERTER, faixa_btu, is_midea, parse_btu

SEG_OUTROS_AC = "Outros AC"
FAIXA_ND = "n.d."

# ── Regra High Wall Inverter (especificação do mantenedor, 17/11/2026) ──────
#: Título que nomeia um tipo que NÃO é hi-wall (ou peça/condensadora avulsa).
#: Sem "PAC": a Philco usa PAC nos splits também; o portátil cai por "portátil".
EXCLUDE_TITLE = re.compile(
    r"janela|port[aá]til|piso|teto|cassete|cassette|\bk7\b|multi|dut[oa]|condensadora|"
    r"evaporadora|cortina|climatizador|window|bi-?split|tri-?split|built|\bacj\b",
    re.I,
)
#: Atributo "tipo de ar condicionado" que exclui (comparação do valor inteiro).
EXCLUDE_TYPE = re.compile(
    r"^(janela|port[aá]til|eletr[oô]nico|mec[aâ]nico|piso teto|multisplit|cassette|cassete.*|"
    r"split teto|portable|central|fan coil|condensadora|evaporadora|ar condicionado janela|"
    r"vertical\s+barril)$",
    re.I,
)
#: Inverter pelo título. ``inver?$``/``inve$`` pegam o título cortado em 40
#: caracteres ("…Elgin Eco Inver"); a especificação original só tinha ``inve$``.
INVERTER = re.compile(r"invert|inver?$|\binv\b|airvolution|ecomaster|windfree", re.I)

ORIGEM_OVERRIDE = "override"
ORIGEM_ATRIBUTO = "atributo"
ORIGEM_TITULO = "titulo"

_MASCARADO = re.compile(r"^[a-zà-ü]+(\.[a-zà-ü]+){2}$")

#: Marcas com linha própria nos gráficos; o resto vira "Outros".
MARCAS_FOCO = ("Midea", "LG", "Gree", "Samsung", "Philco", "Electrolux", "Britânia", "Agratto", "TCL", "Elgin")
OUTRAS = "Outros"

#: Datas promocionais destacadas na curva diária (mês, dia) + Black Friday móvel.
DATAS_FIXAS = {(11, 11): "11/11", (12, 25): "Natal", (12, 24): "Véspera de Natal"}

#: Parceiros prioritários (rótulo do painel). A grafia do Nubimetrics vem de
#: ``dim_partner_sellers``; isto é só a ordem padrão do seletor.
PARCEIROS_PRIORITARIOS = ("Dufrio", "Clima Rio", "Central Ar", "Frio Peças", "Bel Micro",
                          "Web Continental ES", "Leveros", "Web Continental")


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _s(v: object) -> str:
    """Texto ou vazio: atributo ausente chega como ``None`` ou ``NaN`` (pandas)."""
    return v if isinstance(v, str) else ""


def is_hw_inverter(titulo: Optional[str], ac_type_raw: Optional[str] = None,
                   inverter_raw: Optional[str] = None) -> bool:
    """High Wall Inverter pela regra híbrida título + atributos.

    Args:
        titulo: Título do anúncio (pode vir truncado).
        ac_type_raw: Atributo "tipo de ar condicionado" (texto livre).
        inverter_raw: Atributo "com tecnologia inverter" (``Sim``/``Não``/vazio).
    """
    t = _s(titulo)
    tipo = _s(ac_type_raw).strip()
    inverter_raw = _s(inverter_raw)
    if EXCLUDE_TITLE.search(t) or (tipo and EXCLUDE_TYPE.fullmatch(tipo)):
        return False
    return bool(INVERTER.search(t)) or (inverter_raw or "").strip().lower() == "sim" \
        or "inverter" in tipo.lower()


@dataclass(frozen=True)
class Classe:
    """Segmento, faixa de BTU e de onde veio a decisão."""

    segmento: str
    faixa_btu: str
    origem: str


def classificar(titulo: Optional[str], atributos: Optional[Mapping[str, Optional[str]]] = None,
                override: Optional[str] = None) -> Classe:
    """Classe de um anúncio. Override manual vence; atributo complementa o título.

    Args:
        titulo: Título do anúncio.
        atributos: ``ac_type_raw``, ``inverter_raw``, ``btu_raw`` (qualquer um pode faltar).
        override: Segmento forçado pela tabela ``nubi_segment_override``.
    """
    a = atributos or {}
    btu = parse_btu(_s(a.get("btu_raw"))) or parse_btu(titulo)
    faixa = faixa_btu(btu) or FAIXA_ND
    if override:
        return Classe(override, faixa, ORIGEM_OVERRIDE)
    tem_attr = any(_s(a.get(k)) for k in ("ac_type_raw", "inverter_raw"))
    pelo_titulo = is_hw_inverter(titulo)
    hw = is_hw_inverter(titulo, a.get("ac_type_raw"), a.get("inverter_raw"))
    origem = ORIGEM_ATRIBUTO if tem_attr and hw != pelo_titulo else ORIGEM_TITULO
    return Classe(SEG_HW_INVERTER if hw else SEG_OUTROS_AC, faixa, origem)


def anuncio_key(titulo: str, marca: str, seller: str) -> str:
    """Chave estável de (título, marca, seller): o export não traz o código do anúncio."""
    bruto = "\x1f".join((titulo or "", marca or "", seller or ""))
    return hashlib.md5(bruto.encode("utf-8")).hexdigest()[:20]


def is_mascarado(seller: Optional[str]) -> bool:
    """Alias que a Nubimetrics gera para seller anônimo (``castor.magenta.abstrato``)."""
    return bool(seller) and bool(_MASCARADO.match(str(seller).strip()))


def seller_exibicao(seller: Optional[str], parceiros: Optional[Mapping[str, str]] = None) -> str:
    """Nome do seller no painel. Mascarado nunca é desanonimizado."""
    s = (seller or "").strip()
    if not s:
        return "—"
    if is_mascarado(s):
        return f"Seller não identificado (#{hashlib.md5(s.encode()).hexdigest()[:6]})"
    if parceiros and s in parceiros:
        return parceiros[s]
    return s


def marca_grupo(marca: Optional[str]) -> str:
    """Marca agrupada para gráfico: as grafias Midea somadas, foco + "Outros"."""
    if is_midea(marca):
        return "Midea"
    norm = _sem_acento(str(marca or "")).strip().upper()
    for m in MARCAS_FOCO:
        if _sem_acento(m).upper() == norm or (m == "LG" and norm == "LG ELETRONICS"):
            return m
    return OUTRAS


# ── montagem das linhas ─────────────────────────────────────────────────────

def montar_linhas(folhas: pd.DataFrame, atributos: Optional[pd.DataFrame] = None,
                  overrides: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """Folhas do export "Top anúncios por dia" → linhas classificadas.

    Args:
        folhas: ``PivotExport.linhas`` com ``titulo``, ``marca``, ``seller``, ``data``,
            ``unidades``, ``gmv_brl``.
        atributos: Uma linha por ``titulo`` (+ ``marca`` opcional) com
            ``ac_type_raw``/``inverter_raw``/``btu_raw`` (``ler_atributos``).
        overrides: ``{titulo ou anuncio_key: segmento}``.
    """
    falta = {"titulo", "marca", "seller", "data", "unidades", "gmv_brl"} - set(folhas.columns)
    if falta:
        raise ValueError(f"export sem as colunas {sorted(falta)} — use o template "
                         "'RAC | Top anuncios por dia' (título → marca → seller)")
    df = folhas.copy()
    df = df[(df["unidades"] != 0) | (df["gmv_brl"] != 0)]
    df["anuncio_key"] = [anuncio_key(t, m, s) for t, m, s in zip(df["titulo"], df["marca"], df["seller"])]
    attr_idx: Dict[Tuple[str, str], Dict[str, Optional[str]]] = {}
    if atributos is not None and not atributos.empty:
        for r in atributos.to_dict("records"):
            reg = {k: r.get(k) for k in ("ac_type_raw", "inverter_raw", "btu_raw")}
            attr_idx[(str(r.get("titulo") or ""), str(r.get("marca") or ""))] = reg
            attr_idx.setdefault((str(r.get("titulo") or ""), ""), reg)
    ov = dict(overrides or {})
    classes: Dict[Tuple[str, str, str], Classe] = {}
    for t, m, k in df[["titulo", "marca", "anuncio_key"]].drop_duplicates().itertuples(index=False):
        a = attr_idx.get((t, m)) or attr_idx.get((t, ""))
        classes[(t, m, k)] = classificar(t, a, ov.get(k) or ov.get(t))
    c = [classes[(t, m, k)] for t, m, k in zip(df["titulo"], df["marca"], df["anuncio_key"])]
    df["segmento"] = [x.segmento for x in c]
    df["faixa_btu"] = [x.faixa_btu for x in c]
    df["origem"] = [x.origem for x in c]
    df["data"] = pd.to_datetime(df["data"]).dt.date
    return df.reset_index(drop=True)


def agregar(linhas: pd.DataFrame) -> pd.DataFrame:
    """Dia × marca × seller × segmento × faixa — a tabela que vai ao banco."""
    chave = ["data", "marca", "seller", "segmento", "faixa_btu"]
    g = linhas.groupby(chave, dropna=False).agg(
        unidades=("unidades", "sum"), gmv_brl=("gmv_brl", "sum"), anuncios=("anuncio_key", "nunique"),
    ).reset_index()
    g["gmv_brl"] = g["gmv_brl"].round(2)
    return g


def conferir_agregados(linhas: pd.DataFrame, outro: pd.DataFrame, chaves: Sequence[str],
                       tolerancia: float = 0.005) -> List[str]:
    """Confere o "Top anúncios" contra um export agregado (por marca, por seller…).

    Os 5 templates são o mesmo relatório: se a soma por marca do mais fino não
    bate com o "Diário por Marca", um dos dois foi exportado com outro filtro.
    """
    med = [m for m in ("unidades", "gmv_brl") if m in outro.columns]
    a = linhas.groupby(["data", *chaves])[med].sum()
    b = outro.groupby(["data", *chaves])[med].sum()
    j = a.join(b, how="outer", lsuffix="_top", rsuffix="_ref").fillna(0.0)
    erros: List[str] = []
    for m in med:
        dif = (j[f"{m}_top"] - j[f"{m}_ref"]).abs()
        lim = (j[f"{m}_ref"].abs() * tolerancia).clip(lower=1.0)
        ruins = j[dif > lim]
        for idx, r in ruins.head(5).iterrows():
            erros.append(f"{m} {idx}: top={r[f'{m}_top']:,.0f} × ref={r[f'{m}_ref']:,.0f}")
    return erros


# ── contas do painel ────────────────────────────────────────────────────────

def black_friday(ano: int) -> date:
    """Última sexta-feira de novembro."""
    d = date(ano, 11, 30)
    return d - timedelta(days=(d.weekday() - 4) % 7)


def datas_promocionais(inicio: date, fim: date) -> Dict[date, str]:
    """Datas promocionais dentro do intervalo (11/11, Black Friday, Natal)."""
    out: Dict[date, str] = {}
    d = inicio
    while d <= fim:
        if (d.month, d.day) in DATAS_FIXAS:
            out[d] = DATAS_FIXAS[(d.month, d.day)]
        if d.month == 11 and d == black_friday(d.year):
            out[d] = "Black Friday"
        d += timedelta(days=1)
    return out


def periodo_anterior(inicio: date, fim: date) -> Tuple[date, date]:
    """Janela de mesmo tamanho imediatamente antes."""
    n = (fim - inicio).days + 1
    return inicio - timedelta(days=n), inicio - timedelta(days=1)


def kpis(df: pd.DataFrame, anterior: Optional[pd.DataFrame] = None) -> Dict[str, Optional[float]]:
    """Unidades, GMV, preço médio, share e posição da Midea (+ Δ vs período anterior).

    Δ só existe se o período anterior tiver dado (``None`` = sem comparação,
    nunca 0): o export começa no dia 1 do mês e o mês anterior pode não estar
    carregado.
    """
    def _base(x: pd.DataFrame) -> Dict[str, Optional[float]]:
        if x is None or x.empty:
            return {}
        u, g = float(x["unidades"].sum()), float(x["gmv_brl"].sum())
        mid = x[x["marca"].map(is_midea)]
        por_marca = x.assign(_g=x["marca"].map(lambda m: "Midea" if is_midea(m) else str(m))) \
            .groupby("_g")["unidades"].sum().sort_values(ascending=False)
        pos = int(list(por_marca.index).index("Midea") + 1) if "Midea" in por_marca.index else None
        return {
            "unidades": u, "gmv_brl": g, "preco_medio": g / u if u else None,
            "share_midea_unid": float(mid["unidades"].sum()) / u if u else None,
            "share_midea_gmv": float(mid["gmv_brl"].sum()) / g if g else None,
            "posicao_midea": pos,
        }

    atual = _base(df)
    ant = _base(anterior) if anterior is not None else {}
    out: Dict[str, Optional[float]] = dict(atual)
    for k in ("unidades", "gmv_brl", "preco_medio"):
        a, b = atual.get(k), ant.get(k)
        out[f"delta_{k}"] = (a / b - 1) if a is not None and b else None
    for k in ("share_midea_unid", "share_midea_gmv"):
        a, b = atual.get(k), ant.get(k)
        out[f"delta_{k}"] = (a - b) if a is not None and b is not None else None
    a, b = atual.get("posicao_midea"), ant.get("posicao_midea")
    out["delta_posicao_midea"] = (b - a) if a and b else None
    return out


def serie_por_marca(df: pd.DataFrame, medida: str = "unidades") -> pd.DataFrame:
    """Dia × marca agrupada (Midea somada, foco + Outros)."""
    x = df.assign(marca_g=df["marca"].map(marca_grupo))
    return x.groupby(["data", "marca_g"])[medida].sum().reset_index()


def share_diario(df: pd.DataFrame, medida: str = "unidades") -> pd.DataFrame:
    """Share de cada marca agrupada no dia (soma 1 por dia)."""
    s = serie_por_marca(df, medida)
    tot = s.groupby("data")[medida].transform("sum")
    s["share"] = s[medida] / tot.where(tot != 0)
    return s


def matriz_seller(df: pd.DataFrame, medida: str = "unidades", top: int = 25,
                  parceiros: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """Matriz seller × dia, linhas ordenadas pelo total do período (top N)."""
    x = df.assign(seller_d=df["seller"].map(lambda s: seller_exibicao(s, parceiros)))
    p = x.pivot_table(index="seller_d", columns="data", values=medida, aggfunc="sum", fill_value=0)
    ordem = p.sum(axis=1).sort_values(ascending=False).index[:top]
    return p.loc[ordem]


def share_of_wallet(df: pd.DataFrame, sellers: Sequence[str], medida: str = "unidades") -> pd.DataFrame:
    """Por seller × dia: quanto é Midea vs concorrentes (share Midea na venda do parceiro)."""
    x = df[df["seller"].isin(list(sellers))]
    if x.empty:
        return pd.DataFrame(columns=["seller", "data", "midea", "total", "share_midea"])
    x = x.assign(_m=x["marca"].map(is_midea))
    g = x.groupby(["seller", "data"]).apply(
        lambda d: pd.Series({"midea": d.loc[d["_m"], medida].sum(), "total": d[medida].sum()}),
        include_groups=False,
    ).reset_index()
    g["share_midea"] = g["midea"] / g["total"].where(g["total"] != 0)
    return g


def top_anuncios(df: pd.DataFrame, dia: date, n: int = 30,
                 parceiros: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """Ranking de anúncios do dia (título × marca × seller)."""
    x = df[df["data"] == dia]
    if x.empty:
        return pd.DataFrame()
    g = x.groupby(["titulo", "marca", "seller", "segmento", "faixa_btu"], dropna=False)[
        ["unidades", "gmv_brl"]].sum().reset_index()
    g["preco_medio"] = g["gmv_brl"] / g["unidades"].where(g["unidades"] != 0)
    g["seller"] = g["seller"].map(lambda s: seller_exibicao(s, parceiros))
    return g.sort_values("unidades", ascending=False).head(n).reset_index(drop=True)


def origem_pct(df: pd.DataFrame) -> Dict[str, float]:
    """% das unidades por origem da classificação, e % em título truncado (40 car.)."""
    u = float(df["unidades"].sum()) or 1.0
    out = {k: float(v) / u for k, v in df.groupby("origem")["unidades"].sum().items()}
    out["titulo_truncado"] = float(df.loc[df["titulo"].str.len() == 40, "unidades"].sum()) / u
    return out


# ── cruzamento com o tracker ────────────────────────────────────────────────

FORCA_MIN_PONTOS = 20
FORCA_MIN_R = 0.3


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Correlação de Spearman (sem SciPy); ``None`` com < 4 pontos ou variância nula."""
    s = pd.DataFrame({"x": list(x), "y": list(y)}).dropna()
    if len(s) < 4 or s["x"].nunique() < 2 or s["y"].nunique() < 2:
        return None
    r = s["x"].rank().corr(s["y"].rank())
    return None if r is None or math.isnan(r) else float(r)


def elasticidade(preco: Sequence[float], unidades: Sequence[float]) -> Optional[float]:
    """Inclinação log(unidades) × log(preço) — elasticidade-preço simples."""
    s = pd.DataFrame({"p": list(preco), "u": list(unidades)}).dropna()
    s = s[(s["p"] > 0) & (s["u"] > 0)]
    if len(s) < 4 or s["p"].nunique() < 2:
        return None
    lx, ly = s["p"].map(math.log), s["u"].map(math.log)
    var = ((lx - lx.mean()) ** 2).sum()
    return float(((lx - lx.mean()) * (ly - ly.mean())).sum() / var) if var else None


def forca_evidencia(r: Optional[float], n: int) -> str:
    """Rótulo da correlação: com poucos pontos ou |r| baixo é hipótese, não regra."""
    if r is None:
        return "sem dado suficiente"
    if n < FORCA_MIN_PONTOS or abs(r) < FORCA_MIN_R:
        return "evidência FRACA"
    return "evidência moderada" if abs(r) < 0.6 else "evidência forte"


def painel_tracker(coletas: pd.DataFrame) -> pd.DataFrame:
    """Coletas do ML → por dia × seller canônico: melhor posição e preço mediano da Midea.

    Só busca genérica não é distinguível aqui sem a taxonomia, então usa todas
    as buscas e o MELHOR posicionamento da Midea no seller no dia (o que o
    comprador vê primeiro). Seller efetivo = buy box, senão seller.
    """
    if coletas is None or coletas.empty:
        return pd.DataFrame(columns=["data", "seller_canonico", "pos_midea", "preco_midea"])
    from utils.seller_names import normalize_seller_name

    x = coletas.copy()
    x = x[x["marca"].map(is_midea)]
    if x.empty:
        return pd.DataFrame(columns=["data", "seller_canonico", "pos_midea", "preco_midea"])
    eff = x.get("buy_box_seller")
    x["seller_canonico"] = (eff.where(eff.notna() & (eff.astype(str) != ""), x.get("seller"))
                            if eff is not None else x.get("seller")).map(normalize_seller_name)
    x["data"] = pd.to_datetime(x["data"]).dt.date
    x["posicao_organica"] = pd.to_numeric(x.get("posicao_organica"), errors="coerce")
    x["preco"] = pd.to_numeric(x.get("preco"), errors="coerce")
    return x.groupby(["data", "seller_canonico"]).agg(
        pos_midea=("posicao_organica", "min"), preco_midea=("preco", "median")).reset_index()


def cruzar_seller(vendas: pd.DataFrame, tracker: pd.DataFrame,
                  parceiros: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """Vendas Midea por dia × seller (Nubimetrics) ⨝ posição/preço da Midea (tracker).

    A ponte é o nome canônico: ``dim_partner_sellers`` (Nubimetrics → nome do
    parceiro) e ``utils.seller_names`` (nome do parceiro → canônico das coletas).
    Seller mascarado nunca casa.
    """
    from utils.seller_names import normalize_seller_name

    v = vendas[vendas["marca"].map(is_midea) & ~vendas["seller"].map(is_mascarado)]
    if v.empty or tracker is None or tracker.empty:
        return pd.DataFrame()
    v = v.groupby(["data", "seller"])[["unidades", "gmv_brl"]].sum().reset_index()
    v["seller_canonico"] = v["seller"].map(lambda s: normalize_seller_name(seller_exibicao(s, parceiros)))
    return v.merge(tracker, on=["data", "seller_canonico"], how="inner")


def resumo_cruzamento(cruz: pd.DataFrame) -> pd.DataFrame:
    """Por seller: n dias, Spearman(posição, unidades), elasticidade-preço, força."""
    if cruz is None or cruz.empty:
        return pd.DataFrame()
    linhas = []
    for s, g in cruz.groupby("seller_canonico"):
        r_pos = spearman(g["pos_midea"], g["unidades"])
        r_preco = spearman(g["preco_midea"], g["unidades"])
        linhas.append({
            "seller": s, "dias": len(g),
            "r_posicao_unidades": r_pos, "forca_posicao": forca_evidencia(r_pos, len(g)),
            "r_preco_unidades": r_preco, "elasticidade_preco": elasticidade(g["preco_midea"], g["unidades"]),
            "forca_preco": forca_evidencia(r_preco, len(g)),
        })
    return pd.DataFrame(linhas).sort_values("dias", ascending=False).reset_index(drop=True)


def cruzar_bestseller(vendas: pd.DataFrame, bestsellers: pd.DataFrame) -> pd.DataFrame:
    """Melhor posição da marca × faixa no Mais Vendidos do ML vs unidades do dia.

    ``bestsellers``: ``data``, ``marca``, ``produto``/``titulo``, ``rank``
    (plataforma Mercado Livre, referência ``mais_vendidos``). Ranking é ordinal:
    usa só a MELHOR posição do grupo no dia, nunca soma.
    """
    if bestsellers is None or bestsellers.empty or vendas.empty:
        return pd.DataFrame()
    b = bestsellers.copy()
    b["data"] = pd.to_datetime(b["data"]).dt.date
    titulo = b["titulo"] if "titulo" in b else b.get("produto", pd.Series("", index=b.index))
    btu = pd.to_numeric(b["btu"], errors="coerce") if "btu" in b else pd.Series(float("nan"), index=b.index)
    b["faixa_btu"] = [faixa_btu(int(n)) if pd.notna(n) and n else (faixa_btu(parse_btu(t)) or FAIXA_ND)
                      for n, t in zip(btu, titulo)]
    b["marca_g"] = b["marca"].map(marca_grupo)
    b["rank"] = pd.to_numeric(b["rank"], errors="coerce")
    bb = b.groupby(["data", "marca_g", "faixa_btu"])["rank"].min().reset_index()
    v = vendas.assign(marca_g=vendas["marca"].map(marca_grupo)) \
        .groupby(["data", "marca_g", "faixa_btu"])["unidades"].sum().reset_index()
    return v.merge(bb, on=["data", "marca_g", "faixa_btu"], how="inner")


def iter_dias(inicio: date, fim: date) -> Iterable[date]:
    d = inicio
    while d <= fim:
        yield d
        d += timedelta(days=1)
