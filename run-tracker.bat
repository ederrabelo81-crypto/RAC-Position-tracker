@echo off
REM ══════════════════════════════════════════════════════════════
REM  RAC Position Tracker — Execução via Agendador de Tarefas
REM ══════════════════════════════════════════════════════════════
REM  Agende este .bat no Agendador de Tarefas do Windows para
REM  execução às 08:00 e 17:30 (ou horários desejados).
REM
REM  Instruções:
REM  1. Win+R → taskschd.msc
REM  2. Criar Tarefa → Nome: "RAC Position Tracker - Manhã"
REM  3. Disparadores → Novo → Diariamente às 08:00
REM  4. Ações → Novo → Iniciar programa → Selecionar este .bat
REM  5. Repetir para "RAC Position Tracker - Tarde" às 17:30
REM ══════════════════════════════════════════════════════════════

REM Ajuste o caminho abaixo para onde você instalou o projeto
cd /d "C:\Users\%USERNAME%\rac-position-tracker"

REM Verificar se Python está disponível
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERRO] Python nao encontrado no PATH
    echo Instale em: https://python.org
    pause
    exit /b 1
)

echo.
echo ══════════════════════════════════════════════════════
echo   RAC Position Tracker — Iniciando coleta...
echo   Data: %DATE% | Hora: %TIME%
echo ══════════════════════════════════════════════════════
echo.

REM Ativa o ambiente virtual (se existir)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Executa o tracker
python main.py --platforms all --pages 3

REM Verificar resultado
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERRO] Coleta finalizada com erros. Verifique logs/
    echo.
) else (
    echo.
    echo [OK] Coleta finalizada com sucesso.
    echo.
)

REM Log de execução
echo %DATE% %TIME% - Exit code: %ERRORLEVEL% >> logs\scheduler_history.log
