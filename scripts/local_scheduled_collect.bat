@echo off
:: -----------------------------------------------------------------------------
:: local_scheduled_collect.bat - Estagio B da coleta agendada local.
::
:: Chamado por run_local_scheduled.bat DEPOIS do git pull, entao roda sempre na
:: versao mais nova do repo (mudancas aqui chegam ao notebook sozinhas, sem
:: re-registrar tarefa). Concentra a logica de agendamento:
::
::   - TRES turnos desde Set/2026 (8h/14h/20h), cada um com sua janela:
::       manha  -> 8-11h   (turno Abertura)
::       tarde  -> 12-17h  (turno Tarde)
::       noite  -> 18-23h  (turno Fechamento)
::     A janela protege o turno gravado: get_turno() decide Abertura/Tarde/
::     Fechamento pela HORA, entao uma tarefa que dispara atrasada
::     (StartWhenAvailable / gatilho de logon) FORA da janela e PULADA em vez de
::     gravar dados com o turno errado.
::   - Todos os 3 turnos rodam a MESMA varredura: 2 paginas, TODAS as keywords
::     (sem filtro de prioridade) e TODAS as plataformas (ML, Amazon, Magalu,
::     Casas Bahia, Google Shopping, Leroy, Shopee e dealers).
::   - Marcador diario logs\coleta_<slot>_<data>.done: o gatilho de logon pode
::     disparar varias vezes ao dia sem duplicar a coleta. O marcador so e
::     gravado em caso de SUCESSO - se a coleta das 08:00 falhar, o proximo
::     logon dentro da janela tenta de novo.
::   - Alerta Telegram quando a coleta agendada falha (exit != 0), via
::     utils\n8n_notify.py (usa TELEGRAM_BOT_TOKEN/N8N_* do .env).
::
:: Mais Vendidos (bestsellers) NAO e mais coletado desde Set/2026 - foco 100%
:: na coleta de oferta/posicao. O slot antigo continua reconhecido so para
:: avisar e sair sem erro, caso uma tarefa legada ainda o dispare.
::
:: Uso:
::   scripts\local_scheduled_collect.bat manha              (Abertura, 2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat tarde              (Tarde,    2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat noite              (Fechamento,2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat <pages> [prio...]  (legado: repassa)
:: -----------------------------------------------------------------------------

setlocal

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

set "MODE=%~1"

:: Hora/data via PowerShell: %TIME%/%DATE% mudam de formato com a localizacao
:: do Windows; PowerShell e estavel. (Hora local do notebook = BRT.)
set "HOUR="
set "TODAY="
for /f %%H in ('powershell -NoProfile -Command "(Get-Date).Hour" 2^>nul') do set "HOUR=%%H"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "TODAY=%%D"
if not defined TODAY set "TODAY=00000000"

if /i "%MODE%"=="manha" goto :slot_manha
if /i "%MODE%"=="tarde" goto :slot_tarde
if /i "%MODE%"=="noite" goto :slot_noite
if /i "%MODE%"=="bestsellers" goto :slot_bestsellers_removido
goto :legacy

:: Os tres turnos rodam a MESMA varredura (2 paginas, TODAS as keywords - sem
:: filtro de prioridade). Diferem so pela JANELA de hora, que casa com o turno
:: gravado por get_turno() (Abertura<=11h / Tarde 12-17h / Fechamento>=18h). O
:: RAC_JOB_ID e o id do job no livro-razao (utils\pipeline_registry.py): main.py
:: bate ponto de inicio/fim, e o supervisor consegue dizer "o PC coletor nao
:: rodou" em vez de acusar N plataformas criticas caidas.

:slot_manha
set "SLOT=manha"
set "RAC_JOB_ID=local_manha"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=8"
set "WIN_MAX=11"
goto :guarded_run

:slot_tarde
set "SLOT=tarde"
set "RAC_JOB_ID=local_tarde"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=12"
set "WIN_MAX=17"
goto :guarded_run

:slot_noite
set "SLOT=noite"
set "RAC_JOB_ID=local_noite"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=18"
set "WIN_MAX=23"
goto :guarded_run

:slot_bestsellers_removido
:: Mais Vendidos saiu da coleta em Set/2026. Nao e erro: apenas nao ha nada a
:: fazer. Sai 0 para nao disparar o alerta de falha do agendamento.
echo [%DATE% %TIME%] [bestsellers] coleta de mais vendidos foi descontinuada (Set/2026) - nada a fazer
exit /b 0

:guarded_run
if exist "logs\coleta_%SLOT%_%TODAY%.done" (
    echo [%DATE% %TIME%] [%SLOT%] ja coletado hoje - nada a fazer
    exit /b 0
)
if not defined HOUR (
    echo [%DATE% %TIME%] [%SLOT%] AVISO: nao obtive a hora via PowerShell - coletando sem guarda de janela
    goto :collect
)
if %HOUR% LSS %WIN_MIN% (
    echo [%DATE% %TIME%] [%SLOT%] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando
    exit /b 0
)
if %HOUR% GTR %WIN_MAX% (
    echo [%DATE% %TIME%] [%SLOT%] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando
    exit /b 0
)
goto :collect

:legacy
set "SLOT=legado"
set "PAGES=%~1"
if "%PAGES%"=="" set "PAGES=2"
set "PRIORITY="
if not "%~2"=="" set "PRIORITY=%~2"
if not "%~3"=="" set "PRIORITY=%PRIORITY% %~3"
if not "%~4"=="" set "PRIORITY=%PRIORITY% %~4"
goto :collect

:collect
:: Dependencias antes da coleta: o estagio A ja fez `git pull`, entao o codigo
:: e novo mas a venv pode ser antiga. Sem isto o notebook rodava codigo que
:: importa libs que ele nao tem - foi assim que o historico deixou de ir ao
:: Drive (google-api-python-client ausente -> backend cai para local). Sai em
:: ~1s quando nada mudou; nao aborta a coleta se a instalacao falhar (coletar
:: com a venv antiga e melhor que nao coletar).
if exist "%~dp0ensure_deps.bat" (
    echo [%DATE% %TIME%] [%SLOT%] verificando dependencias Python
    call "%~dp0ensure_deps.bat"
    if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: ensure_deps falhou - seguindo com a venv atual
) else (
    echo [%DATE% %TIME%] [%SLOT%] AVISO: ensure_deps.bat ausente - rode scripts\sync_windows.bat
)

echo [%DATE% %TIME%] [%SLOT%] coleta local: %PAGES% pagina(s), prioridade "%PRIORITY%"
call "%~dp0collect_local_authenticated.bat" %PAGES% %PRIORITY%
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] [%SLOT%] coleta finalizada [exit=%RC%]

if not "%RC%"=="0" goto :failed

:: Sucesso: limpa marcadores antigos do slot e grava o de hoje
if not "%SLOT%"=="legado" (
    del /q "logs\coleta_%SLOT%_*.done" 2>nul
    echo ok> "logs\coleta_%SLOT%_%TODAY%.done"
)
exit /b 0

:failed
:: Alerta best-effort no Telegram; falha do alerta nao muda o exit da coleta
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
"%PYEXE%" -c "from utils.n8n_notify import notify_scheduler_failure; notify_scheduler_failure('%SLOT%', %RC%)"
if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: nao consegui enviar o alerta Telegram
exit /b %RC%
