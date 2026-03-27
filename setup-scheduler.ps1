# ══════════════════════════════════════════════════════════════
# Setup automático do Agendador de Tarefas do Windows
# Executa como Administrador:
#   powershell -ExecutionPolicy Bypass -File setup-scheduler.ps1
# ══════════════════════════════════════════════════════════════

$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatFile = Join-Path $ProjectPath "run-tracker.bat"

# Verificar se o .bat existe
if (-not (Test-Path $BatFile)) {
    Write-Error "Arquivo run-tracker.bat nao encontrado em $ProjectPath"
    exit 1
}

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  RAC Position Tracker — Configuração do Agendador" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Tarefa da manhã
$TaskNameAM = "RAC Position Tracker - Manha"
$TriggerAM = New-ScheduledTaskTrigger -Daily -At "08:00AM"
$ActionAM = New-ScheduledTaskAction -Execute $BatFile -WorkingDirectory $ProjectPath
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

try {
    Unregister-ScheduledTask -TaskName $TaskNameAM -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskNameAM `
        -Trigger $TriggerAM `
        -Action $ActionAM `
        -Settings $Settings `
        -Description "Coleta matinal de posicionamento RAC e-commerce (08:00)" | Out-Null
    Write-Host "[OK] Tarefa '$TaskNameAM' criada (08:00)" -ForegroundColor Green
} catch {
    Write-Error "Falha ao criar tarefa matinal: $_"
}

# Tarefa da tarde
$TaskNamePM = "RAC Position Tracker - Tarde"
$TriggerPM = New-ScheduledTaskTrigger -Daily -At "05:30PM"
$ActionPM = New-ScheduledTaskAction -Execute $BatFile -WorkingDirectory $ProjectPath

try {
    Unregister-ScheduledTask -TaskName $TaskNamePM -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskNamePM `
        -Trigger $TriggerPM `
        -Action $ActionPM `
        -Settings $Settings `
        -Description "Coleta vespertina de posicionamento RAC e-commerce (17:30)" | Out-Null
    Write-Host "[OK] Tarefa '$TaskNamePM' criada (17:30)" -ForegroundColor Green
} catch {
    Write-Error "Falha ao criar tarefa vespertina: $_"
}

Write-Host ""
Write-Host "Tarefas agendadas com sucesso!" -ForegroundColor Green
Write-Host "Verifique em: Win+R -> taskschd.msc" -ForegroundColor Gray
Write-Host ""

# Verificar dependências
Write-Host "Verificando dependencias..." -ForegroundColor Yellow

$pythonVersion = & python --version 2>$null
if ($pythonVersion) {
    Write-Host "[OK] Python: $pythonVersion" -ForegroundColor Green
} else {
    Write-Host "[!!] Python NAO encontrado — instale: https://python.org" -ForegroundColor Red
}

$venvPath = Join-Path $ProjectPath "venv\Scripts\python.exe"
if (Test-Path $venvPath) {
    Write-Host "[OK] Ambiente virtual (venv) encontrado" -ForegroundColor Green
} else {
    Write-Host "[!!] venv nao encontrado — execute: python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt" -ForegroundColor Red
}

$playwrightBrowser = Join-Path $env:LOCALAPPDATA "ms-playwright"
if (Test-Path $playwrightBrowser) {
    Write-Host "[OK] Playwright browsers instalados" -ForegroundColor Green
} else {
    Write-Host "[!!] Playwright browsers nao encontrados — execute: python -m playwright install chromium" -ForegroundColor Red
}

Write-Host ""
