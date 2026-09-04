# Reproduce the V1 agent evaluation in the documented order.
#
# Three datasets families exist, and they are not interchangeable:
#
#   *_dev            - development set, used while building the evaluators.
#   *_validation_v1  - the original "holdout". It was reviewed alongside the dev
#                      set and shares templates with it, so it is a regression
#                      baseline, NOT an independent holdout. Kept and replayable.
#   *_holdout        - the blind holdout, written by an agent that never saw the
#                      other sets, the planner internals, or any failure list.
#                      Frozen after its first scored run.
#
# Order matters: schema and cross-set overlap are checked before anything is
# scored, because a holdout that overlaps the dev set cannot be un-run.
#
# A non-zero exit from an evaluator is a failed *gate*, not a crashed script,
# so this runner records each exit code and keeps going.

param(
    # The validation_v1 answerability replay costs ~8 minutes of local model
    # time and reproduces an already-recorded result, so it is opt-in.
    [switch]$IncludeValidationReplay
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$python = '.\.venv\Scripts\python.exe'
$outputRoot = 'evaluation_runs'
New-Item -ItemType Directory -Force $outputRoot | Out-Null

$results = @()

function Invoke-Step {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [string]$ConsoleLog
    )
    Write-Host ''
    Write-Host ('=' * 78)
    Write-Host "STEP: $Name"
    Write-Host ('=' * 78)
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    if ($ConsoleLog) {
        & $script:python @Arguments 2>&1 | Tee-Object -FilePath $ConsoleLog
    } else {
        & $script:python @Arguments
    }
    $code = $LASTEXITCODE
    $stopwatch.Stop()
    Write-Host ("STEP '{0}' exit={1} seconds={2:N2}" -f $Name, $code, $stopwatch.Elapsed.TotalSeconds)
    return [pscustomobject]@{
        Step     = $Name
        ExitCode = $code
        Seconds  = [math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
    }
}

# 1. Static checks.
$results += Invoke-Step 'py_compile' @(
    '-m', 'py_compile',
    'evaluate_orchestrated_routes.py',
    'evaluate_answerability.py',
    'check_evaluation_overlap.py')
$results += Invoke-Step 'unittest' @('-m', 'unittest', 'discover')

# 2. Schema validation for every dataset, before any scoring.
foreach ($dataset in @(
        'eval_orchestrated_routes_dev.json',
        'eval_orchestrated_routes_validation_v1.json',
        'eval_orchestrated_routes_holdout.json')) {
    $results += Invoke-Step "schema:$dataset" @(
        'evaluate_orchestrated_routes.py', '--dataset', $dataset, '--validate-only')
}
foreach ($dataset in @(
        'eval_answerability_dev.json',
        'eval_answerability_validation_v1.json',
        'eval_answerability_holdout.json')) {
    $results += Invoke-Step "schema:$dataset" @(
        'evaluate_answerability.py', '--dataset', $dataset, '--validate-only')
}

# 3. Cross-set overlap. This gates the blind holdout's independence and must
#    pass before the holdout is scored for the first time.
$results += Invoke-Step 'overlap' @('check_evaluation_overlap.py') `
    "$outputRoot\overlap.console.txt"

# 4. Route evaluation. The planner is pure, so one pass per dataset is the whole
#    signal - repeating it would only repeat itself.
$results += Invoke-Step 'route:dev' @(
    'evaluate_orchestrated_routes.py',
    '--dataset', 'eval_orchestrated_routes_dev.json',
    '--output', "$outputRoot\route-dev.json") "$outputRoot\route-dev.console.txt"

$results += Invoke-Step 'route:validation_v1' @(
    'evaluate_orchestrated_routes.py',
    '--dataset', 'eval_orchestrated_routes_validation_v1.json',
    '--output', "$outputRoot\route-validation-v1.json") "$outputRoot\route-validation-v1.console.txt"

$results += Invoke-Step 'route:blind_holdout' @(
    'evaluate_orchestrated_routes.py',
    '--dataset', 'eval_orchestrated_routes_holdout.json',
    '--output', "$outputRoot\route-blind-holdout.json") "$outputRoot\route-blind-holdout.console.txt"

# 5. Answerability. Needs Ollama with qwen3:4b and nomic-embed-text.
#    Only the blind holdout repeats: the generation stage is the only
#    non-deterministic part, and stability is a claim about the holdout.
$results += Invoke-Step 'answerability:dev' @(
    'evaluate_answerability.py',
    '--dataset', 'eval_answerability_dev.json',
    '--runs', '1',
    '--output-dir', "$outputRoot\answerability-dev") "$outputRoot\answerability-dev.console.txt"

if ($IncludeValidationReplay) {
    $results += Invoke-Step 'answerability:validation_v1' @(
        'evaluate_answerability.py',
        '--dataset', 'eval_answerability_validation_v1.json',
        '--runs', '3',
        '--output-dir', "$outputRoot\answerability-validation-v1") "$outputRoot\answerability-validation-v1.console.txt"
} else {
    Write-Host ''
    Write-Host 'SKIPPED answerability:validation_v1 (pass -IncludeValidationReplay to run it)'
}

$results += Invoke-Step 'answerability:blind_holdout' @(
    'evaluate_answerability.py',
    '--dataset', 'eval_answerability_holdout.json',
    '--runs', '3',
    '--output-dir', "$outputRoot\answerability-blind-holdout") "$outputRoot\answerability-blind-holdout.console.txt"

Write-Host ''
Write-Host ('=' * 78)
Write-Host 'Dataset SHA-256'
Write-Host ('=' * 78)
Get-ChildItem @(
    'eval_orchestrated_routes_dev.json',
    'eval_orchestrated_routes_validation_v1.json',
    'eval_orchestrated_routes_holdout.json',
    'eval_answerability_dev.json',
    'eval_answerability_validation_v1.json',
    'eval_answerability_holdout.json') | Get-FileHash -Algorithm SHA256 |
    Format-Table -AutoSize Hash, Path

Write-Host ''
Write-Host ('=' * 78)
Write-Host 'Step results'
Write-Host ('=' * 78)
$results | Format-Table -AutoSize

$failed = @($results | Where-Object { $_.ExitCode -ne 0 })
if ($failed.Count -gt 0) {
    Write-Host ''
    Write-Host "$($failed.Count) step(s) reported a non-zero exit code (failed gate or error)."
    exit 1
}
exit 0
