@echo off
:: RAC Price Collector — Coleta Noite (21:00)
:: Plataformas: ML + Google Shopping + Dealers
:: Prioridade: alta | Páginas: 1
::
:: Para testar manualmente: duplo clique neste arquivo

cd /d "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

echo [%DATE% %TIME%] Iniciando coleta tarde... >> logs\scheduler.log

python main.py --platforms ml google_shopping dealers --pages 1 --priority alta >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] Coleta tarde concluida. >> logs\scheduler.log
