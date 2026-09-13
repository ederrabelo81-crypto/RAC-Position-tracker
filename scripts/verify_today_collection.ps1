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
# ...e le logs\scheduler.log atras da assinatura de falha do Supabase
# (402 / exceed_db_size_quota / "so local" / "upload Supabase").
#
# Com isso separa os tres estados que o painel colapsa em um:
#   - COLETOU e SUBIU          -> tudo certo
#   - COLETOU, NAO SUBIU       -> dado seguro no disco/Drive; o banco e o problema
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
$script:HasData   = $false   # ha alguma prova de coleta (CSV ou batida com linhas)
$script:Supabase  = $true    # o dado chegou ao Supabase (ate prova em contrario)

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
        # Ultima batida por job (a ultima linha do arquivo vence).
        $porJob = [ordered]@{}
        foreach ($reg in $hbToday) { $porJob[$reg.job_id] = $reg }
        foreach ($job in $porJob.Keys) {
            $reg = $porJob[$job]
            $rows = if ($null -ne $reg.rows_written) { "$($reg.rows_written) linha(s)" } else { "sem contagem" }
            $line = "batida: $job = $($reg.status) ($rows)"
            switch ($reg.status) {
                "SUCCESS" { Write-Ok  $line; $script:HasData = $true }
                "PARTIAL" { Write-Warn "$line - concluiu sem gravar linha"; }
                "FAILED"  {
                    # FAILED com linhas > 0 = coletou, mas alguma etapa de
                    # persistencia caiu (tipicamente o upload ao Supabase).
                    if ($reg.rows_written -gt 0) {
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

# --- 4. Assinatura de falha do Supabase no log da coleta ----------------------
Write-Sect "O dado chegou ao Supabase? (logs\scheduler.log)"

$logFile = Join-Path $BaseDir "logs\scheduler.log"
if (Test-Path $logFile) {
    $texto = Get-Content $logFile -Encoding UTF8

    # Padroes que provam que a coleta rodou mas o Supabase recusou. Sao
    # comparados sem depender de acento (o -match e por substring simples).
    $padroesFalha = @(
        "exceed_db_size_quota",              # cota de armazenamento estourada
        "RESTRITO por cota",                 # mensagem do AdminAuto
        "restricted due to the following",   # texto cru da API Supabase
        "\bso local\b",                      # [Heartbeat] "so local" (sem acento no regex)
        "upload Supabase",                   # "Etapa(s) ... com falha: upload Supabase"
        "Supabase upload retornou falha"
    )
    $achou = @()
    foreach ($p in $padroesFalha) {
        $m = $texto | Select-String -Pattern $p -SimpleMatch:($p -notmatch '\\') -ErrorAction SilentlyContinue
        if ($m) { $achou += $m }
    }

    if ($achou.Count -gt 0) {
        $script:Supabase = $false
        Write-Bad "O log tem sinais de que o Supabase recusou a escrita:"
        # Mostra ate 6 linhas relevantes, as mais recentes, sem repetir.
        $vistos = @{}
        foreach ($m in ($achou | Select-Object -Last 30)) {
            $l = $m.Line.Trim()
            if (-not $vistos.ContainsKey($l)) {
                $vistos[$l] = $true
                Write-Info $l
            }
        }
        if ($texto -match "exceed_db_size_quota" -or ($texto -match "RESTRITO por cota")) {
            Write-Info ""
            Write-Info "Causa raiz: BANCO cheio (cota de armazenamento). O dado NAO se perdeu"
            Write-Info "- esta em output\ e no Drive (Parquet). Para religar o Supabase:"
            Write-Info "  1) libere espaco: SQL Editor + scripts\retention_cleanup.sql (+ VACUUM FULL)"
            Write-Info "     ou rode a poda: python scripts\history_cli.py tier --dataset all --confirm"
            Write-Info "  2) quando o banco voltar, reenvie o(s) CSV(s) do dia:"
            Write-Info "     python scripts\history_cli.py import-csv output\rac_monitoramento_*.csv --mirror"
            Write-Info "  (se persistir, o dono do projeto precisa subir o plano / remover spend cap)"
        }
    } else {
        Write-Ok "Sem sinais de recusa do Supabase no log (a escrita provavelmente subiu)"
    }
} else {
    Write-Warn "logs\scheduler.log nao existe - sem log da coleta agendada nesta maquina"
}

# --- Veredito -----------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================="
if ($script:HasData -and $script:Supabase) {
    Write-Host " VEREDITO: COLETOU e SUBIU." -ForegroundColor Green
    Write-Host " Ha dado local do dia e nenhum sinal de recusa do Supabase." -ForegroundColor Green
    $exit = 0
} elseif ($script:HasData -and -not $script:Supabase) {
    Write-Host " VEREDITO: COLETOU, mas o dado NAO chegou ao Supabase." -ForegroundColor Yellow
    Write-Host " A coleta rodou e gravou os arquivos (output\ + Drive); o Supabase" -ForegroundColor Yellow
    Write-Host " recusou a escrita. E por isso que o painel mostra 'NAO EXECUTOU'" -ForegroundColor Yellow
    Write-Host " (ele so le o banco). O dado esta seguro - falta reenviar quando o" -ForegroundColor Yellow
    Write-Host " banco voltar (ver os passos acima)." -ForegroundColor Yellow
    $exit = 0
} else {
    Write-Host " VEREDITO: SEM EVIDENCIA de coleta em $iso." -ForegroundColor Red
    Write-Host " Nao ha CSV nem batida de ponto do dia - a tarefa provavelmente nao" -ForegroundColor Red
    Write-Host " rodou. Diagnostique o agendador:" -ForegroundColor Red
    Write-Host "   PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1" -ForegroundColor Gray
    $exit = 1
}
Write-Host "==========================================================="

if ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost") {
    Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

exit $exit
