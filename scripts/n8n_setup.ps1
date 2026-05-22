# ==============================================================
# scripts/n8n_setup.ps1 - Instalacao do n8n self-hosted (Windows)
#
# Instala Node.js + n8n e registra uma tarefa agendada que sobe o
# n8n no logon. Usado para a orquestracao local de upload de CSV
# (workflow n8n/rac_coleta_monitor.json).
#
# Arquivo em ASCII puro de proposito: o PowerShell do Windows
# quebra o parsing se ler caracteres nao-ASCII sem BOM.
#
# Execute como Administrador:
#   powershell -ExecutionPolicy Bypass -File scripts\n8n_setup.ps1
# ==============================================================

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectPath = Split-Path -Parent $ScriptDir

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  RAC Position Tracker - Instalacao do n8n (local)" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Projeto: $ProjectPath" -ForegroundColor Gray
Write-Host ""

# --- 1. Node.js -----------------------------------------------
Write-Host "[1/4] Verificando Node.js..." -ForegroundColor Yellow
$nodeOk = $false
try {
    $nodeVersion = (& node --version) 2>$null
    if ($nodeVersion -match "v(\d+)\.") {
        if ([int]$Matches[1] -ge 20) {
            Write-Host "[OK] Node.js $nodeVersion" -ForegroundColor Green
            $nodeOk = $true
        } else {
            Write-Host "[!!] Node.js $nodeVersion e antigo demais - o n8n exige Node 20+" -ForegroundColor Red
        }
    }
} catch {
    Write-Host "    Node.js nao encontrado." -ForegroundColor Gray
}

if (-not $nodeOk) {
    Write-Host "    Tentando instalar via winget..." -ForegroundColor Gray
    try {
        & winget install --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
    } catch {
        $LASTEXITCODE = 1
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Node.js instalado - FECHE e reabra o PowerShell, depois rode este script de novo." -ForegroundColor Green
    } else {
        Write-Host "[!!] Nao foi possivel instalar automaticamente." -ForegroundColor Red
        Write-Host "    Instale o Node.js LTS (20+) manualmente: https://nodejs.org" -ForegroundColor Red
    }
    exit 1
}

# --- 2. n8n ---------------------------------------------------
Write-Host ""
Write-Host "[2/4] Instalando o n8n (npm install -g n8n)..." -ForegroundColor Yellow
Write-Host "    Isso pode levar alguns minutos." -ForegroundColor Gray
& npm install -g n8n
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!!] Falha ao instalar o n8n (npm retornou $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}
$n8nCmd = Join-Path $env:APPDATA "npm\n8n.cmd"
if (-not (Test-Path $n8nCmd)) {
    Write-Host "[!!] n8n.cmd nao encontrado em $n8nCmd" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] n8n instalado" -ForegroundColor Green

# --- 3. Tarefa agendada (sobe o n8n no logon) -----------------
Write-Host ""
Write-Host "[3/4] Registrando tarefa agendada 'RAC n8n'..." -ForegroundColor Yellow
$TaskName = "RAC n8n"
$Action   = New-ScheduledTaskAction -Execute $env:ComSpec -Argument "/c `"$n8nCmd`""
$Trigger  = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Sobe o n8n self-hosted no logon (orquestracao RAC)" | Out-Null
    Write-Host "[OK] Tarefa 'RAC n8n' registrada (inicia no logon)" -ForegroundColor Green
} catch {
    Write-Host "[!!] Falha ao registrar a tarefa: $_" -ForegroundColor Red
    Write-Host "    Rode este script como Administrador." -ForegroundColor Red
}

# --- 4. Iniciar o n8n agora -----------------------------------
Write-Host ""
Write-Host "[4/4] Iniciando o n8n..." -ForegroundColor Yellow
$jaRodando = Get-NetTCPConnection -LocalPort 5678 -State Listen -ErrorAction SilentlyContinue
if ($jaRodando) {
    Write-Host "[OK] n8n ja esta rodando na porta 5678" -ForegroundColor Green
} else {
    Start-Process -FilePath $env:ComSpec -ArgumentList "/c `"$n8nCmd`"" -WindowStyle Hidden
    Write-Host "[OK] n8n iniciando em segundo plano (porta 5678)" -ForegroundColor Green
}

# --- Checagem das dependencias do projeto ---------------------
Write-Host ""
Write-Host "Verificando dependencias do projeto..." -ForegroundColor Yellow
$venvPy = Join-Path $ProjectPath "venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host "[OK] venv encontrado" -ForegroundColor Green
} else {
    Write-Host "[!!] venv nao encontrado - rode: python -m venv venv; venv\Scripts\activate; pip install -r requirements.txt" -ForegroundColor Red
}
if (Test-Path (Join-Path $ProjectPath ".env")) {
    Write-Host "[OK] .env encontrado" -ForegroundColor Green
} else {
    Write-Host "[!!] .env nao encontrado - crie com SUPABASE_URL e SUPABASE_KEY" -ForegroundColor Red
}
$outDir = Join-Path $ProjectPath "output"
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}
Write-Host "[OK] pasta output pronta" -ForegroundColor Green

# --- Proximos passos ------------------------------------------
$workflowFile = Join-Path $ProjectPath "n8n\rac_coleta_monitor.json"
$venvPyPath   = Join-Path $ProjectPath "venv\Scripts\python.exe"
$helperPath   = Join-Path $ProjectPath "scripts\n8n_upload.py"
$uploadCmd    = '"' + $venvPyPath + '" "' + $helperPath + '" "{{ $(''Validar CSV'').item.json.filename }}" 2>&1'

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Proximos passos (manuais, dentro do n8n)" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "1. Aguarde ~1 min e abra:  http://localhost:5678" -ForegroundColor Gray
Write-Host "2. Crie a conta de owner (e-mail + senha) - local, sem trial." -ForegroundColor Gray
Write-Host "3. Workflows > Import from File. Selecione:" -ForegroundColor Gray
Write-Host "   $workflowFile" -ForegroundColor White
Write-Host "4. Abra o no 'Coleta Pronta' e coloque no campo 'path':" -ForegroundColor Gray
Write-Host "   $outDir" -ForegroundColor White
Write-Host "5. Abra o no 'Upload CSV' e substitua o comando inteiro por:" -ForegroundColor Gray
Write-Host "   $uploadCmd" -ForegroundColor White
Write-Host "6. Crie a credencial 'Telegram API' e ligue nos nos Telegram." -ForegroundColor Gray
Write-Host "7. Ative o workflow (toggle Active)." -ForegroundColor Gray
Write-Host ""
