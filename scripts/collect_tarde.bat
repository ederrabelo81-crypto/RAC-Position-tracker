@echo off
:: RAC Price Collector — Coleta Noite (21:00 BRT)
:: Plataformas: ML + Google Shopping + Amazon + Leroy + Dealers (Python) | Magalu (Node.js)
:: Prioridade: alta | Paginas: 1

setlocal
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "LOG=%BASE_DIR%\logs\scheduler.log"

cd /d "%BASE_DIR%"
if not exist logs mkdir logs

echo [%DATE% %TIME%] === Iniciando coleta noite === >> "%LOG%"

:: ── Python: ML + Google Shopping + Amazon + Leroy + Dealers ─────────────────
echo [%DATE% %TIME%] Python: ml google_shopping amazon leroy dealers (1 pagina)... >> "%LOG%"
python main.py --platforms ml google_shopping amazon leroy dealers --pages 1 --priority alta >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Python concluido (exit=%ERRORLEVEL%) >> "%LOG%"

:: ── Node.js: Magalu ──────────────────────────────────────────────────────────
echo [%DATE% %TIME%] Node.js: magalu (1 pagina)... >> "%LOG%"
cd /d "%BASE_DIR%\magalu_shopee"
node node_modules\.bin\ts-node src\index.ts --platforms magalu --pages 1 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Node.js Magalu concluido (exit=%ERRORLEVEL%) >> "%LOG%"

echo [%DATE% %TIME%] === Coleta noite concluida === >> "%LOG%"
endlocal
