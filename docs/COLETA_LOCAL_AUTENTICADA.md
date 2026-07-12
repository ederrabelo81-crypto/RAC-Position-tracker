# Coleta local autenticada — Shopee, Magalu e Casas Bahia (notebook Windows)

> **Objetivo:** rodar as coletas dos 3 marketplaces protegidos por antibot
> direto do seu notebook, com o seu Chrome real, sem a extensão do Chrome e de
> forma automatizável (Task Scheduler).
>
> **Status:** ✅ Caminho recomendado no notebook (Jul/2026). Chrome COMUM +
> ataque via CDP.

---

## O que precisa (e o que NÃO precisa) de login

| Plataforma | Login? | Observação |
|------------|--------|------------|
| **Shopee** | ✅ **Sim** | A API v4 responde 403 sem conta. Pode ser via **Google** (funciona neste modo) ou e-mail/telefone. |
| **Casas Bahia** | ❌ Não | Só precisa de IP residencial + Chrome real. |
| **Magalu** | ❌ Não | Idem. |

> Só a Shopee exige login. Casas Bahia e Magalu **não** precisam de conta.

---

## Por que as tentativas anteriores falhavam

1. **CDP no perfil padrão** — o Chrome 136+ ignora `--remote-debugging-port`
   quando o `--user-data-dir` é o seu perfil padrão. Não dá para "ligar o CDP
   no meu Chrome logado".
2. **Copiar o perfil desloga** — a proteção "perfil realocado" do Chrome
   invalida os logins (Google e Shopee → 403). O setup antigo copiava o perfil.
3. **Browser via Playwright é detectado** — abrir o Chrome com
   `launch_persistent_context` sobe o browser com flags de automação e
   `navigator.webdriver`, que o Akamai (Magalu/Casas Bahia) bloqueia na hora
   (403) e o **Google recusa no login** ("navegador pode não ser seguro").

## Como a solução resolve

Abrimos um **Chrome COMUM** (o mesmo do seu PC), como um browser de verdade —
**sem** flags de automação, **sem** `navigator.webdriver` — apontando para um
perfil **dedicado e estável** (`data/chrome_profile/`), com a porta de debug
ligada. Depois **atacamos via CDP** (`connect_over_cdp`) usando o fork
`rebrowser-playwright` (que oculta o `Runtime.enable` do sensor.js).

- **Perfil dedicado (não é cópia)** → o Chrome 136+ permite a porta de debug e o
  login **persiste** (o Chrome não o trata como realocado).
- **Chrome comum** → no login, **nenhum** cliente CDP está conectado, então a
  página vê um browser 100% humano — **o login pelo Google passa**. Na coleta, o
  CDP ataca esse mesmo Chrome real (fingerprint aceito pelo Akamai).
- **IP residencial** do notebook — a combinação que os antibots aceitam.
- Um único Chrome por execução, compartilhado pelos 3 scrapers (uma aba cada). O
  Chrome fica **aberto** entre execuções (perfil "quente").

---

## Setup (uma vez)

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

# 1. Dependências — o fork rebrowser é OBRIGATÓRIO para a coleta passar no Akamai
pip install -r requirements.txt
python -m rebrowser_playwright install chromium

# 2. Abre a Shopee num Chrome comum p/ você logar 1x (Google OU e-mail/telefone)
python scripts\setup_local_profile.py
```

O script abre a Shopee. Faça login (pode ser "Continuar com o Google" — funciona
aqui), volte ao terminal e pressione ENTER. Ele confirma se a sessão ficou
logada. **Deixe esse Chrome aberto** — a coleta reaproveita ele.

Conferir o login depois: `python scripts\setup_local_profile.py --check`

---

## Coleta

```powershell
# Manual (2 páginas). Abre/ataca o Chrome comum sozinha.
scripts\collect_local_authenticated.bat

# 1 página / com prioridade:
scripts\collect_local_authenticated.bat 1
scripts\collect_local_authenticated.bat 2 alta media

# Equivalente cru ao .bat (os 3 marketplaces) — ATENÇÃO à shell:
#   PowerShell:  $env:RAC_LOCAL_CHROME="1"; python main.py --platforms magalu shopee casasbahia --pages 1
#   cmd.exe   :  set RAC_LOCAL_CHROME=1 && python main.py --platforms magalu shopee casasbahia --pages 1

# Teste isolado de UMA plataforma (ex.: Casas Bahia, que não precisa de login):
#   PowerShell:  $env:RAC_LOCAL_CHROME="1"; python main.py --platforms casasbahia --pages 1
```

> ⚠️ **PowerShell não usa `set`.** No PowerShell, `set RAC_LOCAL_CHROME=1` **não**
> exporta a variável de ambiente (é sintaxe do `cmd`) — a coleta cai no caminho
> antigo e o Akamai bloqueia. Use **`$env:RAC_LOCAL_CHROME="1"`** ou, mais
> simples, o `.bat` (que já seta certo). No começo do log a coleta imprime
> `[Chrome local] RAC_LOCAL_CHROME=ON/OFF` — confira que está **ON**.

`RAC_LOCAL_CHROME=1` liga o modo Chrome comum + CDP para os 3 scrapers. Sem essa
env, o comportamento é o antigo (curl_cffi/CDP externo) — nada muda na
VM/GitHub.

> 🔇 **"cannot get world … session closed" inundando o console?** É ruído do
> driver do `rebrowser-playwright` ([issue #57](https://github.com/rebrowser/rebrowser-patches/issues/57))
> tentando instrumentar os iframes de anúncio da página — **inofensivo**, a
> coleta funciona (repare que cada keyword retorna N produtos logo depois). Os
> logs úteis saem em **stdout** e o ruído em **stderr**, então dá pra silenciar:
> - **PowerShell:** `python main.py --platforms casasbahia --pages 1 2>$null`
> - **cmd.exe:** `python main.py --platforms casasbahia --pages 1 2>nul`
> - O `.bat` já joga esse ruído em `logs\driver_stderr.log` (console limpo).

### Automatizar (Task Scheduler)

```powershell
# Agenda 09:00 (Abertura) e 20:00 (Fechamento) + catch-up no logon.
# Remove as tarefas antigas de CDP. RE-RODE 1x depois de atualizar o repo.
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
```

Como funciona (endurecido em Jul/2026, após dois incidentes de "a tarefa não
rodou"):

- A **Action da tarefa é o próprio `run_local_scheduled.bat`** — sem `cmd /c` e
  sem `>> log` na Action. O formato antigo (`cmd.exe /c "..." args >> "..."`)
  quebrava com o **espaço no caminho do projeto** (`C:\Users\Eder Rabelo\...`):
  o cmd.exe descarta a primeira e a última aspas do `/c`, o comando vira
  `C:\Users\Eder ...` e a tarefa morre na hora, **sem escrever log** — era a
  causa raiz de Magalu/Shopee/Casas Bahia "não rodarem" enquanto a tarefa do ML
  (registrada com o `.bat` direto) seguia normal. O log agora é interno:
  `logs\scheduler.log`.
- `run_local_scheduled.bat` (estágio A, **estável** — não mexer sem
  necessidade) faz **`git pull` (self-update)** e chama
  `local_scheduled_collect.bat` (estágio B), que é lido **depois** do pull e
  portanto sempre executa na versão mais nova. Pular o pull num teste:
  `RAC_NO_SELFUPDATE=1`.
- O estágio B aplica **janela de turno** (manhã 9–12h / noite 20–23h) e
  **marcador diário** (`logs\coleta_<slot>_<data>.done`). O gatilho de **logon**
  faz o catch-up: se o notebook estava desligado/deslogado às 9h/20h, a coleta
  roda no próximo logon **dentro da janela** — sem duplicar (marcador) e sem
  gravar turno errado (`get_turno()` marca Abertura até 12h). Se a coleta
  falhar, o marcador não é gravado e o próximo logon na janela tenta de novo.
- Coleta agendada com exit ≠ 0 dispara **alerta no Telegram**
  (`notify_scheduler_failure` em `utils/n8n_notify.py`).

> ⚠️ **A Action fica congelada no Task Scheduler.** Depois de atualizar o repo
> com este fix, é obrigatório **re-rodar o `setup_local_scheduler.ps1` uma
> vez** — sem isso a tarefa continua registrada no formato antigo (quebrado).
> Daí em diante, mudanças de comportamento chegam via `git pull` (estágio B),
> sem precisar re-registrar nada.

**A tarefa não rodou? Rode o diagnóstico** (não precisa de Admin) — decodifica o
resultado da última execução, detecta Action no formato antigo, código
atrasado, venv/rebrowser/perfil ausentes e mostra o fim do `scheduler.log`:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
```

O notebook precisa estar **ligado e com você logado no Windows** nos horários —
ou logar depois, dentro da janela (o catch-up cobre). As tarefas usam
`-WakeToRun` (acorda o notebook em suspensão) e retentam em caso de falha.

---

## Variáveis de ambiente

| Env | Default | Efeito |
|-----|---------|--------|
| `RAC_LOCAL_CHROME` | (desligado) | `1` liga o modo Chrome comum + CDP (Shopee/Magalu/CB). |
| `RAC_CHROME_PROFILE_DIR` | `data/chrome_profile` | Diretório do perfil dedicado. |
| `RAC_CDP_PORT` | `9222` | Porta do DevTools do Chrome comum. |
| `RAC_CHROME_EXE` | (auto) | Caminho do `chrome.exe`/`msedge.exe` se a busca automática falhar. |
| `RAC_LOCAL_CHROME_KEEP` | `1` | `0` encerra o Chrome que a coleta abriu, ao fim (padrão: deixa aberto). |

---

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---------|----------------|------|
| Magalu/CB dão **403 na hora** | `rebrowser-playwright` não instalado (stock vaza CDP) | `pip install rebrowser-playwright && python -m rebrowser_playwright install chromium` |
| "Debugger pausado em outra guia" | Chrome sendo detectado (idem acima) | Instale o rebrowser; confira `--check` |
| Google recusa login | Você tentou logar **antes** de o Chrome comum abrir, ou em outro browser | Logue **na janela que o `setup_local_profile.py` abre** (é um Chrome comum, o Google aceita) |
| Shopee 403 / circuit breaker | Perfil sem login na Shopee | `python scripts\setup_local_profile.py` e faça login; confira com `--check` |
| "Chrome não encontrado" | Chrome fora do caminho padrão | Defina `RAC_CHROME_EXE` com o caminho do `chrome.exe` |
| "Chrome não expôs a porta de debug" | Já havia um Chrome nesse perfil sem a porta, ou porta ocupada | Feche Chromes desse perfil; ou mude `RAC_CDP_PORT` |
| **Tarefa agendada não rodou** / `scheduler.log` sem linhas novas | Action antiga (`cmd /c` + aspas + espaço no caminho) morre sem log; ou tarefa nunca re-registrada | Rode `scripts\check_local_scheduler.ps1`; correção padrão: `git pull` + re-rodar `setup_local_scheduler.ps1` |
| Log mostra "fora da janela … pulando" | Tarefa disparou atrasada (fora de 9–12h / 20–23h) | Comportamento correto — protege o turno do registro; o próximo slot/logon cobre |
| Log mostra "ja coletado hoje" | Gatilho de logon disparou após coleta OK | Comportamento correto (marcador diário evita duplicar) |
| Quero conferir o login | — | `python scripts\setup_local_profile.py --check` |

> **Importante sobre o Google:** o Google só recusa login em browsers
> **automatizados**. O `setup_local_profile.py` abre um Chrome **comum** (sem
> automação), então "Continuar com o Google" funciona normalmente. Não tente
> logar via um Chrome aberto pelo Playwright.

---

## Relação com a VM / GitHub Actions

Esse modo é **opt-in** por `RAC_LOCAL_CHROME`. Na Oracle VM / GitHub Actions,
sem essa env, os scrapers seguem no caminho antigo (curl_cffi + sessão / CDP
externo) — nenhuma regressão. O caminho estrutural para tirar o notebook da rota
continua sendo **proxy residencial BR na VM** (ver
`docs/DIAGNOSTICO_COLETA_JUN2026.md`).

*Atualizado em Jul/2026 — Chrome comum + CDP (login Google no notebook).*
