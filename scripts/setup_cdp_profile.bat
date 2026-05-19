@echo off
:: -----------------------------------------------------------------------------
:: setup_cdp_profile.bat - Wrapper que chama o script PowerShell equivalente.
::
:: Mantido por compatibilidade com chamadas tipo "scripts\setup_cdp_profile.bat".
:: A logica real esta em setup_cdp_profile.ps1 (PowerShell trata JSON melhor).
::
:: Uso:
::   scripts\setup_cdp_profile.bat               -> auto-detecta perfil "Eder"
::   scripts\setup_cdp_profile.bat "Eder"        -> mesmo padrao
::   scripts\setup_cdp_profile.bat "Lumina"      -> outro perfil
:: -----------------------------------------------------------------------------

setlocal

set "PS_SCRIPT=%~dp0setup_cdp_profile.ps1"

if not exist "%PS_SCRIPT%" (
    echo [ERRO] Script PowerShell nao encontrado: %PS_SCRIPT%
    exit /b 1
)

if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ProfileName "%~1"
)

endlocal
exit /b %errorlevel%
