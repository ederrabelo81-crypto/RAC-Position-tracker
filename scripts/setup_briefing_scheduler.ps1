# =============================================================================
# setup_briefing_scheduler.ps1 - Agenda o briefing diario (Claude Code local,
# headless, contra o banco da janela quente - Supabase desde 22/09/2026, ver
# docs/RETORNO_SUPABASE.md) no notebook/PC coletor.
#
# PRE-REQUISITO (uma vez, ver docs/BRIEFING_LOCAL_SETUP.md):
#   1. claude mcp add-json postgres '{"command":"npx","args":["-y","@modelcontextprotocol/server-postgres","<DSN Session Pooler do Supabase, READ-ONLY>"]}'
#   2. claude mcp add --transport http notion https://mcp.notion.com/mcp
#      (abre o navegador para login OAuth - feito uma unica vez, o token
#      renova sozinho depois)
#   3. Testar uma vez a mao: scripts\run_briefing_diario.bat
#
# Cria 1 tarefa: RAC_Briefing_0700 - 07:00 diario + catch-up no logon
# (janela 7-10h). Roda scripts\run_briefing_diario.bat, que faz git pull e
# chama `claude -p` com o prompt de docs\briefing_diario_prompt.md.
#
# Uso (PowerShell como Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1
# Remover:
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1 -Remove
# =============================================================================

param(
    [switch]$Remove,
    [string]$TaskUser = ""
)

$ErrorActionPreference = "Stop"

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
    $origUser = "$env:USERDOMAIN\$env:USERNAME"
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-TaskUser", "`"$origUser`""
    )
    if ($Remove) { $argList += "-Remove" }
    try {
        $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList `
            -Verb RunAs -PassThru -Wait
        exit $proc.ExitCode
    } catch {
        Write-Host "ERRO: elevacao UAC negada." -ForegroundColor Red
        exit 1
    }
}

$BaseDir = Split-Path -Parent $PSScriptRoot
$BriefingScript = Join-Path $BaseDir "scripts\run_briefing_diario.bat"
$TaskName = "RAC_Briefing_0700"

if ([string]::IsNullOrWhiteSpace($TaskUser)) {
    $TaskUser = "$env:USERDOMAIN\$env:USERNAME"
}

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "  Removida: $TaskName" -ForegroundColor Yellow
    }
    Write-Host "Tarefa removida." -ForegroundColor Green
    Wait-Key
    exit 0
}

if (-not (Test-Path $BriefingScript)) {
    Write-Error "Script nao encontrado: $BriefingScript"
    exit 1
}
$PromptFile = Join-Path $BaseDir "docs\briefing_diario_prompt.md"
if (-not (Test-Path $PromptFile)) {
    Write-Error "Prompt nao encontrado: $PromptFile - rode git pull antes."
    exit 1
}

# Checagem best-effort do CLI + conectores MCP (nao bloqueia o registro da
# tarefa - so avisa, porque o comando exato de listagem pode mudar entre
# versoes do CLI).
$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudeCmd) {
    Write-Host "AVISO: comando 'claude' nao encontrado no PATH deste usuario." -ForegroundColor Yellow
    Write-Host "       A tarefa sera criada mesmo assim, mas vai falhar ate o Claude Code" -ForegroundColor Yellow
    Write-Host "       CLI estar instalado e logado nesta conta Windows." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "logs") | Out-Null

$taskPrincipal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited

$action = New-ScheduledTaskAction -Execute "`"$BriefingScript`"" -WorkingDirectory $BaseDir

# O gatilho de logon pode disparar fora da janela 7-10h (StartWhenAvailable,
# notebook desligado e ligado tarde, etc.) e mais de uma vez no mesmo dia -
# run_briefing_diario.bat e quem decide se ainda cabe rodar (guarda de janela
# + marcador logs\briefing_<data>.done), igual ao padrao de
# local_scheduled_collect.bat para a coleta. A tarefa aqui so dispara; a
# decisao de rodar ou pular e do .bat.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$logonTrigger.Delay = "PT2M"

$triggers = @((New-ScheduledTaskTrigger -Daily -At "7:00AM"), $logonTrigger)
# ExecutionTimeLimit generoso (3h): um briefing headless completo faz varias
# passadas de SQL, escreve no Notion, renderiza o painel e da git push - pode
# passar de 1h em dia com mais dado ou rede lenta. Um kill no meio da execucao
# deixa Notion/painel em estado parcial sem chance de reiniciar sozinho no
# mesmo dia (o marcador so e gravado em caso de SUCESSO).
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Write-Host "Registrando: $TaskName (07:00 diario + catch-up no logon, janela 7-10h)" -ForegroundColor Cyan
Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $triggers -Settings $settings -Principal $taskPrincipal `
    -Description "Briefing diario RAC (Claude Code local -> Supabase -> Notion + painel GitHub Pages)" `
    -Force | Out-Null

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Tarefa registrada!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "Testar:      Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "Logs:        Get-Content '$BaseDir\logs\briefing_scheduler.log' -Tail 80" -ForegroundColor Gray
Write-Host ""
Write-Host "IMPORTANTE (setup unico, se ainda nao fez - ver docs\BRIEFING_LOCAL_SETUP.md):" -ForegroundColor Yellow
Write-Host "  1. Conector MCP Postgres/Supabase ja configurado neste 'claude' local?" -ForegroundColor Yellow
Write-Host "     Confira com: claude mcp list" -ForegroundColor Yellow
Write-Host "  2. Conector MCP Notion adicionado (login OAuth feito 1x)?" -ForegroundColor Yellow
Write-Host "     claude mcp add --transport http notion https://mcp.notion.com/mcp" -ForegroundColor Yellow
Write-Host "  3. GitHub Pages deste repo habilitado (Settings -> Pages -> branch" -ForegroundColor Yellow
Write-Host "     main, pasta /docs) para o painel publicar sozinho a cada push." -ForegroundColor Yellow
Write-Host "  4. O notebook precisa estar LIGADO e com voce logado no Windows" -ForegroundColor Yellow
Write-Host "     as 7h (ou no proximo logon dentro da janela 7-10h)." -ForegroundColor Yellow
Write-Host ""
Wait-Key
