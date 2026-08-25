<#
.SYNOPSIS
    Builds the Windows Customization Toolkit into a single .exe with PyInstaller.

.DESCRIPTION
    Building does NOT itself require Administrator rights - only *running*
    the finished .exe does (its embedded manifest requests elevation; see
    packaging/app.manifest). Run this from a normal PowerShell prompt.

.EXAMPLE
    .\packaging\build.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

python -m pip install -r (Join-Path $repoRoot "requirements.txt")
python -m pip install -r (Join-Path $repoRoot "requirements-build.txt")

python -m PyInstaller (Join-Path $repoRoot "packaging\pyinstaller.spec") `
    --noconfirm --clean `
    --distpath (Join-Path $repoRoot "dist") `
    --workpath (Join-Path $repoRoot "build")

$exePath = Join-Path $repoRoot "dist\WindowsCustomizationToolkit.exe"
if (Test-Path $exePath) {
    Write-Host "Built: $exePath"
} else {
    Write-Error "Build finished but $exePath was not found - check the PyInstaller output above."
}
