"""Competitive Intelligence page — Claude-powered analytical report from coletas."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib.supabase import _resolve_secret, get_filter_options, query_coletas


_CI_SYSTEM_PROMPT = """Você é um analista de inteligência de mercado sênior especializado em e-commerce brasileiro e análise competitiva no setor de climatização (ar condicionado residencial - RAC).

Você está analisando dados de monitoramento de posicionamento coletados automaticamente em marketplaces brasileiros para a Midea Carrier Brasil (MCJV).

CONTEXTO DA EMPRESA:
- Midea Carrier (MCJV) tem ~14% de market share online (target: 15-17%)
- Marcas do grupo: "Midea", "Springer Midea", "Springer" (todas = MCJV)
- Concorrentes diretos: LG, Elgin, Samsung, TCL, Philco, Gree, Electrolux, Agratto
- Concorrentes emergentes: Aufit, Hisense, HQ, Britânia
- Linha de produtos 2026: AI Ecomaster (hero, EF7.6), AI Airvolution (EF7.0), Airvolution Lite (EF4.6, entry), AI Ecomaster Black Edition (lançamento abril/2026), Save Connect (legacy)
- Benchmark de preço: Lite 100%, Airvolution 105%, Ecomaster 113%
- Segmentação BTU: 9K (~34-38%), 12K (~46-50% = dominante), 18K (~9-10%), 24K (~6%)
- Sellers-chave no ML: WebContinental, ClimaRio, Dufrio, Centralar.com, Leveros, Midea Store, Engage Eletro, Bwinx, Bagatoli, Friopeças, Comprebel
- Plataformas com ads (patrocinado): Amazon (AMS) e Magalu (HEROs). ML é 100% orgânico.
- ROAS benchmarks: DCA (ML) ≥35x, AMS (Amazon) ≥8x

ESTRUTURA DO RELATÓRIO (seguir exatamente esta estrutura):

# Relatório de Inteligência Competitiva — Monitoramento de Posicionamento RAC HW
## [Data(s)] | Coletas: [horários]

## Sumário Executivo
- 5 pontos numerados com os achados mais críticos, cada um com dados quantitativos específicos
- Linguagem direta, sem hedge words

## 1. Panorama de Marcas
### 1.1 Share of Shelf (Volume de Aparições)
- Tabela: Marca | Manhã | % | Noite | % | Delta | Plataformas
- Incluir MCJV consolidado (Midea + Springer Midea)

### 1.2 Investimento em Patrocinado (Ads)
- Tabela: Marca | Spon AM | % Total | Spon PM | % Total | Principal Canal
- Identificar quem está investindo mais em cada plataforma

### 1.3 Tags de Destaque (ML)
- Tabela comparativa: MAIS VENDIDO, OFERTA IMPERDÍVEL, RECOMENDADO, Escolha
- Posição de Midea em cada tag

## 2. Análise por Canal/Plataforma
### 2.1 Volume e Competição
- Tabela por plataforma: Total, Marcas, Sponsored, Avg/Min/Max preço

### 2.2 Midea por Plataforma — Posição e Top-10
- Tabela: Plataforma | Total AM | T10 AM | T10% AM | Total PM | T10 PM | T10% PM | Trend

### 2.3 Taxa Top-10 Midea vs Concorrentes
- Tabela separada para ML, Amazon, Magalu
- Calcular T10% = (posições top-10 / total aparições) x 100

## 3. Sellers do Mercado Livre
### 3.1 Ranking de Sellers por Volume
- Top 15-20 sellers: Volume, Top10, Avg preço, Perfil resumido

### 3.2 Composição de Marca por Seller
- Para os top 5 sellers: breakdown das marcas que vendem e volumes

### 3.3 Estratégia de Preço por Seller (foco Midea)
- Tabela: Seller | Midea Min 9K | Midea Min 12K | Postura

### 3.4 Midea Store Performance
- Listings, posições, keywords, portfólio disponível

## 4. Insights de Preço
### 4.1 Mapa de Preço por Marca x BTU
- Tabelas para 9K, 12K, 18K, 24K com Min, Med, Avg por marca

### 4.2 Gap de Preço Midea vs Mercado (ML)
- Tabela: BTU | Midea Min | Concorrente mais barato | Gap %
- Separar outliers de preço (parcela, erro) dos preços reais

### 4.3 Movimentações Intra-dia
- Identificar as maiores variações de preço entre AM e PM
- Destacar variações Midea especificamente
- Flagrar anomalias (variações >30%)

## 5. Análise de Produto Midea
### 5.1 Performance por Linha de Produto
- Tabela: Linha | Listings | Top10 | T10% | Avg R$ | Min R$
- Classificar: AI Ecomaster, AI Airvolution, Airvolution Lite, AI Ecomaster Black, Save Connect, Xtreme Save, Other

### 5.2 Keywords Genéricas — Midea Performance ML
- Tabela: Keyword | Best Pos AM | Best Pos PM | T10 AM | T10 PM | Trend
- Focar nas keywords genéricas de alto tráfego

## 6. Recomendações Estratégicas
### 6.1 Ações Imediatas (próximas 48h)
- 2-3 ações com justificativa quantitativa

### 6.2 Ações de Semana
- 2-3 ações táticas

### 6.3 Ações Estruturais
- 2-3 recomendações de médio prazo

REGRAS DE ANÁLISE:
1. SEMPRE usar números específicos. Nunca dizer "significativo" ou "considerável" sem quantificar.
2. Calcular T10% como: (contagem de registros com Posição Geral ≤ 10) / total de registros da marca naquela plataforma × 100
3. Para classificar como "sponsored/patrocinado": o campo "Posição Patrocinada" está preenchido (não vazio, não "nan", não "None")
4. Marcas MCJV = "Midea" + "Springer Midea" + "Springer" (sempre consolidar quando relevante)
5. Ignorar preços < R$100 (provavelmente parcela ou erro de scraping) para análises de preço real
6. Para extrair BTU do nome do produto, procurar: "9000", "9k", "9.000", "12000", "12k", "12.000", "18000", "18k", "18.000", "24000", "24k", "24.000", "30000", "30k"
7. Para classificar linhas de produto Midea: verificar no nome se contém "ecomaster" (+ "pro"/"8.2" para Pro, + "black"/"pret" para Black), "airvolution" (+ "lite" para Lite), "save connect"/"saveconnect", "xtreme save"
8. Tags de destaque relevantes: "MAIS VENDIDO", "OFERTA IMPERDÍVEL", "RECOMENDADO", "Escolha", "Oferta", "Menor preço em 365 dias"
9. Se houver dados de mais de um dia, incluir análise D/D (dia a dia) com tendências
10. Manter linguagem em PORTUGUÊS BRASILEIRO, usar termos do mercado em inglês quando padrão (sell-in, sell-out, Buy Box, ROAS, etc.)

OUTPUT: Relatório completo em Markdown. Sem blocos de código. Sem introduções genéricas. Começar direto com o título do relatório."""

# Columns to send to Claude (Supabase name → display name)
_CI_COL_MAP = {
    "data":               "Data",
    "turno":              "Turno",
    "plataforma":         "Plataforma",
    "keyword":            "Keyword Buscada",
    "marca":              "Marca Monitorada",
    "produto":            "Produto / SKU",
    "posicao_organica":   "Posição Orgânica",
    "posicao_patrocinada":"Posição Patrocinada",
    "posicao_geral":      "Posição Geral",
    "preco":              "Preço (R$)",
    "seller":             "Seller / Vendedor",
    "tag":                "Tag Destaque",
}
_CI_MAX_RAW = 20_000  # Above this, send pre-aggregated tables instead of raw CSV


def _ci_build_payload(df: pd.DataFrame) -> str:
    """Return the data portion of the Claude user message (raw CSV or aggregated tables)."""
    # Rename to display names for readability
    col_map = {k: v for k, v in _CI_COL_MAP.items() if k in df.columns}
    export = df[list(col_map.keys())].rename(columns=col_map)

    if len(df) <= _CI_MAX_RAW:
        return export.to_csv(sep=";", index=False)

    # Pre-aggregate when dataset is too large to send raw
    sections: list[str] = ["DADOS PRÉ-PROCESSADOS (volume elevado — tabelas agregadas):\n"]

    # 1. Brand × platform × shift summary
    brand_agg = df.groupby(
        ["data", "turno", "plataforma", "marca"], dropna=False
    ).agg(
        total=("posicao_geral", "count"),
        top10=("posicao_geral", lambda x: (x <= 10).sum()),
        top5=("posicao_geral", lambda x: (x <= 5).sum()),
        sponsored=("posicao_patrocinada", lambda x: x.notna().sum()),
        avg_price=("preco", "mean"),
        min_price=("preco", "min"),
    ).reset_index()
    sections.append("=== BRAND SUMMARY ===\n" + brand_agg.to_csv(sep=";", index=False))

    # 2. Seller analysis (ML only)
    ml_df = df[df["plataforma"].str.contains("Mercado Livre|ML", case=False, na=False)]
    if not ml_df.empty:
        seller_agg = ml_df.groupby(
            ["data", "turno", "seller", "marca"], dropna=False
        ).agg(
            count=("posicao_geral", "count"),
            top10=("posicao_geral", lambda x: (x <= 10).sum()),
            avg_price=("preco", "mean"),
            min_price=("preco", "min"),
        ).reset_index()
        sections.append("=== SELLER SUMMARY (ML) ===\n" + seller_agg.to_csv(sep=";", index=False))

        # 3. Keyword × brand performance (ML)
        kw_agg = ml_df.groupby(
            ["data", "turno", "keyword", "marca"], dropna=False
        ).agg(
            count=("posicao_geral", "count"),
            best_pos=("posicao_geral", "min"),
            top10=("posicao_geral", lambda x: (x <= 10).sum()),
            min_price=("preco", "min"),
        ).reset_index()
        sections.append("=== KEYWORD PERFORMANCE (ML) ===\n" + kw_agg.to_csv(sep=";", index=False))

    # 4. Tags distribution
    tag_df = df[df["tag"].notna() & (df["tag"] != "")]
    if not tag_df.empty:
        tag_agg = tag_df.groupby(
            ["data", "turno", "tag", "marca"], dropna=False
        ).size().reset_index(name="count")
        sections.append("=== TAGS DISTRIBUTION ===\n" + tag_agg.to_csv(sep=";", index=False))

    # 5. Price by BTU (sample of raw rows with price data, ML only)
    price_df = df[df["preco"].notna() & (df["preco"] >= 100)]
    if not price_df.empty:
        price_sample = (
            price_df[["data", "turno", "plataforma", "marca", "produto", "preco", "seller"]]
            .sort_values("preco")
            .head(5000)
        )
        sections.append("=== PRICE SAMPLE (up to 5000 rows) ===\n" + price_sample.to_csv(sep=";", index=False))

    return "\n\n".join(sections)


def page_ci_analysis() -> None:
    st.title("🧠 Competitive Intelligence")
    st.caption("Análise competitiva gerada por IA com base nos dados de monitoramento de posicionamento.")

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.subheader("Filtros")

        date_range = st.date_input(
            "Período de Análise",
            value=(date.today() - timedelta(days=1), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="ci_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=1)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        turno_filter = st.multiselect(
            "Turno",
            ["Abertura", "Fechamento"],
            default=["Abertura", "Fechamento"],
            key="ci_turno",
        )

        sel_platforms = st.multiselect(
            "Plataformas",
            opts.get("platforms", []),
            key="ci_platforms",
        )

        sel_brands = st.multiselect(
            "Marcas de Foco",
            opts.get("brands", []),
            help="Vazio = todas as marcas",
            key="ci_brands",
        )

        sel_keywords = st.multiselect(
            "Keywords",
            opts.get("keywords", []),
            help="Vazio = todas as keywords",
            key="ci_keywords",
        )

        compare_mode = st.radio(
            "Modo de Análise",
            [
                "Dia Completo (AM vs PM)",
                "Comparativo Diário (D/D)",
                "Período Consolidado",
            ],
            key="ci_mode",
        )

        load_btn = st.button("🔍 Carregar Dados", type="primary", use_container_width=True)

    # ── Session-state keys ─────────────────────────────────────────────────────
    if "ci_df" not in st.session_state:
        st.session_state["ci_df"] = None
    if "ci_report" not in st.session_state:
        st.session_state["ci_report"] = None

    # ── Load data ──────────────────────────────────────────────────────────────
    if load_btn:
        st.session_state["ci_report"] = None  # reset previous report
        with st.spinner("Carregando dados do Supabase…"):
            df = query_coletas(
                start_date,
                end_date,
                platforms=sel_platforms or None,
                brands=sel_brands or None,
                keywords=sel_keywords or None,
                limit=100_000,
            )
        # Filter by turno (not a direct Supabase filter — apply in-memory)
        if turno_filter and not df.empty and "turno" in df.columns:
            df = df[df["turno"].isin(turno_filter)]

        st.session_state["ci_df"] = df if not df.empty else None

    df: pd.DataFrame | None = st.session_state.get("ci_df")

    if df is None:
        st.info("Configure os filtros na barra lateral e clique em **Carregar Dados**.")
        return

    if df.empty:
        st.warning("Nenhum registro encontrado para o período e filtros selecionados.")
        return

    # ── Summary metrics ────────────────────────────────────────────────────────
    n_plat    = df["plataforma"].nunique() if "plataforma" in df.columns else 0
    n_kw      = df["keyword"].nunique()    if "keyword"    in df.columns else 0
    n_brands  = df["marca"].nunique()      if "marca"      in df.columns else 0
    n_days    = df["data"].nunique()       if "data"       in df.columns else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Registros", f"{len(df):,}")
    c2.metric("Plataformas", n_plat)
    c3.metric("Keywords", n_kw)
    c4.metric("Marcas", n_brands)
    c5.metric("Dias", n_days)

    with st.expander("Pré-visualização dos dados (primeiras 100 linhas)"):
        preview_cols = [c for c in _CI_COL_MAP if c in df.columns]
        st.dataframe(
            df[preview_cols].head(100).rename(columns=_CI_COL_MAP),
            use_container_width=True,
            height=300,
        )

    st.divider()

    # ── Generate report ────────────────────────────────────────────────────────
    api_key = _resolve_secret("ANTHROPIC_API_KEY")

    if not api_key:
        st.error(
            "**ANTHROPIC_API_KEY** não encontrada. "
            "Adicione-a em `.env` (local) ou em **Settings → Secrets** (Streamlit Cloud)."
        )
        return

    gen_btn = st.button(
        "🤖 Gerar Análise Competitiva",
        type="primary",
        use_container_width=True,
        key="ci_gen",
    )

    if gen_btn:
        st.session_state["ci_report"] = None

        # Build metadata header for user message
        platforms_str = ", ".join(sorted(df["plataforma"].unique())) if "plataforma" in df.columns else "N/D"
        turnos_str    = ", ".join(sorted(df["turno"].unique()))       if "turno"      in df.columns else "N/D"
        dates_str     = f"{start_date} a {end_date}" if start_date != end_date else str(start_date)

        data_payload = _ci_build_payload(df)
        payload_mode = "dados brutos" if len(df) <= _CI_MAX_RAW else "dados pré-processados"

        user_msg = (
            f"Analise os seguintes dados de monitoramento de posicionamento RAC coletados em {dates_str}.\n\n"
            f"Total de registros: {len(df):,}\n"
            f"Plataformas: {platforms_str}\n"
            f"Keywords: {n_kw} únicas\n"
            f"Turnos: {turnos_str}\n"
            f"Modo de análise solicitado: {compare_mode}\n"
            f"Formato dos dados: {payload_mode}\n\n"
            f"DADOS (separador ponto-e-vírgula):\n{data_payload}"
        )

        with st.spinner(f"Analisando {len(df):,} registros… (pode levar 30–90 s)"):
            try:
                import anthropic as _anthropic
                client_ai = _anthropic.Anthropic(api_key=api_key)
                response = client_ai.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16_000,
                    system=_CI_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                st.session_state["ci_report"] = response.content[0].text
            except Exception as exc:
                st.error(f"Erro ao chamar a Claude API: {exc}")
                return

    # ── Render report ──────────────────────────────────────────────────────────
    report_md: str | None = st.session_state.get("ci_report")

    if report_md:
        st.markdown(report_md)

        st.divider()

        dates_label = f"{start_date}_{end_date}" if start_date != end_date else str(start_date)
        st.download_button(
            label="📥 Download Relatório (.md)",
            data=report_md,
            file_name=f"Relatorio_CI_{dates_label}.md",
            mime="text/markdown",
            use_container_width=True,
        )
