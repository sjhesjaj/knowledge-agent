# Diagnostic report: eval/stage1_trace_qwen.json

- dataset: `eval_answerability_validation_v1.json` · labels: `eval/diagnostic_labels/validation_v1.labels.json` · traces: `eval/artifacts/stage1_trace_qwen/traces.sqlite`
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

- `answer_document_h008` run 1 · trace `b8181709bca743959a235f296241ca2f` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']
- `answer_document_h008` run 2 · trace `4a7389c1867342f29319fee0ad4aca5e` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']
- `answer_document_h008` run 3 · trace `285f5d74bd7541feb76ab3a39fa3161c` · **planning_error** (planning) - signal:requires_freshness: requires_freshness=True, labelled False
    - secondary: evidence_error (policy_outcome) policy refuse with ['freshness_unsupported']

## Latent issues (official pass, a stage rule failed)

None.

## Skipped checks (label does not apply to this run's environment)

Wiki labels were written for `committed_sample`; this run read `published_build:build-0001`.

- expected_evidence:wiki: 18 case runs

## Label coverage

40 cases; overlay fields: acceptable_routes 0, forbidden_tools 0, plan_constraints 1, expected_arguments 10, expected_tool_status 4, expected_evidence 20
