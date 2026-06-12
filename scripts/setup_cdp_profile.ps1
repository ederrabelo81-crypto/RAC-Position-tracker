# =============================================================================
# setup_cdp_profile.ps1 - Copia o perfil Chrome do usuario para uso CDP.
#
# Executar UMA VEZ antes do primeiro start_chrome_cdp.bat. Detecta automaticamente
# em qual slot (Default, Profile 1, Profile 2...) esta o perfil "Eder" lendo o
# arquivo Preferences de cada slot. Copia esse perfil para C:\chrome-rac-cdp,
# preservando cookies, extensoes, historico e fingerprint do browser.
#
# Uso:
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -Slot Default
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -Slot "Profile 1"
#   PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -ProfileName "Lumina"
# (-Slot escolhe direto a pasta do perfil — use quando os nomes locais se
#  repetem, ex.: dois slots "Seu Chrome"; confira o seu em chrome://version)
#
# Re-executar: deleta C:\chrome-rac-cdp antes (apaga sessao acumulada):
#   Remove-Item C:\chrome-rac-cdp -Recurse -Force
# =============================================================================

param(
    [string]$ProfileName = "Eder",
    # Slot direto (ex: "Default", "Profile 1") — resolve ambiguidade quando
    # varios perfis tem o mesmo nome local ("Seu Chrome"/"Pessoa 1")
    [string]$Slot = "",
    # Pasta de dados do Chrome do usuario LOGADO — nao fixar usuario no path
    [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"),
    [string]$CdpDataDir  = "C:\chrome-rac-cdp"
)

$ErrorActionPreference = "Stop"

$CdpProfile = Join-Path $CdpDataDir "Default"

Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "  Setup CDP Profile - Copia perfil Chrome para uso CDP" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

# --- Valida pasta base do Chrome -------------------------------------------
if (-not (Test-Path -LiteralPath $UserDataDir)) {
    Write-Host "[ERRO] Pasta do Chrome nao encontrada:" -ForegroundColor Red
    Write-Host "  $UserDataDir" -ForegroundColor Red
    Write-Host "Chrome esta instalado e ja foi usado pelo menos uma vez?"
    exit 1
}

# --- Lista todos os slots de perfil e seus nomes ----------------------------
Write-Host "Procurando slot do perfil '$ProfileName'..."
Write-Host ""

$profiles = @()
Get-ChildItem -LiteralPath $UserDataDir -Directory |
    Where-Object { $_.Name -match '^(Default|Profile \d+)$' } |
    ForEach-Object {
        $pref = Join-Path $_.FullName "Preferences"
        if (Test-Path -LiteralPath $pref) {
            try {
                # Preferences e UTF-8; sem -Encoding os acentos viram mojibake
                $json = Get-Content -LiteralPath $pref -Raw -Encoding UTF8 | ConvertFrom-Json
                $email = ""
                try {
                    if ($json.account_info -and @($json.account_info).Count -gt 0) {
                        $email = @($json.account_info)[0].email
                    }
                } catch { }
                $profiles += [PSCustomObject]@{
                    Slot  = $_.Name
                    Name  = $json.profile.name
                    Email = $email
                    Path  = $_.FullName
                }
            } catch {
                # Preferences invalido - ignora silenciosamente
            }
        }
    }

if ($profiles.Count -eq 0) {
    Write-Host "[ERRO] Nenhum perfil Chrome encontrado em $UserDataDir." -ForegroundColor Red
    exit 1
}

# Encontra o match — por slot (direto, sem ambiguidade) ou por nome local
if ($Slot) {
    $match = $profiles | Where-Object { $_.Slot -eq $Slot } | Select-Object -First 1
    if ($null -eq $match) {
        Write-Host "[ERRO] Slot '$Slot' nao encontrado em $UserDataDir." -ForegroundColor Red
    }
} else {
    $match = $profiles | Where-Object { $_.Name -eq $ProfileName } | Select-Object -First 1

    # Sem match pelo nome mas so existe UM perfil → usa ele (maquina nova/pessoal)
    if ($null -eq $match -and $profiles.Count -eq 1) {
        $match = $profiles[0]
        Write-Host "[INFO] Perfil '$ProfileName' nao existe; usando o unico perfil da maquina: '$($match.Name)' ($($match.Slot))" -ForegroundColor Yellow
    }
    if ($null -eq $match) {
        Write-Host "[ERRO] Nenhum perfil com nome '$ProfileName' encontrado." -ForegroundColor Red
    }
}

if ($null -eq $match) {
    Write-Host ""
    Write-Host "Perfis disponiveis (slot -> nome local | conta Google):" -ForegroundColor Yellow
    $profiles | ForEach-Object {
        Write-Host ("  {0,-12} -> {1,-20} | {2}" -f $_.Slot, $_.Name, $_.Email) -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Selecione pelo SLOT (recomendado — nomes locais se repetem):" -ForegroundColor Yellow
    Write-Host "  PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -Slot Default" -ForegroundColor Gray
    Write-Host "  PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -Slot 'Profile 3'" -ForegroundColor Gray
    Write-Host "ou pelo nome local:" -ForegroundColor Yellow
    Write-Host "  PowerShell -ExecutionPolicy Bypass -File scripts\setup_cdp_profile.ps1 -ProfileName 'Nome Exato'" -ForegroundColor Gray
    exit 1
}

$SourceProfile = $match.Path

$contaInfo = if ($match.Email) { " | conta: $($match.Email)" } else { "" }
Write-Host "[OK] Perfil selecionado: $($match.Slot) ('$($match.Name)'$contaInfo)" -ForegroundColor Green
Write-Host ""
Write-Host "Origem  : $SourceProfile"
Write-Host "Destino : $CdpProfile"
Write-Host ""

# --- Avisa se ja existe ----------------------------------------------------
if (Test-Path -LiteralPath $CdpProfile) {
    Write-Host "[AVISO] Ja existe um perfil CDP em $CdpProfile." -ForegroundColor Yellow
    Write-Host "Se continuar, sera SOBRESCRITO com o conteudo atual."
    Write-Host "Para preservar a sessao acumulada do CDP, cancele agora (Ctrl+C)."
    Write-Host ""
}

# --- Verifica se Chrome esta aberto ----------------------------------------
$chromeProcs = Get-Process chrome -ErrorAction SilentlyContinue
if ($chromeProcs) {
    Write-Host "[AVISO] Detectados $($chromeProcs.Count) processo(s) Chrome rodando." -ForegroundColor Yellow
    Write-Host "Chrome trava arquivos do perfil enquanto aberto."
    Write-Host "Voce quer matar todos os Chromes agora antes de copiar? [S/N]"
    $resp = Read-Host
    if ($resp -match '^[Ss]') {
        Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 2
        Write-Host "Chromes fechados." -ForegroundColor Green
    } else {
        Write-Host "Continuando com Chrome aberto (alguns arquivos podem nao copiar)..." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Pressione qualquer tecla para iniciar a copia (ou Ctrl+C para cancelar)..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host ""
Write-Host "=== Copiando perfil (pode levar 1-5 min dependendo do tamanho) ===" -ForegroundColor Cyan
Write-Host ""

# --- Robocopy --------------------------------------------------------------
$excludedDirs = @(
    "Cache", "Code Cache", "GPUCache", "Service Worker", "Crashpad",
    "ShaderCache", "DawnCache", "GrShaderCache", "GraphiteDawnCache",
    "blob_storage", "Storage", "VideoDecodeStats", "Site Characteristics Database"
)
$excludedFiles = @("Singleton*", "lockfile", "LOCK", "*.tmp")

$robocopyArgs = @(
    "`"$SourceProfile`"",
    "`"$CdpProfile`"",
    "/E",
    "/XD"
) + $excludedDirs.ForEach({"`"$_`""}) + @(
    "/XF"
) + $excludedFiles.ForEach({"`"$_`""}) + @(
    "/R:1", "/W:1", "/NJH", "/NJS", "/NDL", "/NFL"
)

$proc = Start-Process robocopy -ArgumentList $robocopyArgs -NoNewWindow -PassThru -Wait
$rc = $proc.ExitCode

# Robocopy retorna 0-7 como sucesso (8+ e erro real)
if ($rc -ge 8) {
    Write-Host ""
    Write-Host "[ERRO] Robocopy falhou. Codigo: $rc" -ForegroundColor Red
    Write-Host "Verifique se o Chrome esta totalmente fechado e tente de novo."
    exit 2
}

# --- Copia Local State (metadata dos perfis) -------------------------------
$localStateSrc = Join-Path $UserDataDir "Local State"
$localStateDst = Join-Path $CdpDataDir "Local State"
if (Test-Path -LiteralPath $localStateSrc) {
    Copy-Item -LiteralPath $localStateSrc -Destination $localStateDst -Force
    Write-Host "Local State copiado." -ForegroundColor Gray
}

# --- Calcula tamanho final -------------------------------------------------
$totalSize = (Get-ChildItem -LiteralPath $CdpDataDir -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
$sizeMB = [math]::Round($totalSize / 1MB, 1)

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host "  Copia concluida com sucesso!" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Slot origem  : $($match.Slot) (perfil '$ProfileName')"
Write-Host "Salvo em     : $CdpProfile"
Write-Host "Tamanho      : $sizeMB MB"
Write-Host ""
Write-Host "PROXIMOS PASSOS:" -ForegroundColor Yellow
Write-Host "  1. Voce ja pode reabrir seu Chrome normal - sem conflito de lock." -ForegroundColor Gray
Write-Host "  2. Rode: scripts\start_chrome_cdp.bat" -ForegroundColor Gray
Write-Host "  3. Confirme que o site do Magalu carrega normalmente (HTTPS, sem captcha)" -ForegroundColor Gray
Write-Host "  4. Configure o Task Scheduler: setup_magalu_scheduler.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "Pressione qualquer tecla para fechar..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
