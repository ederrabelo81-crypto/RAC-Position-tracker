# =============================================================================
# check_local_scheduler.ps1 - Diagnostico da coleta local agendada (read-only).
#
# Responde "por que a coleta agendada de Magalu/Shopee/Casas Bahia nao rodou?"
# sem mexer em nada: inspeciona as tarefas RAC_Local_* (formato da Action,
# gatilhos, resultado da ultima execucao decodificado), o estado do repo
# (codigo atrasado vs origin/main), os logs/marcadores do dia e o ambiente
# (venv, rebrowser, .env, perfil do Chrome).
#
# Uso (nao precisa de Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
#
# Saida: [OK] verde / [AVISO] amarelo / [ERRO] vermelho + resumo com proximos
# passos. Exit code 0 = nenhum erro; 1 = ha erros a corrigir.
# =============================================================================

$ErrorActionPreference = "SilentlyContinue"

$BaseDir = Split-Path -Parent $PSScriptRoot
$script:ErrCount  = 0
$script:WarnCount = 0

function Write-Ok   ([string]$msg) { Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Write-Warn ([string]$msg) { Write-Host "  [AVISO] $msg" -ForegroundColor Yellow; $script:WarnCount++ }
function Write-Bad  ([string]$msg) { Write-Host "  [ERRO]  $msg" -ForegroundColor Red;    $script:ErrCount++ }
function Write-Info ([string]$msg) { Write-Host "  $msg" -ForegroundColor Gray }
function Write-Sect ([string]$msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }

# Significado dos LastTaskResult mais comuns (chaves como string para nao
# esbarrar em comparacao Int32 vs UInt32 do hashtable)
$ResultMap = @{
    "0"          = "sucesso"
    "1"          = "falha generica do programa - sintoma classico da Action antiga com cmd /c + aspas (re-rode o setup)"
    "2"          = "arquivo nao encontrado"
    "267008"     = "tarefa pronta (ainda nao rodou nesta definicao)"
    "267009"     = "tarefa em execucao agora"
    "267011"     = "tarefa NUNCA rodou desde que foi registrada"
    "267014"     = "ultima execucao foi encerrada (parada manual ou limite de tempo)"
    "2147750687" = "ja havia uma instancia rodando (0x8004131F)"
    "2147942402" = "arquivo/script da Action nao encontrado (0x80070002)"
    "2147942405" = "acesso negado (0x80070005)"
    "2147943645" = "usuario nao estava logado (0x800704DD)"
    "2147946720" = "tarefa exige usuario logado e nao havia sessao no horario (0x800710E0)"
}

Write-Host "==========================================================="
Write-Host " Diagnostico da coleta local agendada (Magalu+Shopee+CB)"
Write-Host " Projeto: $BaseDir"
Write-Host " Data:    $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "==========================================================="

# --- 1. Tarefas RAC_Local_* -------------------------------------------------
Write-Sect "Tarefas agendadas (RAC_Local_Manha / RAC_Local_Noite / RAC_Bestsellers)"

$expectedBat = Join-Path $BaseDir "scripts\run_local_scheduled.bat"

foreach ($name in @("RAC_Local_Manha", "RAC_Local_Noite", "RAC_Bestsellers")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Bad "$name NAO existe - rode: PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1"
        continue
    }

    if ($task.State -eq "Disabled") {
        Write-Bad "$name existe mas esta DESABILITADA (habilite ou re-rode o setup)"
    } else {
        Write-Ok "$name registrada (estado: $($task.State))"
    }

    $act = $task.Actions | Select-Object -First 1
    Write-Info "Action: $($act.Execute) $($act.Arguments)"

    # Formato antigo (cmd /c + redirect): quebra com espaco no caminho do
    # projeto - o cmd descarta aspas e a tarefa morre sem escrever log.
    if (($act.Execute -match "cmd(\.exe)?`"?$") -or ($act.Arguments -match ">>")) {
        Write-Bad "$name usa a Action ANTIGA (cmd /c ... >> log) - com espacos no caminho ela falha na hora, sem log. Re-rode scripts\setup_local_scheduler.ps1"
    } else {
        $exePath = $act.Execute.Trim('"')
        if (-not (Test-Path $exePath)) {
            Write-Bad "$name aponta para script inexistente: $exePath"
        } elseif ($exePath -ne $expectedBat) {
            Write-Warn "$name nao aponta para $expectedBat (aponta para $exePath)"
        }
    }

    $hasLogon = $task.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" }
    if (-not $hasLogon) {
        Write-Warn "$name sem gatilho de LOGON (catch-up) - registro antigo; re-rode o setup para cobrir notebook desligado no horario"
    }

    $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
    if ($info) {
        $code = [int64]$info.LastTaskResult
        $meaning = $ResultMap["$code"]
        if (-not $meaning) { $meaning = "codigo nao mapeado" }
        $hex = "0x{0:X8}" -f $code
        Write-Info "Ultima execucao: $($info.LastRunTime) | resultado: $code ($hex) = $meaning"
        Write-Info "Proxima execucao: $($info.NextRunTime) | execucoes perdidas: $($info.NumberOfMissedRuns)"

        # O Task Scheduler preserva LastRunTime/LastTaskResult quando a tarefa
        # e re-registrada (mesmo nome). Um erro ANTERIOR ao re-registro e
        # historico da definicao antiga - nao reflete a Action atual.
        $regDate = $null
        try { if ($task.Date) { $regDate = [datetime]$task.Date } } catch { $regDate = $null }
        $isStale = ($regDate -and $info.LastRunTime -and $info.LastRunTime -lt $regDate)

        if ($code -ne 0 -and $code -ne 267009 -and $code -ne 267008) {
            if ($code -eq 267011) {
                Write-Warn "$name nunca rodou desde o registro"
            } elseif ($isStale) {
                Write-Info "Registrada em: $regDate (depois da ultima execucao)"
                Write-Warn "${name}: o erro acima e de ANTES do re-registro (definicao antiga) - valide a nova com: Start-ScheduledTask -TaskName '$name'"
            } else {
                Write-Bad "$name terminou com erro na ultima execucao ($meaning)"
            }
        }
    }
}

# Tarefas legadas que conflitam/duplicam (o setup atual as remove)
$legacy = @("RAC_Autenticada_Manha", "RAC_Autenticada_Noite", "RAC_Chrome_CDP_Startup",
            "RAC_Magalu_Manha", "RAC_Magalu_Noite")
$found = @()
foreach ($t in $legacy) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { $found += $t }
}
if ($found.Count -gt 0) {
    Write-Warn "Tarefas LEGADAS ainda registradas: $($found -join ', ') - re-rode o setup para remove-las"
}

# --- 2. Scripts e ambiente ----------------------------------------------------
Write-Sect "Scripts e ambiente"

foreach ($rel in @("scripts\run_local_scheduled.bat",
                   "scripts\local_scheduled_collect.bat",
                   "scripts\collect_local_authenticated.bat",
                   "scripts\collect_bestsellers.bat",
                   "scripts\ensure_deps.bat")) {
    $p = Join-Path $BaseDir $rel
    if (Test-Path $p) { Write-Ok "$rel presente" }
    else { Write-Bad "$rel AUSENTE - rode scripts\sync_windows.bat (ou git pull)" }
}

$pyExe = $null
foreach ($cand in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
    $p = Join-Path $BaseDir $cand
    if (Test-Path $p) { $pyExe = $p; break }
}
if ($pyExe) {
    Write-Ok "venv encontrada: $pyExe"
    & $pyExe -c "import rebrowser_playwright" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "rebrowser-playwright instalado (obrigatorio p/ passar no Akamai)"
    } else {
        Write-Bad "rebrowser-playwright NAO importavel na venv - rode scripts\ensure_deps.bat --force"
    }

    # Dependencias que faltam em SILENCIO: sem as libs do Google o historico
    # cai no disco local e o CSV nao e espelhado - a coleta "da certo" e o dado
    # fica preso na maquina. A coleta agendada agora roda ensure_deps.bat, mas
    # este check pega o PC que ainda nao pegou o commit que a chama.
    $pkgs = [ordered]@{
        "pyarrow"                  = "historico em Parquet"
        "googleapiclient"          = "upload no Google Drive"
        "google_auth_oauthlib"     = "OAuth do Drive"
        "curl_cffi"                = "Casas Bahia / Shopee"
        "openpyxl"                 = "import do PriceTrack (.xlsx)"
    }
    $faltando = @()
    foreach ($mod in $pkgs.Keys) {
        & $pyExe -c "import $mod" 2>$null
        if ($LASTEXITCODE -ne 0) { $faltando += "$mod ($($pkgs[$mod]))" }
    }
    if ($faltando.Count -gt 0) {
        Write-Bad "Pacote(s) do requirements.txt AUSENTE(s): $($faltando -join '; ') - rode scripts\ensure_deps.bat --force"
    } else {
        Write-Ok "Dependencias criticas do requirements.txt presentes"
    }

    $stamp = Join-Path $BaseDir "logs\deps_state.txt"
    if (Test-Path $stamp) {
        $age = (Get-Date) - (Get-Item $stamp).LastWriteTime
        Write-Info ("Ultima instalacao de dependencias: ha {0:N1} dia(s)" -f $age.TotalDays)
    } else {
        Write-Info "Sem logs\deps_state.txt - as dependencias serao verificadas na proxima coleta agendada"
    }
} else {
    Write-Bad "Nenhuma venv (.venv/venv) - rode scripts\sync_windows.bat"
}

$envFile = Join-Path $BaseDir ".env"
if (Test-Path $envFile) {
    $envText = Get-Content $envFile -Raw
    foreach ($key in @("SUPABASE_URL", "SUPABASE_KEY", "TELEGRAM_BOT_TOKEN", "N8N_TELEGRAM_CHAT_ID")) {
        if ($envText -match "(?m)^\s*$key\s*=\s*\S") { Write-Ok ".env tem $key" }
        else { Write-Warn ".env sem $key (upload/alerta pode nao funcionar)" }
    }

    # QUAL chave, nao so se existe. O papel define o statement_timeout do
    # Postgres (anon 3s / authenticated 8s / service_role 120s) e se a RLS se
    # aplica. Com a chave anon a coleta sobe normalmente (a tabela coletas nao
    # tem RLS) e o estrago aparece depois: etapas da automacao ADMIN morrendo
    # com 57014 e a tabela bestsellers, que TEM RLS, recusando a escrita.
    if ($pyExe) {
        Push-Location $BaseDir
        $papel = & $pyExe -c "import os; from utils.supabase_client import _key_role; print(_key_role(os.getenv('SUPABASE_KEY','')) or 'desconhecido')" 2>$null
        Pop-Location
        $papel = ($papel | Select-Object -First 1)
        if ($papel -eq "service_role") {
            Write-Ok "SUPABASE_KEY e a chave service_role (timeout 120s, ignora RLS)"
        } elseif ($papel -eq "desconhecido" -or -not $papel) {
            Write-Info "Nao consegui identificar o papel da SUPABASE_KEY (formato nao reconhecido)"
        } else {
            Write-Bad "SUPABASE_KEY e a chave '$papel' - timeout de 3s/8s e RLS aplicada: a automacao ADMIN falha com 57014 e a tabela bestsellers recusa escrita. Troque pela service_role (Supabase > Project Settings > API Keys)"
        }
    }
    # Sem GDRIVE_FOLDER_ID o backend do historico e resolvido como 'local'.
    # As credenciais tem DUAS formas validas (utils/history/backends.py):
    # conta de servico (Workspace + Shared Drive) OU o trio OAuth (conta
    # pessoal). Exigir o trio sempre marcaria um Shared Drive correto como erro.
    $hasFolder = $envText -match "(?m)^\s*GDRIVE_FOLDER_ID\s*=\s*\S"
    $hasSvcAcct = $envText -match "(?m)^\s*GDRIVE_SERVICE_ACCOUNT_JSON\s*=\s*\S"
    $hasOAuth = ($envText -match "(?m)^\s*GDRIVE_CLIENT_ID\s*=\s*\S") -and
                ($envText -match "(?m)^\s*GDRIVE_CLIENT_SECRET\s*=\s*\S") -and
                ($envText -match "(?m)^\s*GDRIVE_REFRESH_TOKEN\s*=\s*\S")
    if (-not $hasFolder) {
        Write-Bad ".env sem GDRIVE_FOLDER_ID - o historico/CSV NAO vai para o Drive (rode: python scripts\gdrive_setup.py --client-secrets \"CAMINHO.json\")"
    } elseif ($hasSvcAcct) {
        Write-Ok ".env tem GDRIVE_FOLDER_ID + conta de servico (Shared Drive)"
    } elseif ($hasOAuth) {
        Write-Ok ".env tem GDRIVE_FOLDER_ID + as 3 credenciais OAuth"
    } else {
        Write-Bad ".env tem GDRIVE_FOLDER_ID mas nao as credenciais - defina GDRIVE_SERVICE_ACCOUNT_JSON ou o trio GDRIVE_CLIENT_ID/_SECRET/_REFRESH_TOKEN (python scripts\gdrive_setup.py --client-secrets \"CAMINHO.json\")"
    }
} else {
    Write-Bad ".env nao encontrado em $BaseDir"
}

# --- 2b. Destino do historico (Drive x disco) ----------------------------------
Write-Sect "Destino dos dados coletados"

if ($pyExe) {
    Push-Location $BaseDir
    # Pergunta o backend EFETIVO, nao so o nome resolvido pela politica:
    # com RAC_HISTORY_BACKEND=drive e GDRIVE_FOLDER_ID ausente,
    # resolve_backend_name() responde 'drive' mas get_store() nao consegue
    # construir o GoogleDriveBackend, cai no LocalBackend e a coleta grava em
    # disco. `describe` e a mesma string que aparece no log da coleta
    # (drive:<id> / local:<caminho>), entao o diagnostico e o log concordam.
    # get_store() nao toca na rede (o cliente da Drive API e lazy) nem cria
    # pastas - segue read-only. O stderr descartado engole o log de fallback.
    $destino = & $pyExe -c "from utils.history import resolve_backend_name, csv_mirror_enabled, get_store; import os; print(resolve_backend_name()); print(get_store().backend.describe); print('on' if csv_mirror_enabled() else 'off'); print(os.getenv('RAC_HISTORY','on'))" 2>$null
    Pop-Location

    if ($LASTEXITCODE -ne 0 -or -not $destino) {
        Write-Bad "Nao consegui resolver o destino do historico (imports falhando?) - rode scripts\ensure_deps.bat --force"
    } else {
        $politica   = ($destino | Select-Object -First 1).Trim()
        $efetivo    = ($destino | Select-Object -Skip 1 -First 1).Trim()
        $csvEspelho = ($destino | Select-Object -Skip 2 -First 1).Trim()
        $histOn     = ($destino | Select-Object -Skip 3 -First 1).Trim()
        $noDrive    = $efetivo -like "drive*"

        if ($histOn -match "^(off|0|false)$") {
            Write-Bad "RAC_HISTORY=$histOn no .env - a coleta NAO grava historico nenhum"
        }
        if ($noDrive) {
            Write-Ok "Historico -> Google Drive (Parquet por dia) [$efetivo]"
        } elseif ($politica -eq "drive") {
            Write-Bad "RAC_HISTORY_BACKEND=drive mas o store cai em DISCO LOCAL [$efetivo] - credencial ou lib do Drive faltando. Rode: python scripts\gdrive_setup.py --check"
        } else {
            Write-Bad "Historico -> DISCO LOCAL [$efetivo]: o dado sai da coleta e nao sai da maquina. Configure: python scripts\gdrive_setup.py --client-secrets \"CAMINHO.json\""
        }
        if ($csvEspelho -ne "on") {
            Write-Warn "RAC_DRIVE_CSV=off - o CSV cru fica so em output\ nesta maquina"
        } elseif ($noDrive) {
            Write-Ok "CSV da coleta -> espelhado no Drive (csv_coletas/)"
        } else {
            # mirror_csv_to_drive() pula quando o backend nao e o Drive: dizer
            # [OK] aqui seria afirmar um espelho que nao acontece.
            Write-Bad "CSV da coleta NAO sera espelhado - o historico esta em modo local (conserte o Drive acima)"
        }
        Write-Info "Teste de ida e volta no Drive (grava/le/apaga): python scripts\gdrive_setup.py --check"
    }
}

$profileDir = Join-Path $BaseDir "data\chrome_profile"
if (Test-Path $profileDir) {
    Write-Ok "Perfil dedicado do Chrome presente (data\chrome_profile)"
    Write-Info "Conferir login Shopee: python scripts\setup_local_profile.py --check"
} else {
    Write-Bad "Perfil dedicado AUSENTE - rode: python scripts\setup_local_profile.py (e logue na Shopee)"
}

# --- 3. Repositorio (codigo atrasado?) ----------------------------------------
Write-Sect "Repositorio"

Push-Location $BaseDir
$env:GIT_TERMINAL_PROMPT = "0"
$head = git rev-parse --short HEAD 2>$null
if ($head) {
    Write-Info "Commit local: $head"
    git fetch origin main --quiet 2>$null
    if ($LASTEXITCODE -eq 0) {
        $behind = git rev-list --count "HEAD..origin/main" 2>$null
        if ([int]$behind -gt 0) {
            Write-Warn "Codigo local esta $behind commit(s) atras de origin/main (a coleta agendada faz git pull sozinha; para atualizar agora: git pull --ff-only origin main)"
        } else {
            Write-Ok "Codigo local em dia com origin/main"
        }
    } else {
        Write-Warn "git fetch falhou (sem internet ou sem credencial salva) - o self-update das tarefas tambem falharia; teste: git pull origin main"
    }
    $dirty = (git status --porcelain 2>$null | Measure-Object).Count
    if ($dirty -gt 0) {
        Write-Warn "$dirty arquivo(s) modificados localmente - podem impedir o git pull --ff-only do agendamento"
    }
} else {
    Write-Bad "git nao respondeu em $BaseDir (git instalado? repo integro?)"
}
Pop-Location

# --- 4. Logs e marcadores do dia ----------------------------------------------
Write-Sect "Logs e execucoes de hoje"

$logFile = Join-Path $BaseDir "logs\scheduler.log"
if (Test-Path $logFile) {
    $age = (Get-Date) - (Get-Item $logFile).LastWriteTime
    Write-Info ("scheduler.log: ultima escrita ha {0:N1} h" -f $age.TotalHours)
    if ($age.TotalHours -gt 26) {
        Write-Warn "scheduler.log sem escrita ha mais de 26h - nenhuma tarefa rodou nesse periodo (com a Action antiga a tarefa falha SEM logar; veja o resultado decodificado acima)"
    }
    Write-Info "--- ultimas linhas ---"
    # -Encoding UTF8: o log e UTF-8 (PYTHONUTF8=1); sem isso o PS 5.1 le como
    # ANSI e os acentos/emojis do Loguru viram mojibake na tela.
    Get-Content $logFile -Tail 12 -Encoding UTF8 | ForEach-Object { Write-Info $_ }
} else {
    Write-Warn "logs\scheduler.log nao existe - a coleta agendada nunca chegou a escrever log nesta maquina"
}

$today = Get-Date -Format "yyyyMMdd"
foreach ($slot in @("manha", "noite", "bestsellers")) {
    $marker = Join-Path $BaseDir "logs\coleta_${slot}_${today}.done"
    if (Test-Path $marker) { Write-Ok "Coleta '$slot' de hoje concluida (marcador presente)" }
    elseif ($slot -eq "bestsellers" -and ([int]((Get-Date).DayOfWeek)) -in @(0, 6)) {
        # Mais vendidos so roda em dia util: ausencia de marcador no fim de
        # semana e o comportamento correto, nao uma coleta perdida.
        Write-Info "Coleta 'bestsellers': fim de semana - nao roda (so dia util, janela 9-10h)"
    }
    else { Write-Info "Coleta '$slot' de hoje: sem marcador (ainda nao rodou/nao concluiu)" }
}

# --- 5. Energia (WakeToRun depende de wake timer) -------------------------------
Write-Sect "Energia"

$wake = powercfg /waketimers 2>&1
if ($LASTEXITCODE -eq 0) {
    if ($wake -match "RAC|Task") { Write-Ok "Ha wake timer agendado (WakeToRun deve acordar o notebook)" }
    else { Write-Info "Nenhum wake timer listado agora (normal se a proxima execucao ainda nao foi enfileirada)" }
} else {
    Write-Info "powercfg /waketimers precisa de Admin - pulei. Se o notebook dorme e nao acorda as 9h/20h, verifique 'Permitir temporizadores de despertar' nas opcoes de energia"
}

# --- Resumo ---------------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================="
if ($script:ErrCount -eq 0 -and $script:WarnCount -eq 0) {
    Write-Host " Tudo certo: nenhum problema encontrado." -ForegroundColor Green
} else {
    Write-Host " Resultado: $($script:ErrCount) erro(s), $($script:WarnCount) aviso(s)." -ForegroundColor $(if ($script:ErrCount -gt 0) { "Red" } else { "Yellow" })
    Write-Host ""
    Write-Host " Correcao padrao (resolve a maioria dos erros acima):" -ForegroundColor Yellow
    Write-Host "   1. scripts\sync_windows.bat   (git pull + dependencias + check do Drive)" -ForegroundColor Gray
    Write-Host "   2. Drive ainda nao configurado? python scripts\gdrive_setup.py --client-secrets \"CAMINHO.json\"" -ForegroundColor Gray
    Write-Host "   3. PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1" -ForegroundColor Gray
    Write-Host "   4. Teste: Start-ScheduledTask -TaskName 'RAC_Local_Manha'" -ForegroundColor Gray
    Write-Host "      e confira: Get-Content logs\scheduler.log -Tail 30" -ForegroundColor Gray
}
Write-Host "==========================================================="

if ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost") {
    Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

exit $(if ($script:ErrCount -gt 0) { 1 } else { 0 })
