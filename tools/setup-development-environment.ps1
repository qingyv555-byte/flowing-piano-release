[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv-dev"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

function Invoke-Checked {
    param(
        [string]$Description,
        [string]$Executable,
        [string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败，退出码：$LASTEXITCODE"
    }
}

$versionCheck = "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
$candidates = @()
if ($PythonPath) {
    $candidates += [PSCustomObject]@{ Path = $PythonPath; Arguments = @() }
}
else {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += [PSCustomObject]@{ Path = $command.Source; Arguments = @() }
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates += [PSCustomObject]@{ Path = $pyLauncher.Source; Arguments = @("-3.12") }
    }
}

$candidate = ""
$candidateArguments = @()
foreach ($option in $candidates) {
    if (-not (Test-Path -LiteralPath $option.Path -PathType Leaf)) {
        continue
    }
    $resolved = (Resolve-Path -LiteralPath $option.Path).Path
    & $resolved @($option.Arguments) "-c" $versionCheck
    if ($LASTEXITCODE -eq 0) {
        $candidate = $resolved
        $candidateArguments = @($option.Arguments)
        break
    }
}

if (-not $candidate) {
    throw "未找到可用的 Python 3.12。请安装 Python 3.12，或使用 -PythonPath 指定 python.exe。"
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-Checked -Description "创建 .venv-dev" -Executable $candidate -Arguments @($candidateArguments + @("-m", "venv", $venvPath))
}
else {
    & $venvPython "-c" $versionCheck
    if ($LASTEXITCODE -ne 0) {
        throw ".venv-dev uses an unsupported interpreter. Delete .venv-dev and rerun this script."
    }
}

Push-Location $projectRoot
try {
    Invoke-Checked -Description "升级 pip" -Executable $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked -Description "安装开发依赖" -Executable $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]")
}
finally {
    Pop-Location
}

Write-Host "开发环境已准备完成：$venvPython"
Write-Host "运行测试：`$env:QT_QPA_PLATFORM = 'offscreen'; .\.venv-dev\Scripts\python.exe -m pytest tests\unit tests\qt -q"
