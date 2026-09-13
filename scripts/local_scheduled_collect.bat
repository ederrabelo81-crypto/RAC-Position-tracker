@echo off
:: -----------------------------------------------------------------------------
:: local_scheduled_collect.bat - Estagio B da coleta agendada local.
::
:: Chamado por run_local_scheduled.bat DEPOIS do git pull, entao roda sempre na
:: versao mais nova do repo (mudancas aqui chegam ao notebook sozinhas, sem
:: re-registrar tarefa). Concentra a logica de agendamento:
::
::   - TRES turnos desde Set/2026 (8h/14h/20h), cada um com sua janela:
::       manha  -> 8-11h   (turno Abertura)
::       tarde  -> 12-17h  (turno Tarde)
::       noite  -> 18-23h  (turno Fechamento)
::     A janela protege o turno gravado: get_turno() decide Abertura/Tarde/
::     Fechamento pela HORA, entao uma tarefa que dispara atrasada
::     (StartWhenAvailable / gatilho de logon) FORA da janela e PULADA em vez de
::     gravar dados com o turno errado.
::   - Todos os 3 turnos rodam a MESMA varredura: 2 paginas, TODAS as keywords
::     (sem filtro de prioridade) e as plataformas do PC (ML, Magalu, Casas
::     Bahia, Google Shopping, Leroy, Shopee e dealers). Amazon NAO entra aqui
::     desde Set/2026: roda no GitHub Actions (collect_amazon_sellers.yml).
::   - Marcador diario logs\coleta_<slot>_<data>.done: o gatilho de logon pode
::     disparar varias vezes ao dia sem duplicar a coleta. O marcador so e
::     gravado em caso de SUCESSO - se a coleta das 08:00 falhar, o proximo
::     logon dentro da janela tenta de novo.
::   - Alerta Telegram quando a coleta agendada falha (exit != 0), via
::     utils\n8n_notify.py (usa TELEGRAM_BOT_TOKEN/N8N_* do .env).
::
:: Mais Vendidos (bestsellers) NAO e mais coletado desde Set/2026 - foco 100%
:: na coleta de oferta/posicao. O slot antigo continua reconhecido so para
:: avisar e sair sem erro, caso uma tarefa legada ainda o dispare.
::
:: Uso:
::   scripts\local_scheduled_collect.bat manha              (Abertura, 2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat tarde              (Tarde,    2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat noite              (Fechamento,2 pgs, todas keywords)
::   scripts\local_scheduled_collect.bat <pages> [prio...]  (legado: repassa)
:: -----------------------------------------------------------------------------

setlocal

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

set "MODE=%~1"

:: Hora/data via PowerShell: %TIME%/%DATE% mudam de formato com a localizacao
:: do Windows; PowerShell e estavel. (Hora local do notebook = BRT.)
set "HOUR="
set "TODAY="
for /f %%H in ('powershell -NoProfile -Command "(Get-Date).Hour" 2^>nul') do set "HOUR=%%H"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "TODAY=%%D"
if not defined TODAY set "TODAY=00000000"

if /i "%MODE%"=="manha" goto :slot_manha
if /i "%MODE%"=="tarde" goto :slot_tarde
if /i "%MODE%"=="noite" goto :slot_noite
if /i "%MODE%"=="bestsellers" goto :slot_bestsellers_removido
goto :legacy

:: Os tres turnos rodam a MESMA varredura (2 paginas, TODAS as keywords - sem
:: filtro de prioridade). Diferem so pela JANELA de hora, que casa com o turno
:: gravado por get_turno() (Abertura<=11h / Tarde 12-17h / Fechamento>=18h). O
:: RAC_JOB_ID e o id do job no livro-razao (utils\pipeline_registry.py): main.py
:: bate ponto de inicio/fim, e o supervisor consegue dizer "o PC coletor nao
:: rodou" em vez de acusar N plataformas criticas caidas.

:slot_manha
set "SLOT=manha"
set "RAC_JOB_ID=local_manha"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=8"
set "WIN_MAX=11"
goto :guarded_run

:slot_tarde
set "SLOT=tarde"
set "RAC_JOB_ID=local_tarde"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=12"
set "WIN_MAX=17"
goto :guarded_run

:slot_noite
set "SLOT=noite"
set "RAC_JOB_ID=local_noite"
set "PAGES=2"
set "PRIORITY="
set "WIN_MIN=18"
set "WIN_MAX=23"
goto :guarded_run

:slot_bestsellers_removido
:: Mais Vendidos saiu da coleta em Set/2026. Nao e erro: apenas nao ha nada a
:: fazer. Sai 0 para nao disparar o alerta de falha do agendamento.
echo [%DATE% %TIME%] [bestsellers] coleta de mais vendidos foi descontinuada (Set/2026) - nada a fazer
exit /b 0

:guarded_run
if exist "logs\coleta_%SLOT%_%TODAY%.done" (
    echo [%DATE% %TIME%] [%SLOT%] ja coletado hoje - nada a fazer
    exit /b 0
)
if not defined HOUR (
    echo [%DATE% %TIME%] [%SLOT%] AVISO: nao obtive a hora via PowerShell - coletando sem guarda de janela
    goto :collect
)
if %HOUR% LSS %WIN_MIN% (
    echo [%DATE% %TIME%] [%SLOT%] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando
    exit /b 0
)
if %HOUR% GTR %WIN_MAX% (
    echo [%DATE% %TIME%] [%SLOT%] fora da janela - hora=%HOUR%, janela=%WIN_MIN%-%WIN_MAX%h - pulando
    exit /b 0
)
goto :collect

:legacy
set "SLOT=legado"
set "PAGES=%~1"
if "%PAGES%"=="" set "PAGES=2"
set "PRIORITY="
if not "%~2"=="" set "PRIORITY=%~2"
if not "%~3"=="" set "PRIORITY=%PRIORITY% %~3"
if not "%~4"=="" set "PRIORITY=%PRIORITY% %~4"
goto :collect

:collect
:: Dependencias antes da coleta: o estagio A ja fez `git pull`, entao o codigo
:: e novo mas a venv pode ser antiga. Sem isto o notebook rodava codigo que
:: importa libs que ele nao tem - foi assim que o historico deixou de ir ao
:: Drive (google-api-python-client ausente -> backend cai para local). Sai em
:: ~1s quando nada mudou; nao aborta a coleta se a instalacao falhar (coletar
:: com a venv antiga e melhor que nao coletar).
if exist "%~dp0ensure_deps.bat" (
    echo [%DATE% %TIME%] [%SLOT%] verificando dependencias Python
    call "%~dp0ensure_deps.bat"
    if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: ensure_deps falhou - seguindo com a venv atual
) else (
    echo [%DATE% %TIME%] [%SLOT%] AVISO: ensure_deps.bat ausente - rode scripts\sync_windows.bat
)

echo [%DATE% %TIME%] [%SLOT%] coleta local: %PAGES% pagina(s), prioridade "%PRIORITY%"
call "%~dp0collect_local_authenticated.bat" %PAGES% %PRIORITY%
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] [%SLOT%] coleta finalizada [exit=%RC%]

if not "%RC%"=="0" goto :failed

:: Sucesso: limpa marcadores antigos do slot e grava o de hoje
if not "%SLOT%"=="legado" (
    del /q "logs\coleta_%SLOT%_*.done" 2>nul
    echo ok> "logs\coleta_%SLOT%_%TODAY%.done"
)

:: Estagio C: materializa o fato do seller_app (seller_offer_daily). Reprocessa
:: `coletas` do dia -> tabelas que o seller_app le. Idempotente por data e roda
:: "ontem e hoje", entao a materializacao da NOITE fecha o dia inteiro (3 turnos
:: locais + Amazon do Actions, que pode ter chegado atrasada) e a da manha
:: seguinte ainda recupera Amazon que caiu tarde. Best-effort: falha aqui NAO
:: derruba a coleta (o dado ja esta no Supabase), so vira aviso; a ausencia da
:: batida de ponto (--heartbeat, job local_seller_fact) e que dispara o alarme
:: no pipeline_watch. So nos turnos de verdade (nao no legado).
::
:: Fora de um bloco ( ) de proposito: este .bat roda com `setlocal` SEM
:: enabledelayedexpansion, entao definir e usar %PYEXE% no mesmo bloco expandiria
:: o valor antigo (vazio). No nivel de cima, cada linha reexpande e o valor vale.
if "%SLOT%"=="legado" exit /b 0
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
echo [%DATE% %TIME%] [%SLOT%] materializando seller_offer_daily [seller_app]
call "%PYEXE%" scripts\build_seller_offer_daily.py --heartbeat
if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: build do seller_offer_daily falhou - veja pipeline_watch [SUPABASE_KEY service_role no .env?]

:: Estagio D: poda a janela quente do Supabase (tier -> Drive). Este e o passo
:: que FALTAVA: a escrita ao Drive roda a cada coleta (main.py), mas apagar do
:: Supabase o que saiu dos 15 dias era um comando MANUAL nunca agendado - o
:: banco acumulou ~40 dias em vez de 15 e estourou a cota (Jul e Set/2026). tier
:: le -> grava Parquet -> reverifica -> so entao apaga, entao nunca apaga um dia
:: sem copia fria confirmada. Idempotente e cobre TODOS os dias fora da janela,
:: entao roda so UMA vez ao dia (turno da NOITE): um pulo e recuperado na noite
:: seguinte. Best-effort: falha aqui NAO derruba a coleta (o dado ja esta no
:: Supabase e no Drive); a ausencia da batida (--heartbeat, job
:: local_tier_migration) e que dispara o alarme no pipeline_watch. Requer
:: SUPABASE_KEY service_role (a chave anon nao apaga). Se o banco JA estiver
:: restrito por cota, tier nao roda (a REST recusa ate leitura): o primeiro
:: desbloqueio e manual pelo SQL Editor (scripts\retention_cleanup.sql + VACUUM
:: FULL), e dai em diante esta poda noturna mantem os 15 dias.
if /i "%SLOT%"=="noite" (
    echo [%DATE% %TIME%] [%SLOT%] podando janela quente do Supabase [tier -^> Drive]
    call "%PYEXE%" scripts\history_cli.py tier --dataset all --confirm --heartbeat
    if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: tier falhou/parcial - veja pipeline_watch [SUPABASE_KEY service_role? banco restrito por cota?]
)
exit /b 0

:failed
:: Alerta best-effort no Telegram; falha do alerta nao muda o exit da coleta
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
"%PYEXE%" -c "from utils.n8n_notify import notify_scheduler_failure; notify_scheduler_failure('%SLOT%', %RC%)"
if errorlevel 1 echo [%DATE% %TIME%] [%SLOT%] AVISO: nao consegui enviar o alerta Telegram
exit /b %RC%
