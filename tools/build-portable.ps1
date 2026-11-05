[CmdletBinding()]
param(
    [switch]$SkipClean,
    [string]$SourceCache = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$BuildRoot = Join-Path $ProjectRoot "build"
$DistRoot = Join-Path $ProjectRoot "dist"
$BuildOutput = Join-Path $BuildRoot "ai_piano_studio"
$PortableOutput = Join-Path $DistRoot "AI-Piano-Studio"
$DevelopmentPython = Join-Path $ProjectRoot ".venv-dev\Scripts\python.exe"
$WorkspacePython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $DevelopmentPython) {
    $DevelopmentPython
} elseif (Test-Path -LiteralPath $WorkspacePython) {
    $WorkspacePython
} else {
    throw "No project Python environment found (.venv-dev or .venv)."
}
$ProjectSource = Join-Path $ProjectRoot "src"
$PreviousPythonPath = $env:PYTHONPATH
$PreviousBuildCommit = $env:AI_PIANO_BUILD_COMMIT
if ([string]::IsNullOrWhiteSpace($SourceCache)) {
    $SourceCache = Join-Path $ProjectRoot ".release-source-cache"
}
& $Python (Join-Path $ProjectRoot "tools\collect-release-notices.py") --output (Join-Path $BuildRoot "release-notices") --source-cache $SourceCache
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare exact release notices and source archive records."
}

function Remove-GeneratedDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $resolvedPath = [IO.Path]::GetFullPath($Path)
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $expectedPrefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the project root: $resolvedPath"
    }
    if ([IO.Directory]::Exists($resolvedPath)) {
        [IO.Directory]::Delete($resolvedPath, $true)
    }
}

if (-not $SkipClean) {
    Remove-GeneratedDirectory -Path $BuildOutput -Root $ProjectRoot
    Remove-GeneratedDirectory -Path $PortableOutput -Root $ProjectRoot
}

Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = if ([string]::IsNullOrEmpty($PreviousPythonPath)) {
        $ProjectSource
    } else {
        "$ProjectSource$([IO.Path]::PathSeparator)$PreviousPythonPath"
    }
    $sourceIdentity = & $Python (Join-Path $ProjectRoot "tools\source_identity.py") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Could not read source build identity." }
    $env:AI_PIANO_BUILD_COMMIT = $sourceIdentity.git_commit.Substring(0, 7)
    $pyinstallerArguments = @(
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        $DistRoot,
        "--workpath",
        $BuildRoot,
        (Join-Path $ProjectRoot "ai_piano_studio.spec")
    )
    & $Python @pyinstallerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    Remove-GeneratedDirectory -Path $BuildOutput -Root $ProjectRoot
    Write-Output ("Portable build created at " + $PortableOutput)
} finally {
    $env:PYTHONPATH = $PreviousPythonPath
    $env:AI_PIANO_BUILD_COMMIT = $PreviousBuildCommit
    Pop-Location
}
