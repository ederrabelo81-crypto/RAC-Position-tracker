# pricetrack_api — Cliente tipado da API Externa do PriceTrack

Camada de acesso resiliente à **API Externa do PriceTrack (v1.2.0)**
(`https://api.pricetrack.com.br`), com estratégia de coleta inteligente
(paginado × export em massa), idempotência por `id` de oferta e
observabilidade estruturada.

**Status:** ✅ Produção — é a camada de API usada por
`scripts/pricetrack_api_import.py` (workflows `pricetrack_daily.yml` e
`pricetrack_daily.yml`; o intra-dia horário foi aposentado em 08/08/2026).

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

### Diário de exports — por que o 429 deixou de travar o import

A API permite **3 exports concorrentes por organização** e **não tem endpoint
de cancelamento**: export criado roda até o fim e, enquanto roda, segura um
slot. Enquanto o id do export vivia só na memória do processo, qualquer Ctrl+C
largava um export órfão — e a execução seguinte, sem saber que aquele export
era dela, criava outro para a mesma data e tomava 429. Duas ou três
interrupções ocupavam os 3 slots e travavam todo import posterior no loop
"aguardando slot". Foi assim que o backfill de 36 dias (2026-07-28 →
2026-09-01) parou em 03/09/2026.

`journal.py` grava `{dataset + corpo do POST} → exportId` em
`<data_dir>/exports_state.json` **assim que o POST volta**, antes de qualquer
espera. Na execução seguinte o `ExportManager`:

| Estado do export do diário | O que faz |
|----------------------------|-----------|
| `DONE` | Só baixa — nenhum slot gasto, nenhum polling |
| `PENDING`/`PROCESSING` | **Adota**: entra na fila de polling sem criar export novo |
| `FAILED` / sumido (404) | Esquece e cria um novo |
| Mais velho que `poll_timeout_seconds` | Esquece, cria um novo e AVISA que provavelmente é ele segurando o slot |

O `submitted_at` de um export adotado reflete a idade **real**:
`poll_timeout_seconds` é a vida máxima do export, não a paciência da execução
da vez. A chave do diário é o corpo inteiro do POST — export filtrado
(`marketplaces`, `collectionHourExecutionRange`) nunca adota o export cheio do
mesmo dia, que traria menos linhas do que o pedido, em silêncio.

**Regra dura:** falha do diário nunca derruba o import. Perder o diário custa
um export duplicado; abortar custaria o dia.

### O log fala sozinho enquanto espera

O sintoma que fazia o operador matar runs saudáveis era um console repetindo só
a linha do 429 a cada 30s — o polling do job em voo era `DEBUG`. Agora:

- todo job em voo bate **heartbeat em INFO** (status, progresso, minutos em voo);
- a linha do 429 é *throttled* (`_BLOCK_LOG_INTERVAL`, 60s) e diz há quanto tempo;
- preso há mais de `_CENSUS_AFTER_SECONDS` (2 min), o manager **lista os
  exports da organização** e nomeia o dono de cada slot: desta execução, órfão
  deste projeto (com a data) ou de fora deste import.

Para ver o mesmo censo à mão, sem esperar:

```bash
python -m pricetrack_api exports    # marca `esteProjeto` + `collectionDate`
```

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

> ⚠️ **Este contrato vale para quem escreve preço em qualquer lugar.**
> `scripts/pricetrack_api_import.py` não o seguia até Set/2026: gravava o
> `spotPrice` em vez de `best_cash` e caía em `forwardPrice` (a prazo) quando
> não havia à vista — ~10% acima do painel onde há desconto PIX, com base
> parcelada misturada na mesma série. Ao mexer no preço, mexa nas duas pontas.
> Diagnóstico: [`docs/PRICETRACK_FIDELIDADE.md`](../docs/PRICETRACK_FIDELIDADE.md).

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
