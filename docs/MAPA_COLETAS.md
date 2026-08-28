# Mapa de Coletas — o que roda onde, quando e por quê

> **Fonte de verdade em código:** `utils/pipeline_registry.py`.
> Este documento explica o mapa; o registro é o que o supervisor executa.
> Se os dois divergirem, o registro está certo e o documento está velho.

---

## 0. A regra que decide tudo: quem pode coletar cada plataforma

Não é preferência nem herança histórica. É uma propriedade da **defesa
anti-bot de cada site**, e ela responde a duas perguntas:

| Pergunta | Se sim → |
|---|---|
| O site bloqueia IP de datacenter antes do fingerprint (Akamai/PerimeterX)? | Só roda em **IP residencial** |
| O site exige sessão logada (cookies que expiram em horas)? | Só roda no **Chrome real logado** |

Quem responde "sim" a qualquer das duas **não pode** rodar no GitHub Actions
nem na VM Oracle. Rodar assim mesmo não dá erro: dá **zero linha com aparência
de sucesso** — o pior resultado possível, porque some do radar.

---

## 1. Onde cada coleta deve rodar

### 🖥️ PC coletor (Windows, IP residencial + Chrome logado)

**Agendar em:** Task Scheduler — `scripts\setup_local_scheduler.ps1`
**Diagnóstico:** `PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1`

| Tarefa | Horário | Coleta | Por que aqui |
|---|---|---|---|
| `RAC_Local_Manha` | 09:00 + catch-up no logon | **Mercado Livre, Magalu, Shopee, Casas Bahia** (+ Amazon e Leroy de carona) | ML e Magalu barram datacenter; Shopee exige sessão `SPC_*`/`csrftoken` capturada |
| `RAC_Local_Noite` | 20:00 + catch-up no logon | as mesmas, turno Fechamento | idem |
| `RAC_Bestsellers` | 09:30, **dia útil** | **Mais Vendidos** (6 listas + 14 dealers) | Amazon/ML/Shopee precisam de browser real e sessão; a Amazon abre o PDP de cada item |

> A janela de 09:00–12:00 (manhã) e 20:00–23:00 (noite) é guardada pelo próprio
> `local_scheduled_collect.bat`: fora dela a tarefa **pula** em vez de gravar
> com o turno errado (`get_turno()` marca Abertura até 12h).

### ☁️ GitHub Actions (IP de datacenter, sem sessão)

**Agendar em:** `.github/workflows/*.yml`, bloco `on: schedule`
**Diagnóstico:** aba Actions → workflow → último run

| Workflow | Horário BRT | Faz | Por que aqui |
|---|---|---|---|
| `pricetrack_daily.yml` | **03:20, 04:20, 05:20** (escada) | Importa preços do PriceTrack (D-1) → `pricetrack_daily` | É chamada de API: não precisa de browser, IP nem sessão |
| `collect.yml` | 10:00 e 21:00 | **Amazon, Leroy Merlin, Google Shopping** | As três aceitam IP de datacenter |
| `watchdog.yml` | 20:30 | Watchdog de **dado** (`daily_status_check.py`) | — |
| `pipeline_guard.yml` | **06:35, 12:35, 22:35** | Supervisor de **execução** + portão do briefing | — |

> ⚠️ **Cron do Actions é *best effort*.** A série de agosto/2026 mostra o
> `pricetrack_daily` começando 09:16, 09:29, 09:50, 11:11, 12:11 UTC — e, em
> 27/08, **19:18 UTC** para um cron de 09:00. Nunca agende um job em minuto `0`
> a menos de duas horas de quem consome o dado dele.
>
> Corolário que vale para todo workflow daqui: **identifique o disparo por
> `github.event.schedule`, nunca pelo relógio de parede.** Um run das 13:00 UTC
> que só arranca às 18:20 continua sendo a Abertura; classificado pela hora, ele
> gravaria o turno errado no dado e bateria ponto como o job errado — deixando a
> Abertura constar como "não executou" exatamente enquanto rodava.

### 🛰️ VM Oracle (Brazil East, IP de datacenter BR)

**Agendar em:** crontab do usuário `ubuntu` (instalado por `scripts/oracle_setup.sh`)
**Diagnóstico:** `ssh ubuntu@<vm-ip> 'crontab -l; tail -50 ~/rac-position-tracker/logs/cron.log'`

| Cron (UTC) | Horário BRT | Faz | Por que aqui |
|---|---|---|---|
| `0 13 * * *` | 10:00 | **Dealers (14 lojistas)** + redundância de Amazon/Leroy/Casas Bahia/Google/Magalu | Dealers são lojas pequenas, sem defesa anti-bot pesada; IP BR ajuda no frete/estoque regional |
| `0 0 * * *` | 21:00 | idem, turno Fechamento | — |

> **A VM é a ÚNICA dona de dealers.** Se ela para, ninguém mais coleta lojista.
> Foi o que aconteceu em Ago/2026 — e o watchdog de dado não viu porque
> `ACTIVE_PLATFORMS["dealers"]=False` faz `_expected_platforms()` pular dealers,
> embora o script da VM os passe explicitamente na linha de comando (o flag do
> config é apenas o *default* do CLI). Quem cobra dealers agora é o supervisor
> de execução, pelo contrato do job `vm_coleta_manha`.

### 🚫 O que NÃO deve rodar em cada lugar

| Nunca rode… | …aqui | Porque |
|---|---|---|
| ML, Magalu, Shopee, Casas Bahia | Actions / VM | Akamai barra o IP antes do fingerprint → 0 linha com run verde |
| Dealers | Actions | Fora do foco de marketplace desde Mai/2026 e sem dono lá |
| Mais Vendidos | Actions / VM | Amazon e ML somem de IP de datacenter: 2–3 das 6 listas não voltam |
| PriceTrack | PC coletor | É API — ocupar o notebook com isso é desperdício e cria um segundo dono |

---

## 2. Quem depende de quem (e o horário que isso impõe)

```
03:20 ┬─ PriceTrack D-1 (tentativa 1, --force)
04:20 ├─ PriceTrack D-1 (tentativa 2, no-op se já entrou)
05:20 ├─ PriceTrack D-1 (tentativa 3 + auto-heal de 14 dias)
06:35 ├─ 🚦 PORTÃO DO BRIEFING — verifica frescor e CURA o que falta
07:00 └─ ▶ BRIEFING (consumidor)
09:00 ── PC: coleta manhã          09:30 ── PC: Mais Vendidos
10:00 ── Actions + VM: turno Abertura
12:35 ── Supervisor fecha a janela da manhã
20:00 ── PC: coleta noite
20:30 ── Watchdog de dado (daily_status_check)
21:00 ── Actions + VM: turno Fechamento
22:35 ── Supervisor fecha a janela da noite
```

A regra por trás dos horários: **todo produtor termina com folga antes do seu
consumidor**, e todo consumidor **verifica** em vez de confiar. O briefing das
07:00 não pergunta "a tabela tem dados?" — pergunta "a tabela tem dados **de
ontem**?", via `scripts/briefing_gate.py`.

---

## 3. Os três mecanismos de vigilância (e a pergunta de cada um)

| Mecanismo | Pergunta | Quando | O que dispara |
|---|---|---|---|
| `daily_status_check.py` (watchdog.yml) | **O dado chegou?** | 20:30 | Telegram PASS/FAIL por plataforma |
| `pipeline_watch.py` (pipeline_guard.yml) | **Quem prometeu rodar e não rodou?** | 06:35, 12:35, 22:35 | Telegram acionável + issue de degradação crônica |
| `briefing_gate.py` | **O dado é de ontem mesmo?** | 06:35 (e sob demanda) | Bloqueia/carimba a publicação |

O segundo existe porque os outros dois não conseguem ver ausência de execução:
um job que **não roda** não falha, não gera log e não deixa buraco distinguível
de "rodou e o site bloqueou".

### Como a ausência vira evento

Cada executor grava início e fim em `pipeline_heartbeat` (migração
`docs/migrations/015_pipeline_heartbeat.sql`). O lançador só precisa exportar
`RAC_JOB_ID`; `main.py` e `collect_bestsellers.py` batem o ponto sozinhos.
**Sem batida até o `deadline_min` do contrato = NÃO EXECUTOU** — um *dead man's
switch*, em que o silêncio é que dispara o alarme.

Estados possíveis: `OK`, `EM_JANELA`, `ATRASADO`, `NAO_EXECUTOU`, `TRAVADO`
(bateu início e nunca fechou), `FALHOU`, `SEM_DADO` (rodou verde e trouxe
zero), `EXECUTOR_OFFLINE` (a máquina inteira não rodou nada).

Três detalhes que parecem miudeza e não são:

* **A batida do job vence a contagem do destino.** Amazon e Leroy são coletadas
  em três máquinas: se o job do Actions declarou zero linha, o dado que a VM
  gravou no mesmo recorte não pode deixá-lo verde — seria um run falho se
  escondendo atrás do vizinho.
* **Livro-razão ilegível ≠ livro vazio.** Falha de leitura devolve exit 3
  ("não consegui olhar"), nunca "ninguém rodou". Só a tabela ainda não criada
  (migração 015 pendente) é tratada como adoção em curso.
* **O turno Fechamento vence às 02:00.** A varredura das 22:35 é cedo demais e
  a das 06:35 já olha o dia novo, então o portão da manhã roda com
  `--tambem-ontem` e fecha essa janela — bem a tempo do briefing, que consome
  justamente o dado de ontem.

---

## 4. Contenção e autocorreção

| Situação | O que acontece sozinho | O que exige humano |
|---|---|---|
| PriceTrack D-1 ausente às 06:35 | Reimporta na hora (`briefing_gate --curar`) e revalida antes das 07:00 | — |
| Plataforma volta a coletar | A issue crônica é **fechada** automaticamente | — |
| Import falhou nos 3 degraus | Escada + guardião já tentaram 4 vezes; alerta nomeia o erro | Chave da API / cota |
| Plataforma zerada 1–2 dias | Entra no relatório como aviso | — |
| Plataforma zerada **≥ 3 dias** | Abre (ou atualiza) **issue no GitHub** com a contagem | Consertar o scraper |
| PC coletor desligado | Um alerta de `EXECUTOR_OFFLINE`, não 4 de plataforma caída | Ligar o notebook |
| VM Oracle parada | Alerta com o comando de diagnóstico por SSH | Religar/reprovisionar |
| Job do Actions não disparou | Re-dispatch automático **se** houver `RAC_GH_PAT` | Sem PAT, disparo manual |

**O que deliberadamente NÃO é curado sozinho:** coleta de marketplace. Ela
precisa da máquina certa, leva mais de uma hora e, disparada no executor
errado, devolve zero linha com cara de sucesso — exatamente o modo de falha que
tudo isto existe para eliminar. Para ela, a contenção correta é o alerta com o
comando exato e a issue que transforma repetição em trabalho rastreado.

> Sobre o `RAC_GH_PAT`: o `GITHUB_TOKEN` do próprio run **não** consegue
> disparar outro workflow (proteção anti-recursão do GitHub). Por isso a cura
> por dispatch é opcional, e nunca o único caminho para nada.

---

## 5. Onde mexer quando algo muda

| Mudança | Arquivo |
|---|---|
| Nova plataforma / novo dono | `utils/pipeline_registry.py` (`JOBS`) |
| Novo horário de um job | `JOBS` **e** o agendador correspondente (workflow, crontab, Task Scheduler) |
| Nova fonte que o briefing consome | `scripts/briefing_gate.py` (`FONTES`) |
| Limiar de degradação crônica | `scripts/pipeline_watch.py` (`DIAS_PARA_CRONICO`) |
| Retenção do livro-razão | `cleanup_pipeline_heartbeat` (chamada 1×/dia pelo guardião) |

`tests/test_pipeline_registry.py` roda `validar_registro()` no CI: plataforma
ativa sem dono, dois donos para o mesmo (plataforma, turno) ou job sem
remediação reprovam o PR. É de propósito — foi a falta dessas invariantes que
deixou os dealers órfãos por meses.

---

## 6. Checklist de setup em máquina nova

```bash
# 1. Banco: aplicar a migração do livro-razão (uma vez, SQL Editor do Supabase)
#    docs/migrations/015_pipeline_heartbeat.sql

# 2. Ver o contrato atual e conferir se bate com o que está agendado
python -c "from utils.pipeline_registry import JOBS; [print(f'{j.id:22} {j.executor:15} {j.horario_brt} {j.gatilho}') for j in JOBS]"

# 3. Provar que o supervisor enxerga (sem enviar alerta)
python scripts/pipeline_watch.py --no-notify

# 4. Provar o portão do briefing
python scripts/briefing_gate.py

# 5. Secrets necessários no repositório
#    SUPABASE_URL, SUPABASE_KEY, PRICETRACK_API_KEY,
#    TELEGRAM_BOT_TOKEN, N8N_TELEGRAM_CHAT_ID
#    opcional: RAC_GH_PAT (actions:write) para re-dispatch automático
```
