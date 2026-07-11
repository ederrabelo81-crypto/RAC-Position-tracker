@echo off
:: -----------------------------------------------------------------------------
:: run_local_scheduled.bat - Wrapper agendado da coleta local autenticada.
::
:: Roda ANTES de collect_local_authenticated.bat para garantir que o notebook
:: colete com o codigo mais novo. As tarefas agendadas (RAC_Local_Manha/Noite)
:: NAO passam por sync_windows.bat, entao o .bat em disco pode ficar defasado --
:: um fix mergeado na vespera so chega ao notebook apos um sync manual. Aqui o
:: git pull roda a cada execucao agendada, igual aos scripts da VM Oracle
:: (collect_manha_linux.sh / collect_noite_linux.sh).
::
:: Por que um wrapper separado (e nao git pull dentro do proprio .bat de coleta):
:: o cmd.exe le o .bat do disco por offset de bytes durante a execucao. Se o
:: git pull TROCAR o arquivo que esta rodando, o parse corrompe ("- foi
:: inesperado neste momento."). Este wrapper e estavel e minusculo; o pull
:: acontece aqui e SO DEPOIS invocamos o collect_local_authenticated.bat -- que
:: ja e lido do disco na versao atualizada.
::
:: Uso (mesmos args do collect_local_authenticated.bat, repassados via %*):
::   scripts\run_local_scheduled.bat 2 alta media
:: Pular o git pull (debug/offline): defina RAC_NO_SELFUPDATE=1
:: -----------------------------------------------------------------------------

setlocal

:: Raiz do projeto = pasta pai deste script
for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"

if "%RAC_NO_SELFUPDATE%"=="1" (
    echo [%DATE% %TIME%] [agendado] self-update pulado [RAC_NO_SELFUPDATE=1]
) else (
    echo [%DATE% %TIME%] [agendado] git pull --ff-only origin main
    git pull --ff-only origin main
    if errorlevel 1 echo [%DATE% %TIME%] [agendado] AVISO: git pull falhou - seguindo com codigo local
)

:: Coleta na versao ja atualizada. Repassa PAGES/PRIORITY recebidos da tarefa.
call "%~dp0collect_local_authenticated.bat" %*
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
