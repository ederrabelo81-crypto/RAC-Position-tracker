@echo off
:: RAC Price Collector — Coleta Noite (21:00 BRT)
:: Plataformas: ML + Google Shopping (Oracle VM cuida de magalu/amazon/leroy/dealers)
:: Prioridade: alta | Paginas: 1
::
:: Para testar manualmente: duplo clique neste arquivo

cd /d "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

echo [%DATE% %TIME%] Iniciando coleta noite (ML + Google Shopping)... >> logs\scheduler.log

python main.py --platforms ml google_shopping --pages 1 --priority alta >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] Coleta noite concluida. >> logs\scheduler.log
