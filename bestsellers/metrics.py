"""
bestsellers/metrics.py — Motor de métricas da série de mais vendidos.

O KPI único da rotina é **% do top 10 ocupado pelo grupo Midea, por
plataforma, por dia**. É a métrica que a coleta de posição/preço não consegue
produzir e que antecipa share. Tudo o mais aqui é diagnóstico de apoio.

REGRAS DURAS codificadas neste módulo (não são comentário, são comportamento):

1. **Ranking é ordinal.** Nada aqui soma posições entre plataformas nem
   converte ranking em share de mercado. Share de mercado vem de GfK ou
   Neotrust — o que este módulo produz é *share de topo de ranking*, uma
   medida de prateleira, e o nome dos campos diz isso.
2. **Comparação só entre o mesmo dia da semana.** `escolher_base_comparacao`
   procura a última data com o mesmo weekday; quando não existe, o delta sai
   marcado como não comparável em vez de sair limpo. Sábado promocional contra
   segunda produziu +8,6% de mediana de alta de preço em 10/08/2026 que era
   calendário, não competição.
3. **Nunca somar `vendidos` de bases diferentes.** Mensal (Shopee) e acumulado
   (Mercado Livre) são unidades distintas; `velocidade_declarada` agrega
   apenas `base_vendidos == 'mes'`.
4. **Tendência exige 3 leituras.** Abaixo disso a agregação semanal/mensal sai
   com `tendencia_valida=False` — o número existe, mas não sustenta conclusão.
5. **Lacuna fica vazia.** Nenhuma função aqui interpola ou estima.
"""

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from bestsellers.config import (
    LIMITE_ESTABILIDADE,
    LIMITE_MOVIMENTO_PRECO,
    MINIMO_LEITURAS_TENDENCIA,
    TIPO_ESCOPO_KPI,
    TOP_N,
)

# Períodos de agregação aceitos por `serie_agregada`.
PERIODO_DIARIO = "diario"
PERIODO_SEMANAL = "semanal"
PERIODO_MENSAL = "mensal"

_DIAS_SEMANA = (
    "segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo",
)


# ---------------------------------------------------------------------------
# Preparação
# ---------------------------------------------------------------------------

def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza tipos e deriva as colunas de calendário usadas nas agregações.

    Args:
        df: série histórica crua (CSV ou concatenação de coletas).

    Returns:
        Cópia com `data` como datetime, `dia_semana`, `semana_iso` e `mes`.
    """
    out = df.copy()
    if not len(out):
        return out

    out["data"] = pd.to_datetime(out["data"], errors="coerce")
    out = out[out["data"].notna()]
    out["dia_semana"] = out["data"].dt.weekday
    # Semana ISO no formato 2026-W33: ordenável como string e legível.
    iso = out["data"].dt.isocalendar()
    out["semana_iso"] = (
        iso["year"].astype(str) + "-W" + iso["week"].astype(int).astype(str).str.zfill(2)
    )
    out["mes"] = out["data"].dt.strftime("%Y-%m")
    out["grupo_midea"] = out["grupo_midea"].astype(bool)
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    for coluna in ("preco", "rating", "vendidos", "btu", "reviews"):
        if coluna in out.columns:
            out[coluna] = pd.to_numeric(out[coluna], errors="coerce")
    return out


def escopo_kpi(df: pd.DataFrame) -> pd.DataFrame:
    """Recorta o escopo do KPI: apenas split hi-wall."""
    if not len(df):
        return df
    return df[df["tipo"] == TIPO_ESCOPO_KPI].copy()


# ---------------------------------------------------------------------------
# KPI principal
# ---------------------------------------------------------------------------

def kpi_topo(df: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """
    % do top N ocupado pelo grupo Midea, por plataforma e por dia.

    O denominador (`itens_topN`) é publicado junto do percentual de propósito:
    numa lista contaminada, "50%" pode significar 1 split em 2 — e essa
    diferença muda completamente a leitura.

    Args:
        df: série já passada por `preparar`.
        n:  tamanho do topo (padrão 10).

    Returns:
        DataFrame com data, plataforma, itens, itens_topN, midea_topN,
        pct_topN, pct_lista, melhor_rank e n_posicoes.

    Note:
        Serve para comparar a MESMA plataforma ao longo do tempo, no mesmo dia
        da semana. NÃO serve para comparar plataformas entre si (a lista da
        Amazon tem 30 itens, a do ML tem 20) nem para virar share de mercado.
    """
    hw = escopo_kpi(df)
    if not len(hw):
        return pd.DataFrame(columns=[
            "data", "plataforma", "itens", "itens_topN", "midea_topN",
            "pct_topN", "pct_lista", "melhor_rank", "n_posicoes",
        ])

    linhas = []
    for (data, plataforma), grupo in hw.groupby(["data", "plataforma"], sort=True):
        topo = grupo[grupo["rank"] <= n]
        midea = grupo[grupo["grupo_midea"]]
        linhas.append({
            "data": data,
            "plataforma": plataforma,
            "itens": len(grupo),
            "itens_topN": len(topo),
            "midea_topN": int(topo["grupo_midea"].sum()),
            "pct_topN": round(float(topo["grupo_midea"].mean()) * 100, 1) if len(topo) else np.nan,
            "pct_lista": round(float(grupo["grupo_midea"].mean()) * 100, 1),
            "melhor_rank": int(midea["rank"].min()) if len(midea) else np.nan,
            "n_posicoes": int(grupo["grupo_midea"].sum()),
        })
    return pd.DataFrame(linhas).sort_values(["data", "plataforma"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Séries diária / semanal / mensal
# ---------------------------------------------------------------------------

def serie_agregada(
    df: pd.DataFrame,
    periodo: str = PERIODO_SEMANAL,
    n: int = TOP_N,
) -> pd.DataFrame:
    """
    Agrega o KPI por período, por plataforma.

    A agregação é a MÉDIA das leituras diárias — nunca a soma, que não tem
    significado para uma medida ordinal. `n_leituras` e `tendencia_valida`
    acompanham cada linha: uma semana com 1 leitura produz um número, não uma
    tendência.

    Args:
        df:      série já passada por `preparar`.
        periodo: 'diario', 'semanal' ou 'mensal'.
        n:       tamanho do topo.

    Returns:
        DataFrame com periodo, plataforma, n_leituras, pct_topN_medio,
        pct_topN_min, pct_topN_max, melhor_rank, tendencia_valida.

    Raises:
        ValueError: se `periodo` não for reconhecido.
    """
    coluna = {
        PERIODO_DIARIO: "data",
        PERIODO_SEMANAL: "semana_iso",
        PERIODO_MENSAL: "mes",
    }.get(periodo)
    if coluna is None:
        raise ValueError(
            f"Período inválido: {periodo!r}. Use 'diario', 'semanal' ou 'mensal'."
        )

    kpi = kpi_topo(df, n=n)
    if not len(kpi):
        return pd.DataFrame(columns=[
            "periodo", "plataforma", "n_leituras", "pct_topN_medio",
            "pct_topN_min", "pct_topN_max", "melhor_rank", "tendencia_valida",
        ])

    kpi = kpi.copy()
    if coluna == "data":
        kpi["periodo"] = kpi["data"].dt.strftime("%Y-%m-%d")
    else:
        # Reaproveita o rótulo de calendário já derivado em `preparar`.
        rotulos = df[["data", coluna]].drop_duplicates()
        kpi = kpi.merge(rotulos, on="data", how="left").rename(columns={coluna: "periodo"})

    agregado = (
        kpi.groupby(["periodo", "plataforma"])
        .agg(
            n_leituras=("data", "nunique"),
            pct_topN_medio=("pct_topN", "mean"),
            pct_topN_min=("pct_topN", "min"),
            pct_topN_max=("pct_topN", "max"),
            melhor_rank=("melhor_rank", "min"),
        )
        .reset_index()
    )
    for coluna_pct in ("pct_topN_medio", "pct_topN_min", "pct_topN_max"):
        agregado[coluna_pct] = agregado[coluna_pct].round(1)
    agregado["tendencia_valida"] = (
        agregado["n_leituras"] >= MINIMO_LEITURAS_TENDENCIA
    ) if periodo != PERIODO_DIARIO else False
    return agregado.sort_values(["plataforma", "periodo"]).reset_index(drop=True)


def variacao_periodo(serie: pd.DataFrame) -> pd.DataFrame:
    """
    Ganho ou perda de share de topo de ranking entre períodos consecutivos.

    Este é o número que a rotina existe para produzir: quantos pontos
    percentuais do topo do ranking a Midea ganhou ou perdeu, por plataforma,
    de um período para o outro.

    Args:
        serie: saída de `serie_agregada`.

    Returns:
        A mesma tabela com `pct_topN_anterior`, `delta_pp` e `direcao`
        ('ganho' | 'perda' | 'estável' | 'sem base').

    Note:
        Isto NÃO é share de mercado. É participação nas posições do topo da
        lista de mais vendidos daquela plataforma — uma medida de prateleira
        que antecipa share, e que só vira leitura de share quando confrontada
        com GfK/Neotrust.
    """
    if not len(serie):
        return serie.assign(pct_topN_anterior=None, delta_pp=None, direcao=None)

    out = serie.sort_values(["plataforma", "periodo"]).copy()
    out["pct_topN_anterior"] = out.groupby("plataforma")["pct_topN_medio"].shift(1)
    out["delta_pp"] = (out["pct_topN_medio"] - out["pct_topN_anterior"]).round(1)

    def _direcao(valor) -> str:
        if pd.isna(valor):
            return "sem base"
        if valor > 0:
            return "ganho"
        if valor < 0:
            return "perda"
        return "estável"

    out["direcao"] = out["delta_pp"].apply(_direcao)
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Base de comparação diária
# ---------------------------------------------------------------------------

def escolher_base_comparacao(
    historico: pd.DataFrame, data_alvo
) -> Tuple[Optional[pd.DataFrame], bool, Optional[int]]:
    """
    Escolhe a leitura anterior contra a qual comparar o dia.

    Preferência absoluta pela última data com o MESMO dia da semana. Quando
    não existe nenhuma, devolve a leitura mais recente com `mesmo_dia=False` —
    e o relatório imprime o delta como não comparável.

    Args:
        historico: série histórica já passada por `preparar` (sem o dia atual).
        data_alvo: data da coleta atual (str ou Timestamp).

    Returns:
        Tupla (df_base, mesmo_dia, dias_de_intervalo). `(None, False, None)`
        quando não há histórico.
    """
    if not len(historico):
        return None, False, None

    alvo = pd.to_datetime(data_alvo)
    anteriores = historico[historico["data"] < alvo]
    if not len(anteriores):
        return None, False, None

    mesmo_weekday = anteriores[anteriores["data"].dt.weekday == alvo.weekday()]
    escolhida_df = mesmo_weekday if len(mesmo_weekday) else anteriores
    data_escolhida = escolhida_df["data"].max()

    base = historico[historico["data"] == data_escolhida].copy()
    mesmo_dia = bool(data_escolhida.weekday() == alvo.weekday())
    return base, mesmo_dia, int((alvo - data_escolhida).days)


def delta_diario(hoje: pd.DataFrame, base: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """KPI do dia ao lado da base de comparação, com o delta em pontos percentuais."""
    atual = kpi_topo(hoje, n=n).set_index("plataforma")
    antes = kpi_topo(base, n=n).set_index("plataforma")

    if not len(atual):
        return pd.DataFrame()

    tabela = atual[["pct_topN", "melhor_rank", "n_posicoes", "itens_topN"]].join(
        antes[["pct_topN", "melhor_rank"]], rsuffix="_anterior", how="left"
    )
    tabela["delta_pp"] = (tabela["pct_topN"] - tabela["pct_topN_anterior"]).round(1)
    return tabela.reset_index()


# ---------------------------------------------------------------------------
# Diagnósticos de apoio
# ---------------------------------------------------------------------------

def estabilidade(
    hoje: pd.DataFrame, base: pd.DataFrame, excluir: Optional[List[str]] = None
) -> pd.DataFrame:
    """
    Quanto o ranking se moveu entre duas leituras.

    Um ranking de venda real se mexe. Acima de 35% dos itens EXATAMENTE na
    mesma posição, o que estamos medindo provavelmente é curadoria — foi assim
    que a Leroy Merlin (41% parados, deslocamento mediano de 1 posição) se
    denunciou contra 12–19% e 3–4 posições das demais.

    O pareamento é por `sku_plataforma` quando disponível, com fallback para o
    título: sellers editam título com frequência, e parear por texto inventa
    "itens novos" que na verdade só mudaram de nome.

    Returns:
        DataFrame com itens_comuns, permanencia_pct, rank_inalterado_pct,
        desloc_mediano e veredito.
    """
    excluir = set(excluir or [])
    hw_hoje, hw_base = escopo_kpi(hoje), escopo_kpi(base)
    linhas = []

    plataformas = sorted(
        (set(hw_hoje["plataforma"]) & set(hw_base["plataforma"])) - excluir
    ) if len(hw_hoje) and len(hw_base) else []

    for plataforma in plataformas:
        atual = _chaveado(hw_hoje[hw_hoje["plataforma"] == plataforma])
        antes = _chaveado(hw_base[hw_base["plataforma"] == plataforma])
        juncao = antes.merge(atual, on="chave", suffixes=("_antes", "_hoje"))
        if len(juncao) < 5:
            continue

        desloc = (juncao["rank_hoje"] - juncao["rank_antes"]).abs()
        parados = float((desloc == 0).mean())
        linhas.append({
            "plataforma": plataforma,
            "itens_comuns": len(juncao),
            "permanencia_pct": round(len(juncao) / max(len(atual), 1) * 100),
            "rank_inalterado_pct": round(parados * 100),
            "desloc_mediano": float(desloc.median()),
            "veredito": (
                "CURADORIA SUSPEITA" if parados > LIMITE_ESTABILIDADE else "ranking vivo"
            ),
        })
    return pd.DataFrame(linhas)


def _chaveado(grupo: pd.DataFrame) -> pd.DataFrame:
    """Reduz a lista a (chave de anúncio, rank), sem duplicata."""
    out = grupo.copy()
    sku = out["sku_plataforma"].astype("string") if "sku_plataforma" in out else pd.NA
    titulo = out["titulo"].astype("string").str.strip().str.lower()
    out["chave"] = sku.fillna(titulo) if sku is not pd.NA else titulo
    out = out[out["chave"].notna() & (out["chave"] != "")]
    return out.drop_duplicates("chave")[["chave", "rank"]]


def piso_preco(
    hoje: pd.DataFrame,
    base: pd.DataFrame,
    btus: Tuple[int, ...] = (9000, 12000),
    excluir: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Movimento do piso de preço por plataforma × marca × capacidade.

    Piso = menor preço PRATICADO observado (nunca o preço "de"). Movimento
    relevante é 2% ou mais, e só entre datas do mesmo dia da semana — entre
    sábado 08/08 e segunda 10/08 de 2026, 30 de 36 células que se moveram
    subiram (mediana +8,6%) por rollback de promoção de fim de semana, não por
    movimento competitivo.
    """
    excluir = set(excluir or [])

    def _piso(df: pd.DataFrame) -> pd.Series:
        recorte = escopo_kpi(df)
        if not len(recorte):
            return pd.Series(dtype="float64")
        recorte = recorte[
            recorte["btu"].isin(btus) & ~recorte["plataforma"].isin(excluir)
        ]
        return recorte.groupby(["plataforma", "marca", "btu"])["preco"].min()

    colunas = ["plataforma", "marca", "btu", "anterior", "hoje", "delta_pct"]
    tabela = pd.concat(
        [_piso(base).rename("anterior"), _piso(hoje).rename("hoje")], axis=1
    ).dropna()
    if not len(tabela):
        # Nenhuma célula em comum entre as duas datas: devolve a tabela vazia
        # já com as colunas, para o caller não precisar de um caso especial.
        return pd.DataFrame(columns=colunas)

    tabela["delta_pct"] = ((tabela["hoje"] / tabela["anterior"] - 1) * 100).round(1)
    return tabela.reset_index()[colunas]


def movimentos_relevantes(piso: pd.DataFrame) -> pd.DataFrame:
    """Filtra o piso de preço pelos movimentos de 2% ou mais."""
    if not len(piso) or "delta_pct" not in piso.columns:
        return pd.DataFrame()
    return piso[piso["delta_pct"].abs() >= LIMITE_MOVIMENTO_PRECO].sort_values("delta_pct")


def velocidade_declarada(df: pd.DataFrame) -> pd.DataFrame:
    """
    Unidades por mês declaradas, por marca.

    Agrega SOMENTE `base_vendidos == 'mes'`. Hoje só a Shopee entrega esse
    campo; o "+5mil vendidos" do Mercado Livre é acumulado vitalício e somá-lo
    aqui produziria um número sem unidade.

    Returns:
        DataFrame por marca com itens, un_mes, pct e preco_mediano. O `pct` é
        share DENTRO DA LISTA CAPTURADA — jamais share de mercado.
    """
    if not len(df) or "base_vendidos" not in df.columns:
        return pd.DataFrame()

    recorte = df[(df["base_vendidos"] == "mes") & df["vendidos"].notna()]
    if not len(recorte):
        return pd.DataFrame()

    agregado = (
        recorte.groupby("marca")
        .agg(
            itens=("rank", "size"),
            un_mes=("vendidos", "sum"),
            preco_mediano=("preco", "median"),
        )
        .sort_values("un_mes", ascending=False)
        .reset_index()
    )
    total = agregado["un_mes"].sum()
    agregado["pct"] = (agregado["un_mes"] / total * 100).round(1) if total else np.nan
    return agregado


def marcas_fora_do_radar(df: pd.DataFrame, conhecidas, n: int = TOP_N) -> List[str]:
    """
    Marcas que apareceram no top N e não estão na lista monitorada.

    Cada aparição recorrente é um pedido de inclusão no painel: em 10/08/2026
    apareceram HQ, Hisense, Consul, Britânia, Daikin e Fujitsu.
    """
    hw = escopo_kpi(df)
    if not len(hw):
        return []
    topo = hw[hw["rank"] <= n]
    conhecidas_norm = {str(m).strip().upper() for m in conhecidas}
    achadas = {
        str(m).strip()
        for m in topo["marca"].dropna().unique()
        if str(m).strip().upper() not in conhecidas_norm
        and str(m).strip().lower() != "desconhecida"
    }
    return sorted(achadas)


def lideres(df: pd.DataFrame) -> pd.DataFrame:
    """
    O #1 de cada plataforma — marca, capacidade e preço.

    Faixa de preço do líder é leitura de posicionamento: se o #1 estiver
    abaixo de R$ 1.800 em 9K ou 12K, a disputa do dia é de entrada, e a linha
    de entrada é a arma, não a premium.

    Note:
        A seleção é pela LINHA de menor rank, não por `groupby.first()` —
        aquele pega o primeiro valor não-nulo de cada coluna
        independentemente, e um #1 sem preço sairia com a marca da posição 1
        e o preço da posição 2. Um preço fabricado alimentaria direto a regra
        de decisão "#1 abaixo de R$ 1.800".
    """
    hw = escopo_kpi(df)
    if not len(hw):
        return pd.DataFrame(columns=["plataforma", "rank", "marca", "btu", "preco", "titulo"])
    indices = hw.groupby("plataforma")["rank"].idxmin()
    primeiros = hw.loc[indices]
    return primeiros[["plataforma", "rank", "marca", "btu", "preco", "titulo"]].reset_index(drop=True)


def presenca_por_marca(df: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """
    Quantas posições do top N cada marca ocupa, por plataforma.

    É o mapa competitivo do dia — quem está ocupando o espaço que a Midea não
    ocupa. Contagem por plataforma de propósito: somar posições de plataformas
    diferentes seria tratar ordinal como cardinal.
    """
    hw = escopo_kpi(df)
    if not len(hw):
        return pd.DataFrame()
    topo = hw[hw["rank"] <= n]
    return (
        topo.groupby(["plataforma", "marca"])
        .agg(posicoes=("rank", "size"), melhor_rank=("rank", "min"))
        .reset_index()
        .sort_values(["plataforma", "posicoes"], ascending=[True, False])
    )


def nome_dia_semana(data) -> str:
    """Nome do dia da semana em português, para o cabeçalho do relatório."""
    return _DIAS_SEMANA[pd.to_datetime(data).weekday()]
