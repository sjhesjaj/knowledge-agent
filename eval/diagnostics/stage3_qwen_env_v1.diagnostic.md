# Diagnostic report: eval/stage3_qwen_env_v1.json

- dataset: `eval_answerability_validation_v1.json` · labels: `eval/diagnostic_labels/validation_v1.labels.json` · traces: `eval/artifacts/stage3_qwen_env_v1/traces.sqlite`
- semantic judge: DisabledJudge (calls: 0)
- case runs: 120 · official passed 120 · failed 0
- failed runs with a deterministic primary root cause: 0 · unattributed: 0

## Primary root cause (failed case runs, each counted once)

| category | primary | secondary effects | latent issues (passed runs) |
|---|---:|---:|---:|
| routing_error | 0 | 0 | 0 |
| planning_error | 0 | 0 | 0 |
| tool_error | 0 | 0 | 0 |
| retrieval_error | 0 | 0 | 0 |
| evidence_error | 0 | 0 | 0 |
| generation_error | 0 | 0 | 0 |

## Unattributed (diagnostic gaps, not error categories)

| kind | failed runs |
|---|---:|
| missing_trace | 0 |
| environment_failure | 0 |
| rule_inconclusive | 0 |
| label_gap | 0 |

## Stage status over all case runs

| stage | pass | fail | inconclusive | blocked | not_applicable |
|---|---:|---:|---:|---:|---:|
| routing | 120 | 0 | 0 | 0 | 0 |
| planning | 120 | 0 | 0 | 0 | 0 |
| tool | 96 | 0 | 0 | 0 | 24 |
| retrieval | 72 | 0 | 0 | 0 | 48 |
| evidence | 96 | 0 | 0 | 0 | 24 |
| generation | 84 | 0 | 0 | 0 | 36 |

## Failed case runs

None.

## Latent issues (official pass, a stage rule failed)

None.

## Label coverage

40 cases; overlay fields: acceptable_routes 0, forbidden_tools 0, plan_constraints 1, expected_arguments 10, expected_tool_status 4, expected_evidence 20
