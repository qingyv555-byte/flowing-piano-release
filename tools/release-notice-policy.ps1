# Only this verified legal-material subtree may retain upstream paths named
# docs/tests. The rest of the application still rejects development/user data.
function Get-VerifiedReleaseNoticeRoot {
    param([Parameter(Mandatory = $true)][string]$PortableRoot)

    $noticeRoot = [IO.Path]::GetFullPath((Join-Path $PortableRoot '_internal\ai_piano_studio\resources\third-party'))
    $prefix = $noticeRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $manifestPath = Join-Path $noticeRoot 'NOTICE-FILES.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (-not $manifest.files) { throw 'Missing legal-material file inventory.' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $noticeRoot $record.file))
        if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not $seen.Add($path)) {
            throw "Invalid or duplicate legal-material manifest path."
        }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256) {
            throw "Legal material checksum mismatch: $($record.file)"
        }
    }
    $actualFiles = @(Get-ChildItem -LiteralPath $noticeRoot -File -Recurse | Where-Object { $_.FullName -ne $manifestPath })
    if ($seen.Count -ne $actualFiles.Count) { throw 'Unmanifested files in legal-material directory.' }
    foreach ($required in @('DEPENDENCIES.json', 'SOURCE-ARCHIVES.json', 'PYTHON-LICENSE.txt', 'LICENSE-MSVC-RUNTIME.docx', 'FFMPEG-BUILD.json')) {
        if (-not $seen.Contains((Join-Path $noticeRoot $required))) { throw "Missing legal material: $required" }
    }
    return $prefix
}
