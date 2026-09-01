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

Desde **Set/2026** o PC coletor é o **dono único** da coleta de oferta/posição:
roda **todas as plataformas** em **três turnos** por dia (mais cobertura que os
dois turnos antigos). Cada turno faz a mesma varredura — 2 páginas, todas as
keywords — e difere só pelo turno gravado.

| Tarefa | Horário | Turno | Coleta |
|---|---|---|---|
| `RAC_Local_Manha` | 08:00 + catch-up no logon | Abertura | **ML, Amazon, Magalu, Casas Bahia, Google Shopping, Leroy, Shopee, dealers** |
| `RAC_Local_Tarde` | 14:00 + catch-up no logon | Tarde | as mesmas |
| `RAC_Local_Noite` | 20:00 + catch-up no logon | Fechamento | as mesmas |

> As janelas — manhã 8–11h, tarde 12–17h, noite 18–23h — são guardadas pelo
> próprio `local_scheduled_collect.bat`: fora delas a tarefa **pula** em vez de
> gravar com o turno errado. `get_turno()` marca Abertura até 11h, Tarde de
> 12h a 17h e Fechamento das 18h em diante — exatamente os cortes entre os
> horários agendados, para que um catch-up no logon caia no turno certo.
>
> Mais Vendidos (`RAC_Bestsellers`) foi **descontinuado** em Set/2026; o setup
> remove a tarefa antiga se ela ainda existir.

### ☁️ GitHub Actions (IP de datacenter, sem sessão)

**Agendar em:** `.github/workflows/*.yml`, bloco `on: schedule`
**Diagnóstico:** aba Actions → workflow → último run

| Workflow | Horário BRT | Faz | Por que aqui |
|---|---|---|---|
| `pricetrack_daily.yml` | **03:20, 04:20, 05:20** (escada) | Importa preços do PriceTrack (D-1) → `pricetrack_daily` | É chamada de API: não precisa de browser, IP nem sessão |
| `collect.yml` | **só manual** (cron desligado em Set/2026) | Backup manual: Amazon, Leroy, Google Shopping | O PC virou coletor único; o cron sairia com dois donos do mesmo dado |
| `watchdog.yml` | 20:30 | Watchdog de **dado** (`daily_status_check.py`) — agora nos 3 turnos | — |
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

### 🛰️ VM Oracle (Brazil East) — desativada como coletora (Set/2026)

A VM **não coleta mais**: os dealers e a redundância de marketplace passaram para
o PC coletor, com IP residencial (melhor para o que os dealers precisam). O
executor segue documentado em `EXECUTORES` caso volte a ser usado, mas nenhum
job aponta para ele. Se for reativar a VM como coletora, **primeiro** devolva a
posse das plataformas a um job da VM em `utils/pipeline_registry.py` — senão
haverá dois donos do mesmo `(plataforma, turno)`.

> Ao desligar a VM, **remova o crontab dela** (`ssh ubuntu@<vm-ip> 'crontab -r'`
> ou edite com `crontab -e`). Deixá-lo ligado faz a VM coletar em paralelo com o
> PC e gravar dado redundante — sem quebrar nada, mas desperdiçando a máquina.

### 🚫 O que NÃO deve rodar em cada lugar

| Nunca rode… | …aqui | Porque |
|---|---|---|
| ML, Magalu, Shopee, Casas Bahia | Actions | Akamai barra o IP antes do fingerprint → 0 linha com run verde |
| Coleta de marketplace/dealers | Actions (automático) | O PC é o dono único; auto-disparo criaria dois donos do mesmo dado |
| PriceTrack | PC coletor | É API — ocupar o notebook com isso é desperdício e cria um segundo dono |

---

## 2. Quem depende de quem (e o horário que isso impõe)

```
03:20 ┬─ PriceTrack D-1 (tentativa 1, --force)
04:20 ├─ PriceTrack D-1 (tentativa 2, no-op se já entrou)
05:20 ├─ PriceTrack D-1 (tentativa 3 + auto-heal de 14 dias)
06:35 ├─ 🚦 PORTÃO DO BRIEFING — verifica frescor e CURA o que falta
07:00 └─ ▶ BRIEFING (consumidor)
08:00 ── PC: coleta turno Abertura (todas as plataformas)
12:35 ── Supervisor fecha a janela da manhã
14:00 ── PC: coleta turno Tarde (todas as plataformas)
20:00 ── PC: coleta turno Fechamento (todas as plataformas)
20:30 ── Watchdog de dado (daily_status_check) — Abertura + Tarde
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

* **A batida do job vence a contagem do destino.** Se o job declarou zero linha,
  a contagem do destino (agregada por dia/turno/plataforma) não pode deixá-lo
  verde — seria um run falho se escondendo atrás de dado de outra origem. Com o
  coletor único isso quase não acontece, mas a regra de classificação continua
  valendo (é o que impede um PARTIAL de virar OK).
* **Livro-razão ilegível ≠ livro vazio.** Falha de leitura devolve exit 3
  ("não consegui olhar"), nunca "ninguém rodou". Só a tabela ainda não criada
  (migração 015 pendente) é tratada como adoção em curso.
* **O turno Fechamento (20:00) vence às 23:00, no mesmo dia.** A varredura das
  22:35 pega o Fechamento ainda atrasado (tolerância até 22:00) e a das 06:35 já
  olha o dia novo, então o portão da manhã roda com `--tambem-ontem` e fecha
  essa janela — bem a tempo do briefing, que consome justamente o dado de ontem.

---

## 4. Contenção e autocorreção

| Situação | O que acontece sozinho | O que exige humano |
|---|---|---|
| PriceTrack D-1 ausente às 06:35 | Reimporta na hora (`briefing_gate --curar`) e revalida antes das 07:00 | — |
| Plataforma volta a coletar | A issue crônica é **fechada** automaticamente | — |
| Import falhou nos 3 degraus | Escada + guardião já tentaram 4 vezes; alerta nomeia o erro | Chave da API / cota |
| Plataforma zerada 1–2 dias | Entra no relatório como aviso | — |
| Plataforma zerada **≥ 3 dias** | Abre (ou atualiza) **issue no GitHub** com a contagem | Consertar o scraper |
| PC coletor desligado | Um alerta de `EXECUTOR_OFFLINE`, não N de plataforma caída | Ligar o notebook |
| Import do PriceTrack não disparou | Re-dispatch automático **se** houver `RAC_GH_PAT` | Sem PAT, disparo manual |

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
