# PriceTrack Importer

Módulo standalone para importar exports diários do **PriceTrack** (ferramenta
oficial Midea de monitoramento de preços) para a tabela Supabase
`pricetrack_daily`.

**Status:** ✅ Pronto para uso (módulo isolado — não toca o código existente do
`rac-position-tracker`)

---

## Por que existe

O PriceTrack passou a ser a fonte de verdade de preço/share para a categoria
RAC: cobre 165 marketplaces, 641 sellers e bypassa WAF (Casas Bahia, Shopee,
Carrefour) que nossos scrapers não cobrem. Os scrapers próprios seguem úteis
para *posição orgânica*, *patrocinado* e *URL do anúncio* — o que o PriceTrack
não entrega.

Este módulo ingere o export manual (`.md` ou `.xlsx`) e normaliza para o
schema Supabase, com **idempotência** em reimports.

---

## Setup

### 1. Dependências

```bash
pip install psycopg2-binary openpyxl python-dotenv loguru tqdm pytest
```

### 2. `.env` (no root do projeto)

```env
SUPABASE_DSN=postgresql://postgres:[pwd]@db.[ref].supabase.co:5432/postgres
PRICETRACK_IMPORT_DIR=./imports/pricetrack
PRICETRACK_LOG_DIR=./logs/pricetrack
PRICETRACK_BATCH_SIZE=1000
```

### 3. Migration

Aplique uma vez no Supabase (via SQL Editor ou `psql`):

```bash
psql "$SUPABASE_DSN" -f migrations/001_pricetrack.sql
```

Cria duas tabelas:
- `pricetrack_daily` — linhas diárias (1 linha por dia × marca × SKU × marketplace × seller)
- `pricetrack_import_log` — histórico de execuções (para o modo idempotente do `--dir`)

---

## Uso

### Importar um arquivo

```bash
python -m pricetrack_importer imports/pricetrack/2026-05-27.md
python -m pricetrack_importer imports/pricetrack/2026-05-27.xlsx
```

### Importar um diretório (idempotente)

Pula arquivos já processados com sucesso. Útil para backfill de histórico:

```bash
python -m pricetrack_importer --dir imports/pricetrack/
```

### Validar sem escrever no DB

```bash
python -m pricetrack_importer arquivo.md --dry-run
```

### Forçar reimport (sobrescreve)

```bash
python -m pricetrack_importer arquivo.md --force
```

### Log detalhado

```bash
python -m pricetrack_importer arquivo.md --verbose
```

---

## Output

### Logs estruturados

Cada execução grava `logs/pricetrack/YYYY-MM-DD_HHMMSS.json`:

```json
{
  "execution_id": "2026-05-27_143022",
  "source_file": "imports/pricetrack/2026-05-27.md",
  "started_at": "2026-05-27T14:30:22-03:00",
  "finished_at": "2026-05-27T14:30:48-03:00",
  "duration_seconds": 26,
  "rows": {
    "total_parsed": 13836,
    "metadata_skipped": 3,
    "invalid_seller": 28,
    "valid": 13805,
    "inserted": 13805,
    "updated": 0,
    "rejected": 28
  },
  "rejection_samples": [...],
  "unknown_sellers_count": 12,
  "status": "SUCCESS"
}
```

### Sellers desconhecidos

Sellers sem match no `seller_map.py` viram `logs/pricetrack/unknown_sellers.log`
(formato `raw\tnormalized`). Revise periodicamente e expanda
`SELLER_CANONICAL` no módulo.

---

## Arquitetura

```
pricetrack_importer/
├── __main__.py        # CLI: argparse, orquestração, escreve log JSON
├── parser.py          # Lê .md (streaming) e .xlsx (read_only)
├── normalizer.py      # Data M/D/YY → ISO; decimal; trim/colapso
├── validator.py       # Rejeita metadata + sellers corrompidos
├── seller_map.py      # Mapa canônico FRIOPECAS=FRIOPEÇAS=LOJA OFICIAL...
├── repository.py      # psycopg2 + execute_values + ON CONFLICT
├── logger.py          # ExecutionLog dataclass + JSON output
└── tests/             # pytest (52 testes, sem DB)
```

### Fluxo de uma linha

```
.md/.xlsx
   ↓ parser.parse_file (streaming, anchored right-to-left para pipe no título)
{collectionDate, brand, sku, title, marketplace, seller, MIN..MAX}
   ↓ validator.validate_row
   ├─ METADATA → skip silencioso
   ├─ INVALID_SELLER → log amostra + skip
   └─ MISSING_FIELD → log + skip
   ↓ normalizer (data, seller_canonical, decimais)
{collection_date, brand, sku, ..., seller_canonical, min_price, ...}
   ↓ repository.upsert_rows (batch 1000, ON CONFLICT DO UPDATE)
Supabase pricetrack_daily
```

---

## Quirks tratados

| # | Quirk | Solução |
|---|-------|---------|
| 1 | Data `5/27/26` em M/D/YY ambíguo | `parse_pricetrack_date` com regex estrita e formato fixo |
| 2 | Pipe `\|` dentro do título (NF=13) | Parser ancorado right-to-left: últimas 6 colunas fixas, título é tudo entre |
| 3 | Seller corrompido (`38EZVQA12M5 - 220V`, `(ZQK215BB)`, `530290740`) | `is_invalid_seller` com 4 regras + classifica motivo |
| 4 | Metadados no fim (`Filtros aplicados:`, `Total`) | `is_metadata_row` checa formato da data |
| 5 | Whitespace inconsistente (`FRIOPECAS` vs `FRIOPEÇAS`) | `seller_map.SELLER_CANONICAL` + `normalize_seller` |
| 6 | Decimal com ponto | Mantém float ao longo da pipeline; vírgula só no display |

---

## Testes

```bash
python -m pytest pricetrack_importer/tests/ -v
```

Cobre:
- Parser de `.md` com pipe no título (`test_pipe_in_title`)
- Parser de `.xlsx` equivalente ao `.md` (`test_xlsx_equivalent_to_md`)
- Normalização de data, decimal, texto
- Sellers corrompidos rejeitados com motivo classificado
- Sellers normalizados (3 grafias de "FRIOPEÇAS" → 1 canonical)
- Pipeline ponta a ponta sem DB (`test_pipeline.py`)

Testes que dependem do DB Supabase **não estão neste suite** — rode
manualmente com um arquivo real e verifique via SQL os critérios de aceite:

```sql
-- Idempotência
SELECT rows_inserted, rows_updated
FROM pricetrack_import_log
WHERE source_file = 'imports/pricetrack/2026-05-27.md'
ORDER BY import_finished DESC;

-- Seller corrompido não vai pro DB
SELECT COUNT(*) FROM pricetrack_daily WHERE seller ~ ' - 220V';
-- → 0

-- Sellers normalizados
SELECT COUNT(DISTINCT seller_canonical)
FROM pricetrack_daily
WHERE seller_canonical LIKE '%FRIOP%';
-- → 1
```

---

## Troubleshooting

### `SUPABASE_DSN não configurado`

Crie `.env` no root do projeto (ou exporte `SUPABASE_DSN` no shell).

### `psycopg2 não está instalado`

```bash
pip install psycopg2-binary
```

### `openpyxl não está instalado` (ao tentar `.xlsx`)

```bash
pip install openpyxl
```

### Linha de dados sumiu no log

Cheque `rejection_samples` no JSON da execução — sellers corrompidos e linhas
com `brand`/`sku`/`marketplace` vazios são rejeitados silenciosamente (com
amostragem das 50 primeiras).

### Reimportei o mesmo arquivo e ele foi pulado

Comportamento esperado — o modo `--dir` consulta `pricetrack_import_log` antes
de reimportar. Use `--force` para sobrescrever.

### Muitos `unknown_sellers`

Inspecione `logs/pricetrack/unknown_sellers.log`, identifique grafias que
deveriam virar a mesma canonical (ex: novo dealer com prefixo "LOJA OFICIAL X"
e variante "X.COM.BR") e adicione em `seller_map.SELLER_CANONICAL`.

---

## Quick wins com os dados

Após o primeiro import, dá pra rodar no Supabase:

```sql
-- Spread MIN/MAX por SKU Midea (oportunidade competitiva / violação de MAP)
SELECT sku, title,
       MIN(min_price) AS preco_min,
       MAX(max_price) AS preco_max,
       ROUND(((MAX(max_price) - MIN(min_price)) / MIN(min_price))::numeric * 100, 2) AS spread_pct
FROM pricetrack_daily
WHERE collection_date = '2026-05-27' AND is_midea_group = TRUE
GROUP BY sku, title
HAVING MIN(min_price) > 0
ORDER BY spread_pct DESC
LIMIT 20;

-- Share of shelf 3P dentro do Mercado Livre
SELECT seller_canonical, COUNT(*) AS ofertas
FROM pricetrack_daily
WHERE collection_date = '2026-05-27'
  AND marketplace = 'MERCADO LIVRE'
  AND is_midea_group = TRUE
GROUP BY seller_canonical
ORDER BY ofertas DESC;

-- Gap competitivo por marca
SELECT brand, COUNT(*) AS ofertas
FROM pricetrack_daily
WHERE collection_date = '2026-05-27'
GROUP BY brand
ORDER BY ofertas DESC;
```
