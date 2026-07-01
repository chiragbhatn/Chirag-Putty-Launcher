$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install -r requirements-build.txt
pyinstaller --noconsole --onefile --name ChiragPuttyLauncher device_launcher_app.py
Write-Host "Built: $PSScriptRoot\dist\ChiragPuttyLauncher.exe"
