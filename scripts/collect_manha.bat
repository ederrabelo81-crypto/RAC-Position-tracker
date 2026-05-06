@echo off
:: RAC Price Collector — Coleta Manha (10:00 BRT)
:: Plataformas: ML + Google Shopping + Amazon + Leroy + Dealers (Python) | Magalu (Node.js)
:: Prioridade: alta + media | Paginas: 2

setlocal
set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "LOG=%BASE_DIR%\logs\scheduler.log"

cd /d "%BASE_DIR%"
if not exist logs mkdir logs

echo [%DATE% %TIME%] === Iniciando coleta manha === >> "%LOG%"

:: ── Python: ML + Google Shopping + Amazon + Leroy + Dealers ─────────────────
echo [%DATE% %TIME%] Python: ml google_shopping amazon leroy dealers (2 paginas)... >> "%LOG%"
python main.py --platforms ml google_shopping amazon leroy dealers --pages 2 --priority alta media >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Python concluido (exit=%ERRORLEVEL%) >> "%LOG%"

:: ── Node.js: Magalu ──────────────────────────────────────────────────────────
echo [%DATE% %TIME%] Node.js: magalu (2 paginas)... >> "%LOG%"
cd /d "%BASE_DIR%\magalu_shopee"
node node_modules\.bin\ts-node src\index.ts --platforms magalu --pages 2 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Node.js Magalu concluido (exit=%ERRORLEVEL%) >> "%LOG%"

echo [%DATE% %TIME%] === Coleta manha concluida === >> "%LOG%"
endlocal
