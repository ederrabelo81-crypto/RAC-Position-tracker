@echo off
rem Coleta de um turno: um site por vez, log escrito daqui de dentro.
rem Arquivo 100% ASCII de proposito (ver references/agendamento-windows.md).
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
set LOG=logs\agendador.log
set FALHOU=0

echo [%date% %time%] inicio >> "%LOG%"
for %%S in (mercadolivre amazon magalu casasbahia) do (
    python coletar.py --site %%S --keywords-arquivo keywords.txt --paginas 1 >> "%LOG%" 2>&1
    if errorlevel 1 set FALHOU=1
)
echo [%date% %time%] fim falhou=%FALHOU% >> "%LOG%"
exit /b %FALHOU%
