# Coleta Magalu via Chrome CDP (Windows + Task Scheduler)

**Uso:** Coleta automática diária do Magalu usando o **Chrome real** do PC Windows com IP residencial — bypassa o Akamai sensor.js que bloqueia Playwright e GitHub Actions.

**Status:** ✅ Recomendado como canal primário do Magalu (Mai/2026).

---

## Por que CDP?

| Tentativa | Resultado |
|-----------|-----------|
| Playwright headless | ❌ sensor.js detecta automação → `_abck` em challenge |
| Playwright + xvfb (Oracle VM) | ⚠️ Funciona se perfil acumular histórico |
| GitHub Actions + xvfb + perfil cacheado | ❌ IP datacenter da Azure flagado pelo Akamai |
| **CDP no Chrome real do usuário** | ✅ Chrome genuíno + IP residencial = aceito |

O Chrome aberto pelo usuário tem fingerprint legítimo (sem `navigator.webdriver`, IP residencial, perfil com histórico real). Conectamos via DevTools Protocol e usamos esse Chrome para navegar.

> ⚠️ **Atenção — o Playwright stock vaza CDP.** Ao conectar via `connect_over_cdp()`, o Playwright liga o domínio `Runtime` do DevTools Protocol. O sensor.js do Akamai detecta isso (getter que só dispara quando o `Runtime` do CDP está serializando pro console) e mantém o `_abck` em `challenge` — mesmo num Chrome 100% real. Por isso o `scrapers/magalu.py` usa o fork **`rebrowser-playwright`**, que obtém o execution context sem o `Runtime.enable`. Sem ele, o modo CDP é flagado e toda `/busca/` retorna 403.

---

## Setup inicial (uma vez)

### 1. Copiar perfil Chrome para uso CDP

Esse passo copia seu perfil "Eder" para uma pasta separada (`C:\chrome-rac-cdp`) — preserva cookies, extensões, histórico e fingerprint. Depois, o CDP Chrome roda em paralelo ao Chrome normal sem conflito.

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
scripts\setup_cdp_profile.bat
```

**Importante:** o script pede pra fechar todos os Chromes antes de começar (Chrome trava arquivos do perfil enquanto aberto). Copia leva 1-5 min, ~500MB-2GB no disco.

> Se seu perfil "Eder" não estiver no slot `Default`, edite `setup_cdp_profile.bat` e ajuste a variável `SOURCE_PROFILE` (ex: `Profile 1`, `Profile 2`).

### 2. Registrar tarefas no Windows Task Scheduler

Abra **PowerShell como Administrador**:

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1
```

Cria 3 tarefas:
- `RAC_Chrome_CDP_Startup` — abre Chrome CDP no logon do Windows
- `RAC_Magalu_Manha` — coleta às 10:00 (Abertura, 2 páginas)
- `RAC_Magalu_Noite` — coleta às 21:00 (Fechamento, 1 página)

### 3. Testar manualmente

```powershell
scripts\start_chrome_cdp.bat
```

O Chrome CDP abre em paralelo ao seu Chrome normal (não conflita). Navegue no Magalu por uns minutos para confirmar que carrega normalmente.

### 4. Logout/Login (opcional)

A tarefa `RAC_Chrome_CDP_Startup` só dispara no próximo logon. Faça logout/login do Windows pra ela rodar (ou continue rodando `start_chrome_cdp.bat` manualmente após cada boot).

---

## Uso diário

### Coleta automática (Task Scheduler)

Nada a fazer — as tarefas rodam sozinhas às 10:00 e 21:00.

### Coleta manual

```powershell
# Garante Chrome aberto
.\scripts\start_chrome_cdp.bat

# Roda coleta (faz upload pro Supabase automaticamente)
.\scripts\collect_magalu_cdp.bat       # 2 páginas (default)
.\scripts\collect_magalu_cdp.bat 1     # 1 página
.\scripts\collect_magalu_cdp.bat 2 alta media   # filtro priority
```

---

## Como funciona

```
┌─────────────────────────────────────────┐
│ Windows Task Scheduler                  │
│   10:00 → RAC_Magalu_Manha              │
│   21:00 → RAC_Magalu_Noite              │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ collect_magalu_cdp.bat                  │
│   1. Verifica CDP em :9222              │
│   2. set MAGALU_CDP_URL=...             │
│   3. python main.py --platforms magalu  │
│   4. upload_csv.py output\*.csv         │
└────────────────┬────────────────────────┘
                 │
                 │ MAGALU_CDP_URL=http://localhost:9222
                 ▼
┌─────────────────────────────────────────┐
│ scrapers/magalu.py                      │
│   connect_over_cdp(MAGALU_CDP_URL)      │
│   → reusa context já existente          │
│   → navega /busca/ no Chrome real       │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Chrome do usuário (sempre aberto)       │
│   Port: 9222 (DevTools Protocol)        │
│   Profile: C:\chrome-rac-cdp            │
│   IP: residencial                       │
│   Fingerprint: 100% genuíno             │
└─────────────────────────────────────────┘
```

---

## Variáveis de ambiente

| Variável | Função |
|----------|--------|
| `MAGALU_CDP_URL` | URL do DevTools Protocol (ex: `http://localhost:9222`). **Se setada, ativa modo CDP.** |
| `MAGALU_HEADLESS` | Ignorado quando `MAGALU_CDP_URL` está setado (CDP usa o Chrome existente). |

---

## Troubleshooting

### "Chrome CDP não está rodando em http://localhost:9222"
- Verifique se o Chrome está aberto: `Get-Process chrome -ErrorAction SilentlyContinue`
- Verifique a porta: `curl http://localhost:9222/json/version`
- Se nada, rode `scripts\start_chrome_cdp.bat`

### "Perfil CDP não encontrado em C:\chrome-rac-cdp\Default"
- Você ainda não rodou o setup inicial do perfil. Execute:
  ```powershell
  scripts\setup_cdp_profile.bat
  ```

### Chrome abre, mas com perfil "vazio" (sem extensões, sem histórico)
- Significa que está usando a versão antiga do script (perfil isolado em vez de cópia do seu).
- Atualize o repo (`git pull origin main`) e rode `scripts\setup_cdp_profile.bat` para copiar seu perfil real.

### Chrome abre o site como HTTP em vez de HTTPS
- Atualize o repo: a URL nas versões antigas não estava entre aspas, o `://` confundia o `cmd.exe`.
- Confirme que `scripts\start_chrome_cdp.bat` tem `"https://www.magazineluiza.com.br/"` (com aspas).

### Refresh do perfil CDP (após meses de uso)
Se a cópia ficar muito desatualizada e quiser reiniciar com o perfil atual:
```powershell
rmdir /s /q C:\chrome-rac-cdp
scripts\setup_cdp_profile.bat
```

### Coleta dá 403 / `_abck` fica em `challenge` (mesmo com Chrome real)
Sintoma no log: `_abck não validou em 25s`, `Busca de calibração suspeita`,
`_abck status=challenge`, todas as buscas `HTTP 403, len=1075`.

Isso **não** é problema de perfil — revalidar/recopiar o perfil não resolve.
É o Playwright stock vazando o `Runtime.enable` do CDP. Confira:

```powershell
# 1. O fork anti-detecção está instalado?
.venv\Scripts\activate
python -c "import rebrowser_playwright; print('OK', rebrowser_playwright.__file__)"

# 2. Se der ModuleNotFoundError, instale:
pip install rebrowser-playwright
```

No início da coleta o log deve mostrar
`Playwright: rebrowser-playwright (runtime fix=addBinding)`. Se mostrar
`Playwright stock — modo CDP detectável`, o fork não está sendo usado.

Teste de confirmação: com o Chrome do `:9222` aberto, abra manualmente
`https://www.magazineluiza.com.br/busca/ar+condicionado/` numa aba **sem
rodar o coletor**. Se carregar normal, o bloqueio é da automação (não do
perfil/IP).

### Coleta retorna 0 produtos mesmo com Chrome aberto
- Aqueça o perfil mais (5-10 min de navegação real)
- Faça login no Magalu
- Verifique se o IP atual não está em alguma blocklist do Akamai (raro, mas possível)
- Tente abrir manualmente uma URL de busca no Chrome — se aparecer captcha, resolva uma vez

### Task Scheduler "Status: Disabled" ou não executa
```powershell
# Habilita
Enable-ScheduledTask -TaskName "RAC_Magalu_Manha"

# Testa execução manual
Start-ScheduledTask -TaskName "RAC_Magalu_Manha"

# Ver últimas execuções
Get-ScheduledTaskInfo -TaskName "RAC_Magalu_Manha"
```

### Logs

```powershell
# Log do scheduler (saída do .bat)
Get-Content C:\Users\Eder Rabelo\Downloads\rac-position-tracker\logs\scheduler.log -Tail 100

# Log do bot Python
Get-ChildItem C:\Users\Eder Rabelo\Downloads\rac-position-tracker\logs\bot_*.log |
    Sort-Object LastWriteTime -Desc | Select-Object -First 1 |
    Get-Content -Tail 100
```

---

## Remover tudo

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1 -Remove
```
