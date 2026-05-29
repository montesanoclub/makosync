# Build MakoSync.exe (one-file, GUI, no console) on Windows.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File build\build_exe.ps1
#
# Manager mode (Meet Manager .mdb) reads via the mdbtools `mdb-export` binary,
# bundled from src\makosync\tools (run build\fetch_mdbtools.ps1 to populate it).
# The Mako icon is embedded (--icon); the runtime PNG is bundled for the window.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    if (-not (Test-Path "src\makosync\tools\mdb-export.exe")) {
        Write-Warning "src\makosync\tools\mdb-export.exe is missing - Manager mode will not work in this build. Run build\fetch_mdbtools.ps1 first."
    }

    Write-Host "==> PyInstaller build" -ForegroundColor Cyan
    $env:PYTHONPATH = "src"
    pyinstaller `
        --onefile `
        --noconsole `
        --name MakoSync `
        --icon "src\makosync\assets\mako.ico" `
        --paths src `
        --collect-submodules makosync `
        --add-data "src\makosync\assets\mako.png;makosync\assets" `
        --add-data "src\makosync\tools;makosync\tools" `
        build\run_app.py

    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed ($LASTEXITCODE)" }

    $exe = Join-Path $repo 'dist\MakoSync.exe'
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
