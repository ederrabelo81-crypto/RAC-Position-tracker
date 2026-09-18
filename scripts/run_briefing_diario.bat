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
:: Segue o MESMO padrao de run_local_scheduled.bat: sem "cmd /c" na Action da
:: tarefa (espaco no caminho do projeto quebra o parse), git pull antes de
:: rodar (a rotina sempre usa o prompt mais novo), log dentro do proprio bat,
:: bloco unico parseado por inteiro antes do pull rodar.
::
:: Uso (a tarefa do Task Scheduler chama direto, sem argumento):
::   scripts\run_briefing_diario.bat
:: Pular o git pull (debug/offline): defina RAC_NO_SELFUPDATE=1
:: Log: logs\briefing_scheduler.log
:: -----------------------------------------------------------------------------

setlocal

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

:: git nao pode pedir credencial em sessao agendada (travaria ate o timeout)
set "GIT_TERMINAL_PROMPT=0"

echo [run_briefing_diario] logando em "%BASE_DIR%\logs\briefing_scheduler.log"

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
    :: Execucao nao-interativa: o modo de permissao para rodar sem prompt de
    :: aprovacao e configurado UMA VEZ no settings.json local (nao aqui no
    :: script) - ver docs/BRIEFING_LOCAL_SETUP.md. O DSN do Aiven usado pelo
    :: conector MCP local deve ser uma credencial READ-ONLY - nunca a de
    :: escrita da coleta (RAC_DB_DSN do .env de coleta).
    type "%BASE_DIR%\docs\briefing_diario_prompt.md" | claude -p
    if errorlevel 1 (
        echo [%DATE% %TIME%] [briefing] ERRO: claude terminou com falha
    ) else (
        echo [%DATE% %TIME%] [briefing] concluido
    )
    echo [%DATE% %TIME%] [briefing] === fim ===
    exit /b
) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
