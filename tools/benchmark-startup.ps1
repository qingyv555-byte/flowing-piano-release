[CmdletBinding()]
param(
    [ValidateSet("All", "Dev", "Portable", "Installed")]
    [string]$Target = "All",
    [ValidateRange(5, 20)]
    [int]$Runs = 5,
    [string]$PythonPath = "",
    [string]$PortableExe = "",
    [string]$SetupExe = "",
    [string]$InstalledExe = "",
    [string]$OutputPath = "",
    [string]$ProjectCounts = "0",
    [ValidateSet("windows", "offscreen")]
    [string]$QtPlatform = "windows",
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"

$allowedProjectCounts = @(0, 10, 100, 500)
$projectCountValues = @(
    $ProjectCounts -split '[,;\s]+' |
        Where-Object { $_ -ne "" } |
        ForEach-Object { [int]$_ }
)
$invalidProjectCounts = @($projectCountValues | Where-Object { $_ -notin $allowedProjectCounts })
if ($invalidProjectCounts.Count -gt 0) {
    throw "ProjectCounts must contain only 0, 10, 100, or 500; received: $($invalidProjectCounts -join ', ')"
}

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$BenchmarkPrefix = "ai-piano-startup-benchmark-"
$OwnedRoots = New-Object System.Collections.Generic.List[string]

function Resolve-InputPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

function New-BenchmarkRoot {
    $root = Join-Path $TempBase ($BenchmarkPrefix + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $OwnedRoots.Add($root)
    return $root
}

function Remove-OwnedRoots {
    foreach ($root in @($OwnedRoots)) {
        $resolved = [IO.Path]::GetFullPath($root).TrimEnd([IO.Path]::DirectorySeparatorChar)
        $prefix = Join-Path $TempBase $BenchmarkPrefix
        if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a benchmark path outside the generated temp prefix: $resolved"
        }
        if ([IO.Directory]::Exists($resolved)) {
            [IO.Directory]::Delete($resolved, $true)
        }
    }
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $candidate = Resolve-InputPath $PythonPath
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Python executable not found: $candidate"
        }
        return $candidate
    }
    foreach ($candidate in @(
        (Join-Path $ProjectRoot ".venv-dev\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "No project Python environment found (.venv-dev or .venv)."
}

function ConvertTo-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function New-ProjectPressureFiles {
    param(
        [Parameter(Mandatory = $true)][string]$BenchmarkHome,
        [Parameter(Mandatory = $true)][ValidateSet(0, 10, 100, 500)][int]$Count
    )
    if ($Count -eq 0) {
        return
    }
    $projectsRoot = Join-Path $BenchmarkHome "Projects"
    $emptyProject = '{"schemaVersion":3,"bpm":120,"timeSignature":{"beatsPerBar":4,"beatUnit":4},"notes":[],"playbackNotes":[],"playbackTempo":{"bpm":120,"beatsPerBar":4,"beatUnit":4}}'
    $utf8 = [Text.UTF8Encoding]::new($false)
    for ($index = 1; $index -le $Count; $index++) {
        $folder = Join-Path $projectsRoot ("project-{0:D3}" -f $index)
        $projectPath = Join-Path $folder "project.pianoai.json"
        if (-not (Test-Path -LiteralPath $projectPath -PathType Leaf)) {
            New-Item -ItemType Directory -Force -Path $folder | Out-Null
            [IO.File]::WriteAllText($projectPath, $emptyProject, $utf8)
        }
    }
}

function Invoke-ProfileRun {
    param(
        [Parameter(Mandatory = $true)][string]$TargetName,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$BenchmarkHome,
        [Parameter(Mandatory = $true)][string]$Cohort,
        [Parameter(Mandatory = $true)][int]$RunNumber,
        [Parameter(Mandatory = $true)][ValidateSet(0, 10, 100, 500)][int]$ProjectCount,
        [switch]$Development
    )

    New-ProjectPressureFiles -BenchmarkHome $BenchmarkHome -Count $ProjectCount
    $reportPath = Join-Path $BenchmarkHome ("profile-" + $RunNumber + ".json")
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($null -ne $startInfo.ArgumentList) {
        foreach ($argument in $Arguments) {
            [void]$startInfo.ArgumentList.Add($argument)
        }
    }
    else {
        $startInfo.Arguments = (($Arguments | ForEach-Object {
                    ConvertTo-ProcessArgument $_
                }) -join ' ')
    }
    $startInfo.Environment["QT_QPA_PLATFORM"] = $QtPlatform
    $startInfo.Environment["AI_PIANO_STARTUP_PROFILE"] = "1"
    $startInfo.Environment["AI_PIANO_STARTUP_PROFILE_PATH"] = $reportPath
    $startInfo.Environment["AI_PIANO_STARTUP_BENCHMARK_HOME"] = $BenchmarkHome
    if ($Development) {
        $startInfo.Environment["PYTHONPATH"] = Join-Path $ProjectRoot "src"
    }
    else {
        $startInfo.Environment["PYTHONPATH"] = ""
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not $process.Start()) {
            throw "Could not start $TargetName."
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($StartupTimeoutSeconds * 1000)) {
            try {
                $process.Kill()
            }
            catch {
                # The process may have exited between the timeout and Kill().
            }
            throw "$TargetName startup benchmark run $RunNumber exceeded the ${StartupTimeoutSeconds}s timeout."
        }
        $process.WaitForExit()
        $timer.Stop()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        if ($process.ExitCode -ne 0) {
            throw "$TargetName startup benchmark run $RunNumber failed with exit code $($process.ExitCode). stderr: $stderr stdout: $stdout"
        }
    }
    finally {
        $timer.Stop()
        $process.Dispose()
    }

    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "$TargetName did not produce a startup profile: $reportPath"
    }
    $profile = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $markerByPhase = @{}
    foreach ($marker in @($profile.markers)) {
        $markerByPhase[[string]$marker.phase] = $marker
    }
    $requiredMarkers = @($profile.required_markers | ForEach-Object { [string]$_ })
    if ($requiredMarkers.Count -eq 0) {
        throw "$TargetName profile does not declare required startup markers: $reportPath"
    }
    foreach ($phase in $requiredMarkers) {
        if (-not $markerByPhase.ContainsKey($phase)) {
            throw "$TargetName profile is missing ${phase}: $reportPath"
        }
    }
    $allRss = @($profile.markers | ForEach-Object { [double]$_.rss_mb })
    return [pscustomobject]@{
        target = $TargetName
        cohort = $Cohort
        run = $RunNumber
        elapsed_ms = [math]::Round($timer.Elapsed.TotalMilliseconds, 3)
        t1_ms = [math]::Round([double]$markerByPhase["main_window_visible"].elapsed_ms, 3)
        t2_ms = [math]::Round([double]$markerByPhase["editor_ready"].elapsed_ms, 3)
        process_rss_mb = [math]::Round([double]$markerByPhase["process_start"].rss_mb, 3)
        window_rss_mb = [math]::Round([double]$markerByPhase["main_window_visible"].rss_mb, 3)
        ready_rss_mb = [math]::Round([double]$markerByPhase["editor_ready"].rss_mb, 3)
        peak_rss_mb = [math]::Round((($allRss | Measure-Object -Maximum).Maximum), 3)
    }
}

function Get-Stats {
    param(
        [Parameter(Mandatory = $true)][object[]]$Rows,
        [Parameter(Mandatory = $true)][string]$Property
    )
    $values = @($Rows | ForEach-Object { [double]$_.$Property } | Sort-Object)
    $middle = [int][math]::Floor($values.Count / 2)
    $median = if (($values.Count % 2) -eq 1) {
        $values[$middle]
    }
    else {
        ($values[$middle - 1] + $values[$middle]) / 2.0
    }
    return [ordered]@{
        min_ms = [math]::Round($values[0], 3)
        median_ms = [math]::Round($median, 3)
        max_ms = [math]::Round($values[$values.Count - 1], 3)
    }
}

function Get-MemoryStats {
    param([Parameter(Mandatory = $true)][object[]]$Rows)
    return [ordered]@{
        window_rss_mb = [math]::Round((($Rows | ForEach-Object { [double]$_.window_rss_mb } | Measure-Object -Average).Average), 3)
        ready_rss_mb = [math]::Round((($Rows | ForEach-Object { [double]$_.ready_rss_mb } | Measure-Object -Average).Average), 3)
        peak_rss_mb = [math]::Round((($Rows | ForEach-Object { [double]$_.peak_rss_mb } | Measure-Object -Maximum).Maximum), 3)
    }
}

function Install-ForBenchmark {
    param([Parameter(Mandatory = $true)][string]$Installer)
    $installRoot = New-BenchmarkRoot
    $arguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=$installRoot"
    )
    $installed = Start-Process -FilePath $Installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($installed.ExitCode -ne 0) {
        throw "Setup EXE installation failed with exit code $($installed.ExitCode)."
    }
    $exe = Join-Path $installRoot "AI-Piano-Studio.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Setup EXE did not produce an installed application: $exe"
    }
    return [pscustomobject]@{ exe = $exe; root = $installRoot }
}

function New-TargetSpecs {
    $specs = New-Object System.Collections.Generic.List[object]
    if ($Target -in @("All", "Dev")) {
        $specs.Add([pscustomobject]@{
                name = "Dev"
                executable = Resolve-Python
                arguments = @("-m", "ai_piano_studio", "--startup-benchmark")
                working_directory = $ProjectRoot
                development = $true
            })
    }
    if ($Target -in @("All", "Portable")) {
        $path = if ($PortableExe) { Resolve-InputPath $PortableExe } else { Join-Path $ProjectRoot "dist\AI-Piano-Studio\AI-Piano-Studio.exe" }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $specs.Add([pscustomobject]@{
                    name = "Portable"
                    executable = $path
                    arguments = @("--startup-benchmark")
                    working_directory = Split-Path -Parent $path
                    development = $false
                })
        }
        elseif ($Target -eq "Portable") {
            throw "Portable executable not found: $path"
        }
        else {
            Write-Warning "Portable benchmark skipped; executable not found: $path"
        }
    }
    if ($Target -in @("All", "Installed")) {
        $path = if ($InstalledExe) { Resolve-InputPath $InstalledExe } else { "" }
        if (-not $path -and $SetupExe) {
            $setupPath = Resolve-InputPath $SetupExe
            $installed = Install-ForBenchmark $setupPath
            $path = $installed.exe
        }
        elseif (-not $path) {
            $defaultSetup = Join-Path $ProjectRoot "dist\release\AI-Piano-Studio-Setup.exe"
            if (Test-Path -LiteralPath $defaultSetup -PathType Leaf) {
                $installed = Install-ForBenchmark $defaultSetup
                $path = $installed.exe
            }
        }
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $specs.Add([pscustomobject]@{
                    name = "Installed"
                    executable = $path
                    arguments = @("--startup-benchmark")
                    working_directory = Split-Path -Parent $path
                    development = $false
                })
        }
        elseif ($Target -eq "Installed") {
            throw "Installed EXE or Setup EXE not found."
        }
        else {
            Write-Warning "Installed benchmark skipped; Setup EXE not found."
        }
    }
    return $specs.ToArray()
}

$results = New-Object System.Collections.Generic.List[object]
try {
    $targetSpecs = New-TargetSpecs
    if ($targetSpecs.Count -eq 0) {
        throw "No benchmark targets are available."
    }
    foreach ($projectCount in $projectCountValues) {
        foreach ($spec in $targetSpecs) {
            foreach ($cohort in @("cold", "warm")) {
                $warmRoot = if ($cohort -eq "warm") { New-BenchmarkRoot } else { $null }
                for ($run = 1; $run -le $Runs; $run++) {
                    $runRoot = if ($warmRoot) { $warmRoot } else { New-BenchmarkRoot }
                    Write-Host ("[{0}] projects {1} {2} run {3}" -f $spec.name, $projectCount, $cohort, $run)
                    $row = Invoke-ProfileRun `
                        -TargetName $spec.name `
                        -Executable $spec.executable `
                        -Arguments $spec.arguments `
                        -WorkingDirectory $spec.working_directory `
                        -BenchmarkHome $runRoot `
                        -Cohort $cohort `
                        -RunNumber $run `
                        -ProjectCount $projectCount `
                        -Development:$spec.development
                    $row | Add-Member -NotePropertyName project_count -NotePropertyValue $projectCount
                    $results.Add($row)
                    if (-not $warmRoot) {
                        # The run root is still owned and is removed by the guarded cleanup below.
                    }
                }
            }
        }
    }

    $summaries = foreach ($group in ($results | Group-Object project_count, target, cohort)) {
        $rows = @($group.Group)
        $parts = $group.Name -split ", "
        [ordered]@{
            project_count = [int]$parts[0]
            target = $parts[1]
            cohort = $parts[2]
            runs = $rows.Count
            t1_ms = Get-Stats -Rows $rows -Property "t1_ms"
            t2_ms = Get-Stats -Rows $rows -Property "t2_ms"
            process_elapsed_ms = Get-Stats -Rows $rows -Property "elapsed_ms"
            memory = Get-MemoryStats -Rows $rows
        }
    }
    $document = [ordered]@{
        schema_version = 1
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        git_commit = (& git -C $ProjectRoot rev-parse HEAD).Trim()
        qt_platform = $QtPlatform
        runs_per_cohort = $Runs
        project_counts = @($projectCountValues)
        definitions = [ordered]@{
            t1 = "process_start to main_window_visible"
            t2 = "process_start to editor_ready (visible Piano Roll; isolated project open, note create/sync, playback start/stop)"
            cold = "fresh isolated settings/project directory per process; OS filesystem caches are not forcibly flushed"
            warm = "same isolated settings/project directory reused across five processes"
            project_pressure = "N valid project.pianoai.json files pre-created in the isolated Projects directory before process start"
        }
        summary = $summaries
        runs = $results.ToArray()
    }
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $OutputPath = Join-Path $ProjectRoot "docs\performance\startup-benchmark-latest.json"
    }
    $output = Resolve-InputPath $OutputPath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
    $document | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $output -Encoding utf8

    Write-Host ""
    Write-Host "Startup benchmark summary (milliseconds; min / median / max)"
    foreach ($summary in @($summaries)) {
        Write-Host ("projects {0,-3} {1,-10} {2,-5} T1 {3,8} / {4,8} / {5,8} | T2 {6,8} / {7,8} / {8,8} | ready RSS avg {9,8} MB" -f `
            $summary.project_count,
            $summary.target,
            $summary.cohort,
            $summary.t1_ms.min_ms,
            $summary.t1_ms.median_ms,
            $summary.t1_ms.max_ms,
            $summary.t2_ms.min_ms,
            $summary.t2_ms.median_ms,
            $summary.t2_ms.max_ms,
            $summary.memory.ready_rss_mb)
    }
    Write-Host ("Raw benchmark report: " + $output)
}
finally {
    Remove-OwnedRoots
}
