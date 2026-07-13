$ErrorActionPreference = "Stop"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境。请先执行：py -m venv .venv"
}

Set-Location $PSScriptRoot
& $Python -m streamlit run app.py

