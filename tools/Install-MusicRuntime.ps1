param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $taskRoot '.music-runtime'
if (-not $Python) { $Python = Join-Path $taskRoot '.venv-dev/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Provide a Python 3.12 executable with -Python.' }
$uv = (Get-Command uv -ErrorAction Stop).Source
$runtimePython = Join-Path $runtime 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $uv venv --python $Python $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the optional music runtime.' }
}
# Explicit complete lock avoids Basic Pitch 0.4's Python 3.12 TensorFlow marker.
# The application uses its supported ONNX inference API instead.
& $uv --cache-dir (Join-Path $runtime 'uv-cache') pip install --python $runtimePython --no-deps -r (Join-Path $PSScriptRoot 'music-runtime-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the optional music runtime.' }
& $runtimePython -c 'from basic_pitch.inference import Model; import onnxruntime, music21, pypdfium2; print("Optional ONNX / MusicXML / PDF score runtime ready")'
if ($LASTEXITCODE -ne 0) { throw 'Optional music runtime validation failed.' }
