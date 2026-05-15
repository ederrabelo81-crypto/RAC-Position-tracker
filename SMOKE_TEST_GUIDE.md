# Smoke Test — Guia de Uso Rápido

## O que é

Script Python que valida as melhorias implementadas em **8-12 minutos** (vs 30+ min de coleta completa).

Testa cada plataforma com **1 keyword + 1 página** e valida:
- ✅ Seller extraction (Google Shopping, Magalu, Leroy Merlin)
- ✅ Soft-block detection (Magalu)
- ✅ Upsert com ignore_duplicates (Supabase)
- ✅ Nenhuma plataforma quebrada

## Uso básico

```bash
# Roda todos os testes (recomendado antes de commit)
python scripts/smoke_test.py

# Só testa uma plataforma específica
python scripts/smoke_test.py --only magalu

# Sem upload no Supabase (só valida coleta)
python scripts/smoke_test.py --no-upload

# Browser visível (debug visual)
python scripts/smoke_test.py --headless=false
```

## Output esperado

```
╔═══════════════════════════════════════════════════════════════╗
║  RAC Position Tracker — Smoke Test de Melhorias             ║
╚═══════════════════════════════════════════════════════════════╝
Plataformas: ml, amazon, magalu, google_shopping, leroy, dealers
Headless: True

INFO     | [ML] Iniciando smoke test...
SUCCESS  | [ML] ✅ Passou — 18 itens, 7 sellers únicos

INFO     | [AMAZON] Iniciando smoke test...
SUCCESS  | [AMAZON] ✅ Passou — 12 itens, 1 sellers únicos

INFO     | [MAGALU] Iniciando smoke test...
SUCCESS  | [MAGALU] ✅ Passou — 10 itens, 2 sellers únicos

INFO     | [GOOGLE_SHOPPING] Iniciando smoke test...
SUCCESS  | [GOOGLE_SHOPPING] ✅ Passou — 8 itens, 5 sellers únicos

INFO     | [LEROY] Iniciando smoke test...
WARNING  | [LEROY] ⚠️  Passou com ressalvas:
WARNING  |   ⚠️  100% seller '1P Leroy Merlin' — pode indicar regressão

INFO     | [DEALERS] Iniciando smoke test...
SUCCESS  | [DEALERS] ✅ Passou — 12 itens, 1 sellers únicos

INFO     | [SUPABASE] Testando upsert com ignore_duplicates...
SUCCESS  | [SUPABASE] ✅ Upsert com ignore_duplicates funcionando

╔═══════════════════════════════════════════════════════════════╗
║  RESUMO DO SMOKE TEST                                        ║
╚═══════════════════════════════════════════════════════════════╝
Duração total: 542.3s
Plataformas testadas: 6
  ✅ Passaram: 6
  ❌ Falharam: 0

┌─────────────────┬─────────┬───────┬─────────┬──────────┐
│ Plataforma      │ Status  │ Itens │ Sellers │ Duração  │
├─────────────────┼─────────┼───────┼─────────┼──────────┤
│ ml              │ ✅ PASS │    18 │       7 │   76.2s │
│ amazon          │ ✅ PASS │    12 │       1 │   89.5s │
│ magalu          │ ✅ PASS │    10 │       2 │  112.8s │
│ google_shopping │ ✅ PASS │     8 │       5 │   95.1s │
│ leroy           │ ✅ PASS │    15 │       1 │   78.4s │
│ dealers         │ ✅ PASS │    12 │       1 │   90.3s │
└─────────────────┴─────────┴───────┴─────────┴──────────┘

╔═══════════════════════════════════════════════════════════════╗
║  CHECKLIST DE MELHORIAS IMPLEMENTADAS                        ║
╚═══════════════════════════════════════════════════════════════╝
✅ Google Shopping: extração de seller com 4 estratégias (5 sellers únicos)
✅ Magalu: soft-block detection + retry com backoff (10 itens)
   ⚠️  Confira no log se aparece JSON com 'soft_block_detectado': false
⚠️  Leroy Merlin: 100% seller 1P — pode indicar regressão (mas pode ser catálogo real)
✅ Supabase: upsert com ignore_duplicates=True (INSERT ON CONFLICT DO NOTHING)

🎉 SMOKE TEST PASSOU — todas as plataformas funcionando
```

## Interpretação dos resultados

### ✅ PASS
Plataforma coletou >= quantidade mínima esperada com sellers válidos. Tudo OK.

### ⚠️ PASS com ressalvas
Plataforma funcionou, mas com alertas:
- **Seller extraction baixo** (>50% sem seller) — pode ser DOM mudou
- **Baixa diversidade de sellers** — pode ser normal (ex: Amazon 100% 1P)
- **Leroy 100% 1P** — pode ser regressão de 3P extraction ou catálogo real

### ❌ FAIL
- Quantidade < mínimo esperado (scraper quebrado ou bloqueado)
- Exceção não tratada (bug introduzido)
- Nenhum seller extraído (regression crítica)

## Critérios de qualidade por plataforma

| Plataforma | Min itens | Check seller | Diversidade esperada |
|------------|-----------|--------------|---------------------|
| Mercado Livre | 15 | ✓ | ✓ (≥3 sellers) |
| Amazon | 10 | ✓ | — (pode ser 100% Amazon) |
| Magalu | 8 | ✓ | — |
| Google Shopping | 5 | ✓ | ✓ (≥3 sellers) |
| Leroy Merlin | 10 | ✓ | — (mas avisa se 100% 1P) |
| Dealers | 5 | ✓ | — |

## Quando rodar

### ✅ Sempre antes de:
- Fazer commit de mudanças em scrapers
- Fazer push pro repo
- Deploy na VM Oracle
- Merge de PR

### ✅ Após:
- Implementar fix de scraper
- Atualizar seletores
- Mudanças em utils/ que afetam coleta

### ⚠️ Não substitui:
- Coleta completa com 2-3 páginas (validação final)
- Testes de dealers parados (precisam --no-headless + inspeção HTML)

## Troubleshooting

**"ModuleNotFoundError: No module named 'loguru'"**
```bash
pip install --break-system-packages loguru plotly
# ou
pip install -r requirements.txt
```

**"SUPABASE_URL not found"**
```bash
# Confirme que o .env está na raiz do projeto
ls -la .env
# Se não existe, crie:
cp .env.example .env
# E preencha SUPABASE_URL e SUPABASE_KEY
```

**Magalu sempre falha com 0 itens**
- Pode ser soft-block. Rode com `--headless=false` e veja se aparece captcha/challenge
- Olhe o log — se tiver linha JSON com `"soft_block_detectado": true`, o retry foi ativado

**Leroy sempre 100% 1P**
- Rode o PROMPT 2 (diagnóstico com métricas) para entender se é catálogo real ou regressão
- Verifique `logs/leroy_hits_*.json` se ativar `LEROY_DEBUG_HITS=1`

**Exit code 1 (falhou)**
- Olhe a seção "DETALHES DOS ERROS" no output
- Se for exceção Python, copie o traceback completo e diagnostique
- Se for "quantidade insuficiente", pode ser bloqueio anti-bot ou seletor quebrado

## Integração com CI/CD

```yaml
# .github/workflows/smoke-test.yml
name: Smoke Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python -m playwright install chromium
      - run: python scripts/smoke_test.py --no-upload
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
```

## Métricas históricas

Mantenha um log histórico das execuções para detectar degradação:

```bash
# Roda e salva resultado
python scripts/smoke_test.py 2>&1 | tee "logs/smoke_test_$(date +%Y%m%d_%H%M).log"

# Compara com última rodada
diff -u logs/smoke_test_20260502_1000.log logs/smoke_test_20260502_1500.log
```
