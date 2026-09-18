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
::     que criou este arquivo);
::   - o marcador so e' gravado quando (a) a data de hoje foi obtida via
::     PowerShell E (b) `claude -p` saiu com exit 0 E (c) docs\painel\index.html
::     foi de fato reescrito HOJE. Exit 0 sozinho NAO prova publicacao - o
::     PASSO 0 do prompt permite terminar normalmente so reportando um
::     bloqueio (ex.: conector Aiven ausente), e gravar o marcador nesse caso
::     travaria a proxima tentativa ate o dia seguinte (achado do cubic).
::     Se a data nao puder ser obtida via PowerShell, o marcador e' pulado por
::     completo (nunca usa uma data fixa tipo "00000000", que travaria TODAS
::     as execucoes futuras apos a primeira - outro achado do cubic).
::   - log dentro do proprio bat, bloco unico parseado por inteiro antes do
::     pull rodar (sobrevive ao git pull reescrever este proprio arquivo em
::     disco durante a execucao).
::
:: NUNCA usar "::" DENTRO do bloco entre parenteses abaixo - o cmd.exe trata
:: "::" como ROTULO ali dentro, nao como comentario, e o parse quebra antes
:: de qualquer coisa rodar (achado do cubic). "::" so e seguro FORA de blocos
:: com parenteses, como neste cabecalho; dentro do bloco, use "rem".
::
:: `EnableDelayedExpansion` (necessario para "!RC!" mais abaixo) so e' ligado
:: DEPOIS de calcular BASE_DIR - ligado desde o inicio, um caminho de projeto
:: com "!" (raro, mas possivel) seria corrompido pela propria expansao
:: adiada antes de qualquer coisa rodar (outro achado do cubic).
::
:: Uso (a tarefa do Task Scheduler chama direto, sem argumento):
::   scripts\run_briefing_diario.bat
:: Pular o git pull (debug/offline): defina RAC_NO_SELFUPDATE=1
:: Ignorar a janela/marcador (forcar rodar agora, teste manual): defina RAC_FORCE_BRIEFING=1
:: Log: logs\briefing_scheduler.log
:: -----------------------------------------------------------------------------

setlocal

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

:: git nao pode pedir credencial em sessao agendada (travaria ate o timeout)
set "GIT_TERMINAL_PROMPT=0"

:: Hora/data via PowerShell (nao bash): o Task Scheduler roda esta sessao com
:: PATH reduzido, e o Git for Windows nao garante "bash" nesse contexto. Pelo
:: MESMO motivo, "powershell" pelo nome tambem nao e garantido no PATH dessa
:: sessao - resolvido pelo caminho fixo do PowerShell 5.1 (sempre presente em
:: qualquer Windows suportado), nunca pelo nome nu (achado do cubic: se
:: "powershell" falhar, TODAS as guardas - janela, marcador e a checagem de
:: frescor do painel mais abaixo - colapsam ao mesmo tempo, porque as tres
:: dependem do mesmo comando). A hora local do PC coletor ja e BRT.
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "HOUR="
set "TODAY="
set "DATE_KNOWN=0"
for /f %%H in ('"%PWSH%" -NoProfile -Command "(Get-Date).Hour" 2^>nul') do set "HOUR=%%H"
for /f %%D in ('"%PWSH%" -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "TODAY=%%D"
if defined TODAY set "DATE_KNOWN=1"

set "WIN_MIN=7"
set "WIN_MAX=10"

echo [run_briefing_diario] logando em "%BASE_DIR%\logs\briefing_scheduler.log"

if "%RAC_FORCE_BRIEFING%"=="1" goto :run
if "%DATE_KNOWN%"=="0" (
    (echo [%DATE% %TIME%] [briefing] AVISO: nao obtive a data via PowerShell - marcador diario desativado nesta execucao) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    goto :window_check
)
if exist "logs\briefing_%TODAY%.done" (
    (echo [%DATE% %TIME%] [briefing] ja publicado hoje - nada a fazer) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
    exit /b 0
)

:window_check
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
setlocal EnableDelayedExpansion
(
    set "FINAL_RC=0"
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
        set "FINAL_RC=1"
    ) else (
        echo [%DATE% %TIME%] [briefing] chamando claude -p (nao-interativo^)
        rem Execucao nao-interativa: o modo de permissao para rodar sem prompt
        rem de aprovacao e configurado UMA VEZ no settings.json local (nao aqui
        rem no script) - ver docs/BRIEFING_LOCAL_SETUP.md. O DSN do Aiven usado
        rem pelo conector MCP local deve ser uma credencial READ-ONLY - nunca a
        rem de escrita da coleta (RAC_DB_DSN do .env de coleta).
        rem
        rem NUNCA usar "goto"/label AQUI DENTRO (mesmo bloco entre parenteses
        rem que o cabecalho ja avisa sobre "::") - pular para um rotulo
        rem definido dentro do PROPRIO bloco entre parenteses e' outra
        rem armadilha classica do cmd.exe (a estrutura do bloco se perde).
        rem Por isso o restante deste bloco e' if/else aninhado, nunca goto.
        type "%BASE_DIR%\docs\briefing_diario_prompt.md" | claude -p
        set "RC=!ERRORLEVEL!"
        if not "!RC!"=="0" (
            echo [%DATE% %TIME%] [briefing] ERRO: claude terminou com falha [exit=!RC!]
            set "FINAL_RC=!RC!"
        ) else (
            echo [%DATE% %TIME%] [briefing] claude concluido [exit=0]
            if "%DATE_KNOWN%"=="0" (
                echo [%DATE% %TIME%] [briefing] data de hoje desconhecida - marcador NAO gravado nesta execucao
            ) else (
                rem exit 0 sozinho nao prova publicacao: o PASSO 0 permite
                rem terminar normalmente so reportando um bloqueio (ex.: sem
                rem conector Aiven), sem publicar nada. So grava o marcador se
                rem docs\painel\index.html foi REESCRITO HOJE - evidencia de
                rem que ao menos o PASSO 8 rodou.
                set "PANEL_FRESH=nao"
                for /f %%P in ('"%PWSH%" -NoProfile -Command "if (Test-Path 'docs\painel\index.html') { if ((Get-Item 'docs\painel\index.html').LastWriteTime.Date -eq (Get-Date).Date) { 'sim' } else { 'nao' } } else { 'nao' }" 2^>nul') do set "PANEL_FRESH=%%P"
                if /i "!PANEL_FRESH!"=="sim" (
                    echo [%DATE% %TIME%] [briefing] painel atualizado hoje - marcador gravado
                    del /q "logs\briefing_*.done" 2>nul
                    echo ok> "logs\briefing_%TODAY%.done"
                ) else (
                    echo [%DATE% %TIME%] [briefing] painel NAO foi atualizado hoje [claude terminou sem publicar - provavel bloqueio reportado] - marcador NAO gravado, proxima tentativa tenta de novo
                )
            )
        )
    )
    echo [%DATE% %TIME%] [briefing] === fim === [exit=!FINAL_RC!]
    exit /b !FINAL_RC!
) >> "%BASE_DIR%\logs\briefing_scheduler.log" 2>&1
