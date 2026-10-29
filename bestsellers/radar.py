"""
bestsellers/radar.py — Radar de marcas em ascensão nas listas de Mais Vendidos.

Por que existe (Out/2026)
-------------------------
O GfK de setembro/2026 mostrou Philco e Agratto ganhando share no online. O
sinal estava no banco semanas antes, e ninguém olhava: a Agratto passou de 3
para 20 posições no top 10 do Magalu (rank médio 6,7 → 3,4) entre agosto e
setembro, e chegou ao #1 do Mercado Livre; a Philco subiu no ML (6,0 → 3,3) e
na Shopee (5,5 → 3,2) com o contador de avaliações do líder indo de 809 para
1.429 em um mês. O painel só media a Midea.

Este módulo pergunta, para TODAS as marcas: **quem está ocupando mais topo de
ranking agora do que ocupava antes, em quantas plataformas, e com que tração?**

Regras duras (as de `bestsellers/metrics.py` continuam valendo)
---------------------------------------------------------------
1. **Ranking é ordinal.** O share de topo é calculado e comparado DENTRO de
   cada plataforma; o que se agrega entre plataformas é a CONTAGEM de
   plataformas em que a marca subiu — nunca uma soma ou média de posições.
2. **Janelas em semanas inteiras** (7 dias recentes × 28 anteriores): as duas
   têm o mesmo mix de dias da semana, então sábado promocional não vira
   tendência.
3. **Tendência exige ``MINIMO_LEITURAS_TENDENCIA`` leituras** em cada janela;
   plataforma abaixo disso fica fora da comparação, não entra com zero.
4. **Só mais vendidos, só split hi-wall** (escopo do KPI). Lista de relevância
   não é venda.
5. **Velocidade de avaliações é contagem** (cardinal), por isso pode somar
   entre itens; vendidos/mês só com ``base_vendidos == 'mes'`` (Shopee) — o
   acumulado do ML não tem unidade de tempo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from bestsellers.config import (
    MINIMO_LEITURAS_TENDENCIA,
    REFERENCIA_MAIS_VENDIDOS,
    SOURCES,
    TIPO_ESCOPO_KPI,
    TOP_N,
)

#: Rótulo único do grupo (Midea, Springer, Carrier…): o KPI é do grupo.
GRUPO_MIDEA = "Grupo Midea"

#: Variação de share de topo, em pontos percentuais, para contar a plataforma
#: como "subindo"/"caindo". 5 p.p. = meia posição a mais, em média, num top 10.
LIMIAR_PP = 5.0

#: Aceleração de avaliações/dia (recente ÷ base) que conta como tração.
LIMIAR_TRACAO = 1.5

#: Salto numa plataforma só que já merece olho: uma posição inteira a mais,
#: em média, no top 10. Backtest de 12/09/2026: Agratto +13,5 p.p. no Magalu
#: (6,9% → 20,4%), um mês antes de o GfK de setembro confirmar o ganho.
LIMIAR_SALTO_PP = 10.0

#: Status do radar.
SUBINDO = "SUBINDO"
CAINDO = "CAINDO"
MISTO = "MISTO"
ATENCAO = "ATENÇÃO"
ESTAVEL = "ESTÁVEL"


@dataclass(frozen=True)
class JanelaRadar:
    """Duas janelas consecutivas, em semanas inteiras, terminando em ``ate``."""

    ate: date
    dias_recente: int = 7
    dias_base: int = 28
    top_n: int = TOP_N

    @property
    def inicio_recente(self) -> date:
        return self.ate - timedelta(days=self.dias_recente - 1)

    @property
    def inicio_base(self) -> date:
        return self.inicio_recente - timedelta(days=self.dias_base)


# ---------------------------------------------------------------------------
# Preparação
# ---------------------------------------------------------------------------

def _escopo(df: pd.DataFrame, janela: JanelaRadar) -> pd.DataFrame:
    """Recorte do radar: mais vendidos, hi-wall, dentro das duas janelas."""
    if not len(df) or "rank" not in df.columns:
        return pd.DataFrame(columns=["plataforma", "data", "rank", "marca_radar", "janela"])
    out = df.copy()
    out["data"] = pd.to_datetime(out["data"], errors="coerce")
    out = out[out["data"].notna()]
    if "referencia" in out.columns:
        out = out[out["referencia"].fillna(REFERENCIA_MAIS_VENDIDOS) == REFERENCIA_MAIS_VENDIDOS]
    if "tipo" in out.columns:
        out = out[out["tipo"] == TIPO_ESCOPO_KPI]
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    out = out[out["rank"].notna()]

    dia = out["data"].dt.date
    out = out[(dia >= janela.inicio_base) & (dia <= janela.ate)].copy()
    out["janela"] = np.where(
        out["data"].dt.date >= janela.inicio_recente, "recente", "base"
    )

    marca = out["marca"].astype("string").str.strip().fillna("Desconhecida")
    grupo = out["grupo_midea"].astype(bool) if "grupo_midea" in out.columns else False
    out["marca_radar"] = np.where(grupo, GRUPO_MIDEA, marca)
    return out[out["marca_radar"].str.lower() != "desconhecida"]


def _chave_item(df: pd.DataFrame) -> pd.Series:
    """Identidade do anúncio dentro da plataforma (SKU, senão URL, senão título)."""
    ident = pd.Series(pd.NA, index=df.index, dtype="string")
    for coluna in ("sku_plataforma", "url_produto", "titulo"):
        if coluna in df.columns:
            ident = ident.fillna(df[coluna].astype("string").replace("", pd.NA))
    return df["plataforma"].astype(str) + "|" + ident.fillna("?")


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def share_por_plataforma(df: pd.DataFrame, janela: JanelaRadar) -> pd.DataFrame:
    """
    Share de topo (% do top N) de cada marca, por plataforma, nas duas janelas.

    Dia em que a marca não aparece conta como 0% — ausência do topo é o dado.
    Plataforma com menos de ``MINIMO_LEITURAS_TENDENCIA`` dias numa janela sai
    com ``comparavel=False`` e delta vazio.

    Returns:
        Uma linha por (marca_radar, plataforma): share_base, share_recente,
        delta_pp, rank_base, rank_recente (média do melhor rank nos dias em
        que apareceu), dias_base, dias_recente, comparavel.
    """
    base = _escopo(df, janela)
    topo = base[base["rank"] <= janela.top_n] if len(base) else base
    if not len(topo):
        return pd.DataFrame(columns=[
            "marca_radar", "plataforma", "share_base", "share_recente", "delta_pp",
            "rank_base", "rank_recente", "dias_base", "dias_recente", "comparavel",
        ])

    dias = topo.groupby(["plataforma", "data"])["janela"].first()
    total = topo.groupby(["plataforma", "data"]).size()
    posicoes = (
        topo.groupby(["plataforma", "data", "marca_radar"]).size()
        .unstack("marca_radar", fill_value=0)
    )
    share = posicoes.div(total, axis=0) * 100
    share["janela"] = dias
    longo = share.reset_index().melt(
        id_vars=["plataforma", "data", "janela"], var_name="marca_radar", value_name="share"
    )
    # Marca que nunca apareceu numa plataforma não é "0% nela": é outra praça.
    presentes = topo[["plataforma", "marca_radar"]].drop_duplicates()
    longo = longo.merge(presentes, on=["plataforma", "marca_radar"])

    media = (
        longo.groupby(["marca_radar", "plataforma", "janela"])["share"].mean()
        .unstack("janela")
    )
    contagem = dias.reset_index().groupby(["plataforma", "janela"]).size().unstack("janela")

    melhor = (
        topo.groupby(["marca_radar", "plataforma", "janela", "data"])["rank"].min()
        .groupby(["marca_radar", "plataforma", "janela"]).mean()
        .unstack("janela")
    )

    out = pd.DataFrame(index=media.index)
    out["share_base"] = media.get("base")
    out["share_recente"] = media.get("recente")
    out["rank_base"] = melhor.reindex(media.index).get("base")
    out["rank_recente"] = melhor.reindex(media.index).get("recente")
    out = out.reset_index()
    out["dias_base"] = out["plataforma"].map(contagem.get("base", pd.Series(dtype=float))).fillna(0).astype(int)
    out["dias_recente"] = out["plataforma"].map(contagem.get("recente", pd.Series(dtype=float))).fillna(0).astype(int)
    out["comparavel"] = (
        (out["dias_base"] >= MINIMO_LEITURAS_TENDENCIA)
        & (out["dias_recente"] >= MINIMO_LEITURAS_TENDENCIA)
    )
    out["delta_pp"] = np.where(
        out["comparavel"], out["share_recente"].fillna(0) - out["share_base"].fillna(0), np.nan
    )
    for coluna in ("share_base", "share_recente", "delta_pp", "rank_base", "rank_recente"):
        out[coluna] = out[coluna].astype(float).round(1)
    return out.sort_values(["marca_radar", "plataforma"]).reset_index(drop=True)


def _velocidade_avaliacoes(df: pd.DataFrame) -> pd.Series:
    """
    Avaliações novas por dia, por marca, numa janela.

    Por anúncio: (último − primeiro contador) ÷ dias entre as leituras, com
    ≥ 2 leituras em dias diferentes; contador que encolhe (anúncio refeito)
    conta 0. Soma por marca — avaliação é contagem, não posição.
    """
    if "reviews" not in df.columns:
        return pd.Series(dtype=float)
    leituras = df[pd.to_numeric(df["reviews"], errors="coerce").notna()].copy()
    if not len(leituras):
        return pd.Series(dtype=float)
    leituras["reviews"] = pd.to_numeric(leituras["reviews"], errors="coerce")
    leituras["item"] = _chave_item(leituras)
    por_dia = (
        leituras.groupby(["marca_radar", "item", "data"])["reviews"].max().reset_index()
        .sort_values("data")
    )
    agg = por_dia.groupby(["marca_radar", "item"]).agg(
        primeiro=("reviews", "first"), ultimo=("reviews", "last"),
        d0=("data", "first"), d1=("data", "last"),
    )
    dias = (agg["d1"] - agg["d0"]).dt.days
    agg = agg[dias > 0]
    taxa = ((agg["ultimo"] - agg["primeiro"]).clip(lower=0) / dias[dias > 0])
    return taxa.groupby(level="marca_radar").sum()


def _vendidos_mes(df: pd.DataFrame) -> pd.Series:
    """Unidades/mês declaradas (Shopee), média por anúncio, somadas por marca."""
    if "vendidos" not in df.columns or "base_vendidos" not in df.columns:
        return pd.Series(dtype=float)
    recorte = df[(df["base_vendidos"] == "mes")].copy()
    recorte["vendidos"] = pd.to_numeric(recorte["vendidos"], errors="coerce")
    recorte = recorte[recorte["vendidos"].notna()]
    if not len(recorte):
        return pd.Series(dtype=float)
    recorte["item"] = _chave_item(recorte)
    return recorte.groupby(["marca_radar", "item"])["vendidos"].mean().groupby(level="marca_radar").sum()


def _variacao_preco(df: pd.DataFrame) -> pd.Series:
    """
    Mediana da variação % de preço dos MESMOS anúncios entre as janelas.

    Só anúncios presentes nas duas — comparar a mediana da marca em cada
    janela mediria troca de mix, não de preço.
    """
    if "preco" not in df.columns:
        return pd.Series(dtype=float)
    precos = df.assign(preco=pd.to_numeric(df["preco"], errors="coerce"))
    precos = precos[precos["preco"] > 0]
    if not len(precos):
        return pd.Series(dtype=float)
    precos["item"] = _chave_item(precos)
    med = precos.groupby(["marca_radar", "item", "janela"])["preco"].median().unstack("janela")
    if "base" not in med or "recente" not in med:
        return pd.Series(dtype=float)
    med = med.dropna(subset=["base", "recente"])
    pct = (med["recente"] / med["base"] - 1) * 100
    return pct.groupby(level="marca_radar").median()


# ---------------------------------------------------------------------------
# Radar
# ---------------------------------------------------------------------------

def _nome(plataforma: str) -> str:
    """Nome de exibição da plataforma (a própria chave, se não registrada)."""
    spec = SOURCES.get(str(plataforma))
    return spec.nome if spec is not None else str(plataforma)


def _razao(recente: Optional[float], base: Optional[float]) -> float:
    """recente ÷ base, ou NaN quando a base é zero/ausente."""
    if base is None or pd.isna(base) or base <= 0 or recente is None or pd.isna(recente):
        return np.nan
    return round(float(recente) / float(base), 2)


def radar_marcas(df: pd.DataFrame, janela: JanelaRadar) -> pd.DataFrame:
    """
    Uma linha por marca com o veredito do radar e os sinais que o sustentam.

    Status:
        - **SUBINDO**: ganhou ≥ ``LIMIAR_PP`` de share de topo em 2+ plataformas,
          mais do que as em que perdeu;
        - **CAINDO**: o espelho;
        - **MISTO**: subiu em 2+ e caiu em 2+ — a marca trocou de praça (em
          12/09/2026 o Grupo Midea subiu em 3 e saiu do top 10 do Magalu);
        - **ATENÇÃO**: subiu em 1 plataforma, sem cair em nenhuma, com salto
          ≥ ``LIMIAR_SALTO_PP`` ou tração de avaliações (≥ ``LIMIAR_TRACAO``×)
          ou de vendidos/mês — o começo de um SUBINDO;
        - **ESTÁVEL**: o resto.

    Args:
        df: série de mais vendidos (como lida pelo painel ou do banco).
        janela: janelas de comparação.

    Returns:
        DataFrame ordenado do sinal mais forte para o mais fraco, com
        ``sinais`` (texto pronto para o alerta) e ``plataformas_subindo`` /
        ``plataformas_caindo`` (nomes, separados por vírgula).
    """
    colunas = [
        "marca", "status", "plataformas_subindo", "plataformas_caindo",
        "n_subindo", "n_caindo", "n_comparaveis", "soma_delta_pp",
        "aval_dia_base", "aval_dia_recente", "tracao_avaliacoes",
        "vendidos_mes_base", "vendidos_mes_recente", "tracao_vendidos",
        "preco_var_pct", "sinais",
    ]
    shares = share_por_plataforma(df, janela)
    if not len(shares):
        return pd.DataFrame(columns=colunas)

    escopo = _escopo(df, janela)
    aval = {
        nome: _velocidade_avaliacoes(escopo[escopo["janela"] == nome])
        for nome in ("base", "recente")
    }
    vend = {
        nome: _vendidos_mes(escopo[escopo["janela"] == nome])
        for nome in ("base", "recente")
    }
    preco = _variacao_preco(escopo)

    linhas: List[dict] = []
    for marca, grupo in shares.groupby("marca_radar"):
        comp = grupo[grupo["comparavel"]]
        sobe = comp[comp["delta_pp"] >= LIMIAR_PP].sort_values("delta_pp", ascending=False)
        cai = comp[comp["delta_pp"] <= -LIMIAR_PP].sort_values("delta_pp")

        a_base, a_rec = aval["base"].get(marca, np.nan), aval["recente"].get(marca, np.nan)
        v_base, v_rec = vend["base"].get(marca, np.nan), vend["recente"].get(marca, np.nan)
        tracao_a, tracao_v = _razao(a_rec, a_base), _razao(v_rec, v_base)
        var_preco = preco.get(marca, np.nan)

        com_tracao = (
            (not pd.isna(tracao_a) and tracao_a >= LIMIAR_TRACAO)
            or (not pd.isna(tracao_v) and tracao_v >= 1.3)
        )
        salto = len(sobe) and sobe["delta_pp"].max() >= LIMIAR_SALTO_PP
        if len(sobe) >= 2 and len(sobe) > len(cai):
            status = SUBINDO
        elif len(cai) >= 2 and len(cai) > len(sobe):
            status = CAINDO
        elif len(sobe) >= 2 and len(cai) >= 2:
            status = MISTO
        elif len(sobe) == 1 and not len(cai) and (com_tracao or salto):
            status = ATENCAO
        else:
            status = ESTAVEL

        sinais: List[str] = []
        for _, linha in sobe.iterrows():
            sinais.append(
                f"{_nome(linha['plataforma'])} +{linha['delta_pp']:.0f} p.p. "
                f"({linha['share_base']:.0f}%→{linha['share_recente']:.0f}%)"
            )
        for _, linha in cai.iterrows():
            sinais.append(
                f"{_nome(linha['plataforma'])} {linha['delta_pp']:.0f} p.p. "
                f"({linha['share_base']:.0f}%→{linha['share_recente']:.0f}%)"
            )
        if not pd.isna(tracao_a):
            sinais.append(f"avaliações {a_base:.0f}→{a_rec:.0f}/dia ({tracao_a:.1f}×)")
        if not pd.isna(tracao_v):
            sinais.append(f"vendidos/mês Shopee {v_base:.0f}→{v_rec:.0f} ({tracao_v:.1f}×)")
        if not pd.isna(var_preco):
            sinais.append(f"preço {var_preco:+.1f}% (mesmos anúncios)")

        linhas.append({
            "marca": marca,
            "status": status,
            "plataformas_subindo": ", ".join(map(_nome, sobe["plataforma"])),
            "plataformas_caindo": ", ".join(map(_nome, cai["plataforma"])),
            "n_subindo": len(sobe),
            "n_caindo": len(cai),
            "n_comparaveis": len(comp),
            "soma_delta_pp": round(float(comp["delta_pp"].sum()), 1) if len(comp) else np.nan,
            "aval_dia_base": round(float(a_base), 1) if not pd.isna(a_base) else np.nan,
            "aval_dia_recente": round(float(a_rec), 1) if not pd.isna(a_rec) else np.nan,
            "tracao_avaliacoes": tracao_a,
            "vendidos_mes_base": v_base,
            "vendidos_mes_recente": v_rec,
            "tracao_vendidos": tracao_v,
            "preco_var_pct": round(float(var_preco), 1) if not pd.isna(var_preco) else np.nan,
            "sinais": "; ".join(sinais),
        })

    ordem = {SUBINDO: 0, ATENCAO: 1, MISTO: 2, CAINDO: 3, ESTAVEL: 4}
    out = pd.DataFrame(linhas, columns=colunas)
    out["_ordem"] = out["status"].map(ordem)
    out = out.sort_values(
        ["_ordem", "n_subindo", "soma_delta_pp"], ascending=[True, False, False]
    ).drop(columns="_ordem")
    return out.reset_index(drop=True)


def resumo_texto(radar: pd.DataFrame, janela: JanelaRadar, limite: int = 6) -> str:
    """
    Resumo de poucas linhas para Telegram/brief: quem sobe, quem cai, e o grupo.

    O Grupo Midea entra sempre — "estável" também é leitura.
    """
    if not len(radar):
        return "Radar sem dado suficiente nas duas janelas."
    cab = (
        f"📡 Radar de marcas · {janela.inicio_recente:%d/%m}–{janela.ate:%d/%m} "
        f"× {janela.inicio_base:%d/%m}–{janela.inicio_recente - timedelta(days=1):%d/%m}"
    )
    linhas = [cab]
    destaque = radar[radar["status"].isin((SUBINDO, ATENCAO, CAINDO))].head(limite)
    icone = {SUBINDO: "🔺", ATENCAO: "👀", MISTO: "🔀", CAINDO: "🔻", ESTAVEL: "▫️"}
    for _, linha in destaque.iterrows():
        linhas.append(f"{icone[linha['status']]} {linha['marca']} — {linha['status']}: {linha['sinais']}")
    if GRUPO_MIDEA not in set(destaque["marca"]):
        midea = radar[radar["marca"] == GRUPO_MIDEA]
        if len(midea):
            linha = midea.iloc[0]
            linhas.append(f"{icone[linha['status']]} {GRUPO_MIDEA} — {linha['status']}: {linha['sinais'] or 'sem variação relevante'}")
    if len(linhas) == 1:
        linhas.append("Nenhuma marca mudou de patamar no topo dos rankings.")
    return "\n".join(linhas)
