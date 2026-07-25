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
                    │    3. Supabase         (se der)  ────┼──►   Parquet/dia
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
```

### Ver o que já existe

```bash
python scripts/history_cli.py stats            # backend, dias, buracos na série
python scripts/history_cli.py stats --detail   # linhas por dia e plataforma
```

`stats` aponta **dias sem partição** dentro do intervalo — é o que diz quais
CSVs reimportar.

### Recuperar dias perdidos (CSV → histórico)

Caminho para os 9 dias que o Supabase recusou. Baixe os artifacts do GitHub
Actions (retenção de 30 dias) e:

```bash
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv
python scripts/history_cli.py import-csv arquivo.csv --dry-run   # só confere
```

O `run_id` é derivado do nome do arquivo, então **reimportar o mesmo CSV é
idempotente** — a partição é reescrita, não duplicada.

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
└── coletas/
    ├── data=2026-07-17__run-553da7ef.parquet
    ├── data=2026-07-25__run-36abc8e6.parquet   ← coleta da manhã
    └── data=2026-07-25__run-b1c2d3e4.parquet   ← coleta da noite
```

Uma partição = um arquivo **imutável**. Isso é o que faz o cache local nunca
invalidar, a leitura nunca precisar travar nada, e reprocessar um dia ser
"escrever um arquivo novo" em vez de "editar um existente".

Duas coletas no mesmo dia geram duas partições (`run_id` diferente) e as duas
são lidas. Reprocessar a **mesma** run sobrescreve — não duplica.

---

## Variáveis de ambiente

| Env | Default | Efeito |
|-----|---------|--------|
| `RAC_HISTORY` | `on` | `off` desliga a gravação do histórico na coleta |
| `RAC_HISTORY_BACKEND` | `auto` | `auto` usa Drive se houver `GDRIVE_FOLDER_ID`, senão disco |
| `RAC_HISTORY_DIR` | `data/history` | Destino local e cache das partições do Drive |
| `RAC_HOT_WINDOW_DAYS` | `15` | Dias mantidos no Supabase antes de migrar |
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
| `storageQuotaExceeded` no upload | Conta de serviço no "Meu Drive" | Use OAuth de usuário (ver Setup) |
| `O Google não devolveu refresh_token` | App já autorizado antes | Revogue em https://myaccount.google.com/permissions e repita |
| `tier` retorna código 2 | Supabase restrito (402) recusa até leitura | Libere espaço pelo SQL Editor (`scripts/retention_cleanup.sql`) e repita |
| Dashboard mostra "exibindo N linhas do histórico frio" | Supabase fora do ar | Esperado — é a degradação funcionando |

---

## Limites conhecidos

- **PriceTrack ainda não usa o histórico.** O dataset `pricetrack` existe no
  módulo, mas `query_pricetrack_daily()` continua lendo só do Supabase. A maior
  tabela do banco (224 MB) é justamente essa — migrá-la é o próximo passo de
  maior retorno.
- **A migração precisa do banco de pé.** Com o projeto restrito por cota, a API
  REST recusa até leitura, então `tier` não roda. O desbloqueio inicial continua
  sendo pelo SQL Editor.
- **Filtros do histórico são reproduzidos em pandas** (`_filter_history_coletas`
  em `app.py`). Ao mudar um predicado no lado do PostgREST, mude no outro —
  `tests/test_history_dashboard.py` cobre a paridade.
