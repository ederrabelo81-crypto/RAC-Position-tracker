@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: start_chrome_cdp.bat — Abre Google Chrome com porta de debug remota.
::
:: O scraper Magalu (modo CDP) conecta nesta porta ao invés de lançar um
:: Chrome próprio via Playwright. Como é o Chrome REAL do usuário (com
:: cookies, histórico, fingerprint genuíno), o Akamai aceita como humano.
::
:: Uso:
::   1. Execute UMA VEZ por dia (ou deixe rodando):
::        scripts\start_chrome_cdp.bat
::   2. Faça login no Magalu / navegue um pouco (aquece o perfil)
::   3. Deixe o Chrome aberto — não feche durante a coleta
::   4. Rode a coleta:
::        scripts\collect_magalu_cdp.bat
::
:: Profile separado em C:\chrome-rac-cdp para não conflitar com seu Chrome
:: pessoal — primeira execução pode pedir login do Magalu.
:: ─────────────────────────────────────────────────────────────────────────────

setlocal

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo [ERRO] chrome.exe nao encontrado nos caminhos padrao.
    echo Edite este script e ajuste CHROME_EXE.
    exit /b 1
)

set "PROFILE_DIR=C:\chrome-rac-cdp"
set "DEBUG_PORT=9222"

echo === Abrindo Chrome com CDP na porta %DEBUG_PORT% ===
echo Profile: %PROFILE_DIR%
echo Chrome:  %CHROME_EXE%
echo.
echo IMPORTANTE: deixe esta janela e o Chrome ABERTOS durante a coleta.
echo Para parar: feche o Chrome.
echo.

start "" "%CHROME_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --user-data-dir="%PROFILE_DIR%" ^
    --no-first-run ^
    --no-default-browser-check ^
    https://www.magazineluiza.com.br/

endlocal
