$ErrorActionPreference = "Stop"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "`n=== 1/4 基础检索测试 ===" -ForegroundColor Cyan
& $Python "$PSScriptRoot\evaluate.py"

Write-Host "`n=== 2/4 扩展检索测试 ===" -ForegroundColor Cyan
& $Python "$PSScriptRoot\evaluate.py" "$PSScriptRoot\eval_cases_extended.json"

Write-Host "`n=== 3/4 Agent 路由测试 ===" -ForegroundColor Cyan
& $Python "$PSScriptRoot\evaluate_routes.py"

Write-Host "`n=== 4/4 无答案拒答测试（耗时较长） ===" -ForegroundColor Cyan
& $Python "$PSScriptRoot\evaluate_no_answer.py"
