@echo off
:: RAC Price Collector — Coleta Manhã (10:00)
:: Plataformas: ML + Google Shopping + Dealers
:: Prioridade: alta + media | Páginas: 2
::
:: Para testar manualmente: duplo clique neste arquivo

cd /d "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

echo [%DATE% %TIME%] Iniciando coleta manha... >> logs\scheduler.log

python main.py --platforms ml google_shopping dealers --pages 2 --priority alta media >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] Coleta manha concluida. >> logs\scheduler.log
