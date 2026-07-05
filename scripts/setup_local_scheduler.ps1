# =============================================================================
# setup_local_scheduler.ps1 - Agenda a coleta LOCAL autenticada no notebook.
#
# Coleta Magalu + Shopee + Casas Bahia com o Chrome real logado (perfil
# dedicado data\chrome_profile), IP residencial, SEM CDP e SEM porta de debug.
#
# Cria 2 tarefas (nao precisa mais da tarefa de "abrir Chrome CDP no logon" —
# o proprio Python abre o Chrome quando a coleta roda):
#   1. RAC_Local_Manha  - 10:05 (Abertura, 2 pgs, alta+media)
#   2. RAC_Local_Noite  - 21:05 (Fechamento, 1 pg, alta)
#
# Remove as tarefas antigas que dependiam do Chrome CDP / perfil copiado
# (RAC_Autenticada_*, RAC_Chrome_CDP_Startup, RAC_Magalu_*) para nao duplicar.
#
# PRE-REQUISITO (uma vez): faca login na Shopee no perfil dedicado:
#   python scripts\setup_local_profile.py
#
# Uso (PowerShell como Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
# Remover:
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1 -Remove
# =============================================================================

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# --- Auto-elevacao para Admin -----------------------------------------------
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal   = [Security.Principal.WindowsPrincipal]$currentUser
$isAdmin     = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Precisa de Administrador. Re-executando via UAC..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Remove) { $argList += "-Remove" }
    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait
        exit 0
    } catch {
        Write-Host "ERRO: elevacao UAC negada." -ForegroundColor Red
        exit 1
    }
}

# Raiz do projeto = pasta pai deste script
$BaseDir = Split-Path -Parent $PSScriptRoot
$CollectScript = Join-Path $BaseDir "scripts\collect_local_authenticated.bat"

$TaskUser = "$env:USERDOMAIN\$env:USERNAME"

$Tasks       = @("RAC_Local_Manha", "RAC_Local_Noite")
# Tarefas antigas (CDP / perfil copiado) substituidas por esta versao
$LegacyTasks = @(
    "RAC_Autenticada_Manha", "RAC_Autenticada_Noite", "RAC_Chrome_CDP_Startup",
    "RAC_Magalu_Manha", "RAC_Magalu_Noite"
)

if ($Remove) {
    foreach ($t in $Tasks) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "  Removida: $t" -ForegroundColor Yellow
        }
    }
    Write-Host "Tarefas removidas." -ForegroundColor Green
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 0
}

if (-not (Test-Path $CollectScript)) {
    Write-Error "Script nao encontrado: $CollectScript"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "logs") | Out-Null

# Remove tarefas legadas
foreach ($t in $LegacyTasks) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "  Substituida (removida): $t" -ForegroundColor Yellow
    }
}

# Roda como o usuario logado, sem elevacao (Chrome precisa da sessao de UI).
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited

Write-Host "Registrando: RAC_Local_Manha (10:05 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 2 alta media >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:05AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "RAC_Local_Manha" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal `
    -Description "Coleta local autenticada (Magalu+Shopee+CB) - Abertura, 2 pgs, alta+media" `
    -Force | Out-Null

Write-Host "Registrando: RAC_Local_Noite (21:05 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 1 alta >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:05PM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "RAC_Local_Noite" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal `
    -Description "Coleta local autenticada (Magalu+Shopee+CB) - Fechamento, 1 pg, alta" `
    -Force | Out-Null

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Tarefas registradas!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "Testar:  Start-ScheduledTask -TaskName 'RAC_Local_Manha'" -ForegroundColor Gray
Write-Host "Logs:    Get-Content '$BaseDir\logs\scheduler.log' -Tail 50" -ForegroundColor Gray
Write-Host ""
Write-Host "IMPORTANTE:" -ForegroundColor Yellow
Write-Host "  1. Faca login na Shopee 1x: python scripts\setup_local_profile.py" -ForegroundColor Yellow
Write-Host "  2. O notebook precisa estar LIGADO e com voce logado no Windows" -ForegroundColor Yellow
Write-Host "     nos horarios agendados (o Chrome abre na sua sessao de UI)." -ForegroundColor Yellow
Write-Host ""
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
