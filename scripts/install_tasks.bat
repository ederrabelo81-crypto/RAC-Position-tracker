@echo off
:: ============================================================================
:: install_tasks.bat - Registra as tarefas RAC no Task Scheduler automaticamente
::
:: USO:
::   1. Clique com botao direito neste arquivo
::   2. Selecione "Executar como administrador"
::
:: As tarefas sao criadas com /IT (interactive) e rodam no usuario atual
:: -- nao pede senha pois so executam quando voce estiver logado.
::
:: Tarefas criadas:
::   RAC_Coleta_Manha  -> 10:00 BRT diariamente
::   RAC_Coleta_Tarde  -> 21:00 BRT diariamente
:: ============================================================================

setlocal

set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "MANHA_BAT=%BASE_DIR%\scripts\collect_manha.bat"
set "TARDE_BAT=%BASE_DIR%\scripts\collect_tarde.bat"

echo ============================================================================
echo   RAC Price Tracker - Instalando tarefas no Task Scheduler
echo ============================================================================
echo.

:: Verifica privilegios de administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERRO] Este script precisa ser executado como Administrador.
    echo        Clique com o botao direito e escolha "Executar como administrador".
    pause
    exit /b 1
)

:: Verifica se os .bat existem
if not exist "%MANHA_BAT%" (
    echo [ERRO] Nao encontrado: %MANHA_BAT%
    pause
    exit /b 1
)
if not exist "%TARDE_BAT%" (
    echo [ERRO] Nao encontrado: %TARDE_BAT%
    pause
    exit /b 1
)

echo [1/2] Criando tarefa RAC_Coleta_Manha (10:00 BRT)...
schtasks /Create ^
    /TN "RAC_Coleta_Manha" ^
    /TR "\"%MANHA_BAT%\"" ^
    /SC DAILY ^
    /ST 10:00 ^
    /RL HIGHEST ^
    /RU "%USERNAME%" ^
    /IT ^
    /F

if %errorLevel% neq 0 (
    echo [ERRO] Falha ao criar RAC_Coleta_Manha
    pause
    exit /b 1
)

echo.
echo [2/2] Criando tarefa RAC_Coleta_Tarde (21:00 BRT)...
schtasks /Create ^
    /TN "RAC_Coleta_Tarde" ^
    /TR "\"%TARDE_BAT%\"" ^
    /SC DAILY ^
    /ST 21:00 ^
    /RL HIGHEST ^
    /RU "%USERNAME%" ^
    /IT ^
    /F

if %errorLevel% neq 0 (
    echo [ERRO] Falha ao criar RAC_Coleta_Tarde
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo   Tarefas instaladas com sucesso!
echo ============================================================================
echo.
echo Tarefas ativas:
schtasks /Query /TN "RAC_Coleta_Manha" /FO LIST ^| findstr /C:"TaskName" /C:"Next Run Time" /C:"Status"
echo.
schtasks /Query /TN "RAC_Coleta_Tarde" /FO LIST ^| findstr /C:"TaskName" /C:"Next Run Time" /C:"Status"
echo.
echo Para testar manualmente agora:
echo   schtasks /Run /TN "RAC_Coleta_Manha"
echo   schtasks /Run /TN "RAC_Coleta_Tarde"
echo.
echo Logs em: %BASE_DIR%\logs\scheduler.log
echo.
pause
endlocal
