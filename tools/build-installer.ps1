[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^\d+\.\d+\.\d+$")]
    [string]$Version,
    [string]$PortableDir = "",
    [string]$OutputDir = "",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
. (Join-Path $ProjectRoot 'tools\release-notice-policy.ps1')
$InstallerScript = Join-Path $ProjectRoot "installer\AI-Piano-Studio.iss"
if ([string]::IsNullOrWhiteSpace($PortableDir)) {
    $PortableDir = Join-Path $ProjectRoot "dist\AI-Piano-Studio"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "dist\release"
}

function Resolve-ProjectPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

function Find-InnoCompiler {
    param([string]$RequestedPath)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $candidates.Add($RequestedPath)
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $candidates.Add($command.Source)
    }
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $programFiles = [Environment]::GetEnvironmentVariable("ProgramFiles")
    $localAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA")
    foreach ($candidate in @(
        (Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $programFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $localAppData "Programs\Inno Setup 6\ISCC.exe")
    )) {
        if ($candidate) {
            $candidates.Add($candidate)
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Inno Setup 6 ISCC.exe was not found. Install the official Inno Setup 6 or pass -InnoCompiler."
}

$PortableRoot = Resolve-ProjectPath $PortableDir
$OutputRoot = Resolve-ProjectPath $OutputDir
$PortableExe = Join-Path $PortableRoot "AI-Piano-Studio.exe"
if (-not (Test-Path -LiteralPath $PortableExe -PathType Leaf)) {
    throw "Validated Portable build is missing: $PortableExe"
}
if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
    throw "Installer script is missing: $InstallerScript"
}

$forbiddenNames = @(
    ".git",
    ".github",
    "tests",
    "screenshots",
    "docs",
    ".pytest_cache",
    ".pytest-tmp",
    ".env",
    "findings.md",
    "progress.md",
    "task_plan.md",
    "project.json",
    "logs",
    "credentials"
)
$legalPrefix = Get-VerifiedReleaseNoticeRoot -PortableRoot $PortableRoot
$forbidden = Get-ChildItem -LiteralPath $PortableRoot -Recurse -Force -ErrorAction Stop |
    Where-Object { -not $_.FullName.StartsWith($legalPrefix, [StringComparison]::OrdinalIgnoreCase) -and $forbiddenNames -contains $_.Name }
if ($forbidden) {
    $names = ($forbidden | Select-Object -ExpandProperty FullName) -join "; "
    throw "Portable build contains forbidden development or user data: $names"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$compiler = Find-InnoCompiler $InnoCompiler
$arguments = @(
    "/Q",
    "/DVersion=$Version",
    "/DPortableDir=$PortableRoot",
    "/DOutputDir=$OutputRoot",
    $InstallerScript
)
& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

$setupPath = Join-Path $OutputRoot "AI-Piano-Studio-Setup.exe"
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "Inno Setup completed without producing $setupPath"
}
Write-Output ("Installer created at " + $setupPath)
