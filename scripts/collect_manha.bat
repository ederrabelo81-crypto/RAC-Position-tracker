@echo off
:: RAC Price Collector — Coleta Manha (10:00 BRT)
:: Plataformas: ML + Google Shopping (Oracle VM cuida de magalu/amazon/leroy/dealers)
:: Prioridade: alta + media | Paginas: 2
::
:: Para testar manualmente: duplo clique neste arquivo

cd /d "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

echo [%DATE% %TIME%] Iniciando coleta manha (ML + Google Shopping)... >> logs\scheduler.log

python main.py --platforms ml google_shopping --pages 2 --priority alta media >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] Coleta manha concluida. >> logs\scheduler.log
