@echo off
:: -----------------------------------------------------------------------------
:: ensure_deps.bat - Mantem as dependencias Python do notebook em dia.
::
:: Causa raiz que este script fecha: as tarefas RAC_Local_* fazem `git pull`
:: (run_local_scheduled.bat) mas NUNCA rodavam `pip install`. Toda dependencia
:: nova que entrou no requirements.txt depois do ultimo sync_windows.bat manual
:: simplesmente nao existia na venv - foi assim que o notebook ficou sem as
:: libs do Google Drive (google-api-python-client / google-auth-oauthlib) e o
:: historico caiu no backend local, gravando so em C:\...\data\history.
::
:: Quando instala (qualquer uma basta):
::   1. o requirements.txt mudou desde a ultima instalacao (hash SHA256), ou
::   2. algum pacote critico nao importa na venv (drift real, nao teorico), ou
::   3. o hash nao pode ser calculado (sem PowerShell) - instala por precaucao, ou
::   4. --force foi passado.
:: Fora isso sai em ~1s, entao pode ficar no caminho da coleta agendada.
::
:: O stamp (logs\deps_state.txt) e gravado SO quando tudo verificou: imports
:: passando, browsers instalados e hash real em maos. Stamp otimista e pior que
:: stamp ausente - ele faz a proxima execucao pular justamente o conserto.
::
:: Uso:
::   scripts\ensure_deps.bat            (chamado pela coleta agendada)
::   scripts\ensure_deps.bat --force    (sync manual: reinstala com --upgrade)
::
:: Exit: 0 = venv em dia; 1 = instalacao falhou (o chamador decide se segue).
:: -----------------------------------------------------------------------------

setlocal enabledelayedexpansion

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%" 2>nul || (
    echo [deps] ERRO: nao consegui entrar em "%BASE_DIR%"
    exit /b 1
)
if not exist logs mkdir logs

set "FORCE="
if /i "%~1"=="--force" set "FORCE=1"
if /i "%~1"=="-f" set "FORCE=1"

if not exist "requirements.txt" (
    echo [deps] ERRO: requirements.txt nao encontrado em "%BASE_DIR%"
    exit /b 1
)

:: Mesma ordem de preferencia dos demais .bat do projeto (.venv > venv > sistema)
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
echo [deps] Python: %PYEXE%

:: Pacotes que, se faltarem, quebram um caminho inteiro do pipeline em silencio:
::   pyarrow / googleapiclient / google_auth_oauthlib -> historico no Drive
::   rebrowser_playwright                            -> Akamai (Magalu/CB)
::   curl_cffi                                       -> Casas Bahia / Shopee
::   openpyxl                                        -> import PriceTrack
set "PROBE=import pyarrow, googleapiclient, google_auth_oauthlib, google.oauth2, rebrowser_playwright, curl_cffi, supabase, pandas, filelock, openpyxl, playwright"

set "STAMP=logs\deps_state.txt"

:: Hash via PowerShell: o cmd.exe nao tem como calcular SHA256 sozinho.
set "REQ_HASH="
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 requirements.txt).Hash" 2^>nul') do set "REQ_HASH=%%H"

set "OLD_HASH="
if exist "%STAMP%" set /p OLD_HASH=<"%STAMP%"

"%PYEXE%" -c "%PROBE%" >nul 2>&1
if errorlevel 1 (set "PROBE_FAIL=1") else (set "PROBE_FAIL=")

if defined FORCE (
    echo [deps] --force: reinstalando dependencias
    goto :install
)
if defined PROBE_FAIL (
    echo [deps] pacote critico ausente na venv - instalando requirements.txt
    goto :install
)
:: Sem hash nao ha como saber se o requirements.txt mudou. Instalar e o lado
:: seguro do erro - e o stamp NAO e gravado (ver :install), senao um valor
:: sentinela viraria "hash igual" e congelaria as dependencias para sempre.
if not defined REQ_HASH (
    echo [deps] AVISO: nao consegui o hash do requirements.txt - instalando por precaucao
    goto :install
)
if /i "%REQ_HASH%"=="%OLD_HASH%" (
    echo [deps] requirements.txt sem mudanca e imports OK - nada a fazer
    exit /b 0
)
echo [deps] requirements.txt mudou desde a ultima instalacao - atualizando
echo [deps]   antes: %OLD_HASH%
echo [deps]   agora: %REQ_HASH%
goto :install

:install
"%PYEXE%" -m pip install --upgrade pip --quiet --disable-pip-version-check

:: --upgrade SO no --force (sync manual, com alguem olhando a saida). No
:: caminho agendado o objetivo e "convergir para o requirements.txt", nao
:: "pegar a ultima versao de tudo": os pisos sao >= e um `--upgrade` diario
:: subiria playwright/pandas major sem ninguem pedir, do jeito mais caro de
:: descobrir (coleta noturna quebrada). O pip ja atualiza sozinho o pacote
:: cujo piso subiu no requirements.txt - que e o caso que este script existe
:: para cobrir.
if defined FORCE (
    "%PYEXE%" -m pip install --upgrade -r requirements.txt --disable-pip-version-check
) else (
    "%PYEXE%" -m pip install -r requirements.txt --disable-pip-version-check
)
if errorlevel 1 (
    echo [deps] ERRO: pip install -r requirements.txt falhou
    exit /b 1
)

:: Browsers: baixa so o que falta, entao e barato repetir. O rebrowser tem
:: driver proprio (fork do Playwright) - sem este install o modo CDP do
:: Magalu/Casas Bahia sobe sem chromium e falha no launch.
echo [deps] Verificando browsers do Playwright...
set "BROWSER_FAIL="
"%PYEXE%" -m playwright install chromium
if errorlevel 1 (
    set "BROWSER_FAIL=1"
    echo [deps] AVISO: playwright install chromium falhou
)
"%PYEXE%" -m rebrowser_playwright install chromium
if errorlevel 1 (
    set "BROWSER_FAIL=1"
    echo [deps] AVISO: rebrowser_playwright install chromium falhou
)

:: Grava o hash SO com os imports passando: um stamp otimista faria a proxima
:: execucao pular a instalacao justamente com a venv quebrada.
"%PYEXE%" -c "%PROBE%" >nul 2>&1
if errorlevel 1 (
    echo [deps] ERRO: apos instalar, pacotes criticos ainda nao importam.
    "%PYEXE%" -c "%PROBE%"
    exit /b 1
)

:: Chromium ausente derruba a coleta no launch, entao browser que nao instalou
:: tambem barra o stamp: com ele gravado, a proxima execucao veria "hash igual
:: + imports OK" e nunca tentaria baixar o browser de novo.
if defined BROWSER_FAIL (
    echo [deps] ERRO: browsers do Playwright nao instalaram - stamp NAO gravado,
    echo [deps]       a proxima execucao tenta de novo. Manualmente:
    echo [deps]       "%PYEXE%" -m playwright install chromium
    exit /b 1
)
:: Sem hash nao ha o que gravar: um sentinela ("sem-hash") casaria com ele
:: mesmo na proxima execucao e congelaria as dependencias.
if not defined REQ_HASH (
    echo [deps] Dependencias instaladas, mas sem hash para o stamp - a proxima
    echo [deps]       execucao vai instalar de novo por precaucao.
    exit /b 0
)

> "%STAMP%" echo %REQ_HASH%
echo [deps] Dependencias em dia.
exit /b 0
