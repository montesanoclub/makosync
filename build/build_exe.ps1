# Build MakosDolphinSync.exe (one-file, GUI, no console) on Windows.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File build\build_exe.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    Write-Host "==> PyInstaller build" -ForegroundColor Cyan
    $env:PYTHONPATH = "src"
    pyinstaller `
        --onefile `
        --noconsole `
        --name MakosDolphinSync `
        --paths src `
        --collect-submodules dolphinsync `
        build\run_app.py

    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed ($LASTEXITCODE)" }

    $exe = Join-Path $repo 'dist\MakosDolphinSync.exe'
    if (-not (Test-Path $exe)) { throw "expected $exe not produced" }

    Write-Host ""
    Write-Host "Built: $exe" -ForegroundColor Green
    Write-Host ("Size:  {0:N1} MB" -f ((Get-Item $exe).Length / 1MB))
    Write-Host ""
    Write-Host "Smoke test (--help):"
    & $exe --help
}
finally {
    Pop-Location
}
