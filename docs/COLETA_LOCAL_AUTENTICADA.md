# Coleta local autenticada — Shopee, Magalu e Casas Bahia (notebook Windows)

> **Objetivo:** rodar as coletas dos 3 marketplaces protegidos por antibot
> direto do seu notebook, com o seu Chrome real **logado**, sem a extensão do
> Chrome e de forma automatizável (Task Scheduler).
>
> **Status:** ✅ Caminho recomendado no notebook (Jul/2026). Substitui a
> abordagem antiga de CDP com perfil copiado.

---

## Por que as tentativas anteriores falhavam

A abordagem antiga (`setup_cdp_profile` + `start_chrome_cdp` + conexão via CDP)
falhava por **dois motivos estruturais** — não era um bug pontual:

1. **O Chrome 136+ ignora `--remote-debugging-port` quando o `--user-data-dir`
   aponta para o seu perfil padrão.** Foi uma correção de segurança do Google
   (contra roubo de cookies). Ou seja: **não dá para "ligar o CDP no seu Chrome
   logado"** — o Chrome silenciosamente descarta a porta de debug e o
   `connect_over_cdp` não enxerga a sua sessão. Por isso o setup antigo
   **copiava** o perfil para `C:\chrome-rac-cdp` (um diretório não-padrão).

2. **Copiar o perfil dispara a proteção "perfil realocado" do Chrome, que
   invalida os logins.** O próprio `setup_cdp_profile.ps1` avisava:
   *"vai abrir DESLOGADO… OBRIGATÓRIO logar na Shopee de novo"*. Resultado: a
   Shopee respondia **403** (sessão anônima) e a coleta não acontecia. Somado a
   isso, o replay da API v4 da Shopee via `curl_cffi` não tinha o header
   anti-fraude `af-ac-enc-dat` (gerado pela JS da Shopee) — bloqueio garantido.

## Como a solução nova resolve

Um **único perfil Chrome dedicado e estável** (`data/chrome_profile/`), aberto
pelo próprio Python via `launch_persistent_context` com o **Chrome real**:

- **Sem CDP, sem porta de debug, sem cópia de perfil.** Você loga na Shopee
  **uma vez** e o login persiste entre execuções (o diretório nunca é movido,
  então o Chrome não o trata como realocado).
- **Shopee** passa a coletar **dentro** do Chrome logado, interceptando a
  chamada **nativa** da API v4 — que já carrega o `af-ac-enc-dat`. É o que
  realmente destrava a Shopee.
- **Casas Bahia** e **Magalu** reusam o **mesmo** Chrome (uma aba cada), com o
  fingerprint real + IP residencial do notebook, que o Akamai aceita. Um único
  browser é aberto por execução e fechado ao final.

---

## Setup (uma vez)

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

# 1. Dependências (o fork rebrowser oculta o Runtime.enable do sensor.js)
pip install -r requirements.txt
python -m rebrowser_playwright install chromium

# 2. Faça login na Shopee no perfil dedicado (abre o Chrome de verdade).
#    Loga na Shopee (obrigatório); opcionalmente navega Magalu/Casas Bahia.
python scripts\setup_local_profile.py
```

> **Importante:** feche o Chrome do setup antes de coletar — o perfil só pode
> ser aberto por um Chrome por vez. Se aparecer erro de *SingletonLock* /
> *profile already in use*, feche todas as janelas desse Chrome e tente de novo.

---

## Coleta

```powershell
# Manual (2 páginas):
scripts\collect_local_authenticated.bat

# 1 página / com prioridade:
scripts\collect_local_authenticated.bat 1
scripts\collect_local_authenticated.bat 2 alta media

# Equivalente cru (define a env e chama o main):
set RAC_LOCAL_CHROME=1
python main.py --platforms magalu shopee casasbahia --pages 1
```

`RAC_LOCAL_CHROME=1` é o que liga o modo Chrome local para os 3 scrapers. Sem
essa env, o comportamento é o antigo (curl_cffi/CDP) — nada muda na VM/GitHub.

### Automatizar (Task Scheduler)

```powershell
# Agenda 10:05 (Abertura) e 21:05 (Fechamento). Remove as tarefas antigas de CDP.
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
```

O notebook precisa estar **ligado e com você logado no Windows** nos horários
(o Chrome abre na sua sessão de UI e usa o seu IP residencial).

---

## Variáveis de ambiente

| Env | Default | Efeito |
|-----|---------|--------|
| `RAC_LOCAL_CHROME` | (desligado) | `1` liga o modo Chrome local logado (Shopee/Magalu/CB). |
| `RAC_CHROME_PROFILE_DIR` | `data/chrome_profile` | Sobrescreve o diretório do perfil dedicado. |
| `RAC_LOCAL_HEADLESS` | (desligado) | `1` roda sem janela (só com `xvfb`/display virtual — o sensor.js detecta Chromium headless "cru"). |

---

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---------|----------------|------|
| Shopee: circuit breaker / 0 produtos | Perfil sem login na Shopee | `python scripts\setup_local_profile.py` e faça login; confira com `--headless-check`. |
| "profile already in use" / SingletonLock | Chrome do setup (ou outro) aberto no mesmo perfil | Feche todas as janelas desse Chrome e rode de novo. |
| "Playwright não instalado" | Falta o fork rebrowser | `pip install rebrowser-playwright && python -m rebrowser_playwright install chromium`. |
| Magalu/CB bloqueados mesmo local | Perfil "NOVO" ainda frio ou IP marcado | Navegue 1-2 min nos sites no `setup_local_profile.py` para aquecer; garanta que está no IP residencial (não em VPN corporativa/datacenter). |
| Quero conferir se a Shopee está logada | — | `python scripts\setup_local_profile.py --headless-check` (lê os cookies sem abrir janela). |

---

## Relação com a VM / GitHub Actions

Esse modo é **opt-in** por `RAC_LOCAL_CHROME`. Na Oracle VM / GitHub Actions,
sem essa env, os scrapers seguem no caminho antigo (curl_cffi + sessão / CDP) —
nenhuma regressão. O caminho estrutural para tirar o notebook da rota continua
sendo **proxy residencial BR na VM** (ver `docs/DIAGNOSTICO_COLETA_JUN2026.md`).

*Criado em Jul/2026 — correção da coleta autenticada local.*
