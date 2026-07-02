# pricetrack_api — Cliente tipado da API Externa do PriceTrack

Camada de acesso resiliente à **API Externa do PriceTrack (v1.2.0)**
(`https://api.pricetrack.com.br`), com estratégia de coleta inteligente
(paginado × export em massa), idempotência por `id` de oferta e
observabilidade estruturada.

**Status:** ✅ Produção — é a camada de API usada por
`scripts/pricetrack_api_import.py` (workflows `pricetrack_daily.yml` e
`pricetrack_intraday.yml`).

---

## Arquitetura

```
pricetrack_api/
├── config.py       # PriceTrackSettings — tudo via env, key fora do repr
├── exceptions.py   # taxonomia tipada: 401/400/409/429, FAILED, timeout, URL expirada
├── models.py       # Offer, Shipping, PageMeta, ExportJob, CollectQuery, ExportRequest
├── http.py         # HttpTransport — retry + backoff exponencial c/ jitter
├── client.py       # PriceTrackClient — paginação via hasNextPage, exports, download
├── exports.py      # ExportManager — até 3 exports em voo, polling, renovação de URL
├── store.py        # NdjsonStore — partições por collectionDate, dedup por id
├── normalize.py    # preços (spot/forward/pix/priceFrom), AVAILABLE/UNAVAILABLE
├── metrics.py      # CollectionMetrics + alertas (log/Telegram)
├── collector.py    # SmartCollector — threshold paginado × export
├── __main__.py     # CLI: probe | collect | exports
└── tests/          # 88 testes, zero rede (FakeSession/FakeClock)
```

### Fluxo de uma coleta (`SmartCollector`)

```
collect_offers("2026-07-01")
    ↓
1. SONDA: GET /collects-offers-external?take=1        (pageCount = total exato)
    ├─ 409 → NO_DATA (dia sem tabela de coleta)
    ├─ total ≤ threshold (50k default) → ESTRATÉGIA PAGINADA
    │      GET página a página via meta.hasNextPage (nunca take fixo)
    └─ total > threshold → ESTRATÉGIA EXPORT
           POST /exports-external/collects-offers  {collectionDate, marketplaces?}
           polling GET /exports-external/{id}  (pending → processing → DONE|FAILED)
           download NDJSON.gz (URL pré-assinada, TTL 1h, renovação automática)
           filtros extras aplicados client-side (mesma semântica do paginado)
    ↓
2. NdjsonStore.upsert — partição collection_date=YYYY-MM-DD, dedup por id
    ↓
3. CollectionMetrics.log() — linhas, cobertura por marketplace/marca, tempos
   (falha → alerta Telegram/log via AlertSink)
```

---

## Uso

### Como biblioteca

```python
from pricetrack_api import (
    PriceTrackSettings, PriceTrackClient, SmartCollector, CollectQuery,
)

settings = PriceTrackSettings.from_env()   # PRICETRACK_API_KEY obrigatória
client = PriceTrackClient(settings)

# Coleta inteligente de um dia (auto: paginado × export)
collector = SmartCollector(client)
result = collector.collect_offers("2026-07-01")
print(result.metrics.to_dict())

# Iteração paginada direta (streaming, sem materializar tudo)
query = CollectQuery("2026-07-01", marketplace=["MERCADO LIVRE"],
                     product_brand=["MIDEA"], status="AVAILABLE")
for offer in client.iter_offers(query):
    print(offer.sku, offer.spot_price, offer.pix_price)

# Fretes
for ship in client.iter_shipping(CollectQuery("2026-07-01")):
    print(ship.cep, ship.shipping_cost, ship.deadline)
```

### Export em massa manual

```python
from pricetrack_api import ExportManager, ExportRequest

manager = ExportManager(client, dataset="offers")
outcome = manager.run(ExportRequest("2026-07-01"))     # cria → polling → download
print(outcome.path, outcome.job.row_count, outcome.duration_seconds)

# Vários dias com pipeline de até 3 exports em voo (backfill)
outcomes = manager.run_many([ExportRequest(f"2026-06-{d:02d}") for d in range(1, 8)])
```

### CLI

```bash
python -m pricetrack_api probe   --date 2026-07-01            # volume + estratégia
python -m pricetrack_api collect --date 2026-07-01            # coleta → partição local
python -m pricetrack_api collect --date 2026-07-01 --strategy export
python -m pricetrack_api collect --date 2026-07-01 --dataset shipping
python -m pricetrack_api exports                               # lista exports da org
```

---

## Configuração (variáveis de ambiente)

| Variável | Default | Descrição |
|----------|---------|-----------|
| `PRICETRACK_API_KEY` | — (**obrigatória**) | API key (header `token`). **Nunca** hardcoded/logada/versionada |
| `PRICETRACK_BASE_URL` | `https://api.pricetrack.com.br` | Base da API |
| `PRICETRACK_AUTH_HEADER` | `token` | Nome do header de autenticação (ApiKeyAuth) |
| `PRICETRACK_EXPORT_THRESHOLD_ROWS` | `50000` | Acima disso, coleta via export bulk |
| `PRICETRACK_PAGE_TAKE` | `100` | `take` dos endpoints paginados |
| `PRICETRACK_MAX_RETRIES` | `5` | Tentativas extras p/ falhas transitórias |
| `PRICETRACK_BACKOFF_BASE_SECONDS` | `2.0` | Base do backoff exponencial (c/ jitter) |
| `PRICETRACK_BACKOFF_MAX_SECONDS` | `60.0` | Teto de um backoff individual |
| `PRICETRACK_POLL_INTERVAL_SECONDS` | `30` | Intervalo do polling de exports |
| `PRICETRACK_POLL_TIMEOUT_SECONDS` | `7200` | Timeout por export (2h) |
| `PRICETRACK_MAX_CONCURRENT_EXPORTS` | `3` | Exports em voo (limite fixo da API) |
| `PRICETRACK_DOWNLOAD_URL_TTL_SECONDS` | `3000` | Idade máx. do snapshot antes de renovar a URL (TTL real: 1h) |
| `PRICETRACK_DATA_DIR` | `imports/pricetrack/api` | Raiz dos arquivos locais |

---

## Robustez — o que cada erro significa e o que o cliente faz

| Código/evento | Exceção | Política |
|---------------|---------|----------|
| 400 | `PriceTrackBadRequestError` | Filtros/parâmetros inválidos — sem retry, corrija a query |
| 401 | `PriceTrackAuthError` | Key ausente/revogada — sem retry, verifique `PRICETRACK_API_KEY` |
| 409 | `PriceTrackNoCollectionError` | Nenhuma tabela de coleta p/ a data — tratado como `NO_DATA` |
| 429 | `PriceTrackExportLimitError` | Limite de 3 exports concorrentes — `ExportManager` espera slot (honra `Retry-After`) |
| 5xx / rede | `PriceTrackServerError` / `PriceTrackNetworkError` | Retry com backoff exponencial + jitter (`max_retries`) |
| export `FAILED` | `ExportFailedError` | Terminal — reportado/alertado |
| polling > timeout | `ExportTimeoutError` | Job abandonado após `poll_timeout_seconds` |
| downloadUrl 403/404 | `DownloadUrlExpiredError` (interna) | Renovação automática: novo GET de status traz URL fresca |

A downloadUrl é **sempre** tratada como efêmera: além da renovação reativa
(403/404), o cliente renova proativamente quando o snapshot DONE tem mais de
`download_url_ttl_seconds` (50 min, margem sobre o TTL de 1h).

---

## Idempotência e particionamento

O `NdjsonStore` grava uma partição por dia e dataset:

```
imports/pricetrack/api/partitions/
└── offers/
    └── collection_date=2026-07-01/
        ├── data.ndjson.gz     # registros crus, 1 por id (último snapshot vence)
        └── manifest.json      # row_count, collection_hours, sources, updated_at
```

- **Dedup por `id`:** reprocessar o mesmo dia N vezes converge — ids repetidos
  são sobrescritos (não duplicados), ids novos são adicionados.
- **Múltiplas coletas no dia:** cada `collectionHour` gera ofertas com ids
  próprios; a união por id preserva todas as passadas (manhã + tarde etc.).
  O manifest lista as horas vistas.
- **Escrita atômica:** tmp + `os.replace` — crash no meio nunca corrompe a
  partição anterior.

## Normalização de preços

```python
from pricetrack_api import normalize_prices, effective_price

prices = normalize_prices(offer)
prices.spot / prices.forward / prices.pix / prices.rrp   # None quando ausente/≤0
prices.best_cash              # menor à vista (PIX vs spot)
prices.discount_vs_rrp_pct    # desconto % sobre o priceFrom

effective_price(offer)        # None se status == UNAVAILABLE
```

- `pixPrice`/`priceFrom` nullable no schema; qualquer preço ≤ 0 vira `None`
  (nunca 0.0 contaminando mínimos/médias).
- Ofertas `UNAVAILABLE` mantêm o histórico de preço mas não têm preço efetivo.

## Observabilidade

Cada coleta emite um `CollectionMetrics` (log estruturado via
`logger.bind(pricetrack_metrics=...)`): linhas coletadas/novas/atualizadas,
páginas, cobertura por marketplace e por marca, duração do export, tamanho do
arquivo e erros. Falhas disparam `AlertSink` — `TelegramAlertSink` reusa o
notificador do projeto (`TELEGRAM_BOT_TOKEN` + `N8N_TELEGRAM_CHAT_ID`); sem
Telegram, degrada para log de erro.

## Segurança

- A key vem **somente** do ambiente (`.env` gitignored / GitHub Secrets).
- `PriceTrackSettings.api_key` fica fora do `repr`; nenhuma exceção ou log
  carrega headers de request.
- URLs pré-assinadas de download não recebem header de autenticação.

## Testes

```bash
python -m pytest pricetrack_api/tests/ -v
```

88 testes sem rede: paginação real via `hasNextPage`, mapeamento de
400/401/409/429, backoff exponencial determinístico, fluxo assíncrono
completo (pending → processing → DONE/FAILED/timeout), limite de 3 exports
concorrentes, renovação de downloadUrl, dedup/particionamento e normalização
de preços.
