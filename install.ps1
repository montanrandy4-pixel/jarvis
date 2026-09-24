<#
Installs JARVIS on Windows, start to finish: Python if it is missing, the local
AI engine (Ollama) and a model, JARVIS itself in its own environment, a Start
menu and desktop shortcut with the Ctrl+Alt+J hotkey, and optionally
start-at-login.

Easiest: double-click install.cmd. Or, in PowerShell:
    powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes] [-AutoStart] [-NoAutoStart] [-Model NAME]

Safe to run again: it updates what is already there.
#>
param(
    [switch]$Yes,
    [switch]$AutoStart,
    [switch]$NoAutoStart,
    [string]$Model = $(if ($env:JARVIS_MODEL) { $env:JARVIS_MODEL } else { 'llama3.1:8b' })
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is slow with a progress bar.
$Repo = 'https://github.com/montanrandy4-pixel/jarvis'
$JarvisHome = if ($env:JARVIS_HOME) { $env:JARVIS_HOME } else { Join-Path $env:USERPROFILE '.jarvis' }

function Step($text) { Write-Host "`n> $text" -ForegroundColor Cyan }
function Say($text) { Write-Host "  $text" }
function Fail($text) {
    Write-Host "`nX $text" -ForegroundColor Red
    exit 1
}
function Ask($question, [bool]$default = $true) {
    if ($Yes) { return $default }
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    $answer = Read-Host "  $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith('y')
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Has-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }

Write-Host 'J A R V I S  installer' -ForegroundColor Cyan

# --- 1. Python ---------------------------------------------------------------
Step 'Checking for Python 3.10 or newer'

function Find-Python {
    $candidates = @()
    foreach ($name in 'py', 'python', 'python3') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        # Skip the Microsoft Store placeholder, which opens the Store instead of running.
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $candidates += $cmd.Source }
    }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($exe in $candidates) {
        try {
            $pyArgs = if ((Split-Path $exe -Leaf) -eq 'py.exe') { @('-3') } else { @() }
            # No quotes inside the code: Windows PowerShell 5.1 mangles them.
            $path = & $exe @pyArgs -c 'import sys; sys.version_info >= (3, 10) and print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $path) { return "$path".Trim() }
        } catch { }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    if ((Has-Winget) -and (Ask 'Python 3.10+ is needed. Install Python 3.12 now?')) {
        winget install --exact --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $Python = Find-Python
    }
    if (-not $Python) {
        Fail 'Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.'
    }
}
Say "using $Python"

# --- 2. Ollama, the engine that runs the model on this computer --------------
Step 'Checking for Ollama (runs the AI model on this computer)'

function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path $default) { return $default }
    return $null
}
function Ollama-Up {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

$Ollama = Find-Ollama
if (-not $Ollama) {
    if (-not (Ask 'Ollama is not installed. Install it now?')) {
        Fail 'JARVIS needs Ollama. Get it from https://ollama.com/download and run this again.'
    }
    if (Has-Winget) {
        winget install --exact --id Ollama.Ollama --silent `
            --accept-package-agreements --accept-source-agreements
    } else {
        $setup = Join-Path $env:TEMP 'OllamaSetup.exe'
        Say 'downloading OllamaSetup.exe'
        Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $setup
        Start-Process -FilePath $setup -ArgumentList '/SILENT' -Wait
    }
    Refresh-Path
    $Ollama = Find-Ollama
    if (-not $Ollama) { Fail 'Ollama did not install. Get it from https://ollama.com/download and run this again.' }
}
if (-not (Ollama-Up)) {
    Say 'starting the Ollama server'
    Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden
    $tries = 0
    while (-not (Ollama-Up)) {
        $tries++
        if ($tries -gt 30) { Fail "Ollama did not start. Open Ollama from the Start menu, then run this again." }
        Start-Sleep -Seconds 1
    }
}
Say 'Ollama is running'

# --- 3. The model --------------------------------------------------------------
Step "Downloading the model $Model (about 5 GB the first time)"
$have = @()
try { $have = & $Ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] } } catch { }
if ($have -contains $Model) {
    Say 'already downloaded'
} else {
    & $Ollama pull $Model
    if ($LASTEXITCODE -ne 0) { Fail "Could not download $Model." }
}

# --- 4. JARVIS -------------------------------------------------------------------
Step "Installing JARVIS into $JarvisHome"
$here = $PSScriptRoot
if ($env:JARVIS_SOURCE) {
    $source = $env:JARVIS_SOURCE
} elseif ($here -and (Test-Path (Join-Path $here 'pyproject.toml')) -and
          (Select-String -Path (Join-Path $here 'pyproject.toml') -Pattern '^name = "jarvis"' -Quiet)) {
    $source = $here
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    $source = "git+$Repo"
} else {
    $source = "$Repo/archive/HEAD.zip"
}
Say "from $source"

$venv = Join-Path $JarvisHome 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
New-Item -ItemType Directory -Force -Path $JarvisHome | Out-Null
if (-not (Test-Path $venvPython)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create a Python environment.' }
}
& $venvPython -m pip install --quiet --upgrade pip
# Force the reinstall: pip would otherwise keep an older copy of the same version.
& $venvPython -m pip install --quiet --upgrade --force-reinstall $source
if ($LASTEXITCODE -ne 0) { Fail 'Could not install JARVIS.' }

# Let 'jarvis' work in new terminals too.
$scripts = Join-Path $venv 'Scripts'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $scripts)) {
    $newPath = (@($userPath, $scripts) | Where-Object { $_ }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Say "added $scripts to your PATH"
}

if ($Model -ne 'llama3.1:8b') {
    $configDir = Join-Path $env:USERPROFILE '.config\jarvis'
    $configFile = Join-Path $configDir 'config.toml'
    if (-not (Test-Path $configFile)) {
        New-Item -ItemType Directory -Force -Path $configDir | Out-Null
        Set-Content -Path $configFile -Value "[jarvis]`nmodel = `"$Model`"" -Encoding UTF8
        Say "saved model = $Model in $configFile"
    }
}

# --- 5. Shortcuts ------------------------------------------------------------------
Step 'Adding JARVIS to the Start menu and desktop'
$setupArgs = @('-m', 'jarvis', 'setup')
$wantAutoStart = if ($AutoStart) { $true } elseif ($NoAutoStart) { $false } else {
    Ask 'Start JARVIS in the background when you sign in? (opens instantly)'
}
if ($wantAutoStart) { $setupArgs += '--autostart' }
& $venvPython @setupArgs
if ($LASTEXITCODE -ne 0) { Fail 'Could not create the shortcuts.' }

# --- 6. Check and open ------------------------------------------------------------
Step 'Checking everything works'
& $venvPython -m jarvis doctor

Step 'Opening JARVIS'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
Start-Process -FilePath $pythonw -ArgumentList '-m', 'jarvis', 'app'
Write-Host "`nJARVIS is installed. " -NoNewline -ForegroundColor Green
Write-Host 'Press Ctrl+Alt+J any time to open it, or find it in the Start menu.'
