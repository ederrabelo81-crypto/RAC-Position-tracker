# Módulo Nubimetrics — Sell-out ML

Junta as vendas **estimadas** do Mercado Livre (Nubimetrics) com as coletas do
Tracker para mostrar onde a Midea ganha ou perde sell-out de ar condicionado:
em quais sellers parceiros, em quais BTUs e por quê.

**Status:** 🧪 Beta (Nov/2026) · **Página:** 🛒 Sell-out ML (seção Demanda do
`app.py`) · **Job:** `gh_nubimetrics` (GitHub Actions, 10:43 BRT)

## Como funciona

```
Nubimetrics MCP (mcp.nubimetrics.com)          Coletas do Tracker (ML, 2 dias)
        │ run_daily.py (Actions, 1×/dia)                 │
        ▼                                                 ▼
nubi_mercado_mensal · nubi_ranking_mensal ──► página 🛒 Sell-out ML ◄── coletas
nubi_anuncios (foto) · nubi_concorrente_diario   (st.cache_data 1 h, só leitura)
        │
        └─► Parquet no Drive (dataset `nubimetrics`) — histórico dos anúncios
```

| Arquivo | O que faz |
|---|---|
| `discover.py` | Lista as tools do MCP e grava as amostras e o `catalog/MAPPING_auto.md` |
| `catalog/MAPPING.md` | Mapeamento revisado: tool × fonte, campos, limites e decisões |
| `client.py` | Sessão MCP com retry, intervalo mínimo entre chamadas e cache diário em disco |
| `extract.py` | Uma função por fonte (sempre `MLB1646`, paginando até o fim) |
| `filters.py` | Regra única de segmento, BTU, voltagem e marca |
| `transform.py` | Payload → linhas das tabelas da migração 023 |
| `load.py` | Upsert no Supabase (service_role) + Parquet |
| `run_daily.py` | Orquestra tudo e bate ponto no livro-razão |
| `analytics.py` | Contas do painel (puras, testadas) |
| `page.py` | Página Streamlit |

## Decisões (Nov/2026)

O MCP não entrega tudo o que a interface web do Nubimetrics mostra
(`catalog/MAPPING.md` §4). Decisões tomadas:

1. **Validação contra mês fechado.** O MCP só serve ranking de mês fechado. O
   painel é conferido contra a tela de **outubro** do Nubimetrics, não contra o
   mês corrente parcial. Automaticamente, toda rodada confere se a soma das
   marcas bate com o total da categoria (±2%). Fora disso, a rodada termina
   com ressalva (`PARTIAL`).
2. **High Wall Inverter pelo título.** Não há atributo de anúncio no MCP. O
   painel mostra o % da receita por origem: título explícito, BTU pelo modelo
   ou inferido.
3. **Cruzamento por marca + BTU.** O explorador não traz o código MLB (só um
   hash). O cruzamento principal é por marca × faixa de BTU. O casamento
   anúncio a anúncio por título (score ≥ 0,90) fica como auditoria.
4. **Anúncios = foto diária.** Só a foto mais recente fica no banco (~3 MB,
   trocada por RPC atômica). O histórico vai para o Parquet.
5. **Sellers sem visitas e conversão por anúncio.** A conta tem 1 slot de
   concorrência. A aba Sellers usa receita, share, penetração da Midea,
   catálogo, Full e Premium (exposição `gold_pro`). O slot aparece como
   "concorrente acompanhado". Trocar o seller acompanhado é feito no app do
   Nubimetrics.
6. **Midea = todas as grafias com "Midea".** O ranking de marcas lista MIDEA,
   SPRINGER MIDEA, MIDEA BRANCA e "CARRIER | MIDEA" separados. O painel soma
   todas numa linha só (`analytics.canonizar_marcas`). Em out/2026 isso dá
   17,6% de share, contra 13,3% só da linha MIDEA. Carrier e Springer sozinhos
   continuam como marcas próprias.
7. **Saturação só da categoria.** Não existe por marca, nem o ranking de
   demanda por marca.

**Orçamento do free tier:** cerca de 10 mil linhas por ano no total, sem
`jsonb` bruto no banco. A coleta do PC não é tocada.

## Configurar

1. **Gerar a chave:** no Nubimetrics (conta Midea Brasil), menu **"Ativar o
   MCP"** (`app.nubimetrics.com/mcp`). Para **rotacionar**, gere uma nova lá,
   troque nos dois lugares abaixo e revogue a antiga.
2. **Secret do Actions:** em *Settings → Secrets and variables → Actions*,
   crie `NUBIMETRICS_API_KEY` com só o token, sem `Bearer`. `SUPABASE_URL` e
   `SUPABASE_KEY` (service_role) já existem.
3. **Local:** no `.env`, uma linha `NUBIMETRICS_API_KEY=<token>`. Nunca
   commitar. O `.mcp.json` da raiz lê a mesma variável para o Claude Code.
4. **Banco:** aplique `docs/migrations/023_nubimetrics.sql` no SQL Editor do
   Supabase. Ele é idempotente e traz o seed de `dim_partner_sellers`.
5. **Backfill (uma vez):** Actions → **Nubimetrics Sell-out** → Run workflow
   com `backfill=true` (13 meses de ranking, que formam a série de
   brandshare). Depois ele roda sozinho todo dia às 10:43.
6. **Sellers parceiros:** edite `dim_partner_sellers` no SQL Editor
   (`is_midea_partner`, `tier`, `account_manager`). O seed é provisório.

```bash
python -m nubimetrics.discover              # re-mapear as tools (se o MCP mudar)
python -m nubimetrics.run_daily --dry-run   # extrai e transforma, não grava
python -m nubimetrics.run_daily --backfill  # 13 meses de ranking
```

## O que cada aba responde

| Aba | Pergunta |
|---|---|
| 📊 Visão executiva | Quanto o AC vendeu no ML no mês fechado, qual o share da Midea, o Δ e a distância para a LG, e a série de 13 meses |
| 🎯 Oportunidades | Onde agir primeiro: seller, SKU ou faixa, evidência, ação e impacto em R$. Exporta CSV/XLSX para os sellers e um resumo em inglês para a matriz |
| 🏷️ Produtos | Quais anúncios e SKUs vendem mais, onde cada SKU Midea vende e o share por BTU × marca |
| 🏪 Sellers | Ranking de sellers, penetração da Midea em cada um (índice < 0,8 = sub-representada), mix de marcas e catálogo/Full/Premium |
| 🔎 Busca & demanda | O que se busca em AC, o que está subindo e como está a Midea no BTU de cada termo |
| 🔗 Coletas × vendas | Posição na busca (Tracker) × venda (Nubimetrics) e gap de preço Midea × LG |
| 🔮 Forecast & mercado | Previsão da categoria, saturação e quanto falta para a meta de share |

## Regras das oportunidades

As regras estão em `analytics.oportunidades`. O impacto é estimado em R$, na
janela do explorador:

- **Penetração:** índice < 0,8 numa faixa onde a Midea é forte. Impacto =
  (share Midea na faixa − share no seller) × receita do seller na faixa.
- **SKU ausente:** SKU do top 10 Midea que o parceiro não anuncia, numa faixa
  que ele vende. Impacto = receita do SKU ÷ (sellers que já o têm + 1).
- **Exposição:** anúncio Midea Clássico, sem Full ou fora do catálogo onde a
  maioria dos concorrentes do mesmo seller tem. Impacto = hipótese de +15%
  (`UPLIFT_EXPOSICAO`).
- **Preço:** mediana Midea mais de 10% acima da LG na mesma faixa. Impacto =
  receita da LG na faixa × gap acima do limiar.
- **Busca:** termo subindo numa faixa em que a Midea está abaixo do próprio
  share total.

A regra de conversão abaixo da mediana com visitas altas **não existe**,
porque o MCP não dá visitas por anúncio.
