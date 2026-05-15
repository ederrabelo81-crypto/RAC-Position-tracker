@echo off
:: RAC Price Collector - Teste manual Magalu (Playwright browser persistente)
:: Roda o scraper Python Magalu — agora com browser persistente pra bypassar
:: o sensor.js do Akamai.
::
:: Uso:
::   scripts\test_magalu.bat                    -> 1 pagina, browser visivel (RECOMENDADO)
::   scripts\test_magalu.bat 2                  -> 2 paginas, browser visivel
::   scripts\test_magalu.bat 1 alta             -> 1 pagina, priority alta
::   scripts\test_magalu.bat 1 "" headless      -> 1 pagina, browser headless (mais provavel falhar)
::
:: Dica: browser visivel (default) passa muito mais facil pelo Akamai.
:: Em headless, sensor.js detecta automacao e bloqueia /busca/.

setlocal
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "PAGES=%~1"
set "PRIORITY=%~2"
set "MODE=%~3"
if "%PAGES%"=="" set "PAGES=1"

:: Default: browser visivel (passa pelo sensor.js do Akamai com muito mais facilidade).
:: Use o 3o argumento "headless" pra forcar headless (debug ou Oracle VM).
if /i "%MODE%"=="headless" (
    set "MAGALU_HEADLESS=true"
    set "MODE_LABEL=headless"
) else (
    set "MAGALU_HEADLESS=false"
    set "MODE_LABEL=visible"
)

cd /d "%BASE_DIR%"

:: Ativa ambiente virtual
if not exist ".venv\Scripts\activate.bat" (
    echo [ERRO] .venv nao encontrado. Execute sync_windows.bat primeiro.
    exit /b 1
)
call .venv\Scripts\activate.bat

:: Verifica curl_cffi instalado
python -c "import curl_cffi" 2>nul
if errorlevel 1 (
    echo [INFO] Instalando curl-cffi...
    pip install curl-cffi^>=0.6.0
)

:: Verifica Playwright Chromium instalado
python -c "from playwright.sync_api import sync_playwright" 2>nul
if errorlevel 1 (
    echo [INFO] Instalando playwright...
    pip install playwright
    python -m playwright install chromium
)

echo === Teste Magalu (browser %MODE_LABEL%) - %PAGES% pagina(s) ===
echo MAGALU_HEADLESS=%MAGALU_HEADLESS%
echo.

:: Limpa cache de sessao pra garantir reuso correto
if exist "data\magalu_session.json" del /q "data\magalu_session.json" 2>nul

if "%PRIORITY%"=="" (
    python main.py --platforms magalu --pages %PAGES%
) else (
    python main.py --platforms magalu --pages %PAGES% --priority %PRIORITY%
)

set "EXIT=%ERRORLEVEL%"
echo.
echo === Teste concluido (exit=%EXIT%) ===
echo CSV salvo em: %BASE_DIR%\output\
echo Logs em:      %BASE_DIR%\logs\
endlocal
exit /b %EXIT%
