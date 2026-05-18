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

O Chrome aberto pelo usuário tem fingerprint 100% legítimo (sem `navigator.webdriver`, sem padrões CDP de Playwright). Conectamos via DevTools Protocol e usamos esse Chrome para navegar.

---

## Setup inicial (uma vez)

### 1. Registrar tarefas no Windows Task Scheduler

Abra **PowerShell como Administrador**:

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1
```

Cria 3 tarefas:
- `RAC_Chrome_CDP_Startup` — abre Chrome com CDP no login do Windows
- `RAC_Magalu_Manha` — coleta às 10:00 (Abertura, 2 páginas)
- `RAC_Magalu_Noite` — coleta às 21:00 (Fechamento, 1 página)

### 2. Aquecer o perfil Chrome

Rode manualmente uma vez:

```powershell
.\scripts\start_chrome_cdp.bat
```

Quando o Chrome abrir:
- Navegue pelo site do Magalu por ~5 minutos
- Faça login (opcional, mas ajuda)
- Faça algumas buscas reais: "ar condicionado", "split 12000"
- Clique em alguns produtos

Isso popula cookies e histórico que o Akamai usa pra classificar o browser como "humano confiável".

### 3. Deixar o Chrome aberto

Não feche o Chrome. As tarefas agendadas dependem dele estar rodando.

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

### "Falha ao conectar CDP: ..."
- Outra instância do Chrome (sem `--remote-debugging-port`) pode estar bloqueando o profile dir
- Feche TODOS os Chromes e reabra com `start_chrome_cdp.bat`

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
