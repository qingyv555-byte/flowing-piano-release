[CmdletBinding()]
param(
    [switch]$SkipInstallerSmokeTest
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
. (Join-Path $ProjectRoot 'tools\release-notice-policy.ps1')
$BuildPortable = Join-Path $ProjectRoot "tools\build-portable.ps1"
$BuildInstaller = Join-Path $ProjectRoot "tools\build-installer.ps1"
$DevelopmentPython = Join-Path $ProjectRoot ".venv-dev\Scripts\python.exe"
$WorkspacePython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $DevelopmentPython) {
    $DevelopmentPython
} elseif (Test-Path -LiteralPath $WorkspacePython) {
    $WorkspacePython
} else {
    throw "No project Python environment found (.venv-dev or .venv)."
}

$BuildRoot = Join-Path $ProjectRoot "build"
$DistRoot = Join-Path $ProjectRoot "dist"
$PortableRoot = Join-Path $DistRoot "AI-Piano-Studio"
$ReleaseRoot = Join-Path $DistRoot "release"
$PortableZip = Join-Path $ReleaseRoot "AI-Piano-Studio-Portable.zip"
$SetupPath = Join-Path $ReleaseRoot "AI-Piano-Studio-Setup.exe"
$ChecksumsPath = Join-Path $ReleaseRoot "SHA256SUMS.txt"
$ManifestPath = Join-Path $ReleaseRoot "BUILD-MANIFEST.json"

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

function Read-ProjectVersion {
    $tomlPath = Join-Path $ProjectRoot "pyproject.toml"
    $text = Get-Content -LiteralPath $tomlPath -Raw
    $match = [regex]::Match(
        $text,
        '(?ms)^\[project\]\s.*?^version\s*=\s*"([^"]+)"'
    )
    if (-not $match.Success) {
        throw "Could not read [project].version from pyproject.toml."
    }
    $version = $match.Groups[1].Value
    if ($version -notmatch "^\d+\.\d+\.\d+$") {
        throw "Project version is not a stable three-part version: $version"
    }
    return $version
}

function Test-PortableTree {
    param([Parameter(Mandatory = $true)][string]$Root)

    $portableExe = Join-Path $Root "AI-Piano-Studio.exe"
    $privateArtwork = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Filter "skin-*.png")
    if ($privateArtwork.Count -gt 0) {
        throw "Portable build contains private character artwork."
    }
    $forbiddenRuntime = @(Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $_.Name -match '^(Qt6(WebEngine|WebView|Pdf).*\.dll|qpdf\.dll|api-ms-win-.*\.dll|ucrtbase\.dll|icuuc\.dll)$'
    })
    if ($forbiddenRuntime.Count -gt 0) {
        throw "Portable build contains excluded browser/PDF or Windows system binaries."
    }
    $runtimeBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
    foreach ($runtimeName in @('libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'sqlite3.dll', 'libffi-8.dll')) {
        $expectedHash = (Get-FileHash -LiteralPath (Join-Path $runtimeBase "DLLs\$runtimeName") -Algorithm SHA256).Hash
        $copies = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $runtimeName)
        if ($copies.Count -eq 0) { throw "Missing pinned runtime dependency: $runtimeName" }
        foreach ($copy in $copies) {
            if ((Get-FileHash -LiteralPath $copy.FullName -Algorithm SHA256).Hash -ne $expectedHash) {
                throw "Runtime dependency differs from pinned Python: $($copy.FullName)"
            }
        }
    }
    if (-not (Test-Path -LiteralPath $portableExe -PathType Leaf)) {
        throw "Portable executable is missing: $portableExe"
    }
    $requiredFiles = @(
        "Main.qml",
        "AssistantWindow.qml",
        "AudioImportDialog.qml",
        "SupportDialog.qml",
        "TechnicalDetailsDialog.qml",
        "banks.json",
        "harmony_beginner_guide.md",
        "THIRD_PARTY_NOTICES.md",
        "LICENSE-SALAMANDER.txt",
        "PROVENANCE-SALAMANDER.json",
        "PROVENANCE-SALAMANDER.md"
    )
    foreach ($name in $requiredFiles) {
        $matches = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File -Filter $name)
        if ($matches.Count -eq 0) {
            throw "Portable build is missing required runtime resource: $name"
        }
    }
    $templates = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File -Filter "template.pianoai.json")
    if ($templates.Count -ne 6) {
        throw "Portable build contains $($templates.Count) templates; expected 6."
    }
    $samples = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File -Filter "*.wav")
    if ($samples.Count -ne 108) {
        throw "Portable build contains $($samples.Count) piano samples; expected 108."
    }
    $ffmpegPlugin = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File -Filter "ffmpegmediaplugin.dll")
    if ($ffmpegPlugin.Count -eq 0) {
        throw "Portable build is missing Qt Multimedia's FFmpeg decoder plugin."
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
    $legalPrefix = Get-VerifiedReleaseNoticeRoot -PortableRoot $Root
    $forbidden = @(Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object {
        -not $_.FullName.StartsWith($legalPrefix, [StringComparison]::OrdinalIgnoreCase) -and $forbiddenNames -contains $_.Name
    })
    if ($forbidden.Count -gt 0) {
        $names = ($forbidden | Select-Object -ExpandProperty FullName) -join "; "
        throw "Portable build contains forbidden development or user data: $names"
    }
}

function Invoke-InstallerSmokeTest {
    param([Parameter(Mandatory = $true)][string]$InstallerPath)

    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B5E6E3B9-4E6E-4E50-9E80-3E0A7D2F5A11}_is1'
    # Never let this smoke overwrite an existing installation's registration.
    if (Test-Path -LiteralPath $uninstallKey) {
        throw "Existing installation found; refusing to replace its registration for smoke testing."
    }
    $smokeId = [Guid]::NewGuid().ToString("N")
    $smokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("fp-install-" + $smokeId.Substring(0, 12))
    $userSentinel = Join-Path ([IO.Path]::GetTempPath()) ("AI-Piano-Studio-user-project-" + $smokeId + ".pianoai.json")
    $profileRoot = Join-Path ([IO.Path]::GetTempPath()) ("AI-Piano-Studio-profile-smoke-" + $smokeId)
    New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
    [IO.File]::WriteAllText($userSentinel, '{"user":"sentinel"}')
    try {
        $installArguments = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            '/TASKS=""',
            "/DIR=""$smokeRoot"""
        )
        $install = Start-Process -FilePath $InstallerPath -ArgumentList $installArguments -Wait -PassThru -WindowStyle Hidden
        if ($install.ExitCode -ne 0) {
            throw "Installer smoke install failed with exit code $($install.ExitCode)."
        }

        $installedExe = Join-Path $smokeRoot "AI-Piano-Studio.exe"
        $uninstaller = Join-Path $smokeRoot "unins000.exe"
        if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) {
            throw "Installer smoke install did not create the application executable."
        }
        if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
            throw "Installer smoke install did not create the standard uninstaller."
        }
        $started = Start-Process -FilePath $installedExe -ArgumentList @("--smoke-test", "--profile-home", "`"$profileRoot`"") -WorkingDirectory $smokeRoot -Wait -PassThru -WindowStyle Hidden
        if ($started.ExitCode -ne 0) {
            throw "Installed executable smoke test failed with exit code $($started.ExitCode)."
        }

        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru -WindowStyle Hidden
        if ($uninstall.ExitCode -ne 0) {
            throw "Installer smoke uninstall failed with exit code $($uninstall.ExitCode)."
        }
        if (Test-Path -LiteralPath $installedExe) {
            throw "Installer smoke uninstall left the application executable behind."
        }
        if (-not (Test-Path -LiteralPath $userSentinel -PathType Leaf)) {
            throw "Installer smoke uninstall deleted a user-project sentinel."
        }
        if (-not (Test-Path -LiteralPath $profileRoot -PathType Container)) {
            throw "Installer smoke uninstall removed the external profile."
        }
        Write-Output "Installer smoke test passed: install, launch, uninstaller, and user-file preservation."
    } finally {
        if ([IO.Directory]::Exists($smokeRoot)) {
            Remove-GeneratedDirectory -Path $smokeRoot -Root ([IO.Path]::GetTempPath())
        }
        if ([IO.File]::Exists($userSentinel)) {
            [IO.File]::Delete($userSentinel)
        }
        if ([IO.Directory]::Exists($profileRoot)) {
            Remove-GeneratedDirectory -Path $profileRoot -Root ([IO.Path]::GetTempPath())
        }
    }
}

$sourceIdentity = & $Python (Join-Path $ProjectRoot "tools\source_identity.py") | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not read source build identity." }
$branch = $sourceIdentity.git_branch
$commit = $sourceIdentity.git_commit
Write-Output ("Source branch: " + $branch)
Write-Output ("Source base commit: " + $commit)
$version = Read-ProjectVersion
Write-Output ("Project version: " + $version)

if ($sourceIdentity.git_checkout) {
    & git -C $ProjectRoot diff --check
    if ($LASTEXITCODE -ne 0) { throw "git diff --check failed." }
}
& $Python -m compileall -q (Join-Path $ProjectRoot "src") (Join-Path $ProjectRoot "tests") (Join-Path $ProjectRoot "tools")
if ($LASTEXITCODE -ne 0) {
    throw "compileall failed with exit code $LASTEXITCODE."
}
& $Python (Join-Path $ProjectRoot "tools\run_silent_tests.py") -q
if ($LASTEXITCODE -ne 0) {
    throw "pytest failed with exit code $LASTEXITCODE."
}

Remove-GeneratedDirectory -Path $ReleaseRoot -Root $ProjectRoot
New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null

& $Python (Join-Path $ProjectRoot "tools\build-source-bundle.py") --output $ReleaseRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare matching project and dependency source archives."
}

& $BuildPortable
if ($LASTEXITCODE -ne 0) {
    throw "Portable build script failed with exit code $LASTEXITCODE."
}
Test-PortableTree -Root $PortableRoot
$portableProfile = Join-Path $BuildRoot "portable-smoke-profile"
$portableSmoke = Start-Process -FilePath (Join-Path $PortableRoot "AI-Piano-Studio.exe") -ArgumentList @("--smoke-test", "--profile-home", "`"$portableProfile`"") -WorkingDirectory $PortableRoot -Wait -PassThru -WindowStyle Hidden
if ($portableSmoke.ExitCode -ne 0) {
    throw "Portable executable smoke test failed with exit code $($portableSmoke.ExitCode)."
}
Write-Output "Portable smoke test passed: executable startup and packaged resource validation."

Compress-Archive -Path $PortableRoot -DestinationPath $PortableZip -CompressionLevel Optimal -Force
if (-not (Test-Path -LiteralPath $PortableZip -PathType Leaf)) {
    throw "Portable ZIP was not created: $PortableZip"
}

& $BuildInstaller -Version $version -PortableDir $PortableRoot -OutputDir $ReleaseRoot
if ($LASTEXITCODE -ne 0) {
    throw "Installer build script failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $SetupPath -PathType Leaf)) {
    throw "Setup artifact is missing: $SetupPath"
}
if (-not $SkipInstallerSmokeTest) {
    Invoke-InstallerSmokeTest -InstallerPath $SetupPath
} else {
    Write-Output "Installer smoke test skipped by explicit switch."
}

$artifactEntries = @(Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -Force)
$secretPatterns = @(
    "(?i)(^|[\\/])\.env($|[.])",
    "(?i)(^|[\\/])(api[_-]?key|credential|secret|token|project\.json|logs?)($|[.\\/])",
    "(?i)(^|[\\/])(\.git|tests|screenshots|docs|\.pytest_cache|\.pytest-tmp)($|[.\\/])"
)
$secretEntries = @($artifactEntries | Where-Object {
    $relative = $_.FullName.Substring($ReleaseRoot.Length)
    $secretPatterns | Where-Object { $relative -match $_ }
})
if ($secretEntries.Count -gt 0) {
    $names = ($secretEntries | Select-Object -ExpandProperty FullName) -join "; "
    throw "Release output contains forbidden secret or development paths: $names"
}

$portableHash = (Get-FileHash -LiteralPath $PortableZip -Algorithm SHA256).Hash.ToLowerInvariant()
$setupHash = (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
$projectSourceHash = (Get-FileHash -LiteralPath (Join-Path $ReleaseRoot "Flowing-Piano-Source.zip") -Algorithm SHA256).Hash.ToLowerInvariant()
$dependencySourceHash = (Get-FileHash -LiteralPath (Join-Path $ReleaseRoot "Dependency-Sources.zip") -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumText = ("$portableHash  AI-Piano-Studio-Portable.zip" + [Environment]::NewLine + "$setupHash  AI-Piano-Studio-Setup.exe" + [Environment]::NewLine + "$projectSourceHash  Flowing-Piano-Source.zip" + [Environment]::NewLine + "$dependencySourceHash  Dependency-Sources.zip" + [Environment]::NewLine)
[IO.File]::WriteAllText(
    $ChecksumsPath,
    $checksumText,
    [Text.UTF8Encoding]::new($false)
)

$toolchainJson = & $Python -c "import json, sys, PyInstaller, PySide6; print(json.dumps({'python_version': sys.version.split()[0], 'pyside_version': PySide6.__version__, 'pyinstaller_version': PyInstaller.__version__}))"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the build toolchain."
}
$toolchain = $toolchainJson | ConvertFrom-Json
$manifest = [ordered]@{
    product = "AI Piano Studio"
    version = $version
    git_branch = $branch
    git_commit = $commit
    source_dirty = $sourceIdentity.source_dirty
    build_timestamp = [DateTime]::UtcNow.ToString("o")
    python_version = $toolchain.python_version
    pyside_version = $toolchain.pyside_version
    pyinstaller_version = $toolchain.pyinstaller_version
    installer_tool = "Inno Setup 6"
    portable_sha256 = $portableHash
    setup_sha256 = $setupHash
    project_source_sha256 = $projectSourceHash
    dependency_sources_sha256 = $dependencySourceHash
    candidate_only = $true
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Write-Output ("Release candidate artifacts created in " + $ReleaseRoot)
Write-Output ("Portable ZIP: " + $PortableZip)
Write-Output ("Setup: " + $SetupPath)
Write-Output ("SHA256: " + $ChecksumsPath)
Write-Output ("Manifest: " + $ManifestPath)
