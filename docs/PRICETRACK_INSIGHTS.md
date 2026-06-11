# PriceTrack — Insights de Implementação e Roadmap de Melhorias

> **Contexto:** o projeto importa diariamente (06:00 BRT) o export da API da
> Price Track (`pricetrack.com.br`) — preços min/avg/mode/max por
> `(data, marca, sku, marketplace, seller)` da categoria AR CONDICIONADO —
> para a tabela `pricetrack_daily` do Supabase. Desde 28/05/2026 o PriceTrack
> é a **fonte de verdade de preço** nos dashboards (precedência por
> `(data, sku_resolvido)` sobre as coletas próprias).
>
> Este documento responde: **o que mais dá para extrair de um dado tão
> robusto, e o que endurecer na engenharia.**

## 1. O que já está implementado (baseline)

| Componente | Arquivo | Função |
|------------|---------|--------|
| Import diário via API | `scripts/pricetrack_api_import.py` + `.github/workflows/pricetrack_daily.yml` | Export assíncrono → NDJSON.gz → agrega → upsert em `pricetrack_daily`; auto-heal `--gaps-only` (14 dias) |
| Import manual (md/xlsx) | `pricetrack_importer/` | Parser streaming + validador + dedup + upsert idempotente |
| Espelho na VM | `scripts/pricetrack_import_linux.sh` + cron 06:00 | Redundância do GH Actions |
| Reconciliação | `app.py` (`_PT_TO_CANONICAL_PLATFORM`, `seller_map.py`) | De-para marketplace (22 mapas) e seller (~103 variantes → ~30 canônicos) |
| Consumo | `query_pricetrack_daily()` / `query_price_evolution_data()` | Preço PT com precedência; coletas preenchem gaps e posições |
| Auditoria | `pricetrack_import_log` | rows_total/inserted/rejected + rejection_log JSONB |

## 2. Insights de NEGÓCIO a destravar (o dado já existe)

Ordenados por relação valor ÷ esforço. Todos cruzam `pricetrack_daily` (preço
denso e confiável) com `coletas` (posição, buy box, patrocinado, avaliação).

### 2.1 Monitor de MAP / preço-piso por seller ⭐ prioridade
`min_price` por `(sku, seller, dia)` permite flagrar **rompimento de preço
mínimo anunciado** dos SKUs MCJV (`is_midea_group=TRUE`) e identificar QUEM
rompe primeiro (seller_canonical) e ONDE (marketplace). É o insight de canal
mais acionável para o time comercial.
```sql
-- sellers que romperam o piso da família nos últimos 7 dias
SELECT collection_date, sku, seller_canonical, marketplace, min_price,
       min(min_price) OVER (PARTITION BY sku, collection_date) AS piso_dia
FROM pricetrack_daily
WHERE is_midea_group AND collection_date >= current_date - 7
ORDER BY sku, collection_date, min_price;
```
**Dashboard:** página "🛡️ Price Compliance" com limiar configurável por SKU
(tabela `catalogo` ganha coluna `map_price`) + alerta Telegram via
`daily_status_check.py`.

### 2.2 Preço × Buy Box (elasticidade competitiva)
Join `pricetrack_daily` × `coletas.buy_box_seller` por `(data, sku_resolvido,
plataforma)`: mede **quantos % abaixo do 2º preço o vencedor da buy box
precisa estar** em cada marketplace. Responde "vale baixar R$ 50 para ganhar a
buy box no ML?" com dado histórico, não opinião.

### 2.3 Spread como termômetro de guerra de preço
`spread_pct` (coluna computada) por marca/família ao longo do tempo: spread
abrindo = canal desorganizado/guerra; fechando = disciplina. Comparar MCJV vs
LG/Samsung/Elgin numa série semanal é leitura executiva imediata.

### 2.4 Pricing-power dos sellers-chave
Com `seller_canonical` (WebContinental, Dufrio, Leveros, ClimaRio…): quem
**lidera** os movimentos de preço (baixa primeiro, os demais seguem em D+1?) e
quem só acompanha. Detecta o "price-setter" real de cada família — alvo de
negociação.

### 2.5 Posição orgânica × competitividade de preço
`coletas.posicao_organica` × delta do preço PT vs mediana da keyword: produtos
MCJV bem rankeados mas caros = oportunidade de conversão; baratos e mal
rankeados = problema de SEO/relevância, não de preço.

### 2.6 Cobertura/distribuição numérica
Nº de `(marketplace, seller)` distintos ofertando cada SKU por dia = proxy de
distribuição. Queda súbita de sellers de um SKU MCJV antecipa ruptura de
estoque no canal; explosão de sellers desconhecidos sinaliza mercado cinza.

## 3. Melhorias de ENGENHARIA (roadmap priorizado)

| # | Melhoria | Por quê | Esforço |
|---|----------|---------|---------|
| 1 | **Índice `(sku, collection_date)`** em `pricetrack_daily` | Joins por SKU (2.2, 2.5) varrem a tabela hoje; índice atual cobre só `(date, brand)` | Baixo — 1 migration |
| 2 | **Particionamento por mês** (`PARTITION BY RANGE (collection_date)`) | ~1,7M linhas/4 meses; em 12+ meses queries de janela degradam | Médio — migration + recriar índices |
| 3 | **Views materializadas diárias** (`mv_pt_sku_dia`: piso/mediana/spread/n_sellers por sku×marketplace×dia, refresh pós-import) | Dashboard pagina 50k linhas cruas via PostgREST a cada load; a MV reduz para centenas | Médio |
| 4 | **`seller_map` assistido**: job que agrupa `unknown_sellers.log` por similaridade (rapidfuzz) e abre fila de revisão na página 🧬 Família & SKU | 103 variantes mapeadas na mão; novos sellers viram buraco silencioso de canônico | Médio |
| 5 | **Alerta de gap de import** no `daily_status_check.py`: falhou D-1 do PriceTrack → Telegram (hoje só o auto-heal tenta de novo, sem avisar) | Buraco de preço só é notado no dashboard | Baixo |
| 6 | **Categorias além de AR CONDICIONADO** via env/config (`PRICETRACK_CATEGORIES`) em vez de hardcode `DEFAULT_CATEGORIES` | Expansão (ventilador, ar portátil) exige deploy hoje | Baixo |
| 7 | **Retenção/arquivamento**: sumarizar linhas >12 meses em tabela agregada semanal | Custo Supabase + velocidade | Baixo |
| 8 | **Métrica exata de insert vs update** no modo supabase-py (hoje `updated=0` sempre) — ou padronizar import via DSN psycopg2 | Auditoria imprecisa de reprocessamento | Baixo |
| 9 | **SKU vazio**: rejeitar (ou marcar) linhas sem `sku` no validador — hoje entram e não reconciliam com o catálogo | Qualidade do join | Baixo |
| 10 | **Histórico intra-dia** (se o plano PriceTrack expuser hora da oferta): hoje a agregação por dia perde o momento do reprice | Análise 2.4 ganharia precisão de "quem moveu primeiro" | Depende do fornecedor |

## 4. Princípios de uso (para manter o sistema são)

1. **PriceTrack = preço; coletas = contexto.** Nunca reintroduzir preço de
   scraping onde houver PT para o mesmo `(data, sku)` — a precedência de
   28/05/2026 em `query_price_evolution_data()` é regra de arquitetura.
2. **Todo insight novo entra pelo catálogo** (`catalogo` + `produtos_depara_nome`):
   se o SKU não resolve, o cruzamento PT×coletas não existe. Manter a fila
   REVISAR perto de zero (auto-resolver + página 🧬) é pré-requisito dos
   insights da seção 2.
3. **Monitorar o import como produção**: `pricetrack_import_log` + Data Health.
   Dado de preço com buraco corrói a confiança do dashboard inteiro.

*Criado em Jun/2026. Fontes: `scripts/pricetrack_api_import.py`,
`pricetrack_importer/`, `app.py` (queries PT), `migrations/001_pricetrack.sql`.*
