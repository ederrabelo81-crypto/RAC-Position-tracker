@echo off
:: -----------------------------------------------------------------------------
:: setup_cdp_profile.bat - Copia o perfil Chrome do usuario para uso CDP.
::
:: Executar UMA VEZ antes do primeiro start_chrome_cdp.bat. Detecta automaticamente
:: em qual slot (Default, Profile 1, Profile 2...) esta o perfil "Eder" lendo o
:: arquivo Preferences de cada slot. Copia esse perfil para C:\chrome-rac-cdp,
:: preservando cookies, extensoes, historico e fingerprint do browser.
::
:: Uso:
::   scripts\setup_cdp_profile.bat            -> auto-detecta perfil "Eder"
::   scripts\setup_cdp_profile.bat "Eder"     -> mesmo padrao
::   scripts\setup_cdp_profile.bat "Profile 1" -> usa slot especifico (override)
::
:: Re-executar: deleta C:\chrome-rac-cdp antes (apaga sessao acumulada).
::   rmdir /s /q C:\chrome-rac-cdp
:: -----------------------------------------------------------------------------

setlocal enabledelayedexpansion

set "USER_DATA_DIR=C:\Users\Eder Rabelo\AppData\Local\Google\Chrome\User Data"
set "CDP_DATA_DIR=C:\chrome-rac-cdp"
set "CDP_PROFILE=%CDP_DATA_DIR%\Default"
set "TARGET_NAME=%~1"
if "%TARGET_NAME%"=="" set "TARGET_NAME=Eder"

echo ===========================================================
echo   Setup CDP Profile - Copia perfil Chrome para C:\chrome-rac-cdp
echo ===========================================================
echo.

:: Valida que pasta base do Chrome existe
if not exist "%USER_DATA_DIR%" (
    echo [ERRO] Pasta do Chrome nao encontrada:
    echo   %USER_DATA_DIR%
    echo Chrome esta instalado e ja foi usado pelo menos uma vez?
    exit /b 1
)

echo Procurando slot do perfil "%TARGET_NAME%"...
echo.

:: PowerShell inline: percorre Default, Profile 1, Profile 2... le o Preferences
:: de cada um e imprime "<slot>" se profile.name == TARGET_NAME.
:: Output capturado pra variavel SOURCE_SLOT.
for /f "usebackq delims=" %%S in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ud = '%USER_DATA_DIR%';" ^
    "$target = '%TARGET_NAME%';" ^
    "Get-ChildItem -LiteralPath $ud -Directory ^| Where-Object { $_.Name -match '^(Default^|Profile \d+)$' } ^| ForEach-Object {" ^
    "  $pref = Join-Path $_.FullName 'Preferences';" ^
    "  if (Test-Path -LiteralPath $pref) {" ^
    "    try { $json = Get-Content -LiteralPath $pref -Raw ^| ConvertFrom-Json;" ^
    "          if ($json.profile.name -eq $target) { Write-Output $_.Name; break } } catch {}" ^
    "  }" ^
    "}"`) do (
    set "SOURCE_SLOT=%%S"
)

if "%SOURCE_SLOT%"=="" (
    echo [ERRO] Nenhum slot tem profile.name = "%TARGET_NAME%".
    echo.
    echo Perfis encontrados:
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ud = '%USER_DATA_DIR%';" ^
        "Get-ChildItem -LiteralPath $ud -Directory ^| Where-Object { $_.Name -match '^(Default^|Profile \d+)$' } ^| ForEach-Object {" ^
        "  $pref = Join-Path $_.FullName 'Preferences';" ^
        "  if (Test-Path -LiteralPath $pref) {" ^
        "    try { $json = Get-Content -LiteralPath $pref -Raw ^| ConvertFrom-Json;" ^
        "          Write-Host ('  {0,-12} -> {1}' -f $_.Name, $json.profile.name) } catch {}" ^
        "  }" ^
        "}"
    echo.
    echo Rode novamente passando o nome correto:
    echo   scripts\setup_cdp_profile.bat "Nome Do Perfil"
    exit /b 1
)

set "SOURCE_PROFILE=%USER_DATA_DIR%\%SOURCE_SLOT%"

echo [OK] Perfil "%TARGET_NAME%" encontrado em: %SOURCE_SLOT%
echo.
echo Origem  : %SOURCE_PROFILE%
echo Destino : %CDP_PROFILE%
echo.

if exist "%CDP_PROFILE%" (
    echo [AVISO] Ja existe um perfil CDP em %CDP_PROFILE%.
    echo Se continuar, ele sera SOBRESCRITO com o conteudo atual.
    echo Para preservar a sessao acumulada do CDP, cancele agora (Ctrl+C).
    echo.
)

echo ATENCAO: feche TODOS os Chromes antes de continuar.
echo (Chrome trava arquivos do perfil enquanto aberto. A copia pode falhar
echo  em alguns arquivos sem prejuizo, mas e melhor fechar.)
echo.
pause

echo.
echo === Copiando perfil (pode levar 1-5 min dependendo do tamanho) ===
echo.

:: Robocopy com exclusoes de cache/lock/crashes - ~80%% menor que copia full
robocopy "%SOURCE_PROFILE%" "%CDP_PROFILE%" /E ^
    /XD "Cache" "Code Cache" "GPUCache" "Service Worker" "Crashpad" ^
        "ShaderCache" "DawnCache" "GrShaderCache" "GraphiteDawnCache" ^
        "blob_storage" "Storage" "VideoDecodeStats" "Site Characteristics Database" ^
    /XF "Singleton*" "lockfile" "LOCK" "*.tmp" ^
    /R:1 /W:1 /NJH /NJS /NDL /NFL

if errorlevel 8 (
    echo.
    echo [ERRO] Robocopy falhou. Codigo: %errorlevel%
    echo Verifique se o Chrome esta totalmente fechado e tente de novo.
    exit /b 2
)

:: Copia tambem o Local State (metadata dos perfis)
if exist "%USER_DATA_DIR%\Local State" (
    copy /Y "%USER_DATA_DIR%\Local State" "%CDP_DATA_DIR%\Local State" >nul
    echo Local State copiado.
)

echo.
echo ===========================================================
echo   Copia concluida com sucesso!
echo ===========================================================
echo.
echo Slot origem    : %SOURCE_SLOT% (perfil "%TARGET_NAME%")
echo Salvo em       : %CDP_PROFILE%
echo.
echo PROXIMOS PASSOS:
echo   1. Voce ja pode reabrir seu Chrome normal - sem conflito de lock.
echo   2. Rode: scripts\start_chrome_cdp.bat
echo   3. Confirme que o site do Magalu carrega normalmente (HTTPS, sem captcha)
echo   4. Configure o Task Scheduler: setup_magalu_scheduler.ps1
echo.

endlocal
