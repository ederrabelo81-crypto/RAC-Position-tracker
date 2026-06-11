# =============================================================================
# setup_authenticated_scheduler.ps1 - Agenda a coleta AUTENTICADA completa
# (Magalu + Shopee + Casas Bahia) no Windows Task Scheduler.
#
# Evolucao do setup_magalu_scheduler.ps1: usa o MESMO Chrome CDP, mas o .bat
# agendado renova as sessoes Shopee/CB antes de coletar os 3 marketplaces.
#
# Cria 3 tarefas:
#   1. RAC_Chrome_CDP_Startup   - Abre Chrome com CDP no logon do Windows
#   2. RAC_Autenticada_Manha    - Coleta 10:05 (Abertura, 2 pgs, alta+media)
#   3. RAC_Autenticada_Noite    - Coleta 21:05 (Fechamento, 1 pg, alta)
#
# Se as tarefas antigas RAC_Magalu_* existirem, sao removidas (a coleta
# autenticada ja inclui o Magalu — manter as duas duplicaria os dados).
#
# Uso (PowerShell como Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_authenticated_scheduler.ps1
#
# Para remover:
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_authenticated_scheduler.ps1 -Remove
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
    Write-Host ""
    Write-Host "Este script precisa de privilegios de Administrador." -ForegroundColor Yellow
    Write-Host "Re-executando com elevacao via UAC..." -ForegroundColor Yellow
    Write-Host ""

    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    if ($Remove) { $argList += "-Remove" }

    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait
        exit 0
    } catch {
        Write-Host ""
        Write-Host "ERRO: elevacao UAC negada ou falhou." -ForegroundColor Red
        Write-Host "Rode num PowerShell (Admin):" -ForegroundColor Yellow
        Write-Host "  cd 'C:\Users\Eder Rabelo\Downloads\rac-position-tracker'" -ForegroundColor Gray
        Write-Host "  PowerShell -ExecutionPolicy Bypass -File scripts\setup_authenticated_scheduler.ps1" -ForegroundColor Gray
        exit 1
    }
}

Write-Host "Executando como Administrador (OK)." -ForegroundColor Green
Write-Host ""

$BaseDir = "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
$StartChromeScript = Join-Path $BaseDir "scripts\start_chrome_cdp.bat"
$CollectScript     = Join-Path $BaseDir "scripts\collect_authenticated_cdp.bat"

$TaskUser = "$env:USERDOMAIN\$env:USERNAME"

$Tasks = @(
    "RAC_Chrome_CDP_Startup",
    "RAC_Autenticada_Manha",
    "RAC_Autenticada_Noite"
)

# Tarefas antigas (Magalu-only) substituidas por esta versao
$LegacyTasks = @("RAC_Magalu_Manha", "RAC_Magalu_Noite")

# --- Remocao ----------------------------------------------------------------
if ($Remove) {
    foreach ($t in $Tasks) {
        $existing = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "  Removida: $t" -ForegroundColor Yellow
        }
    }
    Write-Host "Tarefas removidas." -ForegroundColor Green
    Write-Host ""
    Write-Host "Pressione qualquer tecla para fechar esta janela..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 0
}

# --- Validacoes -------------------------------------------------------------
if (-not (Test-Path $StartChromeScript)) {
    Write-Error "Script nao encontrado: $StartChromeScript"
    exit 1
}
if (-not (Test-Path $CollectScript)) {
    Write-Error "Script nao encontrado: $CollectScript"
    exit 1
}

# Garante o diretorio de logs usado no redirect das tarefas (sem ele o
# cmd /c falha e a execucao agendada quebra silenciosamente)
New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "logs") | Out-Null

# Remove tarefas legadas Magalu-only (a autenticada ja cobre o Magalu)
foreach ($t in $LegacyTasks) {
    $existing = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "  Substituida (removida): $t" -ForegroundColor Yellow
    }
}

# Principal: roda como o usuario logado, sem elevacao (Chrome precisa de UI).
$principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited

# --- 1. Chrome CDP no logon do Windows --------------------------------------
Write-Host "Registrando: RAC_Chrome_CDP_Startup (usuario: $TaskUser)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$StartChromeScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "RAC_Chrome_CDP_Startup" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Abre Chrome com CDP na porta 9222 ao logar no Windows" `
    -Force | Out-Null

# --- 2. Coleta manha (10:05) ------------------------------------------------
# 10:05 (e nao 10:00) para nao concorrer com a coleta da Oracle VM / GH Actions
Write-Host "Registrando: RAC_Autenticada_Manha (10:05 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 2 alta media >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:05AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "RAC_Autenticada_Manha" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Coleta autenticada (Magalu+Shopee+CB) turno Abertura via CDP - 2 paginas, alta+media" `
    -Force | Out-Null

# --- 3. Coleta noite (21:05) ------------------------------------------------
Write-Host "Registrando: RAC_Autenticada_Noite (21:05 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 1 alta >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:05PM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "RAC_Autenticada_Noite" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Coleta autenticada (Magalu+Shopee+CB) turno Fechamento via CDP - 1 pagina, alta" `
    -Force | Out-Null

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Tarefas registradas com sucesso!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Listar:    Get-ScheduledTask -TaskName RAC_*" -ForegroundColor Gray
Write-Host "Logs:      Get-Content '$BaseDir\logs\scheduler.log' -Tail 50" -ForegroundColor Gray
Write-Host "Testar:    Start-ScheduledTask -TaskName 'RAC_Autenticada_Manha'" -ForegroundColor Gray
Write-Host "Remover:   .\setup_authenticated_scheduler.ps1 -Remove" -ForegroundColor Gray
Write-Host ""
Write-Host "PROXIMOS PASSOS:" -ForegroundColor Yellow
Write-Host "  1. Rode start_chrome_cdp.bat (ou faca logout/login do Windows)" -ForegroundColor Yellow
Write-Host "  2. No Chrome CDP: faca LOGIN na Shopee (1x — fica salvo no perfil)" -ForegroundColor Yellow
Write-Host "  3. Navegue 2-3 min no Magalu/Casas Bahia para aquecer o perfil" -ForegroundColor Yellow
Write-Host "  4. Deixe o Chrome aberto" -ForegroundColor Yellow
Write-Host "  5. Teste: Start-ScheduledTask -TaskName 'RAC_Autenticada_Manha'" -ForegroundColor Yellow
Write-Host ""
Write-Host "Pressione qualquer tecla para fechar esta janela..." -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
