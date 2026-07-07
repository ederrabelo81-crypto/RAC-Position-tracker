@echo off
:: -----------------------------------------------------------------------------
:: collect_authenticated_cdp.bat - Coleta AUTENTICADA: Magalu + Shopee + Casas Bahia.
::
:: Substitui as coletas manuais dos 3 marketplaces bloqueados por antibot:
::   1. Valida o Chrome CDP (mesmo da coleta Magalu - start_chrome_cdp.bat)
::   2. Renova as sessoes Shopee/Casas Bahia direto do Chrome real (CDP)
::   3. Roda a coleta dos 3 marketplaces (Magalu via CDP, Shopee/CB via
::      curl_cffi com os cookies recem-renovados)
::   4. Faz upload automatico do CSV para o Supabase
::
:: PRE-REQUISITO: Chrome rodando com porta de debug (use start_chrome_cdp.bat)
::                e perfil com login na Shopee (1x, manual).
::
:: Uso:
::   scripts\collect_authenticated_cdp.bat              -> 2 paginas (turno manha)
::   scripts\collect_authenticated_cdp.bat 1            -> 1 pagina (turno noite)
::   scripts\collect_authenticated_cdp.bat 2 alta       -> 2 paginas, prioridade alta
:: -----------------------------------------------------------------------------

setlocal enabledelayedexpansion

set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "PAGES=%~1"
if "%PAGES%"=="" set "PAGES=2"

:: Prioridades: aceita ate 3 valores (ex: "2 alta media" -> --priority alta media)
set "PRIORITY=%~2"
if not "%~3"=="" set "PRIORITY=%PRIORITY% %~3"
if not "%~4"=="" set "PRIORITY=%PRIORITY% %~4"

set "MAGALU_CDP_URL=http://localhost:9222"
set "RAC_CDP_URL=http://localhost:9222"

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

:: Renova sessoes Shopee + Casas Bahia no Chrome real (sem intervencao humana)
echo.
echo === Renovando sessoes via CDP (shopee, casasbahia) ===
python scripts\refresh_sessions_cdp.py --sites shopee casasbahia
if errorlevel 1 (
    echo [WARN] Refresh parcial de sessoes - coleta segue best-effort
)

echo.
echo === Coleta autenticada: magalu shopee casasbahia - %PAGES% pagina(s) ===
echo.

if "%PRIORITY%"=="" (
    python main.py --platforms magalu shopee casasbahia --pages %PAGES%
) else (
    python main.py --platforms magalu shopee casasbahia --pages %PAGES% --priority %PRIORITY%
)
set "COLLECT_EXIT=%ERRORLEVEL%"

echo.
echo === Coleta concluida (exit=%COLLECT_EXIT%) ===

:: Upload automatico do CSV mais recente.
:: IMPORTANTE: usa !VAR! (delayed expansion) em vez de %VAR% porque o cmd
:: expande %VAR% no PARSE time do bloco if (...), nao em runtime.
if %COLLECT_EXIT% EQU 0 (
    echo.
    echo === Upload do CSV para Supabase ===
    set "LATEST_CSV="
    for /f "delims=" %%F in ('dir /b /od /a-d "output\rac_monitoramento_*.csv" 2^>nul') do set "LATEST_CSV=output\%%F"
    if defined LATEST_CSV (
        echo CSV: !LATEST_CSV!
        python scripts\upload_csv.py "!LATEST_CSV!"
    ) else (
        echo [WARN] Nenhum CSV encontrado em output\
    )
)

:: endlocal & exit na MESMA linha: %COLLECT_EXIT% e expandido no parse,
:: antes do endlocal limpar as variaveis locais
endlocal & exit /b %COLLECT_EXIT%
