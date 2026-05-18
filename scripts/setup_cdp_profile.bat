@echo off
:: -----------------------------------------------------------------------------
:: setup_cdp_profile.bat - Copia o perfil Chrome do usuario para uso CDP.
::
:: Executar UMA VEZ antes do primeiro start_chrome_cdp.bat. Copia o perfil
:: "Eder" (Default) do Chrome do usuario para C:\chrome-rac-cdp, preservando
:: cookies, extensoes, historico e fingerprint do browser. Apos esta copia,
:: o CDP Chrome roda em paralelo ao Chrome normal sem conflito de lock.
::
:: Uso:
::   scripts\setup_cdp_profile.bat
::
:: Re-executar: deleta C:\chrome-rac-cdp antes (apaga sessao acumulada).
::   rmdir /s /q C:\chrome-rac-cdp
:: -----------------------------------------------------------------------------

setlocal

set "USER_DATA_DIR=C:\Users\Eder Rabelo\AppData\Local\Google\Chrome\User Data"
set "SOURCE_PROFILE=%USER_DATA_DIR%\Default"
set "CDP_DATA_DIR=C:\chrome-rac-cdp"
set "CDP_PROFILE=%CDP_DATA_DIR%\Default"

echo ===========================================================
echo   Setup CDP Profile - Copia perfil Chrome para C:\chrome-rac-cdp
echo ===========================================================
echo.
echo Origem  : %SOURCE_PROFILE%
echo Destino : %CDP_PROFILE%
echo.

if not exist "%SOURCE_PROFILE%" (
    echo [ERRO] Perfil Chrome do usuario nao encontrado em:
    echo   %SOURCE_PROFILE%
    echo.
    echo Verifique se o Chrome esta instalado e ja foi usado pelo menos uma vez.
    echo Se o perfil "Eder" estiver em outro slot (Profile 1, Profile 2...),
    echo edite este script e ajuste SOURCE_PROFILE.
    exit /b 1
)

if exist "%CDP_PROFILE%" (
    echo [AVISO] Ja existe um perfil CDP em %CDP_PROFILE%.
    echo Se continuar, ele sera SOBRESCRITO com o conteudo atual do Eder.
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
:: /E   = inclui subdiretorios (mesmo vazios)
:: /XD  = exclui diretorios (cache pesado, nao precisa)
:: /XF  = exclui arquivos de lock
:: /R:1 = 1 retry se falhar (lock ainda ativo)
:: /W:1 = 1 segundo entre retries
:: /NFL /NDL /NJH /NJS = output mais limpo
robocopy "%SOURCE_PROFILE%" "%CDP_PROFILE%" /E ^
    /XD "Cache" "Code Cache" "GPUCache" "Service Worker" "Crashpad" ^
        "ShaderCache" "DawnCache" "GrShaderCache" "GraphiteDawnCache" ^
        "blob_storage" "Storage" "VideoDecodeStats" "Site Characteristics Database" ^
    /XF "Singleton*" "lockfile" "LOCK" "*.tmp" ^
    /R:1 /W:1 /NJH /NJS /NDL /NFL

:: Robocopy retorna 0-7 como sucesso (8+ e erro real)
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

:: Mostra tamanho do perfil copiado
for /f "tokens=3" %%a in ('dir "%CDP_DATA_DIR%" /-c /s ^| findstr "arquivo(s)"') do set "SIZE=%%a"
if defined SIZE echo Tamanho do perfil CDP: %SIZE% bytes

echo.
echo PROXIMOS PASSOS:
echo   1. Voce ja pode reabrir seu Chrome normal - sem conflito de lock.
echo   2. Rode: scripts\start_chrome_cdp.bat
echo      (abre uma 2a janela Chrome dedicada ao CDP, na porta 9222)
echo   3. Confirme que o site do Magalu carrega normalmente (HTTPS, sem captcha)
echo   4. Configure o Task Scheduler: setup_magalu_scheduler.ps1
echo.

endlocal
