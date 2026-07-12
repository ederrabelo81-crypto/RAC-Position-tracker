@echo off
:: -----------------------------------------------------------------------------
:: run_local_scheduled.bat - Launcher agendado da coleta local (estagio A).
::
:: E o alvo DIRETO das tarefas RAC_Local_* do Task Scheduler - sem "cmd /c" e
:: sem redirect na Action da tarefa. Com espacos no caminho do projeto
:: (C:\Users\Eder Rabelo\...), o cmd.exe descarta a primeira e a ultima aspas
:: do /c e o comando vira "C:\Users\Eder ..." -> a tarefa morria na hora, SEM
:: escrever nada no log. Era a causa de a coleta agendada de Magalu/Shopee/
:: Casas Bahia "nao rodar" (a tarefa do ML, registrada com o .bat direto,
:: nunca teve esse problema). O log agora e feito AQUI dentro.
::
:: Este arquivo deve ficar ESTAVEL (evitar mudancas): o git pull daqui de
:: dentro reescreve os proprios .bat do repo, e o cmd.exe le o arquivo em
:: execucao por offset de bytes - trocar este arquivo durante a execucao
:: corrompe o parse. Duas defesas:
::   1. o bloco entre parenteses abaixo e parseado INTEIRO antes de executar
::      qualquer coisa (sobrevive ao proprio git pull);
::   2. toda logica que evolui (janela de turno, marcador diario, alerta) mora
::      em local_scheduled_collect.bat, que so e lido DEPOIS do pull - sempre
::      na versao mais nova.
::
:: Uso (as tarefas passam so o slot):
::   scripts\run_local_scheduled.bat manha
::   scripts\run_local_scheduled.bat noite
:: Compat legado: scripts\run_local_scheduled.bat 2 alta media
:: Pular o git pull (debug/offline): defina RAC_NO_SELFUPDATE=1
:: Log: logs\scheduler.log
:: -----------------------------------------------------------------------------

setlocal

:: Raiz do projeto = pasta pai deste script
for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

:: git nao pode pedir credencial em sessao agendada (travaria ate o timeout)
set "GIT_TERMINAL_PROMPT=0"

echo [run_local_scheduled] logando em "%BASE_DIR%\logs\scheduler.log"

:: Bloco unico: parseado por completo antes do pull rodar (nao usar :: aqui
:: dentro - comentario :: quebra o parse de blocos entre parenteses).
(
    echo [%DATE% %TIME%] [agendado] === inicio: args=%* ===
    if "%RAC_NO_SELFUPDATE%"=="1" (
        echo [%DATE% %TIME%] [agendado] self-update pulado [RAC_NO_SELFUPDATE=1]
    ) else (
        echo [%DATE% %TIME%] [agendado] git pull --ff-only origin main
        git pull --ff-only origin main
        if errorlevel 1 echo [%DATE% %TIME%] [agendado] AVISO: git pull falhou - seguindo com codigo local
    )
    if not exist "%~dp0local_scheduled_collect.bat" (
        echo [%DATE% %TIME%] [agendado] ERRO: local_scheduled_collect.bat nao encontrado - rode scripts\sync_windows.bat
        exit /b 1
    )
    call "%~dp0local_scheduled_collect.bat" %*
    exit /b
) >> "%BASE_DIR%\logs\scheduler.log" 2>&1
