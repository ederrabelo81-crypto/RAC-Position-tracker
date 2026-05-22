# Orquestração de Coleta com n8n — RAC Position Tracker

**Uso:** entender o papel do n8n no pipeline de coleta e como o workflow
`n8n/rac_coleta_monitor.json` orquestra notificações e ingestão de CSV manual.

---

## O que o n8n faz — e o que NÃO faz

O n8n é o **orquestrador e a cola** em volta da coleta. Ele não substitui os
scrapers nem a extensão Claude.

| n8n FAZ | n8n NÃO faz |
|---------|-------------|
| Agendar e disparar os scrapers automáticos (`main.py`) | Dirigir a Claude Chrome Extension — ela roda interativa no seu navegador |
| Dividir a coleta automática por plataforma / prioridade | Extrair produtos do DOM (isso é do scraper / da extensão) |
| Receber o CSV da coleta manual, validar e subir ao Supabase | Passar por anti-bot (Akamai/PerimeterX) — quem faz isso é `curl_cffi` ou o Chrome real |
| Notificar PASS/FAIL e o resumo executivo no Telegram | Recalcular marcas / dedup (continua em `utils/`) |

Conclusão: o n8n otimiza o caminho **automático** e o **glue** (validação,
upload, notificação). O passo de IA dentro do navegador continua na extensão —
e a economia de tokens dele está nos guias `docs/manual_*_collection.md`.

---

## Arquitetura do workflow `rac_coleta_monitor.json`

O workflow expõe **dois webhooks independentes**:

### 1. `/coleta` — notificações executivas (já existente)

`Webhook Coleta` → `Telegram`

Usado por `utils/n8n_notify.py` (`notify_start` / `notify_end`). O `main.py`
posta `{event, chat_id, message, ...}` e o n8n encaminha a mensagem pronta ao
Telegram. Nada muda aqui.

### 2. `/coleta-csv` — ingestão da coleta manual (novo)

```
Webhook Coleta CSV → Validar CSV → Nome Valido ┬─(válido)→ Upload CSV → Montar Resultado → Telegram Resultado
                                               └─(inválido)──────────────────────────────→ Telegram Nome Invalido
```

| Nó | Função |
|----|--------|
| `Webhook Coleta CSV` | Recebe `POST /webhook/coleta-csv` com `{filename, chat_id}` |
| `Validar CSV` | Valida `filename` contra `^rac_monitoramento_\d{8}_\d{4}_[a-z]+\.csv$` (também evita injeção de shell no passo seguinte) |
| `Nome Valido` | Bifurca: nome válido segue para o upload; inválido vai direto ao Telegram |
| `Upload CSV` | Confere que o arquivo existe e tem **19 colunas** no cabeçalho, depois roda `reenviar_csv.py` |
| `Montar Resultado` | Lê a saída do upload e classifica **PASS** (`sem discrepâncias`) ou **FAIL** |
| `Telegram Resultado` / `Telegram Nome Invalido` | Enviam o veredito PASS/FAIL ao chat |

Esse caminho **substitui o passo manual** `python reenviar_csv.py ...` descrito
no fim dos guias `manual_*_collection.md`.

---

## Pré-requisitos

1. **n8n no mesmo host do projeto** — o nó `Upload CSV` roda `reenviar_csv.py`
   por `Execute Command`. O caminho padrão é `$HOME/rac-position-tracker`; se o
   projeto estiver em outro lugar, edite o comando do nó `Upload CSV`.
2. **`output/` e `venv/` acessíveis** a partir desse caminho. O nó usa
   `./venv/bin/python` e cai para `python3` se o venv não existir.
3. **Credencial Telegram** `RAC Telegram Bot` (id `1`) configurada no n8n.
4. **`.env`** com `SUPABASE_URL` e `SUPABASE_KEY` na raiz do projeto.

Importar/atualizar o workflow: n8n → *Workflows* → *Import from File* →
`n8n/rac_coleta_monitor.json`.

---

## Como usar a ingestão da coleta manual

1. Rode a coleta na Claude Chrome Extension (ver `docs/manual_<plataforma>_collection.md`).
2. Salve o CSV em `output/` com encoding UTF-8 BOM e o nome no padrão
   `rac_monitoramento_YYYYMMDD_HHMM_<plataforma>.csv`.
3. Dispare o webhook em vez de rodar `reenviar_csv.py` na mão:

```bash
curl -X POST http://localhost:5678/webhook/coleta-csv \
  -H "Content-Type: application/json" \
  -d '{"filename": "rac_monitoramento_20260522_1030_magalu.csv", "chat_id": "123456789"}'
```

O n8n valida o nome, confere as 19 colunas, sobe ao Supabase e responde no
Telegram:

- **PASS** — `✅ Coleta manual - PASS` + nº de registros inseridos
- **FAIL** — `❌ Coleta manual - FAIL` + motivo (nome inválido, arquivo ausente,
  cabeçalho fora de 19 colunas, ou discrepância no upload)

> O cabeçalho esperado (19 colunas, separador `;`) é o mesmo emitido pelos guias
> da extensão: `Data;Turno;Horário;Analista;Plataforma;Tipo Plataforma;Keyword
> Buscada;Categoria Keyword;Marca Monitorada;Produto / SKU;Posição Orgânica;
> Posição Patrocinada;Posição Geral;Preço (R$);Seller / Vendedor;Fulfillment?;
> Avaliação;Qtd Avaliações;Tag Destaque`.

---

## Orquestrar os scrapers automáticos

Para tirar a coleta automática do `cron` puro e ganhar visibilidade, adicione
ao n8n um **Schedule Trigger** ligado a nós `Execute Command`. Divida a carga
pelos eixos que o `main.py` já suporta — `--platforms` e `--priority` — em vez
de rodar tudo de uma vez (a VM Oracle tem só 1 GB de RAM):

```
Schedule (10:00 BRT) → Execute: main.py --platforms ml amazon --pages 2 --priority alta media
                     → Execute: main.py --platforms leroy dealers --pages 2 --priority alta media
                     → Execute: main.py --platforms magalu --pages 2
```

Vantagens de dividir no n8n:
- **Isolamento de falha** — uma plataforma bloqueada não derruba as outras.
- **Menos pressão de memória** — processos menores e sequenciais na VM de 1 GB.
- **Retry centralizado** — configure *Retry On Fail* por nó `Execute Command`.
- **Status por etapa** — cada ramo pode notificar via o webhook `/coleta`.

O `main.py` continua sendo a fonte única da lógica de coleta; o n8n só decide
*quando*, *em que ordem* e *o que fazer quando falha*.

---

## Relação com a economia de tokens da extensão

O n8n assume todo o trabalho **determinístico** (agendar, validar cabeçalho,
subir ao Supabase, notificar), deixando para a Claude Chrome Extension só o
que exige IA: a extração dentro do navegador. Isso mantém a conversa da
extensão curta — menos contexto reenviado a cada passo, menos tokens gastos.
A estratégia de blocos e a recomendação de modelo estão em
`docs/manual_shopee_collection.md`, `manual_magalu_collection.md` e
`manual_casasbahia_collection.md`.
