# Populate src\makosync\tools\ with mdb-export.exe + its DLLs, which Manager mode
# shells out to. mdbtools reads the raw Jet file, ignoring the Hy-Tek database
# password the ACE/ODBC driver can't open, and reads both Meet and Team Manager DBs.
#
# MSYS2 has NO mdbtools package and there's no trustworthy prebuilt Windows binary,
# so we COMPILE it from source under MSYS2/mingw64 (build\build_mdbtools.sh) and
# copy the binary + its full DLL closure into the repo. Run on the BUILD machine
# before build\build_exe.ps1. GitHub windows-latest runners ship MSYS2 at C:\msys64.
#
# Verify afterwards:  & src\makosync\tools\mdb-export.exe --version   -> "mdbtools v..."

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$bash = 'C:\msys64\usr\bin\bash.exe'
$dest = Join-Path $repo 'src\makosync\tools'

if (-not (Test-Path $bash)) {
    throw "MSYS2 not found at C:\msys64. Install it (winget install MSYS2.MSYS2, or https://www.msys2.org), then re-run."
}

# Run the bash build script as a FILE (avoids PowerShell native-arg quoting that
# mangles -c strings). Normalize to LF first so bash doesn't choke on CR.
$sh = Join-Path $repo 'build\build_mdbtools.sh'
[IO.File]::WriteAllText($sh, ([IO.File]::ReadAllText($sh) -replace "`r`n", "`n" -replace "`r", "`n"))
$shUnix = $sh -replace '\\', '/'   # MSYS2 bash accepts a C:/... path as the script file

Write-Host "==> Building mdbtools from source via MSYS2 (a few minutes)..." -ForegroundColor Cyan
& $bash $shUnix
# build_mdbtools.sh redirects its own output to _mdbbuild.log; surface the tail.
$log = Join-Path $repo '_mdbbuild.log'
if (Test-Path $log) { Get-Content $log -Tail 20 }

$exe = Join-Path $dest 'mdb-export.exe'
if (-not (Test-Path $exe)) { throw "mdb-export.exe was not produced - see $log" }
Write-Host "`n==> Verifying bundled mdb-export runs standalone..." -ForegroundColor Cyan
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "bundled mdb-export.exe failed to run (missing DLL?) - see $log" }

Write-Host "`nPopulated ${dest}:" -ForegroundColor Green
Get-ChildItem $dest -File | Where-Object { $_.Name -ne 'README.md' } | Select-Object Name, Length | Format-Table -Auto
