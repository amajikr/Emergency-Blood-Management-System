# Build the EBMS OS engine (C) on Windows and optionally run its C tests.
# Usage (PowerShell, from the ebms\ folder):
#   .\build.ps1            -> builds bin\ebms_os.exe
#   .\build.ps1 -Test      -> builds and runs the C unit tests in tests\
param([switch]$Test)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$gcc = Get-Command gcc -ErrorAction SilentlyContinue
if (-not $gcc) {
    Write-Host "gcc was not found on PATH. Install MinGW-w64 (e.g. via MSYS2 or winget) and reopen PowerShell." -ForegroundColor Red
    exit 1
}
Write-Host "Using gcc: $($gcc.Source)"

$std = @("-std=c11", "-Wall", "-Wextra", "-O2", "-Iinclude")
$modules = @("src\common.c", "src\json_parser.c", "src\scheduler.c", "src\banker.c", "src\deadlock.c",
             "src\producer_consumer.c", "src\readers_writers.c")

New-Item -ItemType Directory -Force -Path "bin" | Out-Null
& gcc @std -o "bin\ebms_os.exe" "src\main.c" @modules
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed." -ForegroundColor Red; exit 1 }
Write-Host "Built bin\ebms_os.exe" -ForegroundColor Green

if ($Test) {
    $tests = @("test_json", "test_scheduler", "test_banker", "test_deadlock", "test_producer_consumer", "test_readers_writers")
    $failed = 0
    foreach ($t in $tests) {
        & gcc @std -g -o "bin\$t.exe" "tests\$t.c" @modules
        if ($LASTEXITCODE -ne 0) { Write-Host "Compile failed: $t" -ForegroundColor Red; $failed++; continue }
        Write-Host "== $t"
        & ".\bin\$t.exe"
        if ($LASTEXITCODE -ne 0) { $failed++ }
    }
    if ($failed -gt 0) { Write-Host "$failed test program(s) failed." -ForegroundColor Red; exit 1 }
    Write-Host "All C test programs passed." -ForegroundColor Green
}
