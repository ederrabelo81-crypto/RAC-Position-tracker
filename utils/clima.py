"""
utils/clima.py — Radar meteorológico: temperatura por região × venda de ar.

Por que existe (Out/2026)
-------------------------
Frio e chuva no Sul/Sudeste em outubro, meta de novembro agressiva, e nenhum
dado no projeto para separar "o mercado parou" de "perdemos share". Ar
condicionado é compra puxada por calor: a hipótese a provar — com histórico,
não com intuição — é que semana mais quente que o normal antecipa venda.

O que este módulo faz (tudo puro, testado em ``tests/test_clima.py``):

1. **Leitura** da resposta do Open-Meteo (gratuito, sem chave, ERA5 desde 1940
   no ``archive-api`` e previsão de 16 dias no ``api``).
2. **Índice regional** (média das cidades da região) e **índice Brasil**
   ponderado pela população das regiões (Censo 2022) — calor em Manaus não
   pesa o mesmo que calor em São Paulo.
3. **Anomalia**: temperatura máxima menos a **normal** da mesma cidade na mesma
   época do ano (± ``JANELA_NORMAL_DIAS``, nos anos anteriores). É a anomalia,
   não o grau absoluto, que move venda: 30 °C é normal em Fortaleza e onda de
   calor em Curitiba.
4. **Proxy diário de venda** a partir dos Mais Vendidos (avaliações novas por
   dia nos anúncios do topo) e **correlação com defasagem** (0–14 dias) entre
   clima e venda — diária com o proxy, mensal com uma série externa (GfK).

Regras duras
------------
1. **Correlação não é calibração.** Com menos de ``MIN_PONTOS_CORRELACAO``
   pontos ou |r| < 0,3 o relatório diz "evidência fraca" — o número sai, a
   conclusão não.
2. **Previsão nunca vira observado.** ``tipo`` separa os dois em toda linha; a
   normal climatológica só aprende com observado.
3. **Sem anos anteriores, sem anomalia.** Normal calculada com menos de
   ``MIN_ANOS_NORMAL`` anos fica vazia — melhor que uma "normal" de 1 ano.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

OBSERVADO = "observado"
PREVISAO = "previsao"

#: Variáveis diárias pedidas ao Open-Meteo (existem nas duas APIs).
VARIAVEIS_DIARIAS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "precipitation_sum",
)

#: Colunas da tabela `clima_diario` (migração 021).
COLUNAS = (
    "data", "cidade", "uf", "regiao", "tipo", "tmax", "tmin", "tmed",
    "sensacao_max", "precip_mm", "fonte",
)

JANELA_NORMAL_DIAS = 7
MIN_ANOS_NORMAL = 3
MIN_PONTOS_CORRELACAO = 60
#: Dia "quente" para a contagem de dias de calor: máxima acima disto.
LIMIAR_DIA_QUENTE = 30.0


@dataclass(frozen=True)
class Cidade:
    """Ponto de coleta: capital ou polo de consumo da região."""

    nome: str
    uf: str
    regiao: str
    lat: float
    lon: float


#: Capitais + polos do interior paulista (o maior mercado de ar do país).
CIDADES: Sequence[Cidade] = (
    Cidade("São Paulo", "SP", "Sudeste", -23.55, -46.63),
    Cidade("Campinas", "SP", "Sudeste", -22.91, -47.06),
    Cidade("Ribeirão Preto", "SP", "Sudeste", -21.18, -47.81),
    Cidade("Rio de Janeiro", "RJ", "Sudeste", -22.91, -43.17),
    Cidade("Belo Horizonte", "MG", "Sudeste", -19.92, -43.94),
    Cidade("Vitória", "ES", "Sudeste", -20.32, -40.34),
    Cidade("Curitiba", "PR", "Sul", -25.43, -49.27),
    Cidade("Florianópolis", "SC", "Sul", -27.60, -48.55),
    Cidade("Porto Alegre", "RS", "Sul", -30.03, -51.23),
    Cidade("Salvador", "BA", "Nordeste", -12.97, -38.50),
    Cidade("Recife", "PE", "Nordeste", -8.05, -34.88),
    Cidade("Fortaleza", "CE", "Nordeste", -3.73, -38.53),
    Cidade("Brasília", "DF", "Centro-Oeste", -15.79, -47.88),
    Cidade("Goiânia", "GO", "Centro-Oeste", -16.68, -49.25),
    Cidade("Cuiabá", "MT", "Centro-Oeste", -15.60, -56.10),
    Cidade("Manaus", "AM", "Norte", -3.10, -60.02),
    Cidade("Belém", "PA", "Norte", -1.46, -48.50),
)

#: Peso de cada região no índice Brasil: participação na população (IBGE,
#: Censo 2022). Proxy de mercado, declarado como tal — não é share de venda.
PESO_REGIAO: Dict[str, float] = {
    "Sudeste": 0.418,
    "Nordeste": 0.269,
    "Sul": 0.147,
    "Norte": 0.085,
    "Centro-Oeste": 0.080,
}

BRASIL = "Brasil (ponderado)"


# ---------------------------------------------------------------------------
# Leitura do Open-Meteo
# ---------------------------------------------------------------------------

def parse_open_meteo(
    resposta: dict, cidade: Cidade, hoje: date, fonte: str
) -> pd.DataFrame:
    """
    Converte a resposta diária do Open-Meteo em linhas de `clima_diario`.

    Args:
        resposta: JSON da API (``{"daily": {"time": [...], ...}}``).
        cidade: cidade consultada.
        hoje: dia corrente BRT — antes dele é observado, a partir dele previsão.
        fonte: ``archive`` ou ``forecast`` (rastreabilidade da linha).

    Returns:
        DataFrame com `COLUNAS`. Dia sem máxima (lacuna da API) é descartado.
    """
    diario = (resposta or {}).get("daily") or {}
    datas = diario.get("time") or []
    if not datas:
        return pd.DataFrame(columns=list(COLUNAS))

    def _serie(chave: str) -> List[Optional[float]]:
        valores = diario.get(chave) or [None] * len(datas)
        return list(valores) + [None] * (len(datas) - len(valores))

    df = pd.DataFrame({
        "data": pd.to_datetime(datas).date,
        "tmax": _serie("temperature_2m_max"),
        "tmin": _serie("temperature_2m_min"),
        "sensacao_max": _serie("apparent_temperature_max"),
        "precip_mm": _serie("precipitation_sum"),
    })
    for coluna in ("tmax", "tmin", "sensacao_max", "precip_mm"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    df = df[df["tmax"].notna()].copy()
    df["tmed"] = ((df["tmax"] + df["tmin"]) / 2).round(1)
    df["cidade"] = cidade.nome
    df["uf"] = cidade.uf
    df["regiao"] = cidade.regiao
    df["tipo"] = np.where(df["data"] < hoje, OBSERVADO, PREVISAO)
    df["fonte"] = fonte
    return df[list(COLUNAS)].reset_index(drop=True)


def sem_previsao_vencida(df: pd.DataFrame, hoje: date) -> pd.DataFrame:
    """
    Tira as linhas de previsão de dias que já passaram.

    A tabela guarda a previsão mesmo depois do dia (chave inclui ``tipo``, e a
    linha observada entra ao lado) — serve para medir acerto da previsão
    depois. Quem lê "o que vem por aí" precisa só da previsão vigente: sem este
    corte, a média dos "próximos 7 dias" mistura previsões velhas.
    """
    if not len(df):
        return df
    datas = pd.to_datetime(df["data"]).dt.date
    return df[(df["tipo"] != PREVISAO) | (datas >= hoje)]


def consolidar(*partes: pd.DataFrame) -> pd.DataFrame:
    """
    Junta leituras, uma linha por (data, cidade, tipo) — a mais recente vence.

    O ``archive`` (ERA5, reanálise) vence o ``forecast`` no observado: é o dado
    revisado. Por isso a ordem de prioridade é por fonte, não por chegada.
    """
    validas = [p for p in partes if p is not None and len(p)]
    if not validas:
        return pd.DataFrame(columns=list(COLUNAS))
    df = pd.concat(validas, ignore_index=True)
    df["data"] = pd.to_datetime(df["data"]).dt.date
    df["_prio"] = (df["fonte"] == "archive").astype(int)
    df = (
        df.sort_values("_prio")
        .drop_duplicates(subset=["data", "cidade", "tipo"], keep="last")
        .drop(columns="_prio")
    )
    return df.sort_values(["data", "regiao", "cidade"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Normal climatológica e anomalia
# ---------------------------------------------------------------------------

def _dia_do_ano(datas: pd.Series) -> pd.Series:
    """Dia do ano 1–365 (29/02 vira 28/02, para os anos casarem)."""
    d = pd.to_datetime(datas)
    doy = d.dt.dayofyear
    bissexto = d.dt.is_leap_year & (doy > 59)
    return (doy - bissexto.astype(int)).clip(upper=365)


def com_anomalia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Acrescenta ``normal_tmax`` e ``anomalia_tmax`` a cada linha.

    A normal de uma linha é a média da máxima OBSERVADA da mesma cidade, nos
    anos ANTERIORES ao da linha, dentro de ± ``JANELA_NORMAL_DIAS`` dias do
    mesmo dia do ano. Usar o próprio ano contaminaria a normal com a anomalia
    que se quer medir.

    Args:
        df: linhas de `clima_diario` (observado e previsão).

    Returns:
        Cópia com as duas colunas (NaN onde não há ``MIN_ANOS_NORMAL`` anos).
    """
    out = df.reset_index(drop=True)
    if not len(out):
        return out.assign(normal_tmax=pd.Series(dtype=float), anomalia_tmax=pd.Series(dtype=float))
    out["_ano"] = pd.to_datetime(out["data"]).dt.year
    out["_doy"] = _dia_do_ano(out["data"])

    obs = out[out["tipo"] == OBSERVADO][["cidade", "_ano", "_doy", "tmax"]].reset_index(drop=True)
    # Média por (cidade, ano, dia do ano ± janela): expande cada observação
    # para os dias vizinhos, uma vez, e agrega.
    deslocamentos = np.arange(-JANELA_NORMAL_DIAS, JANELA_NORMAL_DIAS + 1)
    viz = obs.iloc[np.repeat(np.arange(len(obs)), len(deslocamentos))].copy()
    viz["_doy"] = ((viz["_doy"] - 1 + np.tile(deslocamentos, len(obs))) % 365) + 1
    por_ano = viz.groupby(["cidade", "_doy", "_ano"])["tmax"].mean().reset_index()

    # Para cada (cidade, dia do ano, ano), soma e contagem dos anos ANTERIORES.
    por_ano = por_ano.sort_values(["cidade", "_doy", "_ano"])
    grupo = por_ano.groupby(["cidade", "_doy"])["tmax"]
    por_ano["soma_ant"] = grupo.cumsum() - por_ano["tmax"]
    por_ano["n_ant"] = grupo.cumcount()

    # Linha do ano Y casa com o último ano observado ≤ Y: se for o próprio Y,
    # valem os anteriores; se for anterior (ex.: previsão num dia do ano ainda
    # sem observado), esse ano também entra.
    alvo = out[["cidade", "_doy", "_ano"]].reset_index()
    casado = pd.merge_asof(
        alvo.sort_values("_ano"),
        por_ano.rename(columns={"_ano": "_ano_ref"}).sort_values("_ano_ref"),
        left_on="_ano", right_on="_ano_ref", by=["cidade", "_doy"],
        direction="backward",
    ).set_index("index").reindex(out.index)
    mesmo_ano = casado["_ano_ref"] == casado["_ano"]
    soma = np.where(mesmo_ano, casado["soma_ant"], casado["soma_ant"] + casado["tmax"])
    n = np.where(mesmo_ano, casado["n_ant"], casado["n_ant"] + 1)
    n = pd.Series(n, index=out.index, dtype=float)
    normal = pd.Series(soma, index=out.index, dtype=float) / n
    out["normal_tmax"] = normal.where(n >= MIN_ANOS_NORMAL).round(1)
    out["anomalia_tmax"] = (out["tmax"] - out["normal_tmax"]).round(1)
    return out.drop(columns=["_ano", "_doy"])


# ---------------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------------

def indice_regional(df: pd.DataFrame) -> pd.DataFrame:
    """
    Índice diário por região e Brasil ponderado.

    Por região: média simples das cidades. Brasil: média das regiões ponderada
    por `PESO_REGIAO` (só as regiões com dado no dia, pesos renormalizados).

    Args:
        df: linhas de `clima_diario`, de preferência já com `com_anomalia`.

    Returns:
        Uma linha por (data, tipo, regiao) com tmax, tmed, sensacao_max,
        precip_mm, anomalia_tmax (se houver) e dias_quentes (fração de cidades
        com máxima ≥ ``LIMIAR_DIA_QUENTE``).
    """
    if not len(df):
        return pd.DataFrame()
    base = df.copy()
    base["quente"] = (base["tmax"] >= LIMIAR_DIA_QUENTE).astype(float)
    medidas = ["tmax", "tmed", "sensacao_max", "precip_mm", "quente"]
    if "anomalia_tmax" in base.columns:
        medidas.append("anomalia_tmax")

    reg = base.groupby(["data", "tipo", "regiao"])[medidas].mean().reset_index()
    reg["peso"] = reg["regiao"].map(PESO_REGIAO).fillna(0.0)

    # Média ponderada vetorizada: por medida, Σ(valor·peso) ÷ Σ(peso) só onde
    # há valor — região sem dado no dia sai do denominador (pesos renormalizados).
    pond = reg[["data", "tipo"]].copy()
    for medida in medidas:
        valido = reg[medida].notna() & (reg["peso"] > 0)
        pond[f"{medida}__num"] = (reg[medida] * reg["peso"]).where(valido, 0.0)
        pond[f"{medida}__den"] = reg["peso"].where(valido, 0.0)
    somas = pond.groupby(["data", "tipo"]).sum()
    br = pd.DataFrame(index=somas.index)
    for medida in medidas:
        den = somas[f"{medida}__den"]
        br[medida] = (somas[f"{medida}__num"] / den).where(den > 0)
    br = br.reset_index().assign(regiao=BRASIL)
    out = pd.concat([reg.drop(columns="peso"), br], ignore_index=True)
    out = out.rename(columns={"quente": "dias_quentes"})
    for medida in [m if m != "quente" else "dias_quentes" for m in medidas]:
        out[medida] = out[medida].astype(float).round(2 if medida == "dias_quentes" else 1)
    return out.sort_values(["data", "regiao"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Venda × clima
# ---------------------------------------------------------------------------

def proxy_vendas_diario(bestsellers: pd.DataFrame) -> pd.DataFrame:
    """
    Proxy diário de venda: avaliações novas por dia nos anúncios do topo.

    Para cada anúncio (plataforma + SKU), a diferença do contador de avaliações
    entre duas leituras consecutivas, dividida pelos dias entre elas, é
    atribuída ao dia da leitura nova. Contador que encolhe (anúncio refeito)
    conta 0. Só listas de mais vendidos.

    Args:
        bestsellers: série da tabela `bestsellers`.

    Returns:
        DataFrame [data, avaliacoes_dia, anuncios] — dias sem leitura não
        aparecem (lacuna fica vazia, nunca zero).
    """
    if not len(bestsellers) or "reviews" not in bestsellers.columns:
        return pd.DataFrame(columns=["data", "avaliacoes_dia", "anuncios"])
    df = bestsellers.copy()
    if "referencia" in df.columns:
        df = df[df["referencia"].fillna("mais_vendidos") == "mais_vendidos"]
    df["reviews"] = pd.to_numeric(df["reviews"], errors="coerce")
    df = df[df["reviews"].notna()]
    if not len(df):
        return pd.DataFrame(columns=["data", "avaliacoes_dia", "anuncios"])
    df["data"] = pd.to_datetime(df["data"]).dt.normalize()
    # Identidade do anúncio: SKU, senão URL, senão título. Sem nenhuma, a
    # linha sai — juntar anúncios diferentes numa "história" só misturaria
    # contadores de avaliações de produtos distintos.
    ident = pd.Series(pd.NA, index=df.index, dtype="string")
    for coluna in ("sku_plataforma", "url_produto", "titulo"):
        if coluna in df.columns:
            ident = ident.fillna(df[coluna].astype("string").str.strip().replace("", pd.NA))
    df = df[ident.notna()].copy()
    if not len(df):
        return pd.DataFrame(columns=["data", "avaliacoes_dia", "anuncios"])
    df["item"] = df["plataforma"].astype(str) + "|" + ident[ident.notna()]

    por_dia = df.groupby(["item", "data"])["reviews"].max().reset_index().sort_values(["item", "data"])
    por_dia["delta"] = por_dia.groupby("item")["reviews"].diff()
    por_dia["dias"] = por_dia.groupby("item")["data"].diff().dt.days
    por_dia = por_dia[por_dia["dias"] > 0]
    por_dia["taxa"] = por_dia["delta"].clip(lower=0) / por_dia["dias"]

    out = por_dia.groupby("data").agg(
        avaliacoes_dia=("taxa", "sum"), anuncios=("item", "nunique")
    ).reset_index()
    out["data"] = out["data"].dt.date
    out["avaliacoes_dia"] = out["avaliacoes_dia"].round(1)
    return out


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman sem scipy: Pearson sobre os ranks."""
    return float(a.rank().corr(b.rank()))


@dataclass(frozen=True)
class Correlacao:
    """Correlação clima × venda numa defasagem."""

    defasagem: int
    pontos: int
    spearman: float

    @property
    def forte(self) -> bool:
        """Evidência mínima: pontos suficientes e |r| ≥ 0,3."""
        return self.pontos >= MIN_PONTOS_CORRELACAO and abs(self.spearman) >= 0.3


def correlacao_defasada(
    clima: pd.Series,
    vendas: pd.Series,
    defasagens: Iterable[int] = range(0, 15),
    suavizar: int = 7,
) -> List[Correlacao]:
    """
    Spearman entre clima (dia t) e venda (dia t + defasagem).

    As duas séries são suavizadas por média móvel de ``suavizar`` dias antes —
    tira o ciclo da semana (sábado promocional) que, sem isso, domina a
    correlação diária.

    Args:
        clima: série indexada por data (ex.: anomalia do índice Brasil).
        vendas: série indexada por data (ex.: `proxy_vendas_diario`).
        defasagens: dias de atraso da venda em relação ao clima.
        suavizar: janela da média móvel (1 = sem suavização).

    Returns:
        Uma `Correlacao` por defasagem com pelo menos 3 pontos em comum.
    """
    c = pd.Series(clima, dtype=float).copy()
    v = pd.Series(vendas, dtype=float).copy()
    c.index = pd.to_datetime(c.index)
    v.index = pd.to_datetime(v.index)
    c = c.sort_index().asfreq("D")
    v = v.sort_index().asfreq("D")
    if suavizar > 1:
        c = c.rolling(suavizar, min_periods=max(2, suavizar // 2)).mean()
        v = v.rolling(suavizar, min_periods=max(2, suavizar // 2)).mean()

    resultado: List[Correlacao] = []
    for lag in defasagens:
        par = pd.concat([c, v.shift(-lag)], axis=1, keys=["c", "v"]).dropna()
        # Série constante não tem correlação (desvio zero): fica de fora em
        # vez de entrar como NaN no veredito.
        if len(par) < 3 or par["c"].nunique() < 2 or par["v"].nunique() < 2:
            continue
        resultado.append(Correlacao(int(lag), len(par), round(_spearman(par["c"], par["v"]), 3)))
    return resultado


def correlacao_mensal(clima_diario: pd.DataFrame, vendas_mensais: pd.DataFrame, regiao: str = BRASIL) -> Optional[Correlacao]:
    """
    Spearman mensal entre o clima de uma região e uma série externa de venda.

    É o caminho para provar a hipótese com HISTÓRICO longo: o backfill do
    clima vai a 2016, e o GfK/sell-out mensal da empresa tem anos — muito mais
    pontos que os ~3 meses de Mais Vendidos.

    Args:
        clima_diario: saída de `indice_regional` (observado).
        vendas_mensais: colunas ``mes`` (AAAA-MM) e ``vendas`` (unidades,
            valor ou share — o que o usuário tiver).
        regiao: região do clima a comparar.

    Returns:
        A correlação (defasagem 0), ou None sem meses em comum.
    """
    idx = clima_diario[(clima_diario["regiao"] == regiao) & (clima_diario["tipo"] == OBSERVADO)].copy()
    if not len(idx) or not len(vendas_mensais):
        return None
    coluna = "anomalia_tmax" if idx.get("anomalia_tmax", pd.Series(dtype=float)).notna().any() else "tmax"
    idx["mes"] = pd.to_datetime(idx["data"]).dt.strftime("%Y-%m")
    clima_mes = idx.groupby("mes")[coluna].mean()
    vendas = vendas_mensais.assign(mes=vendas_mensais["mes"].astype(str).str[:7]).set_index("mes")["vendas"]
    par = pd.concat([clima_mes, pd.to_numeric(vendas, errors="coerce")], axis=1, keys=["c", "v"]).dropna()
    if len(par) < 3 or par["c"].nunique() < 2 or par["v"].nunique() < 2:
        return None
    return Correlacao(0, len(par), round(_spearman(par["c"], par["v"]), 3))


def ler_veredito(correlacoes: Sequence[Correlacao]) -> str:
    """
    Uma frase honesta sobre o que as correlações sustentam.

    Nunca diz "comprovado" com evidência fraca — ver a regra 1 do módulo.
    """
    if not correlacoes:
        return "Sem pontos em comum entre clima e venda — nada a concluir ainda."
    melhor = max(correlacoes, key=lambda c: abs(c.spearman))
    sentido = "mais calor, mais venda" if melhor.spearman > 0 else "mais calor, MENOS venda"
    texto = (
        f"Maior correlação com {melhor.defasagem} dia(s) de defasagem: "
        f"Spearman {melhor.spearman:+.2f} ({sentido}), {melhor.pontos} pontos."
    )
    if not melhor.forte:
        texto += (
            f" Evidência FRACA (precisa de ≥ {MIN_PONTOS_CORRELACAO} pontos e "
            "|r| ≥ 0,3) — leia como hipótese, não como regra."
        )
    return texto
