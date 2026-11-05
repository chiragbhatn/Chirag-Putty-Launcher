$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install -r requirements-build.txt
python test_launcher.py
pyinstaller --noconfirm ChiragPuttyLauncher_NetBox.spec
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" ChiragPuttyLauncher_NetBox_Setup.iss
Write-Host "Built: $PSScriptRoot\dist\ChiragPuttyLauncher_NetBox.exe and the installer in installer_output\"
