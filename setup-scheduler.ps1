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
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

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
$nodeVersion = & node --version 2>$null
if ($nodeVersion) {
    Write-Host "[OK] Node.js: $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "[!!] Node.js NAO encontrado — instale: https://nodejs.org" -ForegroundColor Red
}

$npmModules = Join-Path $ProjectPath "node_modules"
if (Test-Path $npmModules) {
    Write-Host "[OK] node_modules encontrado" -ForegroundColor Green
} else {
    Write-Host "[!!] Dependencias nao instaladas — execute: npm install" -ForegroundColor Red
}

Write-Host ""
