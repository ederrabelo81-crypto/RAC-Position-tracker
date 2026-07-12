# =============================================================================
# check_local_scheduler.ps1 - Diagnostico da coleta local agendada (read-only).
#
# Responde "por que a coleta agendada de Magalu/Shopee/Casas Bahia nao rodou?"
# sem mexer em nada: inspeciona as tarefas RAC_Local_* (formato da Action,
# gatilhos, resultado da ultima execucao decodificado), o estado do repo
# (codigo atrasado vs origin/main), os logs/marcadores do dia e o ambiente
# (venv, rebrowser, .env, perfil do Chrome).
#
# Uso (nao precisa de Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
#
# Saida: [OK] verde / [AVISO] amarelo / [ERRO] vermelho + resumo com proximos
# passos. Exit code 0 = nenhum erro; 1 = ha erros a corrigir.
# =============================================================================

$ErrorActionPreference = "SilentlyContinue"

$BaseDir = Split-Path -Parent $PSScriptRoot
$script:ErrCount  = 0
$script:WarnCount = 0

function Write-Ok   ([string]$msg) { Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Write-Warn ([string]$msg) { Write-Host "  [AVISO] $msg" -ForegroundColor Yellow; $script:WarnCount++ }
function Write-Bad  ([string]$msg) { Write-Host "  [ERRO]  $msg" -ForegroundColor Red;    $script:ErrCount++ }
function Write-Info ([string]$msg) { Write-Host "  $msg" -ForegroundColor Gray }
function Write-Sect ([string]$msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }

# Significado dos LastTaskResult mais comuns (chaves como string para nao
# esbarrar em comparacao Int32 vs UInt32 do hashtable)
$ResultMap = @{
    "0"          = "sucesso"
    "1"          = "falha generica do programa - sintoma classico da Action antiga com cmd /c + aspas (re-rode o setup)"
    "2"          = "arquivo nao encontrado"
    "267008"     = "tarefa pronta (ainda nao rodou nesta definicao)"
    "267009"     = "tarefa em execucao agora"
    "267011"     = "tarefa NUNCA rodou desde que foi registrada"
    "267014"     = "ultima execucao foi encerrada (parada manual ou limite de tempo)"
    "2147750687" = "ja havia uma instancia rodando (0x8004131F)"
    "2147942402" = "arquivo/script da Action nao encontrado (0x80070002)"
    "2147942405" = "acesso negado (0x80070005)"
    "2147943645" = "usuario nao estava logado (0x800704DD)"
    "2147946720" = "tarefa exige usuario logado e nao havia sessao no horario (0x800710E0)"
}

Write-Host "==========================================================="
Write-Host " Diagnostico da coleta local agendada (Magalu+Shopee+CB)"
Write-Host " Projeto: $BaseDir"
Write-Host " Data:    $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "==========================================================="

# --- 1. Tarefas RAC_Local_* -------------------------------------------------
Write-Sect "Tarefas agendadas (RAC_Local_Manha / RAC_Local_Noite)"

$expectedBat = Join-Path $BaseDir "scripts\run_local_scheduled.bat"

foreach ($name in @("RAC_Local_Manha", "RAC_Local_Noite")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Bad "$name NAO existe - rode: PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1"
        continue
    }

    if ($task.State -eq "Disabled") {
        Write-Bad "$name existe mas esta DESABILITADA (habilite ou re-rode o setup)"
    } else {
        Write-Ok "$name registrada (estado: $($task.State))"
    }

    $act = $task.Actions | Select-Object -First 1
    Write-Info "Action: $($act.Execute) $($act.Arguments)"

    # Formato antigo (cmd /c + redirect): quebra com espaco no caminho do
    # projeto - o cmd descarta aspas e a tarefa morre sem escrever log.
    if (($act.Execute -match "cmd(\.exe)?`"?$") -or ($act.Arguments -match ">>")) {
        Write-Bad "$name usa a Action ANTIGA (cmd /c ... >> log) - com espacos no caminho ela falha na hora, sem log. Re-rode scripts\setup_local_scheduler.ps1"
    } else {
        $exePath = $act.Execute.Trim('"')
        if (-not (Test-Path $exePath)) {
            Write-Bad "$name aponta para script inexistente: $exePath"
        } elseif ($exePath -ne $expectedBat) {
            Write-Warn "$name nao aponta para $expectedBat (aponta para $exePath)"
        }
    }

    $hasLogon = $task.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" }
    if (-not $hasLogon) {
        Write-Warn "$name sem gatilho de LOGON (catch-up) - registro antigo; re-rode o setup para cobrir notebook desligado no horario"
    }

    $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
    if ($info) {
        $code = [int64]$info.LastTaskResult
        $meaning = $ResultMap["$code"]
        if (-not $meaning) { $meaning = "codigo nao mapeado" }
        $hex = "0x{0:X8}" -f $code
        Write-Info "Ultima execucao: $($info.LastRunTime) | resultado: $code ($hex) = $meaning"
        Write-Info "Proxima execucao: $($info.NextRunTime) | execucoes perdidas: $($info.NumberOfMissedRuns)"

        # O Task Scheduler preserva LastRunTime/LastTaskResult quando a tarefa
        # e re-registrada (mesmo nome). Um erro ANTERIOR ao re-registro e
        # historico da definicao antiga - nao reflete a Action atual.
        $regDate = $null
        try { if ($task.Date) { $regDate = [datetime]$task.Date } } catch { $regDate = $null }
        $isStale = ($regDate -and $info.LastRunTime -and $info.LastRunTime -lt $regDate)

        if ($code -ne 0 -and $code -ne 267009 -and $code -ne 267008) {
            if ($code -eq 267011) {
                Write-Warn "$name nunca rodou desde o registro"
            } elseif ($isStale) {
                Write-Info "Registrada em: $regDate (depois da ultima execucao)"
                Write-Warn "$name: o erro acima e de ANTES do re-registro (definicao antiga) - valide a nova com: Start-ScheduledTask -TaskName '$name'"
            } else {
                Write-Bad "$name terminou com erro na ultima execucao ($meaning)"
            }
        }
    }
}

# Tarefas legadas que conflitam/duplicam (o setup atual as remove)
$legacy = @("RAC_Autenticada_Manha", "RAC_Autenticada_Noite", "RAC_Chrome_CDP_Startup",
            "RAC_Magalu_Manha", "RAC_Magalu_Noite")
$found = @()
foreach ($t in $legacy) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { $found += $t }
}
if ($found.Count -gt 0) {
    Write-Warn "Tarefas LEGADAS ainda registradas: $($found -join ', ') - re-rode o setup para remove-las"
}

# --- 2. Scripts e ambiente ----------------------------------------------------
Write-Sect "Scripts e ambiente"

foreach ($rel in @("scripts\run_local_scheduled.bat",
                   "scripts\local_scheduled_collect.bat",
                   "scripts\collect_local_authenticated.bat")) {
    $p = Join-Path $BaseDir $rel
    if (Test-Path $p) { Write-Ok "$rel presente" }
    else { Write-Bad "$rel AUSENTE - rode scripts\sync_windows.bat (ou git pull)" }
}

$pyExe = $null
foreach ($cand in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
    $p = Join-Path $BaseDir $cand
    if (Test-Path $p) { $pyExe = $p; break }
}
if ($pyExe) {
    Write-Ok "venv encontrada: $pyExe"
    & $pyExe -c "import rebrowser_playwright" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "rebrowser-playwright instalado (obrigatorio p/ passar no Akamai)"
    } else {
        Write-Bad "rebrowser-playwright NAO importavel na venv - pip install -r requirements.txt && python -m rebrowser_playwright install chromium"
    }
} else {
    Write-Bad "Nenhuma venv (.venv/venv) - rode scripts\sync_windows.bat"
}

$envFile = Join-Path $BaseDir ".env"
if (Test-Path $envFile) {
    $envText = Get-Content $envFile -Raw
    foreach ($key in @("SUPABASE_URL", "SUPABASE_KEY", "TELEGRAM_BOT_TOKEN", "N8N_TELEGRAM_CHAT_ID")) {
        if ($envText -match "(?m)^\s*$key\s*=\s*\S") { Write-Ok ".env tem $key" }
        else { Write-Warn ".env sem $key (upload/alerta pode nao funcionar)" }
    }
} else {
    Write-Bad ".env nao encontrado em $BaseDir"
}

$profileDir = Join-Path $BaseDir "data\chrome_profile"
if (Test-Path $profileDir) {
    Write-Ok "Perfil dedicado do Chrome presente (data\chrome_profile)"
    Write-Info "Conferir login Shopee: python scripts\setup_local_profile.py --check"
} else {
    Write-Bad "Perfil dedicado AUSENTE - rode: python scripts\setup_local_profile.py (e logue na Shopee)"
}

# --- 3. Repositorio (codigo atrasado?) ----------------------------------------
Write-Sect "Repositorio"

Push-Location $BaseDir
$env:GIT_TERMINAL_PROMPT = "0"
$head = git rev-parse --short HEAD 2>$null
if ($head) {
    Write-Info "Commit local: $head"
    git fetch origin main --quiet 2>$null
    if ($LASTEXITCODE -eq 0) {
        $behind = git rev-list --count "HEAD..origin/main" 2>$null
        if ([int]$behind -gt 0) {
            Write-Warn "Codigo local esta $behind commit(s) atras de origin/main (a coleta agendada faz git pull sozinha; para atualizar agora: git pull --ff-only origin main)"
        } else {
            Write-Ok "Codigo local em dia com origin/main"
        }
    } else {
        Write-Warn "git fetch falhou (sem internet ou sem credencial salva) - o self-update das tarefas tambem falharia; teste: git pull origin main"
    }
    $dirty = (git status --porcelain 2>$null | Measure-Object).Count
    if ($dirty -gt 0) {
        Write-Warn "$dirty arquivo(s) modificados localmente - podem impedir o git pull --ff-only do agendamento"
    }
} else {
    Write-Bad "git nao respondeu em $BaseDir (git instalado? repo integro?)"
}
Pop-Location

# --- 4. Logs e marcadores do dia ----------------------------------------------
Write-Sect "Logs e execucoes de hoje"

$logFile = Join-Path $BaseDir "logs\scheduler.log"
if (Test-Path $logFile) {
    $age = (Get-Date) - (Get-Item $logFile).LastWriteTime
    Write-Info ("scheduler.log: ultima escrita ha {0:N1} h" -f $age.TotalHours)
    if ($age.TotalHours -gt 26) {
        Write-Warn "scheduler.log sem escrita ha mais de 26h - nenhuma tarefa rodou nesse periodo (com a Action antiga a tarefa falha SEM logar; veja o resultado decodificado acima)"
    }
    Write-Info "--- ultimas linhas ---"
    # -Encoding UTF8: o log e UTF-8 (PYTHONUTF8=1); sem isso o PS 5.1 le como
    # ANSI e os acentos/emojis do Loguru viram mojibake na tela.
    Get-Content $logFile -Tail 12 -Encoding UTF8 | ForEach-Object { Write-Info $_ }
} else {
    Write-Warn "logs\scheduler.log nao existe - a coleta agendada nunca chegou a escrever log nesta maquina"
}

$today = Get-Date -Format "yyyyMMdd"
foreach ($slot in @("manha", "noite")) {
    $marker = Join-Path $BaseDir "logs\coleta_${slot}_${today}.done"
    if (Test-Path $marker) { Write-Ok "Coleta '$slot' de hoje concluida (marcador presente)" }
    else { Write-Info "Coleta '$slot' de hoje: sem marcador (ainda nao rodou/nao concluiu)" }
}

# --- 5. Energia (WakeToRun depende de wake timer) -------------------------------
Write-Sect "Energia"

$wake = powercfg /waketimers 2>&1
if ($LASTEXITCODE -eq 0) {
    if ($wake -match "RAC|Task") { Write-Ok "Ha wake timer agendado (WakeToRun deve acordar o notebook)" }
    else { Write-Info "Nenhum wake timer listado agora (normal se a proxima execucao ainda nao foi enfileirada)" }
} else {
    Write-Info "powercfg /waketimers precisa de Admin - pulei. Se o notebook dorme e nao acorda as 9h/20h, verifique 'Permitir temporizadores de despertar' nas opcoes de energia"
}

# --- Resumo ---------------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================="
if ($script:ErrCount -eq 0 -and $script:WarnCount -eq 0) {
    Write-Host " Tudo certo: nenhum problema encontrado." -ForegroundColor Green
} else {
    Write-Host " Resultado: $($script:ErrCount) erro(s), $($script:WarnCount) aviso(s)." -ForegroundColor $(if ($script:ErrCount -gt 0) { "Red" } else { "Yellow" })
    Write-Host ""
    Write-Host " Correcao padrao (resolve a maioria dos erros acima):" -ForegroundColor Yellow
    Write-Host "   1. git pull --ff-only origin main   (ou scripts\sync_windows.bat)" -ForegroundColor Gray
    Write-Host "   2. PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1" -ForegroundColor Gray
    Write-Host "   3. Teste: Start-ScheduledTask -TaskName 'RAC_Local_Manha'" -ForegroundColor Gray
    Write-Host "      e confira: Get-Content logs\scheduler.log -Tail 30" -ForegroundColor Gray
}
Write-Host "==========================================================="

if ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost") {
    Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

exit $(if ($script:ErrCount -gt 0) { 1 } else { 0 })
