@echo off
:: -----------------------------------------------------------------------------
:: run_briefing_diario.bat - Launcher agendado do briefing diario (Claude Code
:: local, headless, apontando pro Aiven via o conector MCP "postgres" ja
:: configurado nesta maquina).
::
:: Por que roda AQUI e nao no Cowork/claude.ai: o Cowork nao tem (e hoje nao
:: pode ter) um conector MCP Postgres generico - so servicos com conector
:: OAuth publicado no diretorio (Supabase, Neon, etc.), e o Aiven nao e um
:: deles. A rotina anterior, agendada no Cowork, ficou apontando pro projeto
:: Supabase antigo depois da migracao e publicou "outage total" 3 dias
:: seguidos (12-17/09/2026) - ver CLAUDE.md, secao "Briefing/resumo diario -
:: nunca consultar o banco de memoria". Rodando aqui, usa o mesmo conector
:: Postgres/Aiven que ja existe no `claude` deste PC (docs/MIGRACAO_AIVEN.md).
::
:: Segue o MESMO padrao de run_local_scheduled.bat + local_scheduled_collect.bat:
::   - sem "cmd /c" na Action da tarefa (espaco no caminho do projeto quebra
::     o parse) - a tarefa chama este .bat direto;
::   - git pull antes de rodar (a rotina sempre usa o prompt mais novo);
::   - JANELA de hora (7-10h) + MARCADOR diario logs\briefing_<data>.done: o
::     gatilho de logon do Task Scheduler pode disparar fora do horario e/ou
::     mais de uma vez no mesmo dia (StartWhenAvailable, VPN reconectando,
::     etc.) - sem essa guarda, cada disparo reexecutaria o briefing inteiro,
::     reescrevendo o Notion e o painel varias vezes (achado do cubic no PR
::     que criou este arquivo). O marcador so e gravado em caso de SUCESSO;
::   - log dentro do proprio bat, bloco unico parseado por inteiro antes do
::     pull rodar (sobrevive ao git pull reescrever este proprio arquivo em
::     disco durante a execucao).
::
:: NUNCA usar "::" DENTRO do bloco entre parenteses abaixo - o cmd.exe trata
:: "::" como ROTULO ali dentro, nao como comentario, e o parse quebra antes
:: de qualquer coisa rodar (mesmo achado do cubic). "::" so e seguro FORA de
:: blocos com parenteses, como neste cabecalho; dentro do bloco, use "rem".
::
:: Uso (a tarefa do Task Scheduler chama direto, sem argumento):
::   scripts\run_briefing_diario.bat
:: Pular o git pull (debug/offline): defina RAC_NO_SELFUPDATE=1
:: Ignorar a janela/marcador (forcar rodar agora, teste manual): defina RAC_FORCE_BRIEFING=1
:: Log: logs\briefing_scheduler.log
:: -----------------------------------------------------------------------------

setlocal EnableDelayedExpansion

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

:: git nao pode pedir credencial em sessao agendada (travaria ate o timeout)
set "GIT_TERMINAL_PROMPT=0"

:: Hora/data via PowerShell (nao bash): o Task Scheduler roda esta sessao com
:: PATH reduzido, e o Git for Windows nao garante "bash" nesse contexto. A
:: hora local do PC coletor ja e BRT.
set "HOUR="
set "TODAY="
for /f %%H in ('powershell -NoProfile -Command "(Get-Date).Hour" 2^>nul') do set "HOUR=%%H"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "TODAY=%%D"
if not defined TODAY set "TODAY=00000000"

set "WIN_MIN=7"
set "WIN_MAX=10"

echo [run_briefing_diario] logando em "%BASE_DIR%\logs\briefing_scheduler.log"

if "%RAC_FORCE_BRIEFING%"=="1" goto :run
if exist "logs\briefing_%TODAY%.done" (
    (echo [%DATE% %TIME%] [briefing] ja publicado hoje - nada a fazer) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    exit /b 0
)
if not defined HOUR (
    (echo [%DATE% %TIME%] [briefing] AVISO: nao obtive a hora via PowerShell - rodando sem guarda de janela) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    goto :run
)
if %HOUR% LSS %WIN_MIN% (
    (echo [%DATE% %TIME%] [briefing] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    exit /b 0
)
if %HOUR% GTR %WIN_MAX% (
    (echo [%DATE% %TIME%] [briefing] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    exit /b 0
)

:run
(
    echo [%DATE% %TIME%] [briefing] === inicio ===
    if "%RAC_NO_SELFUPDATE%"=="1" (
        echo [%DATE% %TIME%] [briefing] self-update pulado [RAC_NO_SELFUPDATE=1]
    ) else (
        echo [%DATE% %TIME%] [briefing] git pull --ff-only origin main
        git pull --ff-only origin main
        if errorlevel 1 echo [%DATE% %TIME%] [briefing] AVISO: git pull falhou - seguindo com o prompt local
    )
    if not exist "%BASE_DIR%\docs\briefing_diario_prompt.md" (
        echo [%DATE% %TIME%] [briefing] ERRO: docs\briefing_diario_prompt.md nao encontrado
        exit /b 1
    )
    echo [%DATE% %TIME%] [briefing] chamando claude -p (nao-interativo^)
    rem Execucao nao-interativa: o modo de permissao para rodar sem prompt de
    rem aprovacao e configurado UMA VEZ no settings.json local (nao aqui no
    rem script) - ver docs/BRIEFING_LOCAL_SETUP.md. O DSN do Aiven usado pelo
    rem conector MCP local deve ser uma credencial READ-ONLY - nunca a de
    rem escrita da coleta (RAC_DB_DSN do .env de coleta).
    type "%BASE_DIR%\docs\briefing_diario_prompt.md" | claude -p
    set "RC=!ERRORLEVEL!"
    if not "!RC!"=="0" (
        echo [%DATE% %TIME%] [briefing] ERRO: claude terminou com falha [exit=!RC!]
    ) else (
        echo [%DATE% %TIME%] [briefing] concluido
        del /q "logs\briefing_*.done" 2>nul
        echo ok> "logs\briefing_%TODAY%.done"
    )
    echo [%DATE% %TIME%] [briefing] === fim ===
    exit /b
) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
