@echo off
:: RAC Price Collector — Coleta Noite (21:00 BRT)
:: Plataformas: ML (Playwright — IP residencial necessario)
:: Prioridade: alta | Paginas: 1
:: Oracle VM ja cuida de: google_shopping, amazon, leroy, dealers, magalu

setlocal
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "LOG=%BASE_DIR%\logs\scheduler.log"

cd /d "%BASE_DIR%"
if not exist logs mkdir logs

echo [%DATE% %TIME%] === Iniciando coleta noite (ML) === >> "%LOG%"

:: Ativa ambiente virtual
if not exist ".venv\Scripts\activate.bat" (
    echo [%DATE% %TIME%] ERRO: .venv nao encontrado. Execute sync_windows.bat primeiro. >> "%LOG%"
    exit /b 1
)
call .venv\Scripts\activate.bat

:: ── Python: ML ───────────────────────────────────────────────────────────────
echo [%DATE% %TIME%] Python: ml (1 pagina, alta)... >> "%LOG%"
python main.py --platforms ml --pages 1 --priority alta >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Python concluido (exit=%ERRORLEVEL%) >> "%LOG%"

echo [%DATE% %TIME%] === Coleta noite concluida === >> "%LOG%"
endlocal
