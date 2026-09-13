# =============================================================================
# verify_today_collection.ps1 - A coleta de hoje virou ARQUIVO? (read-only)
#
# Responde a pergunta que o painel "Saude da Pipeline" NAO consegue responder:
# "o PC coletou de verdade, mesmo que o dado nao tenha chegado ao Supabase?"
#
# O painel (scripts\pipeline_watch.py) le dois sinais e os DOIS moram no
# Supabase: a batida de ponto (pipeline_heartbeat) e as linhas em `coletas`.
# Quando o banco esta fora (cota estourada / HTTP 402, chave errada, rede), a
# coleta roda, grava CSV+Parquet no disco/Drive, mas nem o dado nem a batida
# sobem - e o painel pinta "NAO EXECUTOU". Este script olha o DISCO do PC
# coletor (a unica testemunha nesse caso) e cruza tres provas locais:
#
#   1. output\rac_monitoramento_*.csv / raw_*.csv  -> o dado coletado
#   2. logs\coleta_<slot>_<data>.done              -> marcador de SUCESSO (exit 0)
#   3. logs\heartbeat.jsonl                        -> espelho local da batida
#
# ...e le logs\scheduler.log (SO os blocos de run do dia alvo, delimitados pelo
# banner "=== inicio ===") decidindo se o dado chegou ao Supabase por EVIDENCIA
# POSITIVA - "registros inseridos com sucesso" / "ja existiam no banco" -, nunca
# por ausencia de falha: um dia sem credencial (upload PULADO) tambem nao imprime
# falha, e inferir "subiu" do silencio marcaria como enviado um dado preso no disco.
#
# Com isso separa os tres estados que o painel colapsa em um:
#   - COLETOU e SUBIU          -> tudo certo
#   - COLETOU, NAO SUBIU       -> dado seguro no disco (output\); o banco e o problema
#   - SEM EVIDENCIA de coleta  -> a tarefa realmente nao rodou
#
# Uso (nao precisa de Admin):
#   PowerShell -ExecutionPolicy Bypass -File scripts\verify_today_collection.ps1
#   PowerShell -ExecutionPolicy Bypass -File scripts\verify_today_collection.ps1 -Data 2026-09-12
#
# Exit code: 0 = ha evidencia de coleta hoje (subiu OU ficou so no disco);
#            1 = sem evidencia de coleta no dia (a tarefa nao rodou/nao chegou
#                a gravar nada).
# =============================================================================

[CmdletBinding()]
param(
    # Dia a verificar. Aceita yyyy-MM-dd ou yyyyMMdd. Default: hoje (BRT = hora
    # local do notebook coletor).
    [string]$Data = ""
)

$ErrorActionPreference = "SilentlyContinue"

$BaseDir = Split-Path -Parent $PSScriptRoot
$script:HasData = $false      # ha alguma prova de coleta (CSV ou batida com linhas)
# O dado chegou ao Supabase? Decidido por EVIDENCIA POSITIVA no log, nunca por
# ausencia de falha (ver o cabecalho). Comeca UNKNOWN e so vira UP com prova.
#   UNKNOWN | UP | FAILED | MIXED | SKIPPED
$script:SbState = "UNKNOWN"

function Write-Ok   ([string]$msg) { Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Write-Warn ([string]$msg) { Write-Host "  [AVISO] $msg" -ForegroundColor Yellow }
function Write-Bad  ([string]$msg) { Write-Host "  [ERRO]  $msg" -ForegroundColor Red }
function Write-Info ([string]$msg) { Write-Host "  $msg" -ForegroundColor Gray }
function Write-Sect ([string]$msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }

# --- Normaliza a data alvo em duas formas (compacta p/ nomes de arquivo, ISO
#     p/ o campo data_ref do heartbeat) ----------------------------------------
$targetDate = $null
if ([string]::IsNullOrWhiteSpace($Data)) {
    $targetDate = (Get-Date).Date
} else {
    foreach ($fmt in @("yyyy-MM-dd", "yyyyMMdd")) {
        try {
            $targetDate = [datetime]::ParseExact($Data, $fmt, $null)
            break
        } catch { }
    }
    if (-not $targetDate) {
        Write-Host "Data invalida: '$Data' (use yyyy-MM-dd ou yyyyMMdd)" -ForegroundColor Red
        exit 2
    }
}
$compact = $targetDate.ToString("yyyyMMdd")   # 20260913 -> nomes de arquivo
$iso     = $targetDate.ToString("yyyy-MM-dd")  # 2026-09-13 -> data_ref no jsonl
$isToday = ($targetDate -eq (Get-Date).Date)

Write-Host "==========================================================="
Write-Host " Verificacao da coleta local do dia (a coleta virou arquivo?)"
Write-Host " Projeto: $BaseDir"
Write-Host " Dia:     $iso$(if ($isToday) { ' (hoje)' } else { '' })"
Write-Host " Agora:   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "==========================================================="

# --- 1. CSV/raw da coleta -----------------------------------------------------
Write-Sect "Dado coletado (output\)"

$outDir = Join-Path $BaseDir "output"
$csvHits = @()
if (Test-Path $outDir) {
    # rac_monitoramento_<timestamp>.csv usa o timestamp da coleta no nome;
    # raw_<RUN_ID>.csv nao carrega data no nome, entao filtramos pelo
    # LastWriteTime cair no dia alvo. Os dois juntos cobrem os dois formatos.
    $csvHits += Get-ChildItem (Join-Path $outDir "rac_monitoramento_*.csv") -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime.Date -eq $targetDate }
    $csvHits += Get-ChildItem (Join-Path $outDir "raw_*.csv") -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime.Date -eq $targetDate }
}

if ($csvHits.Count -gt 0) {
    $script:HasData = $true
    foreach ($f in ($csvHits | Sort-Object LastWriteTime)) {
        # Linhas = total - cabecalho. ReadAllLines aguenta os ~14k do dia sem
        # dor; o BOM do utf-8-sig nao atrapalha a contagem de linhas.
        $rows = 0
        try { $rows = [Math]::Max(([System.IO.File]::ReadAllLines($f.FullName)).Count - 1, 0) } catch { $rows = -1 }
        $kb = [Math]::Round($f.Length / 1KB, 1)
        $rowTxt = if ($rows -ge 0) { "$rows linha(s)" } else { "linhas ilegiveis" }
        Write-Ok ("{0} - {1}, {2} KB, {3:HH:mm}" -f $f.Name, $rowTxt, $kb, $f.LastWriteTime)
    }
} else {
    Write-Warn "Nenhum CSV com data de $iso em output\ (nem rac_monitoramento_*, nem raw_*)"
}

# --- 2. Marcadores de sucesso -------------------------------------------------
Write-Sect "Marcadores de conclusao (logs\coleta_<slot>_$compact.done)"

# O marcador so e gravado em caso de SUCESSO (exit 0) - ver
# local_scheduled_collect.bat. Marcador AUSENTE + CSV PRESENTE e a assinatura
# classica de "coletou mas a persistencia falhou e o .bat saiu com exit!=0".
$marcadores = @()
foreach ($slot in @("manha", "tarde", "noite")) {
    $marker = Join-Path $BaseDir "logs\coleta_${slot}_${compact}.done"
    if (Test-Path $marker) {
        Write-Ok "turno '$slot' concluido com exit 0 (marcador presente)"
        $marcadores += $slot
    } else {
        Write-Info "turno '$slot': sem marcador (nao rodou, ainda em janela, ou terminou com erro)"
    }
}

# --- 3. Espelho local da batida de ponto --------------------------------------
Write-Sect "Livro-razao local (logs\heartbeat.jsonl)"

$hbFile = Join-Path $BaseDir "logs\heartbeat.jsonl"
$hbToday = @()
if (Test-Path $hbFile) {
    foreach ($linha in (Get-Content $hbFile -Encoding UTF8)) {
        if ([string]::IsNullOrWhiteSpace($linha)) { continue }
        $reg = $null
        try { $reg = $linha | ConvertFrom-Json } catch { continue }
        if ($reg.data_ref -eq $iso) { $hbToday += $reg }
    }
    if ($hbToday.Count -gt 0) {
        # So os jobs de COLETA provam que um coletor rodou. local_seller_fact e
        # local_tier_migration sao manutencao downstream (reprocessam/podam o que
        # ja existe) e podem bater SUCCESS num dia sem coleta nova - nao podem
        # ligar o HasData sozinhos.
        $jobsColeta = @("local_manha", "local_tarde", "local_noite")
        # Ultima batida por job (a ultima linha do arquivo vence).
        $porJob = [ordered]@{}
        foreach ($reg in $hbToday) { $porJob[$reg.job_id] = $reg }
        foreach ($job in $porJob.Keys) {
            $reg = $porJob[$job]
            $ehColeta = $jobsColeta -contains $job
            $rows = if ($null -ne $reg.rows_written) { "$($reg.rows_written) linha(s)" } else { "sem contagem" }
            $line = "batida: $job = $($reg.status) ($rows)"
            switch ($reg.status) {
                "SUCCESS" { Write-Ok  $line; if ($ehColeta) { $script:HasData = $true } }
                "PARTIAL" { Write-Warn "$line - concluiu sem gravar linha"; }
                "FAILED"  {
                    # FAILED com linhas > 0 num job de COLETA = coletou, mas
                    # alguma etapa de persistencia caiu (tipicamente o upload).
                    if ($ehColeta -and $reg.rows_written -gt 0) {
                        Write-Warn "$line - coletou, mas o job terminou em erro (persistencia?)"
                        $script:HasData = $true
                    } else {
                        Write-Bad "$line"
                    }
                }
                "STARTED" { Write-Warn "$line - comecou e nao registrou fim (travou/matou o processo?)" }
                default   { Write-Info $line }
            }
        }
    } else {
        Write-Warn "heartbeat.jsonl existe, mas sem nenhuma batida com data_ref=$iso"
    }
} else {
    Write-Warn "logs\heartbeat.jsonl nao existe - a coleta instrumentada nunca bateu ponto nesta maquina"
}

# --- 4. O dado chegou ao Supabase? (evidencia positiva, escopada ao dia) ------
Write-Sect "O dado chegou ao Supabase? (logs\scheduler.log, so o dia $iso)"

# Decidimos por EVIDENCIA POSITIVA, nunca por ausencia de falha. E escopamos ao
# DIA: scheduler.log e append-only (run_local_scheduled.bat redireciona toda
# execucao para ca), entao uma falha de ontem nao pode condenar a coleta de hoje.
# Cada run comeca com o banner "=== inicio ===" carimbado com %DATE% - usamos
# esse banner para so olhar as linhas dos runs do dia alvo.
$logFile = Join-Path $BaseDir "logs\scheduler.log"
$quotaNoDia = $false
if (Test-Path $logFile) {
    $cultura   = [System.Globalization.CultureInfo]::CurrentCulture
    $noDia     = $false   # a linha atual pertence a um run do dia alvo?
    $vistoBanner = $false # o log tem banners de run (formato atual)?
    $sinaisUp   = @()     # "inseridos com sucesso" / "ja existiam no banco"
    $sinaisFail = @()     # cota/402/erro de upload/heartbeat so-local
    $sinaisSkip = @()     # "upload IGNORADO" (sem credencial)
    # ⚠ do Loguru: [Heartbeat] so gravou local. String (nao char): o overload
    # String.Contains(char) so existe no .NET Core - no PS 5.1 (Framework) da erro.
    $marcaAlerta = ([char]0x26A0).ToString()

    $falhaTokens = @(
        "exceed_db_size_quota",            # cota de armazenamento estourada
        "RESTRITO por cota",               # mensagem do AdminAuto
        "restricted due to the following", # texto cru da API Supabase
        "com falha: upload Supabase",      # "Etapa(s) ... com falha: upload Supabase"
        "Upload falhou para todos",        # nenhum registro entrou
        "Upload parcial",                  # alguns registros com erro
        "Falha de REDE",                   # queda de rede/DNS no meio do upload
        "retornou falha"                   # "Supabase upload retornou falha"
    )

    foreach ($linha in (Get-Content $logFile -Encoding UTF8)) {
        if ($linha -match '===\s*inicio') {
            $vistoBanner = $true
            $noDia = $false
            # Data do banner: primeiro token dd/mm/aaaa. Parse com a cultura da
            # maquina (foi ela que formatou %DATE%). Regex sem ancora no '[' para
            # tolerar locales que prefixam o dia da semana (ex.: "qua 13/09/2026");
            # o %TIME% (17:52:05) usa ':' e nao casa com estes separadores. Se nao
            # parsear, o bloco fica FORA do dia - preferimos perder um match a
            # atribuir a linha ao dia errado (exatamente o bug que corrigimos).
            $m = [regex]::Match($linha, '(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})')
            if ($m.Success) {
                try {
                    $d = [datetime]::Parse($m.Groups[1].Value, $cultura)
                    if ($d.Date -eq $targetDate) { $noDia = $true }
                } catch { $noDia = $false }
            }
            continue
        }
        if (-not $noDia) { continue }

        if ($linha -match 'inseridos com sucesso' -or $linha -match 'existiam no banco') {
            $sinaisUp += $linha.Trim()
        }
        if ($linha -match 'upload IGNORADO') { $sinaisSkip += $linha.Trim() }
        $ehFalha = $false
        foreach ($tok in $falhaTokens) {
            if ($linha -like "*$tok*") { $ehFalha = $true; break }
        }
        # Heartbeat que denuncia falha - robusto a codepage. O marcador local-only
        # e "⚠ só local" (Loguru/UTF-8, PYTHONUTF8=1), mas se o scheduler.log for
        # gravado em cp1252/OEM o ⚠ vira mojibake e o Contains falha. Por isso
        # casamos TAMBEM o token ASCII estavel FAILED na mesma linha do Heartbeat,
        # que sobrevive a qualquer codepage - a linha so-local sempre traz o status
        # (ex.: "[Heartbeat] ⚠ só local local_tarde FAILED"). Um SUCCESS so-local
        # (dado subiu, so a batida nao) fica de fora de proposito: nao e falha de dado.
        if (-not $ehFalha -and $linha -match 'Heartbeat' -and
            ($linha -match '\bFAILED\b' -or $linha.Contains($marcaAlerta))) {
            $ehFalha = $true
        }
        if ($ehFalha) {
            $sinaisFail += $linha.Trim()
            if ($linha -like "*exceed_db_size_quota*" -or $linha -like "*RESTRITO por cota*") {
                $quotaNoDia = $true
            }
        }
    }

    $hasUp   = $sinaisUp.Count   -gt 0
    $hasFail = $sinaisFail.Count -gt 0
    $hasSkip = $sinaisSkip.Count -gt 0

    if     ($hasFail -and $hasUp) { $script:SbState = "MIXED" }
    elseif ($hasFail)             { $script:SbState = "FAILED" }
    elseif ($hasUp)               { $script:SbState = "UP" }
    elseif ($hasSkip)             { $script:SbState = "SKIPPED" }
    else                          { $script:SbState = "UNKNOWN" }

    # Mostra as linhas de prova (deduplicadas), as mais recentes.
    function Show-Linhas ([string]$rotulo, [string[]]$linhas) {
        if ($linhas.Count -eq 0) { return }
        Write-Info "${rotulo}:"
        $vistos = @{}
        foreach ($l in ($linhas | Select-Object -Last 20)) {
            if (-not $vistos.ContainsKey($l)) { $vistos[$l] = $true; Write-Info "  $l" }
        }
    }

    switch ($script:SbState) {
        "UP"     { Write-Ok "Upload CONFIRMADO por evidencia positiva no log do dia."
                   Show-Linhas "Prova" $sinaisUp }
        "FAILED" { Write-Bad "O log do dia mostra que o Supabase recusou a escrita:"
                   Show-Linhas "Sinais" $sinaisFail }
        "MIXED"  { Write-Warn "Sinais MISTOS no dia: parte subiu, parte falhou (turnos diferentes?)."
                   Show-Linhas "Subiu"  $sinaisUp
                   Show-Linhas "Falhou" $sinaisFail }
        "SKIPPED"{ Write-Bad "Upload PULADO no dia (SUPABASE_URL/KEY ausentes) - o dado ficou so no disco:"
                   Show-Linhas "Sinais" $sinaisSkip }
        default  {
            if ($vistoBanner) {
                Write-Warn "Sem sinal de upload (nem sucesso, nem falha) para $iso - envio NAO confirmado."
            } else {
                Write-Warn "scheduler.log nao tem banner de run por dia - nao da para escopar ao dia; envio NAO confirmado."
            }
        }
    }

    if ($quotaNoDia) {
        Write-Info ""
        Write-Info "Causa raiz: BANCO cheio (cota de armazenamento). O dado NAO se perdeu"
        Write-Info "- esta em output\ (e no Drive/Parquet, se o espelho estiver configurado;"
        Write-Info "  confira com: python scripts\history_cli.py stats). Para religar o Supabase:"
        Write-Info "  1) libere espaco: SQL Editor + scripts\retention_cleanup.sql (+ VACUUM FULL)"
        Write-Info "     ou rode a poda: python scripts\history_cli.py tier --dataset all --confirm"
        Write-Info "  2) quando o banco voltar, reenvie o(s) CSV(s) do dia:"
        Write-Info "     python scripts\history_cli.py import-csv output\rac_monitoramento_*.csv --mirror"
        Write-Info "  (se persistir, o dono do projeto precisa subir o plano / remover spend cap)"
    }
} else {
    Write-Warn "logs\scheduler.log nao existe - sem log da coleta agendada nesta maquina"
}

# --- Veredito -----------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================="
if (-not $script:HasData) {
    Write-Host " VEREDITO: SEM EVIDENCIA de coleta em $iso." -ForegroundColor Red
    Write-Host " Nao ha CSV nem batida de ponto do dia - a tarefa provavelmente nao" -ForegroundColor Red
    Write-Host " rodou. Diagnostique o agendador:" -ForegroundColor Red
    Write-Host "   PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1" -ForegroundColor Gray
    $exit = 1
} else {
    switch ($script:SbState) {
        "UP" {
            Write-Host " VEREDITO: COLETOU e SUBIU." -ForegroundColor Green
            Write-Host " Ha dado local do dia e o log confirma o upload ao Supabase." -ForegroundColor Green
        }
        "FAILED" {
            Write-Host " VEREDITO: COLETOU, mas o dado NAO chegou ao Supabase." -ForegroundColor Yellow
            Write-Host " A coleta rodou e gravou o arquivo local (output\); o Supabase" -ForegroundColor Yellow
            Write-Host " recusou a escrita. E por isso que o painel mostra 'NAO EXECUTOU'" -ForegroundColor Yellow
            Write-Host " (ele so le o banco). O dado local esta seguro - falta reenviar quando" -ForegroundColor Yellow
            Write-Host " o banco voltar (ver os passos acima)." -ForegroundColor Yellow
        }
        "MIXED" {
            Write-Host " VEREDITO: COLETOU; parte do dia NAO chegou ao Supabase." -ForegroundColor Yellow
            Write-Host " Ha upload confirmado E falha no mesmo dia (turnos diferentes)." -ForegroundColor Yellow
            Write-Host " Reenvie os CSVs dos turnos que falharam (ver os passos acima)." -ForegroundColor Yellow
        }
        "SKIPPED" {
            Write-Host " VEREDITO: COLETOU, mas o upload foi PULADO (sem credencial)." -ForegroundColor Yellow
            Write-Host " O dado ficou so no arquivo local (output\). Configure SUPABASE_URL" -ForegroundColor Yellow
            Write-Host " e SUPABASE_KEY (service_role) no .env e reenvie o(s) CSV(s):" -ForegroundColor Yellow
            Write-Host "   python scripts\history_cli.py import-csv output\rac_monitoramento_*.csv --mirror" -ForegroundColor Gray
        }
        default {
            # UNKNOWN: ha dado local, mas o log nao prova nem nega o upload.
            # NUNCA afirmamos 'SUBIU' aqui - seria inferir do silencio.
            Write-Host " VEREDITO: COLETOU; envio ao Supabase NAO CONFIRMADO." -ForegroundColor Yellow
            Write-Host " Ha dado local do dia, mas o log nao traz prova de upload nem de" -ForegroundColor Yellow
            Write-Host " falha para $iso. Confirme no banco ou reenvie por seguranca:" -ForegroundColor Yellow
            Write-Host "   python scripts\history_cli.py import-csv output\rac_monitoramento_*.csv --mirror" -ForegroundColor Gray
        }
    }
    # Dado local existe nos cinco casos acima: a coleta rodou, o dado nao se
    # perdeu. Exit 0 reserva o 1 para 'nao rodou'.
    $exit = 0
}
Write-Host "==========================================================="

if ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost") {
    Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

exit $exit
