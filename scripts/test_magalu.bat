@echo off
:: RAC Price Collector — Teste manual Magalu (curl_cffi, sem browser)
:: Roda 1 pagina do scraper Python novo pra validar bypass do Akamai.
::
:: Uso:
::   scripts\test_magalu.bat                    -> 1 pagina, sem priority filter
::   scripts\test_magalu.bat 2                  -> 2 paginas
::   scripts\test_magalu.bat 1 alta             -> 1 pagina, so prioridade alta
::
:: O scraper usa curl_cffi com TLS chrome impersonation — bypassa o Akamai
:: que bloqueava o Puppeteer (magalu_shopee/).

setlocal
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "PAGES=%~1"
set "PRIORITY=%~2"
if "%PAGES%"=="" set "PAGES=1"

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

echo === Teste Magalu Python (curl_cffi) — %PAGES% pagina(s) ===
echo.

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
