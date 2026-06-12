@echo off
:: -----------------------------------------------------------------------------
:: start_chrome_cdp.bat - Abre Chrome dedicado ao CDP em paralelo ao seu Chrome.
::
:: PRE-REQUISITO: rodar scripts\setup_cdp_profile.bat UMA VEZ antes.
:: Esse script copia seu perfil Eder para C:\chrome-rac-cdp.
::
:: Este script:
:: - Verifica se o perfil CDP existe
:: - Abre Chrome com --user-data-dir=C:\chrome-rac-cdp + porta de debug 9222
:: - Como e um user-data-dir diferente, NAO conflita com seu Chrome normal:
::   ambos podem rodar simultaneamente.
::
:: Uso:
::   scripts\start_chrome_cdp.bat
:: -----------------------------------------------------------------------------

setlocal

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
:: Instalacao por usuario (sem admin) fica em %LOCALAPPDATA%
if not exist "%CHROME_EXE%" set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo [ERRO] chrome.exe nao encontrado em locais padrao.
    echo Edite este script e ajuste CHROME_EXE.
    exit /b 1
)

set "CDP_DATA_DIR=C:\chrome-rac-cdp"
set "DEBUG_PORT=9222"

:: Valida que o perfil ja foi copiado
if not exist "%CDP_DATA_DIR%\Default" (
    echo [ERRO] Perfil CDP nao encontrado em %CDP_DATA_DIR%\Default
    echo.
    echo Execute primeiro:
    echo   scripts\setup_cdp_profile.bat
    echo.
    echo Esse script copia seu perfil Chrome para a pasta CDP.
    exit /b 2
)

:: Verifica se ja tem Chrome rodando na porta 9222 - evita duplicar
netstat -ano | findstr ":9222" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Chrome CDP ja rodando na porta %DEBUG_PORT%. Nada a fazer.
    exit /b 0
)

echo === Abrindo Chrome CDP ===
echo Chrome    : %CHROME_EXE%
echo Profile   : %CDP_DATA_DIR%
echo Debug port: %DEBUG_PORT%
echo URL       : https://www.magazineluiza.com.br/
echo.

:: --profile-directory=Default     -> pula a tela de selecao de perfil
:: --no-first-run                  -> nao mostra wizard de boas-vindas
:: --no-default-browser-check      -> nao pergunta sobre tornar default
:: --restore-last-session=false    -> nao restaura abas da ultima sessao
:: URL entre aspas duplas pra preservar o ://
start "" "%CHROME_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --user-data-dir="%CDP_DATA_DIR%" ^
    --profile-directory="Default" ^
    --no-first-run ^
    --no-default-browser-check ^
    --restore-last-session=false ^
    "https://www.magazineluiza.com.br/"

:: Aguarda o Chrome subir e a porta ficar disponivel (ate 15s)
echo Aguardando CDP ficar disponivel em :%DEBUG_PORT% ...
set /a TRIES=0
:wait_loop
set /a TRIES+=1
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":9222" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto cdp_ready
if %TRIES% lss 15 goto wait_loop
echo [AVISO] CDP nao respondeu em 15s. Verifique manualmente.
exit /b 3

:cdp_ready
echo [OK] CDP ativo na porta %DEBUG_PORT%.
echo Voce pode usar seu Chrome normal em paralelo sem problemas.

endlocal
