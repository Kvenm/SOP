[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.10",
    [string]$NodeVersion = "20.19.4",
    [string]$InnoVersion = "6.7.3",
    [string]$AppVersion = "0.2.0",
    [switch]$SkipSmoke,
    [switch]$SkipCompile,
    [switch]$InstallInnoSetup
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WorkRoot = Join-Path $RepoRoot ".tmp\windows-installer"
$DownloadDir = Join-Path $WorkRoot "downloads"
$ExtractDir = Join-Path $WorkRoot "extract"
$StageDir = Join-Path $WorkRoot "stage\app"
$DistDir = Join-Path $WorkRoot "dist"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Assert-LastExitCode([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Get-CachedDownload([string]$Url, [string]$Destination) {
    if (Test-Path $Destination) {
        Write-Host "Using cached download: $Destination"
        return
    }
    Write-Host "Downloading: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

function Find-InnoCompiler {
    $Candidates = @(
        $env:INNO_SETUP_COMPILER,
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno\ISCC.exe")
    ) | Where-Object { $_ }
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            return (Resolve-Path $Candidate).Path
        }
    }
    return ""
}

Write-Step "Preparing a clean Windows package staging directory"
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
if (Test-Path (Join-Path $WorkRoot "stage")) {
    Remove-Item -Recurse -Force (Join-Path $WorkRoot "stage")
}
if (Test-Path $ExtractDir) {
    Remove-Item -Recurse -Force $ExtractDir
}
New-Item -ItemType Directory -Force -Path $StageDir, $ExtractDir, $DistDir | Out-Null

$PythonZip = Join-Path $DownloadDir "python-$PythonVersion-embed-amd64.zip"
$NodeZip = Join-Path $DownloadDir "node-v$NodeVersion-win-x64.zip"
$GetPip = Join-Path $DownloadDir "get-pip.py"
Get-CachedDownload "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $PythonZip
Get-CachedDownload "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip" $NodeZip
Get-CachedDownload "https://bootstrap.pypa.io/get-pip.py" $GetPip

Write-Step "Assembling portable Python $PythonVersion"
$PythonRuntime = Join-Path $StageDir "runtime\python"
New-Item -ItemType Directory -Force -Path $PythonRuntime | Out-Null
Expand-Archive -Path $PythonZip -DestinationPath $PythonRuntime -Force
$PthFile = Get-ChildItem $PythonRuntime -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) {
    throw "python*._pth was not found in the portable Python runtime"
}
$PthLines = [System.Collections.Generic.List[string]]::new()
foreach ($Line in (Get-Content $PthFile.FullName)) {
    if ($Line.Trim() -eq "#import site") {
        $PthLines.Add("import site")
    } else {
        $PthLines.Add($Line)
    }
}
if (-not ($PthLines -contains "Lib\site-packages")) {
    $PthLines.Insert([Math]::Max(1, $PthLines.Count - 1), "Lib\site-packages")
}
[System.IO.File]::WriteAllLines($PthFile.FullName, $PthLines, [System.Text.Encoding]::ASCII)

$PythonExe = Join-Path $PythonRuntime "python.exe"
& $PythonExe $GetPip --disable-pip-version-check --no-warn-script-location
Assert-LastExitCode "Install pip"
& $PythonExe -m pip install --no-cache-dir --disable-pip-version-check --no-warn-script-location -r (Join-Path $RepoRoot "requirements.txt")
Assert-LastExitCode "Install Python dependencies"

Write-Step "Copying application runtime files"
Copy-Item (Join-Path $RepoRoot "cli.py") $StageDir
Copy-Item (Join-Path $RepoRoot "requirements.txt") $StageDir
Copy-Item (Join-Path $RepoRoot "package.json") $StageDir
Copy-Item (Join-Path $RepoRoot "package-lock.json") $StageDir
Copy-Item (Join-Path $RepoRoot "README.md") $StageDir
Copy-Item (Join-Path $RepoRoot "scripts") $StageDir -Recurse
Copy-Item (Join-Path $PSScriptRoot "launcher.py") (Join-Path $StageDir "launcher.py")
Get-ChildItem $StageDir -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $StageDir -File -Recurse -Include "*.pyc", ".DS_Store" | Remove-Item -Force

Write-Step "Assembling Node.js $NodeVersion and Playwright without browser binaries"
$NodeExtract = Join-Path $ExtractDir "node"
Expand-Archive -Path $NodeZip -DestinationPath $NodeExtract -Force
$NodeHome = Join-Path $NodeExtract "node-v$NodeVersion-win-x64"
$NodeRuntime = Join-Path $StageDir "runtime\node"
New-Item -ItemType Directory -Force -Path $NodeRuntime | Out-Null
Copy-Item (Join-Path $NodeHome "node.exe") $NodeRuntime

$PreviousSkipBrowserDownload = $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
Push-Location $StageDir
try {
    & (Join-Path $NodeHome "npm.cmd") ci --omit=dev --ignore-scripts --no-audit --no-fund
    Assert-LastExitCode "Install Node and Playwright dependencies"
} finally {
    Pop-Location
    $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = $PreviousSkipBrowserDownload
}
if (-not (Test-Path (Join-Path $StageDir "node_modules\playwright\package.json"))) {
    throw "Playwright is missing from the staging directory"
}

Write-Step "Validating the staged runtime"
$ValidationData = Join-Path $WorkRoot "validation-data"
if (Test-Path $ValidationData) {
    Remove-Item -Recurse -Force $ValidationData
}
$PreviousDataDir = $env:SOP1688_DATA_DIR
$PreviousNodeBin = $env:NODE_BIN
$PreviousPath = $env:Path
$PreviousPythonUtf8 = $env:PYTHONUTF8
$PreviousPythonIoEncoding = $env:PYTHONIOENCODING
$env:SOP1688_DATA_DIR = $ValidationData
$env:NODE_BIN = Join-Path $NodeRuntime "node.exe"
$env:Path = "$NodeRuntime;$PreviousPath"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try {
    & $PythonExe (Join-Path $PSScriptRoot "smoke_test.py")
    Assert-LastExitCode "Run Windows launcher smoke tests"
    & $PythonExe (Join-Path $StageDir "launcher.py") --dry-run --app-dir $StageDir | Out-Host
    Assert-LastExitCode "Validate the Windows launcher"
    & (Join-Path $NodeRuntime "node.exe") --check (Join-Path $StageDir "scripts\capabilities\tag_collect\rpa_collect.mjs")
    Assert-LastExitCode "Check rpa_collect.mjs"
    & (Join-Path $NodeRuntime "node.exe") --check (Join-Path $StageDir "scripts\capabilities\tag_collect\rpa_detail.mjs")
    Assert-LastExitCode "Check rpa_detail.mjs"
    & $PythonExe -m py_compile `
        (Join-Path $StageDir "scripts\capabilities\tag_collect\service.py") `
        (Join-Path $StageDir "scripts\capabilities\tag_collect\web.py") `
        (Join-Path $StageDir "scripts\capabilities\tag_collect\rpa.py")
    Assert-LastExitCode "Check Python runtime files"
    if (-not $SkipSmoke) {
        Push-Location $StageDir
        try {
            & $PythonExe (Join-Path $StageDir "scripts\capabilities\tag_collect\smoke_test.py")
            Assert-LastExitCode "Run staged package smoke tests"
        } finally {
            Pop-Location
        }
    }
} finally {
    $env:SOP1688_DATA_DIR = $PreviousDataDir
    $env:NODE_BIN = $PreviousNodeBin
    $env:Path = $PreviousPath
    $env:PYTHONUTF8 = $PreviousPythonUtf8
    $env:PYTHONIOENCODING = $PreviousPythonIoEncoding
}

Get-ChildItem $StageDir -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $StageDir -File -Recurse -Include "*.pyc", ".DS_Store" | Remove-Item -Force

if ($SkipCompile) {
    Write-Host "`nStaged package created: $StageDir" -ForegroundColor Green
    exit 0
}

Write-Step "Compiling the single-file Inno Setup installer"
$InnoCompiler = Find-InnoCompiler
if (-not $InnoCompiler -and $InstallInnoSetup) {
    $InnoInstaller = Join-Path $DownloadDir "innosetup-$InnoVersion.exe"
    Get-CachedDownload "https://github.com/jrsoftware/issrc/releases/download/is-$($InnoVersion.Replace('.', '_'))/innosetup-$InnoVersion.exe" $InnoInstaller
    $InnoInstallDir = Join-Path $env:LOCALAPPDATA "Programs\Inno"
    & $InnoInstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER "/DIR=$InnoInstallDir"
    Assert-LastExitCode "Install Inno Setup"
    $InnoCompiler = Find-InnoCompiler
}
if (-not $InnoCompiler) {
    throw "Inno Setup 6 was not found. Install it first or rerun with -InstallInnoSetup."
}

$IssFile = Join-Path $PSScriptRoot "SOP1688.iss"
& $InnoCompiler "/DSourceDir=$StageDir" "/DOutputDir=$DistDir" "/DAppVersion=$AppVersion" $IssFile
Assert-LastExitCode "Compile the Inno Setup installer"

$Installer = Join-Path $DistDir "SOP1688-Setup-x64.exe"
if (-not (Test-Path $Installer)) {
    throw "The compiler finished but the expected installer was not found: $Installer"
}
$Hash = Get-FileHash $Installer -Algorithm SHA256
$HashFile = Join-Path $DistDir "SOP1688-Setup-x64.sha256.txt"
"$($Hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($Installer))" | Set-Content $HashFile -Encoding ASCII

Write-Host "`nInstaller created: $Installer" -ForegroundColor Green
Write-Host "SHA-256: $($Hash.Hash.ToLowerInvariant())" -ForegroundColor Green
