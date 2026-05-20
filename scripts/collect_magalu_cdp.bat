@echo off
:: -----------------------------------------------------------------------------
:: collect_magalu_cdp.bat - Coleta Magalu via CDP no Chrome aberto.
::
:: PRE-REQUISITO: Chrome rodando com porta de debug (use start_chrome_cdp.bat).
::
:: Uso:
::   scripts\collect_magalu_cdp.bat              -> 2 paginas (turno manha)
::   scripts\collect_magalu_cdp.bat 1            -> 1 pagina (turno noite)
::   scripts\collect_magalu_cdp.bat 2 alta       -> 2 paginas, prioridade alta
::
:: O upload para o Supabase e feito pelo proprio main.py durante a coleta.
:: -----------------------------------------------------------------------------

setlocal

set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "PAGES=%~1"
set "PRIORITY=%~2"
if "%PAGES%"=="" set "PAGES=2"

set "MAGALU_CDP_URL=http://localhost:9222"

cd /d "%BASE_DIR%"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERRO] .venv nao encontrado. Execute sync_windows.bat primeiro.
    exit /b 1
)
call .venv\Scripts\activate.bat

:: Valida que Chrome com CDP esta acessivel
echo === Verificando Chrome CDP em %MAGALU_CDP_URL% ===
python -c "import urllib.request,sys; urllib.request.urlopen('%MAGALU_CDP_URL%/json/version', timeout=5); print('  OK: CDP respondendo')" 2>nul
if errorlevel 1 (
    echo [ERRO] Chrome CDP nao esta rodando em %MAGALU_CDP_URL%
    echo Execute primeiro: scripts\start_chrome_cdp.bat
    exit /b 2
)

echo.
echo === Coleta Magalu via CDP - %PAGES% pagina(s) ===
echo.

if "%PRIORITY%"=="" (
    python main.py --platforms magalu --pages %PAGES%
) else (
    python main.py --platforms magalu --pages %PAGES% --priority %PRIORITY%
)
set "COLLECT_EXIT=%ERRORLEVEL%"

echo.
echo === Coleta concluida (exit=%COLLECT_EXIT%) ===
echo O upload para o Supabase ja foi feito pelo main.py durante a coleta.

endlocal
exit /b %COLLECT_EXIT%
