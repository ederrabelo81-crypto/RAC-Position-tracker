"""
seller_app/seller_insights.py — contas puras do Track Position Seller (Nov/2026).

Módulo irmão do `app.py`, sem Streamlit e sem Supabase: recebe os dataframes
que o app já carrega e devolve números prontos para a tela. Existe para que as
regras do painel (share = soma/soma, foto por plataforma, "onde agir") sejam
testáveis sem subir o app — `tests/test_seller_insights.py`.

O app é publicado SOZINHO no Streamlit Community Cloud (não importa `utils/`),
por isso as cores de plataforma estão copiadas aqui. `tests/test_seller_insights.py`
reprova o PR se elas divergirem de `utils/ui_tokens.PLATFORM_COLORS` — uma
plataforma tem uma cor só, no painel interno e no do lojista.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

import pandas as pd

# ── Cores (espelho de utils/ui_tokens.PLATFORM_COLORS — teste garante) ──────
PLATFORM_COLORS: Dict[str, str] = {
    "Mercado Livre":   "#eab308",
    "Amazon":          "#f97316",
    "Magalu":          "#2563eb",
    "Casas Bahia":     "#1e3a8a",
    "Shopee":          "#ea580c",
    "Leroy Merlin":    "#16a34a",
    "Google Shopping": "#64748b",
}
_ROTACAO = ["#4d7c0f", "#b45309", "#be185d", "#0369a1", "#6d28d9", "#475569"]


def cores_plataformas(colunas: Iterable[str]) -> List[str]:
    """Lista de cores na ORDEM das colunas (formato de `st.line_chart(color=)`)."""
    out, i = [], 0
    for c in colunas:
        cor = PLATFORM_COLORS.get(str(c))
        if cor is None:
            cor = _ROTACAO[i % len(_ROTACAO)]
            i += 1
        out.append(cor)
    return out


# ── Share de buy box: foto atual × foto de referência ────────────────────────

@dataclass
class ShareFoto:
    """Share agregado (soma/soma) numa foto por plataforma.

    Attributes:
        pct: Share em % (0–100), ou None sem universo.
        detidos: Produtos com a BB.
        universo: Produtos observados.
        plataformas: Quantas plataformas entraram na foto.
    """

    pct: Optional[float]
    detidos: int
    universo: int
    plataformas: int


def _foto(df: pd.DataFrame) -> ShareFoto:
    detidos = int(pd.to_numeric(df["produtos_detidos"], errors="coerce").fillna(0).sum())
    universo = int(pd.to_numeric(df["produtos_universo"], errors="coerce").fillna(0).sum())
    pct = 100.0 * detidos / universo if universo else None
    return ShareFoto(pct, detidos, universo, int(df["plataforma"].nunique()))


def _datas(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["data"]).dt.date


def share_atual_e_anterior(share: pd.DataFrame, dias: int = 7) -> dict:
    """Share de buy box do seller agora e ``dias`` antes.

    Cada plataforma entra no SEU último dia observado (a Amazon materializa com
    atraso; ancorar no max() global de `data` a apagava). A referência é, por
    plataforma, o último dia ≤ (último dia − ``dias``).

    O delta compara só as plataformas que têm as DUAS fotos (``atual_comp`` ×
    ``anterior``): plataforma sem referência entraria somando universo de um
    lado só, e o delta mediria a entrada dela, não o mercado. O número de
    destaque (``atual``) leva todas.

    Returns:
        ``{"atual": ShareFoto, "atual_comp": ShareFoto | None,
        "anterior": ShareFoto | None}``.
    """
    vazio = {"atual": ShareFoto(None, 0, 0, 0), "atual_comp": None, "anterior": None}
    if share is None or share.empty:
        return vazio
    df = share.assign(_d=_datas(share))
    atual = df[df["_d"] == df.groupby("plataforma")["_d"].transform("max")]

    refs = []
    for _, bloco in df.groupby("plataforma"):
        limite = bloco["_d"].max() - timedelta(days=dias)
        candidatos = bloco[bloco["_d"] <= limite]
        if not candidatos.empty:
            refs.append(candidatos[candidatos["_d"] == candidatos["_d"].max()])
    if not refs:
        return {"atual": _foto(atual), "atual_comp": None, "anterior": None}
    anterior = pd.concat(refs)
    comp = atual[atual["plataforma"].isin(set(anterior["plataforma"]))]
    return {"atual": _foto(atual), "atual_comp": _foto(comp), "anterior": _foto(anterior)}


# ── Posição relativa no ranking de cada plataforma ───────────────────────────

def ranking_relativo(mercado: pd.DataFrame, seller: str) -> pd.DataFrame:
    """Posição do seller no ranking de share de BB de cada plataforma.

    Foto do último dia observado POR plataforma. Colunas: plataforma, data,
    posicao, total, share_pct, lider, share_lider, gap_lider_pp (positivo =
    distância até o líder; 0 quando o seller lidera).
    """
    cols = ["plataforma", "data", "posicao", "total", "share_pct",
            "lider", "share_lider", "gap_lider_pp"]
    if mercado is None or mercado.empty:
        return pd.DataFrame(columns=cols)
    df = mercado.assign(_d=_datas(mercado),
                        share_buybox_pct=pd.to_numeric(mercado["share_buybox_pct"], errors="coerce"))
    df = df[df["_d"] == df.groupby("plataforma")["_d"].transform("max")]
    linhas = []
    for plat, bloco in df.groupby("plataforma"):
        bloco = bloco.sort_values("share_buybox_pct", ascending=False)
        bloco = bloco.assign(posicao=bloco["share_buybox_pct"].rank(ascending=False, method="min"))
        meu = bloco[bloco["seller_canonical"] == seller]
        if meu.empty:
            continue
        lider = bloco.iloc[0]
        minha = float(meu["share_buybox_pct"].iloc[0])
        linhas.append(dict(
            plataforma=plat, data=meu["_d"].iloc[0], posicao=int(meu["posicao"].iloc[0]),
            total=len(bloco), share_pct=minha, lider=lider["seller_canonical"],
            share_lider=float(lider["share_buybox_pct"]),
            gap_lider_pp=max(0.0, float(lider["share_buybox_pct"]) - minha),
        ))
    out = pd.DataFrame(linhas, columns=cols)
    return out.sort_values(["posicao", "share_pct"], ascending=[True, False]).reset_index(drop=True)


# ── Onde agir: perdas com a diferença de preço para o novo detentor ──────────

_TURNO_ORD = {"Abertura": 0, "Tarde": 1, "Fechamento": 2}


def _chave_produto(df: pd.DataFrame) -> pd.Series:
    """(plataforma, id do produto) — o título só quando não há id."""
    ident = df["marketplace_product_id"] if "marketplace_product_id" in df.columns else None
    if ident is None:
        ident = pd.Series([None] * len(df), index=df.index)
    ident = ident.astype(object).where(ident.notna(), "t:" + df["produto"].astype(str))
    return df["plataforma"].astype(str) + "|" + ident.astype(str)


def _momento(df: pd.DataFrame) -> pd.Series:
    return (pd.to_datetime(df["data"]).dt.strftime("%Y-%m-%d") + "#"
            + df["turno"].map(_TURNO_ORD).fillna(9).astype(int).astype(str))


def onde_agir(perdidos: pd.DataFrame, fato: pd.DataFrame) -> pd.DataFrame:
    """Perdas de buy box, cada uma com o preço de quem levou e o seu último.

    Para cada produto perdido (última perda por produto): o preço do NOVO
    detentor naquele turno, o SEU último preço observado antes da perda (das
    suas linhas de detentor), a diferença e se você já recuperou a BB depois.
    Ordena pelas perdas ainda abertas e de menor diferença percentual — as mais
    baratas de reverter primeiro.

    A diferença só sai quando os dois preços existem. Sem o seu preço anterior
    a linha fica (a perda é real), mas sem número inventado.
    """
    cols = ["data", "turno", "plataforma", "produto", "marca", "levou",
            "preco_dele", "seu_preco", "diferenca_pct", "recuperado"]
    if perdidos is None or perdidos.empty:
        return pd.DataFrame(columns=cols)
    p = perdidos.copy()
    p["_k"] = _chave_produto(p)
    p["_m"] = _momento(p)
    p = p.sort_values("_m").groupby("_k", as_index=False).tail(1)

    meus = fato.copy() if fato is not None else pd.DataFrame()
    if not meus.empty:
        meus = meus[meus["detentor_buybox"] == True]  # noqa: E712
    if not meus.empty:
        meus["_k"] = _chave_produto(meus)
        meus["_m"] = _momento(meus)
        meus["preco"] = pd.to_numeric(meus["preco"], errors="coerce")

    linhas = []
    for _, r in p.iterrows():
        seu, recuperado = None, False
        if not meus.empty:
            mesmos = meus[meus["_k"] == r["_k"]]
            antes = mesmos[(mesmos["_m"] < r["_m"]) & mesmos["preco"].notna()]
            if not antes.empty:
                seu = float(antes.sort_values("_m")["preco"].iloc[-1])
            recuperado = bool((mesmos["_m"] > r["_m"]).any())
        dele = pd.to_numeric(pd.Series([r.get("preco")]), errors="coerce").iloc[0]
        dele = None if pd.isna(dele) else float(dele)
        dif = (100.0 * (dele - seu) / seu) if (dele is not None and seu) else None
        linhas.append(dict(
            data=r["data"], turno=r["turno"], plataforma=r["plataforma"],
            produto=r["produto"], marca=r.get("marca"), levou=r["seller_canonical"],
            preco_dele=dele, seu_preco=seu, diferenca_pct=dif, recuperado=recuperado,
        ))
    out = pd.DataFrame(linhas, columns=cols)
    out["_abs"] = pd.to_numeric(out["diferenca_pct"], errors="coerce").abs().fillna(1e9)
    out = out.sort_values(["recuperado", "_abs"]).drop(columns="_abs")
    return out.reset_index(drop=True)


# ── Cobertura: o selo do topo ────────────────────────────────────────────────

@dataclass
class CoberturaResumo:
    """Turnos observados no último dia com registro de cobertura."""

    nivel: str            # ok | parcial | sem_registro
    dia: Optional[date]
    completas: int        # plataformas com os 3 turnos
    plataformas: int
    faltando: List[str]   # "Magalu (Tarde)" …


def resumo_cobertura(cobertura: pd.DataFrame) -> CoberturaResumo:
    """Resume a cobertura do último dia num selo de uma linha.

    Silêncio não é mercado: turno não observado precisa aparecer ANTES do
    número, não numa aba no fim da página.
    """
    if cobertura is None or cobertura.empty:
        return CoberturaResumo("sem_registro", None, 0, 0, [])
    df = cobertura.assign(_d=_datas(cobertura), observado=cobertura["observado"].astype(bool))
    dia = df["_d"].max()
    hoje = df[df["_d"] == dia]
    faltando, completas = [], 0
    for plat, bloco in hoje.groupby("plataforma"):
        obs = set(bloco.loc[bloco["observado"], "turno"])
        falta = [t for t in _TURNO_ORD if t not in obs]
        if falta:
            faltando.append(f"{plat} ({', '.join(falta)})")
        else:
            completas += 1
    total = int(hoje["plataforma"].nunique())
    return CoberturaResumo("ok" if not faltando else "parcial", dia, completas, total, faltando)
