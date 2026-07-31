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
::   3. --force foi passado.
:: Fora isso sai em ~1s, entao pode ficar no caminho da coleta agendada.
::
:: Uso:
::   scripts\ensure_deps.bat            (chamado pela coleta agendada)
::   scripts\ensure_deps.bat --force    (reinstala tudo - usado no sync_windows)
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
if not defined REQ_HASH set "REQ_HASH=sem-hash"

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
"%PYEXE%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo [deps] ERRO: pip install -r requirements.txt falhou
    exit /b 1
)

:: Browsers: baixa so o que falta, entao e barato repetir. O rebrowser tem
:: driver proprio (fork do Playwright) - sem este install o modo CDP do
:: Magalu/Casas Bahia sobe sem chromium e falha no launch.
echo [deps] Verificando browsers do Playwright...
"%PYEXE%" -m playwright install chromium
if errorlevel 1 echo [deps] AVISO: playwright install chromium falhou
"%PYEXE%" -m rebrowser_playwright install chromium
if errorlevel 1 echo [deps] AVISO: rebrowser_playwright install chromium falhou

:: Grava o hash SO com os imports passando: um stamp otimista faria a proxima
:: execucao pular a instalacao justamente com a venv quebrada.
"%PYEXE%" -c "%PROBE%" >nul 2>&1
if errorlevel 1 (
    echo [deps] ERRO: apos instalar, pacotes criticos ainda nao importam.
    "%PYEXE%" -c "%PROBE%"
    exit /b 1
)

> "%STAMP%" echo %REQ_HASH%
echo [deps] Dependencias em dia.
exit /b 0
