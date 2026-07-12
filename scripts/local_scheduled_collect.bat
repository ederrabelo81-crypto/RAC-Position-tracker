@echo off
:: -----------------------------------------------------------------------------
:: local_scheduled_collect.bat - Estagio B da coleta agendada local.
::
:: Chamado por run_local_scheduled.bat DEPOIS do git pull, entao roda sempre na
:: versao mais nova do repo (mudancas aqui chegam ao notebook sozinhas, sem
:: re-registrar tarefa). Concentra a logica de agendamento:
::
::   - Janela valida por slot (manha 9-12h / noite 20-23h): protege o turno do
::     registro - get_turno() marca Abertura ate 12h, entao uma tarefa que
::     dispara atrasada (StartWhenAvailable / gatilho de logon) fora da janela
::     e PULADA em vez de gravar dados com turno errado.
::   - Marcador diario logs\coleta_<slot>_<data>.done: o gatilho de logon pode
::     disparar varias vezes ao dia sem duplicar a coleta. O marcador so e
::     gravado em caso de SUCESSO - se a coleta das 09:00 falhar, o proximo
::     logon dentro da janela tenta de novo.
::   - Alerta Telegram quando a coleta agendada falha (exit != 0), via
::     utils\n8n_notify.py (usa TELEGRAM_BOT_TOKEN/N8N_* do .env).
::
:: Uso:
::   scripts\local_scheduled_collect.bat manha              (2 pgs, alta+media)
::   scripts\local_scheduled_collect.bat noite              (1 pg, alta)
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
if /i "%MODE%"=="noite" goto :slot_noite
goto :legacy

:slot_manha
set "SLOT=manha"
set "PAGES=2"
set "PRIORITY=alta media"
set "WIN_MIN=9"
set "WIN_MAX=12"
goto :guarded_run

:slot_noite
set "SLOT=noite"
set "PAGES=1"
set "PRIORITY=alta"
set "WIN_MIN=20"
set "WIN_MAX=23"
goto :guarded_run

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
