# Orquestração de Coleta com n8n — RAC Position Tracker

**Uso:** entender o papel do n8n no pipeline de coleta e como o workflow
`n8n/rac_coleta_monitor.json` orquestra notificações e ingestão de CSV manual.

---

## O que o n8n faz — e o que NÃO faz

O n8n é o **orquestrador e a cola** em volta da coleta. Ele não substitui os
scrapers nem a extensão Claude.

| n8n FAZ | n8n NÃO faz |
|---------|-------------|
| Detectar um CSV novo na pasta `output/` e subir ao Supabase | Dirigir a Claude Chrome Extension — ela roda no seu navegador |
| Validar o cabeçalho da coleta manual antes do upload | Extrair produtos do DOM (isso é do scraper / da extensão) |
| Notificar PASS/FAIL no Telegram | Passar por anti-bot (Akamai/PerimeterX) — quem faz isso é `curl_cffi` ou o Chrome real |
| — | Orquestrar os scrapers automáticos da VM Oracle (este n8n roda local, sem acesso à VM) |

Conclusão: este n8n roda **self-hosted na máquina local** e cuida do **glue** da
coleta manual (validar, subir, notificar). O passo de IA dentro do navegador
continua na extensão — a economia de tokens dele está nos guias
`docs/manual_*_collection.md`.

---

## Arquitetura do workflow `rac_coleta_monitor.json`

O workflow tem **três gatilhos de entrada independentes**:

### 1. `/coleta` — notificações executivas

`Webhook Coleta` → `Telegram`

Pensado para `utils/n8n_notify.py` (`notify_start` / `notify_end`). Com o n8n
rodando **local**, o `main.py` da VM Oracle não alcança este webhook — ele cai
no envio direto ao Telegram (fallback do próprio `n8n_notify.py`). O nó fica no
workflow para o caso de você rodar um n8n também na VM.

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
| `Upload CSV` | Chama `scripts/n8n_upload.py`, que confere se o arquivo existe e tem **19 colunas** no cabeçalho e então sobe via `reenviar_csv.py` |
| `Montar Resultado` | Lê a saída do upload e classifica **PASS** (`sem discrepâncias`) ou **FAIL** |
| `Telegram Resultado` / `Telegram Nome Invalido` | Enviam o veredito PASS/FAIL ao chat |

Esse caminho **substitui o passo manual** `python reenviar_csv.py ...` descrito
no fim dos guias `manual_*_collection.md`.

### 3. `output/` — upload automático (Local File Trigger)

```
Coleta Pronta → Preparar Upload → Validar CSV → (mesma cadeia do /coleta-csv)
```

| Nó | Função |
|----|--------|
| `Coleta Pronta` | Vigia a pasta `output/`; dispara quando um arquivo novo termina de ser escrito |
| `Preparar Upload` | Aceita só nomes `rac_monitoramento_AAAAMMDD_HHMM_<plataforma>.csv` e ignora o resto (inclusive os CSVs sem sufixo de plataforma gerados pelo `main.py` automático — evita upload em dobro). Preenche o `chat_id` a partir de `$env.N8N_TELEGRAM_CHAT_ID` |

A partir de `Validar CSV` reusa exatamente a cadeia do webhook `/coleta-csv`
(valida 19 colunas → `reenviar_csv.py` → Telegram PASS/FAIL). É o modo
**hands-off**: salvou o CSV em `output/`, o n8n sobe sozinho.

---

## Instalação (n8n self-hosted no Windows)

O workflow roda em um n8n **self-hosted na máquina local** (Windows), junto do
projeto — porque os nós `Coleta Pronta` (lê a pasta `output/`) e `Upload CSV`
(roda Python) precisam do shell e do filesystem locais, que o n8n Cloud não
oferece.

Instalação automatizada (PowerShell como Administrador):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\n8n_setup.ps1
```

O script instala Node.js + n8n, registra uma tarefa agendada que sobe o n8n no
logon e inicia o serviço em `http://localhost:5678`.

## Pré-requisitos

1. **Projeto + `venv/` + `.env`** na máquina local. O nó `Upload CSV` chama
   `venv\Scripts\python.exe scripts\n8n_upload.py`; o `.env` precisa de
   `SUPABASE_URL` e `SUPABASE_KEY`.
2. **Caminho do projeto** — o workflow assume `%USERPROFILE%\rac-position-tracker`.
   Se o projeto estiver em outro lugar, ajuste o comando do nó `Upload CSV`.
3. **Credencial Telegram** configurada no n8n e ligada nos nós Telegram.
4. **Nó `Coleta Pronta`** — ajuste o campo `path` para o caminho absoluto real
   da pasta `output/` (ex: `C:/Users/SEU_USUARIO/rac-position-tracker/output`).
5. **`N8N_TELEGRAM_CHAT_ID`** no ambiente do processo n8n — ou fixe o `chat_id`
   no nó `Preparar Upload`.

Importar/atualizar o workflow: n8n → *Workflows* → *Import from File* →
`n8n/rac_coleta_monitor.json`.

---

## Como usar a ingestão da coleta manual

Há **dois modos** — escolha um, não use os dois para o mesmo arquivo (cada
upload gera um `run_id` novo; subir duas vezes cria um snapshot duplicado).

### Modo A — automático (recomendado)

Com o gatilho `Coleta Pronta` ativo, basta salvar o CSV em `output/`:

1. Rode a coleta na Claude Chrome Extension (ver `docs/manual_<plataforma>_collection.md`).
2. Salve o CSV em `output/` com encoding UTF-8 BOM e o nome no padrão
   `rac_monitoramento_YYYYMMDD_HHMM_<plataforma>.csv`.
3. Pronto — o n8n detecta o arquivo, valida, sobe ao Supabase e notifica no
   Telegram. Repita para as 3 plataformas; cada CSV é processado sozinho ao
   aparecer na pasta.

### Modo B — webhook manual

1. Rode a coleta e salve o CSV em `output/` (passos 1–2 acima).
2. Dispare o webhook em vez de rodar `reenviar_csv.py` na mão:

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

## E os scrapers automáticos?

Os scrapers automáticos (`main.py`) continuam na VM Oracle, no `cron`, com o
próprio upload ao Supabase — este n8n local **não** os orquestra (não tem
acesso ao shell da VM). O n8n aqui cuida só da **ingestão da coleta manual**
feita pela extensão Claude.

Se no futuro você quiser que o n8n também agende e divida os scrapers
automáticos (por plataforma / prioridade, para isolar falhas), isso exige um
n8n rodando **na própria VM** — aí valeria um segundo workflow com `Schedule
Trigger` + `Execute Command`. Fora do escopo deste setup local.

---

## Relação com a economia de tokens da extensão

O n8n assume todo o trabalho **determinístico** (agendar, validar cabeçalho,
subir ao Supabase, notificar), deixando para a Claude Chrome Extension só o
que exige IA: a extração dentro do navegador. Isso mantém a conversa da
extensão curta — menos contexto reenviado a cada passo, menos tokens gastos.
A estratégia de blocos e a recomendação de modelo estão em
`docs/manual_shopee_collection.md`, `manual_magalu_collection.md` e
`manual_casasbahia_collection.md`.
