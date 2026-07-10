@echo off
:: RAC Price Collector — Coleta Manha (10:00 BRT)
:: Plataformas: ML + Shopee (Playwright/curl_cffi — IP residencial necessario)
:: Prioridade: alta + media | Paginas: 2
:: Oracle VM ja cuida de: google_shopping, amazon, leroy, dealers, magalu, casasbahia
:: Shopee roda aqui (IP residencial BR + sessao capturada localmente)

setlocal
:: Força UTF-8 no Python (evita UnicodeEncodeError do log em cp1252 ao
:: redirecionar stdout para o scheduler.log — caracteres "→", acentos etc.)
set "PYTHONUTF8=1"
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "LOG=%BASE_DIR%\logs\scheduler.log"

cd /d "%BASE_DIR%"
if not exist logs mkdir logs

echo [%DATE% %TIME%] === Iniciando coleta manha (ML) === >> "%LOG%"

:: Ativa ambiente virtual
if not exist ".venv\Scripts\activate.bat" (
    echo [%DATE% %TIME%] ERRO: .venv nao encontrado. Execute sync_windows.bat primeiro. >> "%LOG%"
    exit /b 1
)
call .venv\Scripts\activate.bat

:: ── Python: ML (+ Shopee se houver sessao capturada) ─────────────────────────
:: Shopee (API v4) precisa da sessao SPC_*/csrftoken — capture com:
::   python utils\session_grabber.py --site shopee
set "PLATFORMS=ml"
if exist "%BASE_DIR%\utils\sessions\shopee.json" (
    set "PLATFORMS=%PLATFORMS% shopee"
    echo [%DATE% %TIME%] Shopee: sessao encontrada - incluida na coleta >> "%LOG%"
) else (
    echo [%DATE% %TIME%] Shopee: sem sessao - pulando >> "%LOG%"
)
echo [%DATE% %TIME%] Python: %PLATFORMS% (2 paginas, alta+media)... >> "%LOG%"
python main.py --platforms %PLATFORMS% --pages 2 --priority alta media >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Python concluido (exit=%ERRORLEVEL%) >> "%LOG%"

echo [%DATE% %TIME%] === Coleta manha concluida === >> "%LOG%"
endlocal
