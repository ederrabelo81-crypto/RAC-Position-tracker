@echo off
:: ════════════════════════════════════════════════════════════════════════════
:: RAC Position Tracker — Sync Windows
:: Coloca o PC coletor em dia: repositorio, dependencias Python (+ browsers),
:: dependencias Node e a configuracao do Google Drive (destino do historico e
:: dos CSVs). Execute uma vez apos clonar e sempre que quiser um catch-up
:: manual — a coleta agendada ja faz `git pull` + `ensure_deps` sozinha.
::
:: Uso: clique duplo ou execute no Prompt de Comando
:: Requisitos: git, python (3.10+), node (18+), npm
:: ════════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

:: Raiz do projeto = pasta pai deste script. NAO voltar a fixar o caminho
:: ("C:\Users\<nome>\Downloads\..."): o script morre em qualquer outra maquina
:: ou se a pasta for movida, e o erro aparece como "diretorio nao encontrado".
for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
set "LOG=%BASE_DIR%\logs\sync.log"

cd /d "%BASE_DIR%" 2>nul || (
    echo ERRO: diretorio nao encontrado: %BASE_DIR%
    pause
    exit /b 1
)
if not exist logs mkdir logs

echo.
echo ========================================
echo   RAC - Sync Windows
echo   %DATE% %TIME%
echo   %BASE_DIR%
echo ========================================
echo.

echo [%DATE% %TIME%] === Iniciando sync Windows === >> "%LOG%"

:: ── 1. Git pull ─────────────────────────────────────────────────────────────
echo [1/6] Atualizando repositorio via git pull...
git pull origin main >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo       AVISO: git pull falhou ^(sem internet?^). Continuando com codigo local.
    echo [%DATE% %TIME%] WARN: git pull falhou (exit=%ERRORLEVEL%) >> "%LOG%"
) else (
    echo       OK - commit atual: & git rev-parse --short HEAD
    for /f "tokens=*" %%i in ('git rev-parse --short HEAD') do (
        echo [%DATE% %TIME%] git pull OK - commit: %%i >> "%LOG%"
    )
)

:: ── 2. Ambiente virtual Python ───────────────────────────────────────────────
echo.
echo [2/6] Verificando ambiente virtual Python (.venv)...
if exist ".venv\Scripts\activate.bat" (
    echo       .venv ja existe.
) else if exist "venv\Scripts\activate.bat" (
    echo       venv ^(nome antigo^) encontrada - usando ela.
) else (
    echo       Criando novo ambiente virtual...
    python -m venv .venv >> "%LOG%" 2>&1
    if !ERRORLEVEL! neq 0 (
        echo       ERRO: falha ao criar .venv. Python instalado?
        pause & exit /b 1
    )
    echo       Ambiente virtual criado com sucesso.
    echo [%DATE% %TIME%] .venv criado >> "%LOG%"
)

:: ── 3. Dependências Python (+ browsers) ──────────────────────────────────────
:: Delegado ao ensure_deps.bat — MESMO script que a coleta agendada usa, para
:: que "atualizei na mao" e "a tarefa atualizou sozinha" nunca divirjam. Ele
:: tambem grava o hash do requirements.txt, poupando a proxima execucao.
echo.
echo [3/6] Instalando/atualizando dependencias Python (pode demorar)...
call "%BASE_DIR%\scripts\ensure_deps.bat" --force >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo       AVISO: dependencias Python com erro. Veja %LOG%
    echo [%DATE% %TIME%] WARN: ensure_deps --force falhou >> "%LOG%"
) else (
    echo       Python OK ^(requirements + browsers Playwright^).
    echo [%DATE% %TIME%] ensure_deps --force OK >> "%LOG%"
)

:: ── 4. Verificação dos imports ───────────────────────────────────────────────
echo.
echo [4/6] Verificando imports Python...

set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"

"%PYEXE%" -c "import playwright, pandas, supabase; print('      Python: imports OK')" 2>>"%LOG%"
if %ERRORLEVEL% neq 0 echo       ERRO: imports Python falharam. Veja %LOG%

:: ── 5. Google Drive (destino do historico e dos CSVs) ────────────────────────
:: O que faltava neste PC: sem GDRIVE_* no .env, o historico e gravado em
:: data\history e o CSV fica so em output\ - se a maquina sumir, o dia some
:: com ela (e com o Supabase restrito por cota nao ha outra copia).
::
:: Vem ANTES do Node de proposito: e a verificacao que motiva rodar este
:: script, e o passo do Node (fallback da Shopee) nao pode atrasa-la nem
:: esconde-la quando falha.
echo.
echo [5/6] Verificando o destino do historico (Google Drive)...
if not exist ".env" (
    echo       ERRO: .env nao existe. Copie o .env.example e preencha.
    goto :drive_help
)
findstr /r /c:"^ *GDRIVE_FOLDER_ID *= *[^ ]" ".env" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo       AVISO: .env sem GDRIVE_FOLDER_ID - o historico esta indo para o disco local.
    goto :drive_help
)
echo       GDRIVE_FOLDER_ID presente. Testando ida e volta no Drive...
"%PYEXE%" scripts\gdrive_setup.py --check
if %ERRORLEVEL% neq 0 (
    echo       ERRO: o teste do Drive falhou ^(veja as mensagens acima^).
    goto :drive_help
)
echo       Drive OK - historico vai para o Drive.
:: O espelho do CSV e um interruptor separado: dizer "e os CSVs tambem" com
:: RAC_DRIVE_CSV=off seria relatar algo que a coleta nao faz. findstr aceita
:: varios /c: como OU logico - cobre as formas que csv_mirror_enabled() nega.
findstr /i /r /c:"^ *RAC_DRIVE_CSV *= *off" /c:"^ *RAC_DRIVE_CSV *= *0" /c:"^ *RAC_DRIVE_CSV *= *false" /c:"^ *RAC_DRIVE_CSV *= *no" ".env" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo       CSV cru: NAO espelhado ^(RAC_DRIVE_CSV desligado no .env^) - fica so em output\.
) else (
    echo       CSV cru: espelhado no Drive em csv_coletas\.
)
goto :node

:drive_help
echo.
echo       Como configurar ^(uma vez, ~3 min^):
echo         1. Baixe o JSON do "ID do cliente OAuth" ^(tipo: App para computador^)
echo            em https://console.cloud.google.com/ ^(ative a Google Drive API^).
echo         2. python scripts\gdrive_setup.py --client-secrets "%USERPROFILE%\Downloads"
echo            ^(a pasta do download basta - o script acha o client_secret*.json.
echo            As aspas importam: seu caminho tem espaco no nome^)
echo         3. Cole no .env as 5 linhas que ele imprime ^(GDRIVE_* + RAC_HISTORY_BACKEND^)
echo         4. python scripts\gdrive_setup.py --check
echo       Passo a passo: docs\HISTORICO_DRIVE.md
echo [%DATE% %TIME%] WARN: Drive nao configurado/validado >> "%LOG%"

:node
:: ── 6. Dependências Node.js (magalu_shopee) ──────────────────────────────────
:: `call npm`: npm no Windows e npm.cmd, e chamar um .cmd de dentro de um .bat
:: SEM `call` transfere o controle de vez - quando o npm termina, este script
:: termina com ele. Era por isso que o sync morria aqui e os passos seguintes
:: (inclusive a verificacao do Drive) nunca rodavam, sem erro nenhum na tela.
echo.
echo [6/6] Instalando/atualizando dependencias Node.js (magalu_shopee)...
if not exist "magalu_shopee\package.json" (
    echo       AVISO: magalu_shopee\package.json nao encontrado. Pulando Node.js.
) else (
    cd /d "%BASE_DIR%\magalu_shopee"
    call npm install --silent >> "%LOG%" 2>&1
    if !ERRORLEVEL! neq 0 (
        echo       AVISO: npm install falhou - veja o fim de %LOG%.
        echo              Nao bloqueia a coleta: o Node aqui e so o fallback da Shopee.
        echo [%DATE% %TIME%] WARN: npm install com erros >> "%LOG%"
    ) else (
        echo       Node.js OK.
        echo [%DATE% %TIME%] npm install OK >> "%LOG%"
    )
    cd /d "%BASE_DIR%"
)
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
echo Proximos passos:
echo   - Diagnostico do agendamento: PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
echo   - (Re)registrar tarefas:      PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
echo Log completo: %LOG%
echo.

echo [%DATE% %TIME%] === Sync Windows concluido === >> "%LOG%"

pause
endlocal
