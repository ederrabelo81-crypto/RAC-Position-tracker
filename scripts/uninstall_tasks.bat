@echo off
:: ============================================================================
:: uninstall_tasks.bat - Remove as tarefas RAC do Task Scheduler
::
:: USO:
::   Clique com botao direito -> "Executar como administrador"
:: ============================================================================

setlocal

echo ============================================================================
echo   RAC Price Tracker - Removendo tarefas do Task Scheduler
echo ============================================================================
echo.

:: Verifica privilegios de administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERRO] Este script precisa ser executado como Administrador.
    pause
    exit /b 1
)

echo Removendo RAC_Coleta_Manha...
schtasks /Delete /TN "RAC_Coleta_Manha" /F 2>nul
if %errorLevel% equ 0 (
    echo   OK - removida
) else (
    echo   Tarefa nao encontrada ou ja removida
)

echo.
echo Removendo RAC_Coleta_Tarde...
schtasks /Delete /TN "RAC_Coleta_Tarde" /F 2>nul
if %errorLevel% equ 0 (
    echo   OK - removida
) else (
    echo   Tarefa nao encontrada ou ja removida
)

echo.
echo ============================================================================
echo   Concluido.
echo ============================================================================
pause
endlocal
