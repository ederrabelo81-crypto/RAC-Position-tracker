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

### Automatizar (Task Scheduler)

```powershell
# Agenda 10:05 (Abertura) e 21:05 (Fechamento). Remove as tarefas antigas de CDP.
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
```

O notebook precisa estar **ligado e com você logado no Windows** nos horários (o
Chrome abre na sua sessão de UI e usa o seu IP residencial).

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
