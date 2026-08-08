# Histórico frio no Google Drive — arquitetura híbrida

> **Status:** ✅ Implementado (Jul/2026) · **Módulo:** `utils/history/` ·
> **CLI:** `scripts/history_cli.py` · **Setup:** `scripts/gdrive_setup.py`
>
> Resolve o teto estrutural documentado em
> [`ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md`](ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md):
> o plano free do Supabase comporta ~24 dias de histórico, e só.

---

## A ideia em uma figura

```
                    ┌──────────────────────────────────────┐
   coleta ──────────┤  main.py                             │
   (5.6 mil/dia)    │    1. CSV local        (sempre)      │
                    │    2. Histórico frio   (sempre)  ────┼──► Google Drive
                    │    3. Espelho do CSV   (sempre)  ────┼──►   Parquet/dia
                    │    4. Supabase         (se der)  ────┼──►   + CSV cru
                    └──────────────────────────────────────┘         │
                                                                     │
   dashboard ◄── query_coletas() ──┬── Supabase: janela QUENTE (15d)  │
   relatórios                      └── Histórico: todo o resto ◄──────┘
```

Duas propriedades sustentam o desenho:

1. **Escrita dupla e independente.** O histórico é gravado **antes** do
   Supabase e não depende dele. Quando o banco está restrito por cota (HTTP
   402), o dia entra no histórico do mesmo jeito. Foi exatamente essa
   independência que faltou entre 16 e 25/07/2026, quando 9 dias de coleta
   ficaram só nos artifacts do GitHub Actions.
2. **Leitura costurada.** `query_coletas()` continua consultando o Supabase
   primeiro e **completa com o histórico os dias que o banco não devolveu** —
   sejam dias já migrados, seja o banco inteiro fora do ar. Nenhuma página do
   dashboard precisou mudar.

---

## Por que Parquet (a conta que justifica tudo)

Benchmark medido neste repositório com um dia sintético no formato de produção
(5.651 linhas, 27 colunas, URLs únicas por linha — o pior caso para compressão):

| Formato | Tamanho de 1 dia | Relativo |
|---------|------------------|----------|
| Postgres (medido em produção: 0,87 KB/linha) | **4.916 KB** | 1× |
| CSV (UTF-8, `;`) | 1.750 KB | 2,8× menor |
| **Parquet (zstd + dicionário)** | **104 KB** | **47× menor** |

As colunas do RAC são altamente repetitivas (plataforma, keyword, marca,
seller, tipo_seller), e é disso que o dicionário do Parquet se alimenta.

**Projeção:** no ritmo atual (~36 mil linhas/dia somando coletas e PriceTrack),
um ano inteiro de histórico ocupa **~0,23 GB**. Os 15 GB gratuitos do Drive
comportam **décadas** — o conceito de "janela de retenção" simplesmente deixa
de existir.

---

## Setup (uma vez)

### 1. Credencial OAuth no Google Cloud

Conta pessoal (@gmail.com) exige **OAuth de usuário**, não conta de serviço:
contas de serviço não têm cota de armazenamento própria no "Meu Drive" e o
upload falha com `storageQuotaExceeded` mesmo com a pasta compartilhada.

1. Crie um projeto em https://console.cloud.google.com/
2. Ative a **Google Drive API**.
3. *Credenciais* → *Criar credenciais* → **ID do cliente OAuth** → tipo
   **App para computador**. Baixe o JSON.

> **Google Workspace com Shared Drive?** Aí a conta de serviço funciona (a cota
> é do Shared Drive). Use `GDRIVE_SERVICE_ACCOUNT_JSON` e pule para o passo 3.

### 2. Autorize

```bash
pip install -r requirements.txt
python scripts/gdrive_setup.py --client-secrets client_secret.json
```

O nome real do arquivo baixado é `client_secret_<id>.apps.googleusercontent.com.json`
— longo, e o que a página do Console mostra é o *client id*, igualzinho mas sem o
`.json` do fim. Em vez de digitar, aponte a pasta (ou um curinga) e o script
resolve; se houver mais de um cliente lá, ele lista os caminhos para você
escolher:

```powershell
python scripts\gdrive_setup.py --client-secrets "$env:USERPROFILE\Downloads"
```

O navegador abre o consentimento do Google. Ao fim, o script cria a pasta
`RAC Position Tracker - Historico` no seu Drive e imprime as linhas do `.env`:

```env
GDRIVE_CLIENT_ID=...
GDRIVE_CLIENT_SECRET=...
GDRIVE_REFRESH_TOKEN=...
GDRIVE_FOLDER_ID=...
RAC_HISTORY_BACKEND=drive
```

> ⚠️ O refresh token é credencial. **Nunca** comite o `.env`. Na VM Oracle e no
> GitHub Actions, cadastre os quatro valores como secrets.

### 3. Confirme

```bash
python scripts/gdrive_setup.py --check
```

Grava um marcador, relê e apaga. Se disser `Drive OK`, está pronto.

---

## Operação

### Coleta (automática)

Nada a fazer: `main.py` grava o histórico a cada coleta. Desligue com
`RAC_HISTORY=off` se precisar.

```
[Histórico] coletas/data=2026-07-25__run-36abc8e6.parquet — 5651 linhas, 104 KB → drive:1AbC...
[Drive] CSV espelhado: csv_coletas/rac_monitoramento_20260725_1013.csv (1.7 MB) — cópia crua fora da máquina que coletou.
```

> 🔎 **Leia o sufixo do log do histórico.** `→ drive:<id>` é o esperado;
> `→ local:C:\...\data\history` significa que este host **não** tem credencial
> do Drive e o dia inteiro ficou numa máquina só. O espelho do CSV avisa no
> mesmo run (`CSV NÃO espelhado — o histórico deste host está em modo local`).

### PriceTrack (automático desde Ago/2026)

O import (`scripts/pricetrack_api_import.py`, usado pelos workflows diário e
horário) grava a partição do dia **antes** do Supabase e independente dele —
mesma ordem da coleta:

```
[Histórico] 8.412 linhas em 1 partição(ões) → drive:1KX1...
```

O `run_id` é fixo (`import`), então **reimportar a mesma data sobrescreve** em
vez de duplicar — que é o comportamento certo para o import horário do
intra-dia, que passa várias vezes pelo mesmo dia. Quando a migração alcançar
esse dia, ela grava a versão do banco (já resolvida) e apaga esta.

> ⚠️ Os workflows precisam dos secrets `GDRIVE_*`. Sem eles a gravação cai no
> disco do runner e some com o job — sobra só o Parquet no artifact, que expira
> em 14 dias. `RAC_HISTORY=off` desliga a escrita nos dois pipelines.

### Por que o CSV cru também vai ao Drive

O Parquet é o formato de leitura do dashboard, mas não abre no Excel e não volta
pelo `scripts/upload_csv.py`. Sem o espelho, o CSV da coleta ficava só em
`output/` da máquina que coletou — e quando o Supabase está restrito por cota
(HTTP 402, como em 31/07/2026), essa é a **única cópia crua do dia**.

`RAC_DRIVE_CSV=off` desliga o espelho. Backfill de CSVs antigos que ficaram no
disco:

```bash
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv --mirror
```

O `--mirror` grava a partição Parquet **e** sobe o CSV cru. Reimportar o mesmo
arquivo é idempotente nos dois destinos.

### Ver o que já existe

```bash
python scripts/history_cli.py stats            # backend, dias, buracos na série
python scripts/history_cli.py stats --detail   # linhas por dia e plataforma
```

`stats` aponta **dias sem partição** dentro do intervalo — é o que diz quais
CSVs reimportar.

### Verificação diária (automática)

O watchdog (`.github/workflows/watchdog.yml`, 20:30 BRT) checa o frio junto com
o Supabase e alerta no Telegram:

```bash
python scripts/daily_status_check.py --no-notify   # roda a checagem à mão
```

O check **relê** o Parquet do dia em vez de só listar o arquivo — partição
corrompida ou credencial revogada aparecem como FAIL agora, não no dia do
resgate. Ele roda **antes** do Supabase e não depende dele: a redundância
precisa ser verificável justamente quando o banco está fora.

| Status | Significa |
|--------|-----------|
| ✅ PASS | o dia está no Drive e volta a sair de lá |
| ⚠️ WARN | está gravado, mas em disco local (some com a máquina) ou com menos de 100 linhas |
| ❌ FAIL | não há partição do dia, ela não abre, ou o Drive foi pedido e o store caiu para o disco |

> `FAIL` no frio conta como falha crítica: o job do watchdog termina vermelho.
> Foi a ausência exata desse alarme que deixou a pasta `coletas/` vazia por
> cinco dias em 26–31/07/2026.

### Recuperar dias perdidos (CSV → histórico)

Com os CSVs já em mãos:

```bash
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv
python scripts/history_cli.py import-csv arquivo.csv --dry-run   # só confere
```

O `run_id` é derivado do nome do arquivo, então **reimportar o mesmo CSV é
idempotente** — a partição é reescrita, não duplicada.

### `output/raw_<RUN_ID>.csv` — o dump que não depende de formatação

Desde Ago/2026 a coleta grava, **antes de qualquer tipagem**, um dump bruto de
tudo que os scrapers devolveram:

```bash
python scripts/history_cli.py import-csv output/raw_<RUN_ID>.csv
python scripts/upload_csv.py output/raw_<RUN_ID>.csv       # repõe no Supabase
```

Ele existe porque o CSV formatado pode falhar **pelo conteúdo do dado**: no run
`#174`, um valor que não cabia em `Int64` derrubou a exportação e levou junto o
histórico e o Supabase — 6.047 registros e 1h22m de scraping perdidos. O dump
bruto não converte nada, sai primeiro e é independente das demais etapas; o
prefixo `raw_` o mantém fora dos globs de import automático (que procuram
`rac_monitoramento_*.csv`). Ele também **preserva colunas que o CSV formatado
descarta**, como `Produto Normalizado`.

Vai junto no artifact `rac-coleta-*` do Actions.

### Resgate direto dos artifacts do Actions

Quando a gravação falhou mas a coleta rodou, o CSV está no artifact do job.
`recover_from_artifacts.py` faz o caminho inteiro — lista, baixa, extrai e
carrega no histórico — sem ninguém abrir 24 zips à mão:

```bash
export GITHUB_TOKEN=...        # fine-grained com leitura de Actions

python scripts/recover_from_artifacts.py --list                        # o que dá pra resgatar
python scripts/recover_from_artifacts.py --start 2026-07-16 --dry-run  # baixa e conta
python scripts/recover_from_artifacts.py --start 2026-07-16            # grava no frio
python scripts/recover_from_artifacts.py --start 2026-07-16 --supabase # e repõe no banco
```

> ⏳ **Artifact expira em 30 dias.** Passado o prazo o dia não é mais
> recuperável por este caminho — o `--list` marca os que já se foram.

### Migração: Supabase → Drive

Move para o histórico tudo que saiu da janela quente (default: 15 dias).

```bash
python scripts/history_cli.py tier              # grava no histórico, NÃO apaga
python scripts/history_cli.py tier --confirm    # apaga do Supabase o já verificado
python scripts/history_cli.py tier --dry-run    # só relata
```

Ordem de segurança por dia: **lê do banco → grava a partição → relê o Parquet
para conferir a contagem → só então apaga**. Se a releitura vier com menos
linhas que a origem, o dia **não** é apagado e o erro aparece no log.

> Rodar mensalmente (ou quando a cota apertar) mantém o Supabase dentro do
> plano free indefinidamente.

### Relatórios direto do histórico

```bash
python scripts/history_cli.py export \
    --start 2026-01-01 --end 2026-07-25 -o reports/historico.csv

python scripts/history_cli.py export --start 2026-01-01 --end 2026-07-25 \
    -o reports/frio.csv --cold-only     # ignora o Supabase
```

Em Python:

```python
from datetime import date
from utils.history import read_coletas
from utils.supabase_client import _get_client

df = read_coletas(date(2026, 1, 1), date.today(), supabase_client=_get_client())
```

`read_coletas` costura frio + quente e resolve dias duplicados com precedência
do **Supabase** (é lá que a automação Admin aplica normalização e de-para).

---

## Como fica no Drive

```
RAC Position Tracker - Historico/
├── coletas/
│   ├── data=2026-07-17__run-553da7ef.parquet
│   ├── data=2026-07-25__run-36abc8e6.parquet   ← coleta da manhã
│   └── data=2026-07-25__run-b1c2d3e4.parquet   ← coleta da noite
└── csv_coletas/
    ├── rac_monitoramento_20260725_1013.csv     ← cru, abre no Excel
    └── rac_monitoramento_20260725_2107.csv
```

`coletas/` é o que o dashboard lê; `csv_coletas/` é a cópia humana/reprocessável
(mesmo arquivo que sai em `output/`). Nomes de CSV colidem só se duas coletas
começarem no mesmo minuto — nesse caso o arquivo é sobrescrito, e a partição
Parquet daquele run continua intacta.

Uma partição = um arquivo **imutável**. Isso é o que faz o cache local quase
nunca invalidar, a leitura nunca precisar travar nada, e reprocessar um dia ser
"escrever um arquivo novo" em vez de "editar um existente".

Duas coletas no mesmo dia geram duas partições (`run_id` diferente) e as duas
são lidas. Reprocessar a **mesma** run sobrescreve — não duplica. Quando isso
acontece, outros hosts detectam pelo `md5Checksum` que a Drive API devolve na
listagem e rebaixam a partição; um cache válido nunca é rebaixado.

### A migração substitui o que a coleta gravou

Um mesmo dia pode ter partição de duas origens:

| Origem | `run_id` | Tem colunas de resolução? |
|--------|----------|---------------------------|
| Coleta (`main.py`, `import-csv`) | uuid do run | ❌ Não — a automação Admin ainda não rodou |
| Migração (`tier`) | `tierMMDD` | ✅ Sim — vem do Supabase já resolvido |

Se as duas coexistissem, o dia seria lido **em dobro**. Por isso `tier`, depois
de gravar e verificar a sua partição, **apaga as da coleta** naquele dia — a
versão resolvida é a autoritativa.

### Resolução e o filtro do dashboard

`estado_match`, `familia_resolvida`, `sku_resolvido` e `voltagem_resolvida` são
preenchidos pela automação Admin **depois** do upload. Uma partição gravada
direto pela coleta não os tem.

O filtro padrão do dashboard é `estado_match = MAPEADO`. Uma partição sem essa
coluna seria escondida por inteiro — foi o que fez as 97 mil linhas recuperadas
do Drive aparecerem como **zero** no painel em 26/07/2026.

A distinção que resolve isso: essas linhas estão **não classificadas**, não
*rejeitadas*. Tratá-las como REVISAR/NAO_AC apaga o histórico recuperado;
tratá-las como MAPEADO infla as métricas curadas. Por isso elas entram por uma
porta própria:

> **Filtros Globais → “Incluir histórico do Drive sem de-para”** (ligado por
> padrão)

| Interruptor | Comportamento |
|-------------|---------------|
| **Ligado** (padrão) | Partições sem colunas de resolução passam pelos filtros de resolução. Os demais filtros (plataforma, marca, BTU…) continuam valendo normalmente. |
| **Desligado** | Paridade estrita com o PostgREST: coluna ausente ⇒ nenhuma linha passa, como um `NULL` que não casa com `.in_(...)`. |

Partições **com** as colunas (as que vieram da migração `tier`) são filtradas
de verdade nos dois modos — o interruptor não as afeta.

O `query_coletas` marca cada linha do frio em `_origem`
(`historico` ou `historico_sem_depara`), para que as páginas possam sinalizar a
procedência dos números.

Fora do painel, o `export` do CLI não aplica filtro de resolução — é o caminho
direto para ler qualquer período, resolvido ou não.

---

## Variáveis de ambiente

| Env | Default | Efeito |
|-----|---------|--------|
| `RAC_HISTORY` | `on` | `off` desliga a gravação do histórico na coleta |
| `RAC_HISTORY_BACKEND` | `auto` | `auto` usa Drive se houver `GDRIVE_FOLDER_ID`, senão disco |
| `RAC_HISTORY_DIR` | `data/history` | Destino local e cache das partições do Drive |
| `RAC_HOT_WINDOW_DAYS` | `15` | Dias mantidos no Supabase antes de migrar |
| `RAC_DRIVE_CSV` | `on` | `off` desliga o espelho do CSV cru em `csv_coletas/` |
| `GDRIVE_FOLDER_ID` | — | Pasta raiz do histórico |
| `GDRIVE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | — | OAuth de usuário |
| `GDRIVE_SERVICE_ACCOUNT_JSON` | — | Alternativa (Workspace + Shared Drive) |

**Degradação por desenho:** se o Drive foi pedido mas a credencial falha, o
histórico cai no disco local e registra o motivo. Perder o dado é pior que
gravá-lo no lugar errado — e `history_cli.py stats` mostra onde ele está.

---

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---------|----------------|------|
| `pyarrow não instalado` | Dependência nova | `pip install pyarrow` |
| Histórico foi para o disco em vez do Drive | Credencial ausente/inválida | `python scripts/gdrive_setup.py --check` |
| PC coletor grava `→ local:C:\...` e o CSV não é espelhado | `.env` daquela máquina sem `GDRIVE_*` (o repo estava em dia, a máquina não) | `scripts\sync_windows.bat` → o passo 6 diagnostica e ensina o setup |
| Libs do Drive ausentes só no notebook | A coleta agendada fazia `git pull` mas nunca `pip install` | Corrigido: `local_scheduled_collect.bat` chama `scripts\ensure_deps.bat` a cada run |
| `storageQuotaExceeded` no upload | Conta de serviço no "Meu Drive" | Use OAuth de usuário (ver Setup) |
| `O Google não devolveu refresh_token` | App já autorizado antes | Revogue em https://myaccount.google.com/permissions e repita |
| `tier` retorna código 2 | Supabase restrito (402) recusa até leitura | Libere espaço pelo SQL Editor (`scripts/retention_cleanup.sql`) e repita |
| Dashboard mostra "exibindo N linhas do histórico frio" | Supabase fora do ar | Esperado — é a degradação funcionando |
| Dashboard avisa "Histórico frio indisponível" | Deploy sem `pyarrow`/libs do Google | Use `requirements_app.txt` (já traz as quatro) e faça reboot do app |
| Drive parou de receber partições e o CSV continua saindo | Coleta morre entre o CSV e o histórico | Ver o fim do log do run: `UnboundLocalError` em `main()` foi essa falha em 26–31/07/2026 (`tests/test_pipeline_entrypoint.py` cobre a regressão) |
| GitHub Actions roda "com sucesso" e nada chega ao Drive | Secrets `GDRIVE_*` não cadastrados no repositório | O workflow emite `::warning` no início do job; cadastre os quatro secrets |
| Coleta falhou e ninguém avisou | Alerta do Telegram sem secrets no repositório | `collect.yml` avisa por `::warning` quando `TELEGRAM_BOT_TOKEN`/`N8N_TELEGRAM_CHAT_ID` faltam |
| Dias perdidos e os CSVs ficaram só no runner | Job morreu antes de gravar | `python scripts/recover_from_artifacts.py --list` (prazo: 30 dias do run) |
| Log diz "Exportação do CSV falhou" mas o job seguiu | Valor impossível de tipar numa coluna inteira | É o comportamento novo: as demais etapas continuam e o dado está em `output/raw_<RUN_ID>.csv` (`tests/test_csv_int_cast.py` cobre a regressão) |

---

## Limites conhecidos

- **A migração precisa do banco de pé** (vale para os dois datasets). Com o
  projeto restrito por cota, a API REST recusa até leitura, então `tier` não
  roda. O desbloqueio inicial continua sendo pelo SQL Editor. A **escrita
  dupla**, essa sim, não depende do banco — é o caminho que garante o dia.
- **Filtros do histórico são reproduzidos em pandas**
  (`_filter_history_coletas` e `_filter_history_pricetrack` em `app.py`). Ao
  mudar um predicado no lado do PostgREST, mude no outro —
  `tests/test_history_dashboard.py` e `tests/test_pricetrack_history.py`
  cobrem a paridade.
- **Partições da coleta não têm de-para** até a migração trazer a versão
  resolvida. Elas aparecem no painel pelo interruptor "Incluir histórico do
  Drive sem de-para", mas não participam dos filtros de família/SKU — para
  isso, é preciso que a automação Admin as resolva no Supabase e a migração
  `tier` as traga de volta. Enquanto o banco estiver restrito por cota, esse
  ciclo não roda.
- **O gap-fill respeita o cap de linhas** de `query_coletas` (50 mil por
  padrão): um intervalo histórico muito longo é truncado pelos dias mais
  recentes, igual ao keyset do Supabase.
