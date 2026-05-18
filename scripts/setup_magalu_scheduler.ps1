# =============================================================================
# setup_magalu_scheduler.ps1 - Registra tarefas no Windows Task Scheduler
#
# Cria 3 tarefas:
#   1. RAC_Chrome_CDP_Startup  - Abre Chrome com CDP no logon do Windows
#   2. RAC_Magalu_Manha        - Coleta Magalu as 10:00 (turno Abertura)
#   3. RAC_Magalu_Noite        - Coleta Magalu as 21:00 (turno Fechamento)
#
# Uso (PowerShell como Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1
#
# Para remover:
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1 -Remove
# =============================================================================

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# --- Auto-elevacao para Admin -----------------------------------------------
# Register-ScheduledTask exige privilegios administrativos. Se nao estiver
# rodando como Admin, abre nova janela elevada via UAC.
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
        Write-Host ""
        Write-Host "Solucao manual:" -ForegroundColor Yellow
        Write-Host "  1. Pressione Win + X" -ForegroundColor Gray
        Write-Host "  2. Clique em 'Terminal (Admin)' ou 'Windows PowerShell (Admin)'" -ForegroundColor Gray
        Write-Host "  3. Rode novamente:" -ForegroundColor Gray
        Write-Host "       cd 'C:\Users\Eder Rabelo\Downloads\rac-position-tracker'" -ForegroundColor Gray
        Write-Host "       PowerShell -ExecutionPolicy Bypass -File scripts\setup_magalu_scheduler.ps1" -ForegroundColor Gray
        Write-Host ""
        exit 1
    }
}

Write-Host "Executando como Administrador (OK)." -ForegroundColor Green
Write-Host ""

$BaseDir = "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
$StartChromeScript  = Join-Path $BaseDir "scripts\start_chrome_cdp.bat"
$CollectScript      = Join-Path $BaseDir "scripts\collect_magalu_cdp.bat"

# Usuario que vai executar as tarefas (mesmo logon, runlevel limitado)
$TaskUser = "$env:USERDOMAIN\$env:USERNAME"

$Tasks = @(
    "RAC_Chrome_CDP_Startup",
    "RAC_Magalu_Manha",
    "RAC_Magalu_Noite"
)

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

# Principal: roda como o usuario logado, sem elevacao. Importante:
# Chrome precisa do user logado para mostrar UI; rodar como SYSTEM
# nao funciona pra browser visivel.
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

# --- 2. Coleta manha (10:00) ------------------------------------------------
Write-Host "Registrando: RAC_Magalu_Manha (10:00 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 2 alta media >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "RAC_Magalu_Manha" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Coleta Magalu turno Abertura via CDP - 2 paginas, alta+media" `
    -Force | Out-Null

# --- 3. Coleta noite (21:00) ------------------------------------------------
Write-Host "Registrando: RAC_Magalu_Noite (21:00 diario)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 1 alta >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:00PM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "RAC_Magalu_Noite" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Coleta Magalu turno Fechamento via CDP - 1 pagina, alta" `
    -Force | Out-Null

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Tarefas registradas com sucesso!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Listar:    Get-ScheduledTask -TaskName RAC_*" -ForegroundColor Gray
Write-Host "Logs:      Get-Content '$BaseDir\logs\scheduler.log' -Tail 50" -ForegroundColor Gray
Write-Host "Testar:    Start-ScheduledTask -TaskName 'RAC_Magalu_Manha'" -ForegroundColor Gray
Write-Host "Remover:   .\setup_magalu_scheduler.ps1 -Remove" -ForegroundColor Gray
Write-Host ""
Write-Host "PROXIMOS PASSOS:" -ForegroundColor Yellow
Write-Host "  1. Faca LOGOUT/LOGIN do Windows (ou rode start_chrome_cdp.bat agora)" -ForegroundColor Yellow
Write-Host "  2. No Chrome que abrir, navegue no Magalu por uns 5 min" -ForegroundColor Yellow
Write-Host "     (login se quiser, busque por 'ar condicionado') - aquece o perfil" -ForegroundColor Yellow
Write-Host "  3. Deixe o Chrome aberto" -ForegroundColor Yellow
Write-Host "  4. Aguarde 10:00 ou 21:00 (ou teste com Start-ScheduledTask)" -ForegroundColor Yellow
Write-Host ""
Write-Host "Pressione qualquer tecla para fechar esta janela..." -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
