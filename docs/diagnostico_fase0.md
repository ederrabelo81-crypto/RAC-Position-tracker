# Painel "Diagnóstico" — relatório da Fase 0 (descoberta)

> 04/11/2026. Nenhum código de produção foi escrito nesta fase. O OpenAPI
> 1.2.0 foi salvo em `docs/pricetrack_openapi_1.2.0.yaml` (o arquivo é JSON,
> que também é YAML válido).

## 1. O que já existe no repositório

O prompt original parte de "o PriceTrack entra hoje via importador de
markdown/xlsx". **Isso está desatualizado**: o import via API 1.2.0 já roda em
produção há meses.

| Peça | Onde | Estado |
|---|---|---|
| Cliente tipado da API | `pricetrack_api/` (`client.py`, `http.py`, `exports.py`, `journal.py`, `models.py`, `normalize.py`) | ✅ produção, 88 testes sem rede |
| Retry/backoff + jitter, 401/400/409/429 tipados | `pricetrack_api/http.py`, `exceptions.py` | ✅ |
| Até 3 exports em voo, polling, renovação da `downloadUrl` (TTL 1h) | `pricetrack_api/exports.py` (`ExportManager`) | ✅ |
| Adoção de export órfão (anti-429) | `pricetrack_api/journal.py` → `imports/pricetrack/api/exports_state.json` | ✅ |
| Import diário D-1 → `pricetrack_daily` | `scripts/pricetrack_api_import.py` (1.375 linhas) | ✅ |
| Agendamento | `.github/workflows/pricetrack_daily.yml` — escada 00:01 / 04:20 / 05:20 BRT + portão 06:35 (`pipeline_guard.yml`) | ✅ |
| Importador antigo md/xlsx | `pricetrack_importer/` | ✅ continua como fallback |
| Clientes Supabase | escrita: `utils/supabase_client._get_client()` (service_role do `.env`); leitura no painel: `app.py::_get_supabase()`; adaptador Postgres: `utils/db.py` | ✅ |
| Páginas Streamlit | **arquivo único** `app.py` (13.683 linhas), dicionário `PAGES` + `_NAV_GROUPS`. **Não existe diretório `pages/`** | — |

**Consequência para a Fase 1:** não faz sentido criar um `pricetrack_api/` novo
— já existe com esse nome e cobre cliente, exports, polling, 429 e diário. A
Fase 1 vira **extensão** dele (ver §5).

## 2. OpenAPI 1.2.0 — confirmações

- Base URL: `https://api.pricetrack.com.br`.
- Auth: `ApiKeyAuth`, header **`token`** (bate com `PRICETRACK_AUTH_HEADER`).
- `Offer` (schema documentado, camelCase): `id, title, category, subcategory,
  spotPrice, forwardPrice, installmentNumber, installmentValue, priceFrom,
  pixPrice, seller, brand, family, color, marketplace, screenshotUrl, status,
  url, collectionDate, collectionHour, collectionHourExecution, productName,
  sku`.
- **O NDJSON do export vem em snake_case** (`spot_price`, `pix_price`,
  `collection_hour`…), não camelCase — já tratado no importador.
- **Não há campo de BTU, tecnologia (Inverter/On-Off), ciclo ou voltagem** no
  schema. Características terão de ser derivadas (catálogo
  `produtos_catalogo`, `pricetrack_dashboard/peer.py`, `shelf_insights.extract_btu`
  no título). A confirmar no NDJSON real.
- **Não há id de seller** no `Offer` — só o nome. A regra "seller vazio →
  `3P:<id>`" não tem de onde tirar o id.
- `proofFilter` existe só no corpo do export de **ofertas** (V1 e V2), padrão
  `WITH_PROOF`. V2 lê do Redis, só os últimos 90 dias, `title` e
  `collection_hour` = null.

## 3. Schema real de `pricetrack_daily` (consultado em 04/11/2026)

Colunas: `id, collection_date, brand, sku, title (NOT NULL), marketplace,
seller, seller_canonical, min_price, avg_price, mode_price, max_price,
spread_pct (gerada), is_midea_group (gerada), imported_at, source_file, turno
(Diário/Manhã/Tarde), price_basis, last_price, last_hour, spot_min_price,
pix_min_price, obs_count, unavailable_count`.

**Chave única:** `(collection_date, turno, brand, sku, marketplace, seller)` —
não `(sku, marketplace, seller, collection_date)` como no prompt. Um upsert
com o `on_conflict` do prompt **falharia** (não há constraint com essas
colunas). O Diagnóstico deve ler `turno = 'Diário'`.

Conteúdo hoje:

| | |
|---|---|
| Janela no banco | 21/10 → 03/11/2026 (14 dias) |
| Linhas | 496.242 (≈ 3 turnos × ~13 mil/dia) |
| `price_basis` | 100% `best_cash` nessa janela |
| Marketplaces/dia | 62–64 |
| **Tamanho da tabela** | **257 MB** |
| **Tamanho do banco** | **637 MB — acima da cota de 500 MB do free tier** |

Ofertas (`sku|seller`) por marketplace em 03/11, turno Diário, só AR
CONDICIONADO: Magazine Luiza 1.880 · Mercado Livre 1.852 · Carrefour 1.436 ·
Casas Bahia 1.276 · Fast Shop 1.226 · Amazon 897 · Leroy Merlin 669 ·
Americanas 569 · Web Continental 448 · Lojas Colombo 431.

## 4. Achados que mudam o plano

1. **O import de produção não envia `proofFilter`** (`ExportRequest.to_body`
   só manda `collectionDate`, `marketplaces`, `collectionHourExecutionRange`).
   Portanto cai em `WITH_PROOF` e **subconta ofertas** desde sempre. Isso afeta
   o Diagnóstico e também tudo que já lê `pricetrack_daily`
   (Daily Price Vision, Top Movers, Preços 9K/12K…). Mudar para `ALL` vai
   gerar degrau de volume no dia da troca — precisa ser decisão consciente e
   carimbada (como foi o `price_basis`).
2. **Espaço no banco.** O banco está em 637 MB (cota 500 MB; ver
   `docs/RETORNO_SUPABASE.md` §5.2). Guardar 31 dias de RAC para o Diagnóstico,
   com colunas novas e com `ALL` (mais linhas), **não cabe** no desenho atual.
   Os 3 turnos triplicam a tabela; o Diagnóstico só precisa do Diário.
3. **Agregação por dia já existe.** Várias execuções no dia já colapsam em
   `min_price` (menor entre spot e PIX, só AVAILABLE), `last_price` e
   `obs_count` — é o "menor preço à vista + n_coletas" pedido.
4. **Agenda.** As regras do `pipeline_registry` pedem um dono por janela e
   nunca minuto `0`. D-0 às 23:40 lê um dia ainda em coleta (o PriceTrack
   coleta 24h); o D-1 já fecha às 00:01/04:20/05:20. Um V2 de D-0 serviria só
   como prévia.
5. **Âncora `42EZVQA09M5`.** Hoje há 2.828 linhas desse SKU em
   `pricetrack_daily` (14 dias × 3 turnos). Preciso saber **quais janelas** da
   Tabela Detalhada dão 701/772 para reproduzir a consulta.
6. **Página.** Não há `pages/`; a página entra como `page_diagnostico` em
   `app.py` (lógica pura num módulo testável, p.ex. `utils/diagnostico.py`),
   usando `_page_period()` e `_inherit_global(...)` como mandam as regras do
   Cockpit.

## 5. Mapeamento NDJSON → `pricetrack_daily` (proposta)

| NDJSON | Coluna | Situação |
|---|---|---|
| `collection_date` | `collection_date` | existe |
| `brand` (upper) | `brand` | existe |
| `sku` | `sku` | existe (sem SKU = rejeitada hoje) |
| `product_name` / `title` | `title` | existe; no V2 usar `product_name` |
| `marketplace` | `marketplace` | existe |
| `seller` | `seller`, `seller_canonical` | existe |
| min(`spot_price`,`pix_price`) AVAILABLE | `min_price`, `last_price`, … | existe |
| `category` | **`category`** | nova |
| `subcategory` | **`subcategory`** | nova (bloco 3 e filtro) |
| `family` | **`family`** | nova |
| `forward_price` | **`forward_min_price`** | nova (diagnóstico; nunca vira preço) |
| `screenshot_url` não nulo | **`has_proof`** (bool / contagem) | nova — separa WITH/WITHOUT proof |
| `url` | **`url`** | nova (drill-down) — pesa em disco |
| derivado do catálogo/título | **`btu`, `tecnologia`, `ciclo`, `voltagem`** | novas, derivadas, não vêm da API |

Alternativa ao inchar `pricetrack_daily`: tabela estreita
`pricetrack_diag_daily` só com Diário e as colunas do Diagnóstico.

## 6. O que faltou na Fase 0 e por quê

O passo 3 (export V2 de teste com `proofFilter: "ALL"`) **não foi executado**:
o container da sessão não tem `PRICETRACK_API_KEY` nem NDJSON em cache
(`imports/pricetrack/api/raw/` vazio). Ficam pendentes: lista real de colunas
do NDJSON, valores distintos de `category`/`subcategory`/`marketplace`,
presença de características e o tamanho do degrau `WITH_PROOF` → `ALL`.

## 7. Decisões do mantenedor (04/11/2026) e o que mudou

| Tema | Decisão | Consequência |
|---|---|---|
| `proofFilter` | Irrelevante para o que consumimos — desconsiderar | O export segue sem o campo (padrão da API). Nada muda em produção |
| Storage | Supabase é crítico; nele fica só D-1, o resto vai como Parquet ao Drive | O Diagnóstico **não** lê 31 dias do Supabase: lê o Parquet (ver abaixo) |
| Agenda | Mantida como definida | Sem job novo de D-0 |
| Export de teste | Roda no PC coletor | `scripts/pricetrack_discovery.py` (só leitura) |

### Por que o banco estava com 14 dias de `pricetrack_daily`

A regra de "só a janela quente no banco" já existia (`RAC_HOT_WINDOW_DAYS=2`
= hoje + D-1) e a poda noturna (`history_cli.py tier`, estágio D) rodava com
sucesso todo dia. O problema estava no degrau das 05:20 do
`pricetrack_daily.yml`: o `--gaps-only` ("auto-heal de 14 dias") procurava
buracos **só no banco**, achava os 12 dias que a poda acabara de levar ao
Parquet e os **reimportava**. Toda manhã a tabela voltava a ter ~14 dias
(257 MB de 637 MB do banco), e de quebra o heal gastava exports à toa.

Correção (`scripts/pricetrack_api_import.py::_is_cold_day`): no
`--gaps-only`, um dia **fora** da janela quente que já está no histórico frio
não é buraco. Dia ausente dos dois lados continua sendo curado; dia da janela
quente ausente do banco também. Se o histórico estiver ilegível, o heal volta
ao comportamento antigo (gasta espaço, mas não perde dia). Efeito esperado:
`pricetrack_daily` cai de ~14 para 2 dias (≈ 37 MB) após a próxima poda,
sem apagar nada além do que a poda já apaga hoje.

### Arquitetura revisada do Diagnóstico

- **Fonte:** Parquet no Drive, não RPC no Supabase. A Fase 2 ("funções SQL")
  vira um módulo Python puro (`utils/diagnostico.py`) com as mesmas fórmulas,
  testado com pandas.
- **Dado:** `pricetrack_daily` não tem categoria, subcategoria, família nem
  status por oferta. Em vez de inchar a tabela, o importador grava um dataset
  novo no histórico frio (`pricetrack_offers`, uma linha por oferta-dia, só
  as colunas do Diagnóstico) a partir do NDJSON **que ele já baixa** — sem
  export extra e sem tocar no Supabase.
- **Página:** `page_diagnostico` em `app.py`, lendo o Parquet com cache.

### Sobre a "âncora 42EZVQA09M5"

O prompt pedia que, após a ingestão, `COUNT(*)` desse SKU nas janelas da
Tabela Detalhada continuasse 701 / 772. É só um teste de regressão: provar
que mexer no import não alterou o que o Top Movers já mostra. Como não há
registro de onde vieram esses números (e o banco agora guarda 2 dias, então
as janelas antigas nem estão lá), a proposta é trocar por uma checagem
reproduzível: contagem de linhas por dia de `pricetrack_daily` antes e depois
de cada mudança no importador.

## 8. Regra dura de escopo (04/11/2026)

**O Diagnóstico só apresenta ar-condicionado, com foco total em Split
Hi-Wall Inverter.**

- Nenhuma outra linha de produto (lavadoras, refrigeradores etc.) aparece em
  gráfico, tabela, filtro ou export — nem como "TODAS".
- O recorte padrão de todas as visões é **Split Hi-Wall Inverter**. Outras
  subcategorias de AR CONDICIONADO (Janela, Portátil, Piso-Teto, On-Off) só
  entram se o usuário escolher no filtro.
- Fora do escopo RAC (Cassete, Duto, Multi-Split, VRF, Chiller, acima de
  30.000 BTU, ventiladores, climatizadores, peças) não entra nunca.
- A validação contra a tela do PriceTrack usa âncoras de ar-condicionado; a
  âncora de lavadoras/refrigeradores (20/10) foi descartada.
