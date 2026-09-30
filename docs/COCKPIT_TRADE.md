# Cockpit do Trade — definições (Set/2026)

A página inicial do dashboard (`streamlit run app.py` → **🏠 Cockpit do Trade**)
substituiu o antigo Overview. Este documento diz o que cada número é, de onde
vem e por que foi desenhado assim. O código das métricas é puro e testado:
`utils/shelf_insights.py` (`tests/test_shelf_insights.py`).

## Por que o Overview antigo saiu

Validado contra o banco em 30/09/2026:

| O que o Overview mostrava | O problema |
|---|---|
| **Registros (8d) = 30,000** | Era o **teto** de linhas (15 mil do banco + 15 mil do histórico), não a contagem. Com ~35 mil linhas/dia, todo gráfico via ~1 dos 8 dias. |
| **Volume por Plataforma** | Recorte truncado escolhido pela **ordem de inserção** — a Amazon (que grava por último, pelo Actions) dominava. E é métrica de pipeline, não de mercado. |
| **Share de Marcas (donut)** | Contava registros, misturando buscas de marca ("ar condicionado midea") com genéricas — o próprio `config.BRAND_NEUTRAL_CATEGORIES` proíbe isso. E contava linhas duplicadas (abaixo). |
| **Preço Médio Midea** | Média de todos os BTUs e formatos juntos (9K com 24K, portátil com hi-wall). |
| **Tendência de Preço por Marca** | Trocava de fonte no meio da série: PriceTrack nos dias importados, coletas nos outros (25, 26 e 29/09 sem PriceTrack) — a Philco "caía" de R$ 13 mil para R$ 2 mil por troca de base. |
| Faixa COBERTURA/FORA DE ESCOPO/NÃO-AC/REVISAR no topo | Métrica de curadoria do de-para em todas as páginas. Agora só em 🩺 Data Health, 🤖 Automação e 🧬 Família & SKU. |

## As três regras

1. **Uma observação por posição.** Até 30/09/2026 cada turno do PC coletor era
   gravado duas vezes em `coletas` (run UUID4 do `main.py` + run UUID5 do
   reforço `upload_csv.py`, ver `utils/run_sidecar.py`). `dedup_snapshot` fica
   com a última run de cada (data, turno, plataforma) e uma linha por posição.
2. **Share só em busca neutra de marca.** O KPI de prateleira usa só as
   buscas **genéricas** (`utils/keyword_taxonomy.py` → `config.BRAND_NEUTRAL_CATEGORIES`).
   As buscas de marca têm leitura própria (Defesa & conquista).
3. **Silêncio não é mudança de mercado.** Turno com coleta parcial (menos de
   30% do melhor turno da plataforma na janela) sai da comparação e vira
   ressalva. E a comparação entre dias usa só os turnos **e as buscas** que os
   dois dias coletaram em cada plataforma.

## Definições

**Grupo Midea Carrier** = marcas canônicas `Midea` (inclui Springer / Springer
Midea / Midea Carrier), `Carrier` e `Comfee` — o mesmo corte de grupo do GfK
usado em `bestsellers.config.GRUPO_MIDEA`. Nas tabelas aparece como "Midea Carrier".

**Prateleira** = as 10 primeiras posições **orgânicas** de uma busca numa
plataforma. Denominador = todas as posições do top 10, de qualquer marca
(inclusive fora do catálogo), **menos** o que não é ar-condicionado
(`estado_match = NAO_AC`: acessório, peça, ruído de busca).

| KPI | Definição |
|---|---|
| **Share prateleira** | posições do grupo ÷ posições no top 10 das buscas genéricas, último dia vs. dia anterior (mesmos turnos e buscas). Delta em pontos percentuais. |
| **Midea no top 3** | pares plataforma × busca genérica em que a melhor oferta do grupo está entre as 3 primeiras. |
| **Rival líder** | maior marca fora do grupo na mesma prateleira. |
| **Buy box Midea** | seller que mais vence a buy box das ofertas do grupo nos **marketplaces** no último dia. Só conta buy box **observada** (`buy_box_seller`); oferta sem buy box vai para a cobertura, não vira "Amazon"/"Casas Bahia". Uma oferta = (data, turno, plataforma, produto), qualquer que seja a busca. |
| **Preço 12K (índice)** | mediana de preço do grupo ÷ mediana dos rivais × 100, split **hi-wall** 12.000 BTU, top 20 do último dia. 100 = paridade. É preço de **vitrine** (o que a SERP mostra); preço de referência por SKU continua no PriceTrack (💰 Preços 9K/12K). |

## Abas

- **📊 Prateleira por plataforma** — barra 100% empilhada (grupo sempre no
  primeiro segmento, top 6 rivais, resto em "Outras") + tabela com Δ pp e
  "Midea − líder (pp)".
- **🔎 Batalha das genéricas** — uma linha por busca genérica (todas as
  plataformas somadas), ordenada pela distância ao líder; detalhe por
  plataforma × busca.
- **🛡️ Defesa & conquista** — buscas pela própria marca (quanto do top 10 é
  nosso; o resto é rival interceptando) e buscas por rivais (quanto a Midea
  aparece nelas).
- **📈 Evolução** — share diário por marca com **peso igual por plataforma**:
  um bloqueio de coleta num marketplace não move a linha.
- **🏷️ Buy box Midea** — ranking de sellers nas ofertas do grupo + cobertura.
- **💰 Preço de vitrine** — índice por BTU (9K/12K/18K/24K).
- **🩺 Cobertura** — linhas por turno nos 3 últimos dias, com ⚠️ na coleta parcial.

## Alertas ("O que mudou e pede ação")

Gerados por `build_alerts`, só entre dias comparáveis:

- 🔴 share do grupo numa plataforma caiu ≥ 5 pp · 🟢 subiu ≥ 5 pp;
- 🟠 rival avançou ≥ 5 pp numa plataforma;
- 🔴 a Midea saiu do top 3 em buscas genéricas (agrupado por plataforma) ·
  🟢 entrou no top 3;
- ⚪ ressalva: plataforma com coleta parcial no último dia (fora dos alertas de share).

## Filtros

O Cockpit usa o **Período**, **Canal** e **Plataformas** globais. **Marca não
filtra o share** (o denominador precisa de todas as marcas); as marcas
escolhidas no global entram como linhas destacadas na aba Evolução.
