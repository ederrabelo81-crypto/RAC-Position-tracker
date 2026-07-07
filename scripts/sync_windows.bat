@echo off
:: ════════════════════════════════════════════════════════════════════════════
:: RAC Position Tracker — Sync Windows
:: Sincroniza o repositório local com o GitHub e atualiza TODAS as dependências
:: (Python + Node.js). Execute uma vez após clonar e sempre que o repo mudar.
::
:: Uso: clique duplo ou execute no Prompt de Comando
:: Requisitos: git, python (3.10+), node (18+), npm
:: ════════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

set "BASE_DIR=C:\Users\Eder Rabelo\Downloads\rac-position-tracker"
set "LOG=%BASE_DIR%\logs\sync.log"

:: ── Garante pasta de logs ───────────────────────────────────────────────────
cd /d "%BASE_DIR%" 2>nul || (
    echo ERRO: diretorio nao encontrado: %BASE_DIR%
    echo Ajuste BASE_DIR neste script e tente novamente.
    pause
    exit /b 1
)
if not exist logs mkdir logs

echo.
echo ========================================
echo   RAC — Sync Windows
echo   %DATE% %TIME%
echo ========================================
echo.

echo [%DATE% %TIME%] === Iniciando sync Windows === >> "%LOG%"

:: ── 1. Git pull ─────────────────────────────────────────────────────────────
echo [1/5] Atualizando repositorio via git pull...
git pull origin main >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo       AVISO: git pull falhou ^(sem internet?^). Continuando com codigo local.
    echo [%DATE% %TIME%] WARN: git pull falhou (exit=%ERRORLEVEL%) >> "%LOG%"
) else (
    echo       OK — commit atual: & git rev-parse --short HEAD
    for /f "tokens=*" %%i in ('git rev-parse --short HEAD') do (
        echo [%DATE% %TIME%] git pull OK — commit: %%i >> "%LOG%"
    )
)

:: ── 2. Ambiente virtual Python ───────────────────────────────────────────────
echo.
echo [2/5] Verificando ambiente virtual Python (.venv)...
if not exist ".venv\Scripts\activate.bat" (
    echo       Criando novo ambiente virtual...
    python -m venv .venv >> "%LOG%" 2>&1
    if %ERRORLEVEL% neq 0 (
        echo       ERRO: falha ao criar .venv. Python instalado?
        pause & exit /b 1
    )
    echo       Ambiente virtual criado com sucesso.
    echo [%DATE% %TIME%] .venv criado >> "%LOG%"
) else (
    echo       .venv ja existe.
)

:: ── 3. Dependências Python ───────────────────────────────────────────────────
echo.
echo [3/5] Instalando/atualizando dependencias Python...
call .venv\Scripts\activate.bat
pip install --upgrade pip --quiet >> "%LOG%" 2>&1
pip install -r requirements.txt --quiet >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo       AVISO: algumas dependencias Python falharam. Veja %LOG%
    echo [%DATE% %TIME%] WARN: pip install com erros >> "%LOG%"
) else (
    echo       Python OK.
    echo [%DATE% %TIME%] pip install OK >> "%LOG%"
)

:: Instala browsers Playwright (só baixa se necessário)
echo       Instalando browsers Playwright (pode demorar na primeira vez)...
python -m playwright install chromium >> "%LOG%" 2>&1
echo [%DATE% %TIME%] playwright install chromium OK >> "%LOG%"

:: ── 4. Dependências Node.js (magalu_shopee) ──────────────────────────────────
echo.
echo [4/5] Instalando/atualizando dependencias Node.js (magalu_shopee)...
if not exist "magalu_shopee\package.json" (
    echo       AVISO: magalu_shopee\package.json nao encontrado. Pulando Node.js.
) else (
    cd /d "%BASE_DIR%\magalu_shopee"
    npm install --silent >> "%LOG%" 2>&1
    if %ERRORLEVEL% neq 0 (
        echo       AVISO: npm install falhou. Veja %LOG%
        echo [%DATE% %TIME%] WARN: npm install com erros >> "%LOG%"
    ) else (
        echo       Node.js OK.
        echo [%DATE% %TIME%] npm install OK >> "%LOG%"
    )
    cd /d "%BASE_DIR%"
)

:: ── 5. Verificação final ─────────────────────────────────────────────────────
echo.
echo [5/5] Verificacao final...

:: Testa importação Python rápida
.venv\Scripts\python.exe -c "import playwright, pandas, supabase; print('Python: imports OK')" 2>>"%LOG%"
if %ERRORLEVEL% neq 0 (
    echo       ERRO: imports Python falharam. Veja %LOG%
) else (
    echo       Python imports OK.
)

:: Testa Node
if exist "magalu_shopee\node_modules\.bin\ts-node" (
    echo       Node.js ts-node OK.
) else (
    echo       AVISO: ts-node nao encontrado em magalu_shopee\node_modules.
)

echo.
echo ========================================
echo   Sync concluido! %DATE% %TIME%
echo ========================================
echo.
echo Proximo passo: execute collect_manha.bat ou collect_tarde.bat
echo Log completo: %LOG%
echo.

echo [%DATE% %TIME%] === Sync Windows concluido === >> "%LOG%"

pause
endlocal
