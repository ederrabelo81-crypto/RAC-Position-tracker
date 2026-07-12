# =============================================================================
# setup_local_scheduler.ps1 - Agenda a coleta LOCAL autenticada no notebook.
#
# Coleta Magalu + Shopee + Casas Bahia com o Chrome real logado (perfil
# dedicado data\chrome_profile), IP residencial, SEM CDP e SEM porta de debug.
#
# Cria 2 tarefas (nao precisa mais da tarefa de "abrir Chrome CDP no logon" —
# o proprio Python abre o Chrome quando a coleta roda):
#   1. RAC_Local_Manha  - 09:00 diario + catch-up no logon (janela 9-12h)
#   2. RAC_Local_Noite  - 20:00 diario + catch-up no logon (janela 20-23h)
#
# A ACTION das tarefas e o proprio run_local_scheduled.bat, com argumento so
# "manha"/"noite" — SEM "cmd.exe /c" e SEM redirect ">> log". Motivo (causa
# raiz das coletas agendadas que "nao rodavam"): com 4 aspas + ">>" o cmd.exe
# descarta a primeira e a ultima aspas do /c; como o caminho do projeto tem
# espaco (C:\Users\Eder Rabelo\...), o comando virava "C:\Users\Eder ..." e a
# tarefa morria na hora, sem escrever log. A tarefa do ML (install_tasks.bat)
# sempre funcionou porque usa o .bat direto com log interno — este setup agora
# segue o mesmo padrao (o log e feito dentro do .bat, em logs\scheduler.log).
#
# O run_local_scheduled.bat faz `git pull` e SO ENTAO roda a coleta (via
# local_scheduled_collect.bat) — o notebook sempre coleta com o codigo mais
# novo, sem depender de sync_windows.bat manual. O gatilho de logon cobre o
# notebook desligado/deslogado no horario: ao logar, a tarefa dispara e o
# local_scheduled_collect.bat decide (janela de turno + marcador diario) se
# ainda cabe coletar — sem duplicar e sem gravar turno errado.
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
# Diagnostico (sem admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
# =============================================================================

param(
    [switch]$Remove,
    # Usuario Windows dono das tarefas. Preenchido automaticamente no hop de
    # elevacao (ver abaixo) para preservar o usuario INTERATIVO original —
    # se a UAC usar credenciais de outro admin, $env:USERNAME apos a elevacao
    # seria o admin, e as tarefas rodariam na sessao errada (Chrome nao abriria).
    [string]$TaskUser = ""
)

$ErrorActionPreference = "Stop"

# Pausa so quando ha console interativo — evita travar/erro em execucao
# agendada/nao-interativa (a tarefa que chama este script nao tem teclado).
function Wait-Key {
    if ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost") {
        Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
}

# --- Auto-elevacao para Admin -----------------------------------------------
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal   = [Security.Principal.WindowsPrincipal]$currentUser
$isAdmin     = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Precisa de Administrador. Re-executando via UAC..." -ForegroundColor Yellow
    # Captura o usuario interativo ATUAL e repassa pro processo elevado, para
    # nao depender do $env:USERNAME de dentro da elevacao (que pode diferir).
    $origUser = "$env:USERDOMAIN\$env:USERNAME"
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-TaskUser", "`"$origUser`""
    )
    if ($Remove) { $argList += "-Remove" }
    try {
        # -PassThru + exit code do filho: sem isto o pai sempre sai 0 e mascara
        # falhas do setup elevado.
        $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList `
            -Verb RunAs -PassThru -Wait
        exit $proc.ExitCode
    } catch {
        Write-Host "ERRO: elevacao UAC negada." -ForegroundColor Red
        exit 1
    }
}

# Raiz do projeto = pasta pai deste script
$BaseDir = Split-Path -Parent $PSScriptRoot
# Wrapper agendado: faz `git pull` e SO ENTAO chama collect_local_authenticated.bat.
# As tarefas nao passam por sync_windows.bat, entao sem esse pull o .bat em disco
# fica defasado (um fix mergeado na vespera so valeria apos sync manual — foi o que
# fez Magalu/Shopee/Casas Bahia nao coletarem numa manha com o .bat ainda quebrado).
$CollectScript = Join-Path $BaseDir "scripts\run_local_scheduled.bat"

# Usa o usuario interativo repassado na elevacao; se rodou ja como admin sem o
# parametro, cai no usuario atual.
if ([string]::IsNullOrWhiteSpace($TaskUser)) {
    $TaskUser = "$env:USERDOMAIN\$env:USERNAME"
}

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
    Wait-Key
    exit 0
}

if (-not (Test-Path $CollectScript)) {
    Write-Error "Script nao encontrado: $CollectScript"
    exit 1
}
# Estagio B (logica de janela/marcador) — precisa existir no disco tambem
$StageB = Join-Path $BaseDir "scripts\local_scheduled_collect.bat"
if (-not (Test-Path $StageB)) {
    Write-Error "Script nao encontrado: $StageB — rode scripts\sync_windows.bat (ou git pull) antes."
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

# ACTION = o proprio .bat, com aspas embutidas (mesmo formato da tarefa do ML,
# que nunca falhou). NUNCA voltar para "cmd.exe /c ... >> log": com espacos no
# caminho o cmd.exe descarta aspas e a tarefa morre sem log (ver cabecalho).
# Paginas/prioridade ficam no local_scheduled_collect.bat — a Action so leva o
# slot, entao ajustes futuros chegam via git pull sem re-registrar a tarefa.
function New-RacAction([string]$Slot) {
    New-ScheduledTaskAction -Execute "`"$CollectScript`"" -Argument $Slot `
        -WorkingDirectory $BaseDir
}

# Gatilho de catch-up: ao logar no Windows, a tarefa dispara (com 2 min de
# folga pra rede/Wi-Fi subir) e o estagio B decide se ainda cabe coletar
# (janela do turno + marcador diario). Cobre notebook desligado/deslogado
# no horario agendado — principal buraco do agendamento fixo.
function New-RacLogonTrigger {
    $t = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
    $t.Delay = "PT2M"
    return $t
}

# WakeToRun: acorda o notebook em suspensao no horario (senao a coleta so rodaria
# quando alguem usasse a maquina). StartWhenAvailable: se a hora foi perdida
# (desligado), roda assim que possivel. RestartCount/Interval: retenta se a
# execucao falhar (ex.: pull/coleta com erro transiente). IgnoreNew: os gatilhos
# de horario e de logon podem coincidir — nao empilha instancias.
Write-Host "Registrando: RAC_Local_Manha (09:00 diario + catch-up no logon)" -ForegroundColor Cyan
$triggers = @((New-ScheduledTaskTrigger -Daily -At "9:00AM"), (New-RacLogonTrigger))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "RAC_Local_Manha" `
    -Action (New-RacAction "manha") -Trigger $triggers -Settings $settings -Principal $taskPrincipal `
    -Description "Coleta local autenticada (Magalu+Shopee+CB) - Abertura, janela 9-12h" `
    -Force | Out-Null

Write-Host "Registrando: RAC_Local_Noite (20:00 diario + catch-up no logon)" -ForegroundColor Cyan
$triggers = @((New-ScheduledTaskTrigger -Daily -At "8:00PM"), (New-RacLogonTrigger))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "RAC_Local_Noite" `
    -Action (New-RacAction "noite") -Trigger $triggers -Settings $settings -Principal $taskPrincipal `
    -Description "Coleta local autenticada (Magalu+Shopee+CB) - Fechamento, janela 20-23h" `
    -Force | Out-Null

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Tarefas registradas!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "Testar:      Start-ScheduledTask -TaskName 'RAC_Local_Manha'" -ForegroundColor Gray
Write-Host "Logs:        Get-Content '$BaseDir\logs\scheduler.log' -Tail 50" -ForegroundColor Gray
Write-Host "Diagnostico: PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "IMPORTANTE:" -ForegroundColor Yellow
Write-Host "  1. Faca login na Shopee 1x: python scripts\setup_local_profile.py" -ForegroundColor Yellow
Write-Host "  2. O notebook precisa estar LIGADO e com voce logado no Windows" -ForegroundColor Yellow
Write-Host "     nos horarios (o Chrome abre na sua sessao de UI). Se estiver" -ForegroundColor Yellow
Write-Host "     desligado, a coleta roda no proximo LOGON dentro da janela" -ForegroundColor Yellow
Write-Host "     (manha 9-12h / noite 20-23h)." -ForegroundColor Yellow
Write-Host ""
Wait-Key
