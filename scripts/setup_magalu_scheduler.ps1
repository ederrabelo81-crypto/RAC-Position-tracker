# =============================================================================
# setup_magalu_scheduler.ps1 — Registra tarefas no Windows Task Scheduler
#
# Cria 3 tarefas:
#   1. RAC_Chrome_CDP_Startup  — Abre Chrome com CDP no login do Windows
#   2. RAC_Magalu_Manha        — Coleta Magalu às 10:00 (turno Abertura)
#   3. RAC_Magalu_Noite        — Coleta Magalu às 21:00 (turno Fechamento)
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

$BaseDir = "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
$StartChromeScript  = Join-Path $BaseDir "scripts\start_chrome_cdp.bat"
$CollectScript      = Join-Path $BaseDir "scripts\collect_magalu_cdp.bat"

$Tasks = @(
    "RAC_Chrome_CDP_Startup",
    "RAC_Magalu_Manha",
    "RAC_Magalu_Noite"
)

# ─── Remoção ────────────────────────────────────────────────────────────────
if ($Remove) {
    foreach ($t in $Tasks) {
        $existing = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "  Removida: $t" -ForegroundColor Yellow
        }
    }
    Write-Host "Tarefas removidas." -ForegroundColor Green
    exit 0
}

# ─── Validações ─────────────────────────────────────────────────────────────
if (-not (Test-Path $StartChromeScript)) {
    Write-Error "Script não encontrado: $StartChromeScript"
    exit 1
}
if (-not (Test-Path $CollectScript)) {
    Write-Error "Script não encontrado: $CollectScript"
    exit 1
}

# ─── 1. Chrome CDP no login do Windows ──────────────────────────────────────
Write-Host "Registrando: RAC_Chrome_CDP_Startup" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$StartChromeScript`""
$trigger = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "RAC_Chrome_CDP_Startup" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Abre Chrome com CDP na porta 9222 ao logar no Windows" `
    -Force | Out-Null

# ─── 2. Coleta manhã (10:00) ────────────────────────────────────────────────
Write-Host "Registrando: RAC_Magalu_Manha (10:00 diário)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 2 alta media >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "RAC_Magalu_Manha" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Coleta Magalu turno Abertura via CDP — 2 páginas, alta+media" `
    -Force | Out-Null

# ─── 3. Coleta noite (21:00) ────────────────────────────────────────────────
Write-Host "Registrando: RAC_Magalu_Noite (21:00 diário)" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$CollectScript`" 1 alta >> `"$BaseDir\logs\scheduler.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:00PM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "RAC_Magalu_Noite" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Coleta Magalu turno Fechamento via CDP — 1 página, alta" `
    -Force | Out-Null

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Tarefas registradas com sucesso!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "Listar:    Get-ScheduledTask -TaskName RAC_*" -ForegroundColor Gray
Write-Host "Logs:      Get-Content '$BaseDir\logs\scheduler.log' -Tail 50" -ForegroundColor Gray
Write-Host "Testar:    Start-ScheduledTask -TaskName 'RAC_Magalu_Manha'" -ForegroundColor Gray
Write-Host "Remover:   .\setup_magalu_scheduler.ps1 -Remove" -ForegroundColor Gray
Write-Host ""
Write-Host "PRÓXIMOS PASSOS:" -ForegroundColor Yellow
Write-Host "  1. Faça LOGOUT/LOGIN do Windows (ou rode start_chrome_cdp.bat agora)" -ForegroundColor Yellow
Write-Host "  2. No Chrome que abrir, navegue no Magalu por uns 5 min" -ForegroundColor Yellow
Write-Host "     (login se quiser; busque por 'ar condicionado') — aquece o perfil" -ForegroundColor Yellow
Write-Host "  3. Deixe o Chrome aberto" -ForegroundColor Yellow
Write-Host "  4. Aguarde 10:00 ou 21:00 (ou teste com Start-ScheduledTask)" -ForegroundColor Yellow
