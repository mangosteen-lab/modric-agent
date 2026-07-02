<#
  Modric Agent installer (Windows).

    irm https://raw.githubusercontent.com/mangosteen-lab/modric-agent/master/scripts/install.ps1 | iex

  Run in an ELEVATED PowerShell (Run as Administrator). Downloads the latest public
  release, installs it to "C:\Program Files\mangosteen\modric-agent", prompts for the
  Toil connection settings, and registers the agent (a background service, or the
  interactive-desktop mode for GUI job steps).

  Non-interactive: pre-set the MODRIC_* env vars and no prompts are shown, e.g.
    $env:MODRIC_TOIL_WSS_URL="wss://toil/ws/soil"; $env:MODRIC_TOIL_API_KEY="key"
    $env:MODRIC_AGENT_SERVICE_MODE="1"   # 1=service, 2=interactive desktop
    irm .../install.ps1 | iex

  Env overrides: MODRIC_AGENT_HOME, MODRIC_AGENT_TARBALL_URL, MODRIC_AGENT_REPO,
  MODRIC_TOIL_WSS_URL, MODRIC_TOIL_API_KEY, MODRIC_AGENT_NAME, MODRIC_AGENT_CAPACITY,
  MODRIC_AGENT_LABELS, MODRIC_AGENT_UPGRADE_CHANNEL, MODRIC_AGENT_AUTO_UPGRADE,
  MODRIC_AGENT_SERVICE_MODE, FORCE_CONFIG.
#>
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Log($m)  { Write-Host ">> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!! $m" -ForegroundColor Yellow }

# --- preconditions ---------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent() `
         ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { throw "Please run this in an elevated PowerShell (Run as Administrator)." }

$repo = if ($env:MODRIC_AGENT_REPO) { $env:MODRIC_AGENT_REPO } else { "mangosteen-lab/modric-agent" }
$installDir = if ($env:MODRIC_AGENT_HOME) { $env:MODRIC_AGENT_HOME }
              else { Join-Path ${env:ProgramFiles} "mangosteen\modric-agent" }

# Prompt helper: returns the env var if already set, else asks (with optional default).
function Ask($name, $prompt, $default) {
  $cur = [Environment]::GetEnvironmentVariable($name, 'Process')
  if ($cur) { return $cur }
  if ($default) {
    $in = Read-Host "$prompt [$default]"
    if (-not $in) { $in = $default }
  } else {
    do { $in = Read-Host $prompt } while (-not $in)
  }
  [Environment]::SetEnvironmentVariable($name, $in, 'Process')
  return $in
}

# --- 1. resolve the tarball URL -------------------------------------------
if ($env:MODRIC_AGENT_TARBALL_URL) {
  $url = $env:MODRIC_AGENT_TARBALL_URL
} else {
  Log "Finding the latest release of $repo"
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" `
                           -Headers @{ 'User-Agent' = 'modric-installer' }
  $asset = $rel.assets | Where-Object { $_.name -like '*.tar.gz' } | Select-Object -First 1
  if (-not $asset) { throw "No .tar.gz asset in the latest release of $repo." }
  $url = $asset.browser_download_url
}
$tarName = Split-Path $url -Leaf
Log "Release artifact: $tarName"

# --- 2. download (+ verify against SHA256SUMS if present) ------------------
$tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP ("modric-" + [guid]::NewGuid()))
$tarPath = Join-Path $tmp $tarName
Invoke-WebRequest $url -OutFile $tarPath -UseBasicParsing
try {
  $sumsUrl = ($url -replace '/[^/]+$', '/SHA256SUMS')
  $sums = (Invoke-WebRequest $sumsUrl -UseBasicParsing).Content
  $want = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($tarName) } |
           Select-Object -First 1) -split '\s+' | Select-Object -First 1
  $got = (Get-FileHash $tarPath -Algorithm SHA256).Hash.ToLower()
  if ($want -and ($want.ToLower() -eq $got)) { Log "Checksum OK" }
  else { Warn "Checksum could not be verified — continuing." }
} catch { Warn "No SHA256SUMS published for this release — skipping checksum." }

# --- 3. extract into the install dir (config.ini is not in the archive) ----
Log "Installing to $installDir"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
& tar.exe -xzf $tarPath --strip-components=1 -C $installDir
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed ($LASTEXITCODE)." }

# --- 4. uv (per-project venv) ---------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Log "Installing uv"
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv is not on PATH after install." }

Log "Syncing dependencies (uv sync)"
Push-Location $installDir
try {
  & uv sync
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed ($LASTEXITCODE)." }

  # --- 5. prompt for connection settings ----------------------------------
  Ask 'MODRIC_TOIL_WSS_URL'        'Toil WebSocket URL (wss://host/ws/soil)' $null | Out-Null
  Ask 'MODRIC_TOIL_API_KEY'        'Toil API key'                           $null | Out-Null
  Ask 'MODRIC_AGENT_NAME'          'Machine name'                           $env:COMPUTERNAME | Out-Null
  Ask 'MODRIC_AGENT_LABELS'        'Labels (comma-separated key=value)'     '' | Out-Null
  Ask 'MODRIC_AGENT_CAPACITY'      'Max concurrent jobs'                    '10' | Out-Null
  Ask 'MODRIC_AGENT_UPGRADE_CHANNEL' 'Upgrade channel'                     'stable' | Out-Null
  Ask 'MODRIC_AGENT_AUTO_UPGRADE'  'Auto-upgrade (true/false)'             'true' | Out-Null

  # --- 6. render conf\config.ini (reuses app.bootstrap) -------------------
  $config = Join-Path $installDir "conf\config.ini"
  $env:MODRIC_AGENT_CONFIG = $config
  if ((Test-Path $config) -and -not $env:FORCE_CONFIG) {
    Log "Keeping existing $config (set `$env:FORCE_CONFIG=1 to overwrite)"
  } else {
    Log "Writing $config"
    & uv run python -c "from app.bootstrap import render_config; render_config()"
    if ($LASTEXITCODE -ne 0) { throw "Config rendering failed ($LASTEXITCODE)." }
  }

  # --- 7. install the agent (background service or interactive desktop) ----
  $mode = Ask 'MODRIC_AGENT_SERVICE_MODE' `
    "Run mode - [1] background service (no desktop)  [2] interactive desktop (GUI job steps)" '1'
  if ($mode -eq '2') {
    Log "Installing interactive-desktop launcher"
    & uv run python -m app.main service install-interactive
  } else {
    Log "Installing the modric-agent service"
    & uv run python -m app.main service install
  }
} finally {
  Pop-Location
}

Log "Done. Machine '$env:MODRIC_AGENT_NAME' should appear in Toil shortly."
Log "Logs: $installDir\logs\agent.log"
