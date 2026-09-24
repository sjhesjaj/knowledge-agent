# Diagnostic report: eval/env_v1_validation_qwen.json

- dataset: `eval_answerability_validation_v1.json` · labels: `eval/environments/eval-env-v1/labels/validation_v1.labels.json` · traces: `eval/artifacts/env_v1_validation_qwen/traces.sqlite`
- semantic judge: DisabledJudge (calls: 0)
- case runs: 120 · official passed 117 · failed 3
- failed runs with a deterministic primary root cause: 3 · unattributed: 0

## Primary root cause (failed case runs, each counted once)

| category | primary | secondary effects | latent issues (passed runs) |
|---|---:|---:|---:|
| routing_error | 0 | 0 | 0 |
| planning_error | 3 | 0 | 0 |
| tool_error | 0 | 0 | 0 |
| retrieval_error | 0 | 0 | 0 |
| evidence_error | 0 | 3 | 0 |
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
| planning | 117 | 3 | 0 | 0 | 0 |
| tool | 96 | 0 | 0 | 0 | 24 |
| retrieval | 72 | 0 | 0 | 0 | 48 |
| evidence | 93 | 3 | 0 | 0 | 24 |
| generation | 81 | 0 | 0 | 3 | 36 |

## Failed case runs

- `answer_document_h008` run 1 · trace `3684eb9ed88a4d94994bf3a450970bd3` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']
- `answer_document_h008` run 2 · trace `19bd677f05f54194a665a1aaee15a352` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']
- `answer_document_h008` run 3 · trace `2dacc7690bca4e4cb1ee7438061179e4` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']

## Latent issues (official pass, a stage rule failed)

None.

## Label coverage

40 cases; overlay fields: acceptable_routes 0, forbidden_tools 0, plan_constraints 1, expected_arguments 10, expected_tool_status 4, expected_evidence 20
