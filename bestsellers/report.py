"""
bestsellers/report.py — Briefs em markdown da rotina de mais vendidos.

Três relatórios:

  * `brief_diario`   — validação, KPI do dia contra o mesmo dia da semana
                       anterior, velocidade declarada, piso de preço,
                       estabilidade e onde a Midea não aparece.
  * `relatorio_periodico` — a evolução semanal ou mensal do share de topo de
                       ranking por plataforma: o ganho/perda que a rotina
                       existe para medir.

O script produz os números; a LEITURA DE NEGÓCIO é do analista. Nenhum destes
textos deve ser entregue cru — o que procurar está listado na seção final do
brief diário.
"""

from typing import Dict, List, Optional

import pandas as pd

from bestsellers import metrics
from bestsellers.config import (
    MINIMO_LEITURAS_TENDENCIA,
    SOURCES,
    TOP_N,
)
from bestsellers.validate import NIVEL_QUARENTENA, Ocorrencia, comparar_endpoints

_RODAPE = (
    "---\n"
    "*Ranking é medida ordinal: não somar posições entre plataformas nem "
    "converter em share de mercado (isso vem de GfK/Neotrust). "
    "Campo sem dado fica vazio — nada aqui é estimado.*"
)


def _tabela(df: pd.DataFrame) -> str:
    """Markdown de um DataFrame, ou uma frase quando ele está vazio."""
    if df is None or not len(df):
        return "_Sem dados nesta seção._"
    return df.to_markdown(index=False)


# ---------------------------------------------------------------------------
# Brief diário
# ---------------------------------------------------------------------------

def brief_diario(
    hoje: pd.DataFrame,
    historico: pd.DataFrame,
    ocorrencias: List[Ocorrencia],
    *,
    marcas_monitoradas=(),
    n: int = TOP_N,
) -> str:
    """
    Monta o brief do dia.

    Args:
        hoje:        coleta do dia, já passada por `metrics.preparar`.
        historico:   série acumulada SEM o dia atual, já preparada.
        ocorrencias: saída de `validate.validar`.
        marcas_monitoradas: marcas do painel, para apontar quem aparece de fora.
        n:           tamanho do topo do ranking.

    Returns:
        Texto markdown.
    """
    linhas: List[str] = []
    data = pd.to_datetime(hoje["data"].iloc[0]) if len(hoje) else pd.Timestamp.now()
    data_str = data.strftime("%Y-%m-%d")
    linhas.append(f"# Mais Vendidos RAC · {data_str} ({metrics.nome_dia_semana(data)})\n")

    # --- 0. Validação ----------------------------------------------------
    linhas.append("## 0. Validação da coleta\n")
    if not ocorrencias:
        linhas.append("Sem ocorrências. Todas as plataformas passaram nos portões.\n")
    else:
        linhas.append("| Nível | Plataforma | Ocorrência |")
        linhas.append("|---|---|---|")
        for ocorrencia in ocorrencias:
            linhas.append(
                f"| {ocorrencia.nivel} | {ocorrencia.plataforma} | {ocorrencia.mensagem} |"
            )
        linhas.append("")

    quarentena = [o.plataforma for o in ocorrencias if o.nivel == NIVEL_QUARENTENA]
    if quarentena:
        linhas.append(
            f"**Em quarentena e fora da análise: {', '.join(quarentena)}.** "
            "Sem o parâmetro de ordenação a lista não é de mais vendidos.\n"
        )

    # --- 1. KPI ----------------------------------------------------------
    linhas.append(f"## 1. KPI: % do top {n} ocupado pelo grupo Midea\n")
    base, mesmo_dia, intervalo = metrics.escolher_base_comparacao(historico, data)

    # Plataformas cujo endpoint mudou desde a leitura anterior: a URL nova é
    # outra população. Calculado UMA vez e aplicado a todas as comparações do
    # brief — KPI, preço e estabilidade.
    trocaram: List[str] = []
    if base is not None and len(base):
        trocaram = [o.plataforma for o in comparar_endpoints(hoje, base)]

    if base is not None and len(base):
        linhas.append(_tabela(metrics.delta_diario(hoje, base, n=n, excluir=trocaram)))
        linhas.append("")
        if trocaram:
            linhas.append(
                f"**Endpoint mudou em: {', '.join(trocaram)} — `delta_pp` "
                "vazio nessas linhas. A URL nova é outra população; a "
                "diferença mediria troca de amostra, não competição.**\n"
            )
        base_str = pd.to_datetime(base["data"].iloc[0]).strftime("%Y-%m-%d")
        if mesmo_dia:
            linhas.append(
                f"Base de comparação: {base_str} "
                f"({metrics.nome_dia_semana(base['data'].iloc[0])}, "
                f"{intervalo} dias atrás).\n"
            )
        else:
            linhas.append(
                f"**Base de comparação é {base_str} "
                f"({metrics.nome_dia_semana(base['data'].iloc[0])}), dia da semana "
                f"DIFERENTE — {intervalo} dias de intervalo. O delta não é "
                "comparável; tratar como leitura isolada.**\n"
            )
    else:
        linhas.append(_tabela(metrics.kpi_topo(hoje, n=n)))
        linhas.append("\nPrimeira leitura da série — sem base de comparação.\n")

    linhas.append(
        f"`itens_topN` é o denominador: numa lista contaminada, {n} posições "
        "podem conter poucos splits, e o percentual muda de sentido.\n"
    )

    # --- 2. Velocidade declarada ----------------------------------------
    linhas.append("## 2. Velocidade declarada (unidades/mês)\n")
    velocidade = metrics.velocidade_declarada(hoje)
    if len(velocidade):
        linhas.append(_tabela(velocidade))
        linhas.append(
            "\nSó entram anúncios com `base_vendidos = 'mes'` (hoje, apenas "
            "Shopee). O `pct` é share DENTRO DA LISTA CAPTURADA — não é share "
            "de mercado. O acumulado do Mercado Livre não é somado aqui: é "
            "outra unidade.\n"
        )
    else:
        linhas.append("Nenhuma plataforma trouxe unidades por mês nesta coleta.\n")

    # --- 3 e 4. Preço e estabilidade -------------------------------------
    if base is not None and len(base):
        linhas.append("## 3. Piso de preço por marca e capacidade (9K/12K)\n")
        piso = metrics.piso_preco(hoje, base, excluir=trocaram)
        movimentos = metrics.movimentos_relevantes(piso)
        if len(movimentos):
            altas = int((movimentos["delta_pct"] > 0).sum())
            baixas = int((movimentos["delta_pct"] < 0).sum())
            linhas.append(
                f"{len(movimentos)} de {len(piso)} células moveram 2% ou mais. "
                f"Altas: {altas}. Baixas: {baixas}. "
                f"Mediana das que moveram: {movimentos['delta_pct'].median():+.1f}%.\n"
            )
            linhas.append(_tabela(movimentos))
            linhas.append("")
            if not mesmo_dia:
                linhas.append(
                    "**Atenção: a base não é do mesmo dia da semana. Movimento "
                    "de piso entre sábado e segunda costuma ser rollback de "
                    "promoção de fim de semana, não competição.**\n"
                )
        else:
            linhas.append("Nenhum movimento relevante de piso.\n")

        linhas.append("## 4. Estabilidade do ranking\n")
        estabilidade = metrics.estabilidade(hoje, base, excluir=trocaram)
        linhas.append(_tabela(estabilidade))
        if len(estabilidade):
            linhas.append(
                "\n`CURADORIA SUSPEITA` = mais de 35% dos itens exatamente na "
                "mesma posição. Ranking de venda real se mexe.\n"
            )

    # --- 5. Mapa competitivo ---------------------------------------------
    linhas.append(f"## 5. Quem ocupa o top {n}\n")
    linhas.append(_tabela(metrics.presenca_por_marca(hoje, n=n)))
    linhas.append("")

    linhas.append("### Líder de cada plataforma\n")
    linhas.append(_tabela(metrics.lideres(hoje).drop(columns=["titulo"], errors="ignore")))
    linhas.append(
        "\nSe o #1 estiver abaixo de R$ 1.800 em 9K ou 12K, a disputa do dia é "
        "de entrada — a linha de entrada é a arma, não a premium.\n"
    )

    if marcas_monitoradas:
        fora = metrics.marcas_fora_do_radar(hoje, marcas_monitoradas, n=n)
        if fora:
            linhas.append(
                f"**Marcas no top {n} fora da lista monitorada: "
                f"{', '.join(fora)}.** Aparição recorrente é pedido de inclusão "
                "no painel.\n"
            )

    # --- 6. Ausências ----------------------------------------------------
    linhas.append(f"## 6. Onde a Midea não aparece no top {n}\n")
    linhas.append(_ausencias(hoje, n))

    # --- 7. Armadilhas ---------------------------------------------------
    linhas.append("\n## 7. Como ler cada plataforma\n")
    linhas.append("| Plataforma | Mecânica | O que pode enganar |")
    linhas.append("|---|---|---|")
    for chave in sorted(set(hoje["plataforma"])) if len(hoje) else []:
        spec = SOURCES.get(chave)
        if spec:
            linhas.append(f"| {spec.nome} | {spec.mecanica} | {spec.armadilha} |")
    linhas.append("")

    linhas.append(_RODAPE)
    return "\n".join(linhas)


def _ausencias(hoje: pd.DataFrame, n: int) -> str:
    """Plataformas com zero posições Midea no topo, com o líder de cada uma."""
    hw = metrics.escopo_kpi(hoje)
    if not len(hw):
        return "_Sem itens no escopo do KPI (split hi-wall)._"

    linhas: List[str] = []
    for plataforma, grupo in hw.groupby("plataforma"):
        topo = grupo[grupo["rank"] <= n]
        if len(topo) and bool(topo["grupo_midea"].any()):
            continue
        lider = grupo.sort_values("rank").iloc[0]
        preco = f"R$ {lider['preco']:,.0f}".replace(",", ".") if pd.notna(lider["preco"]) else "preço n/d"
        btu = f"{int(lider['btu'])} BTU" if pd.notna(lider["btu"]) else "BTU n/d"
        melhor = grupo[grupo["grupo_midea"]]["rank"].min()
        posicao = f"melhor posição #{int(melhor)}" if pd.notna(melhor) else "ausente da lista"
        nome = SOURCES[plataforma].nome if plataforma in SOURCES else plataforma
        linhas.append(
            f"- **{nome}**: zero posições no top {n} ({posicao}). "
            f"Líder: {lider['marca']} {btu} a {preco}."
        )
    return "\n".join(linhas) if linhas else (
        f"Midea presente no top {n} de todas as plataformas válidas."
    )


# ---------------------------------------------------------------------------
# Relatório periódico (semanal / mensal)
# ---------------------------------------------------------------------------

def relatorio_periodico(
    historico: pd.DataFrame,
    periodo: str,
    *,
    ultimos: int = 8,
    n: int = TOP_N,
) -> str:
    """
    Evolução do share de topo de ranking por plataforma.

    É o relatório de ganho/perda: quantos pontos percentuais do topo a Midea
    ganhou ou perdeu de um período para o outro, em cada plataforma.

    Args:
        historico: série completa, já passada por `metrics.preparar`.
        periodo:   'semanal' ou 'mensal'.
        ultimos:   quantos períodos exibir.
        n:         tamanho do topo.

    Returns:
        Texto markdown.
    """
    rotulo = "Semanal" if periodo == metrics.PERIODO_SEMANAL else "Mensal"
    linhas: List[str] = [f"# Mais Vendidos RAC · Evolução {rotulo}\n"]

    if not len(historico):
        linhas.append("_Sem histórico coletado ainda._")
        return "\n".join(linhas)

    serie = metrics.serie_agregada(historico, periodo=periodo, n=n)
    if not len(serie):
        linhas.append("_Sem leituras no escopo do KPI (split hi-wall)._")
        return "\n".join(linhas)

    variacao = metrics.variacao_periodo(serie)
    periodos = sorted(variacao["periodo"].unique())[-ultimos:]
    recorte = variacao[variacao["periodo"].isin(periodos)]

    linhas.append(
        f"Cobertura: {historico['data'].nunique()} dias de coleta, "
        f"de {historico['data'].min():%Y-%m-%d} a {historico['data'].max():%Y-%m-%d}.\n"
    )

    linhas.append(f"## Share de topo de ranking (% do top {n}), por plataforma\n")
    pivot = recorte.pivot(index="periodo", columns="plataforma", values="pct_topN_medio")
    linhas.append(_tabela(pivot.reset_index()))
    linhas.append(
        "\nCada célula é a MÉDIA das leituras diárias do período — média, "
        "nunca soma: posição é ordinal.\n"
    )

    linhas.append(f"## Ganho e perda ({rotulo.lower()})\n")
    colunas = [
        "periodo", "plataforma", "n_leituras", "pct_topN_medio",
        "pct_topN_anterior", "delta_pp", "direcao", "melhor_rank",
        "tendencia_valida",
    ]
    linhas.append(_tabela(recorte[colunas]))
    linhas.append(
        f"\n`tendencia_valida = False` significa menos de "
        f"{MINIMO_LEITURAS_TENDENCIA} leituras no período: o número existe, mas "
        "não sustenta conclusão de tendência.\n"
    )

    frageis = recorte[~recorte["tendencia_valida"]]
    if len(frageis):
        linhas.append(
            f"**{len(frageis)} de {len(recorte)} linhas estão abaixo do mínimo "
            "de leituras.** Coletar todo dia útil é o que torna a série "
            "utilizável.\n"
        )

    linhas.append("## Cobertura da coleta por plataforma\n")
    cobertura = (
        historico.groupby("plataforma")
        .agg(
            dias=("data", "nunique"),
            primeira=("data", "min"),
            ultima=("data", "max"),
            posicoes=("rank", "size"),
        )
        .reset_index()
    )
    linhas.append(_tabela(cobertura))
    linhas.append("")
    linhas.append(_RODAPE)
    return "\n".join(linhas)


def resumo_telegram(
    hoje: pd.DataFrame, ocorrencias: List[Ocorrencia], n: int = TOP_N
) -> str:
    """
    Resumo curto da coleta, para a notificação.

    Deliberadamente sem interpretação: diz o que foi coletado e o que falhou.
    """
    kpi = metrics.kpi_topo(hoje, n=n)
    partes: List[str] = []
    data = pd.to_datetime(hoje["data"].iloc[0]).strftime("%d/%m/%Y") if len(hoje) else "?"
    partes.append(f"<b>Mais Vendidos RAC — {data}</b>")

    if len(kpi):
        for _, linha in kpi.iterrows():
            nome = SOURCES[linha["plataforma"]].nome if linha["plataforma"] in SOURCES else linha["plataforma"]
            pct = "n/d" if pd.isna(linha["pct_topN"]) else f"{linha['pct_topN']:.0f}%"
            melhor = "ausente" if pd.isna(linha["melhor_rank"]) else f"#{int(linha['melhor_rank'])}"
            partes.append(f"• {nome}: top{n} {pct} | melhor {melhor}")
    else:
        partes.append("Nenhuma posição no escopo do KPI.")

    problemas: Dict[str, int] = {}
    for ocorrencia in ocorrencias:
        problemas[ocorrencia.nivel] = problemas.get(ocorrencia.nivel, 0) + 1
    if problemas:
        resumo = ", ".join(f"{v} {k.lower()}" for k, v in problemas.items())
        partes.append(f"Validação: {resumo}")
    return "\n".join(partes)
