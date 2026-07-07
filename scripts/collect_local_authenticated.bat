@echo off
:: -----------------------------------------------------------------------------
:: collect_local_authenticated.bat - Coleta AUTENTICADA no notebook do usuario.
::
:: Roda Magalu + Shopee + Casas Bahia usando UM Chrome real, persistente e
:: LOGADO (perfil dedicado do projeto: data\chrome_profile), com o IP
:: residencial do notebook. Substitui a coleta manual via extensao do Chrome.
::
:: Diferente da abordagem antiga (CDP + perfil copiado), aqui:
::   - NAO copia o perfil (a copia deslogava as contas).
::   - NAO usa --remote-debugging-port (o Chrome 136+ ignora isso no perfil
::     padrao - era a causa de "liguei o CDP e nao conectou").
::   - O proprio Python abre o Chrome persistente logado.
::
:: PRE-REQUISITO (uma vez):
::   python scripts\setup_local_profile.py   -> abra e FACA LOGIN na Shopee.
::
:: Uso:
::   scripts\collect_local_authenticated.bat            -> 2 paginas
::   scripts\collect_local_authenticated.bat 1          -> 1 pagina
::   scripts\collect_local_authenticated.bat 2 alta media
:: -----------------------------------------------------------------------------

setlocal enabledelayedexpansion

:: Raiz do projeto = pasta pai deste script
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"

set "PAGES=%~1"
if "%PAGES%"=="" set "PAGES=2"

set "PRIORITY=%~2"
if not "%~3"=="" set "PRIORITY=%PRIORITY% %~3"
if not "%~4"=="" set "PRIORITY=%PRIORITY% %~4"

:: Liga o modo Chrome local logado para TODA a coleta.
set "RAC_LOCAL_CHROME=1"

:: Ativa a venv (aceita .venv ou venv)
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [AVISO] Nenhuma venv encontrada (.venv/venv) - usando Python do sistema.
)

:: A coleta abre o Chrome comum e conecta via CDP sozinha (RAC_LOCAL_CHROME). Se a Shopee
:: nao estiver logada, o scraper avisa nos logs. Para logar/checar:
::   python scripts\setup_local_profile.py           (login na Shopee)
::   python scripts\setup_local_profile.py --check    (status do login)

echo.
echo === Coleta local autenticada: magalu shopee casasbahia - %PAGES% pagina(s) ===
echo.

:: Ruido do driver Node do rebrowser (stderr) vai pra um arquivo, deixando o
:: console limpo com os logs da coleta (stdout). O arquivo fica pra debug.
:: Cria a pasta ANTES: o cmd resolve o redirect 2>> antes de rodar o python,
:: entao sem a pasta o comando falha com "path specified".
if not exist "logs" mkdir "logs"
set "DRIVER_LOG=logs\driver_stderr.log"

if "%PRIORITY%"=="" (
    python main.py --platforms magalu shopee casasbahia --pages %PAGES% 2>>"%DRIVER_LOG%"
) else (
    python main.py --platforms magalu shopee casasbahia --pages %PAGES% --priority %PRIORITY% 2>>"%DRIVER_LOG%"
)
set "COLLECT_EXIT=%ERRORLEVEL%"

echo.
echo === Coleta concluida (exit=%COLLECT_EXIT%) ===

:: Upload do CSV mais recente (main.py ja sobe pro Supabase se .env tiver as
:: credenciais; este passo e um reforco para quem coleta sem SUPABASE_* no .env)
if %COLLECT_EXIT% EQU 0 (
    if exist "scripts\upload_csv.py" (
        set "LATEST_CSV="
        for /f "delims=" %%F in ('dir /b /od /a-d "output\rac_monitoramento_*.csv" 2^>nul') do set "LATEST_CSV=output\%%F"
        if defined LATEST_CSV (
            echo === Upload do CSV para Supabase: !LATEST_CSV! ===
            python scripts\upload_csv.py "!LATEST_CSV!"
        )
    )
)

endlocal & exit /b %COLLECT_EXIT%
