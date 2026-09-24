# Answerability evaluation

- Dataset: `C:\Users\h000_\Documents\ChatGPT\agent项目改进\knowledge-agent\eval_answerability_validation_v1.json`
- Dataset SHA-256: `e4ad670c6dd6512c8ffcf647b967861d9d1bbef44dbc7872d3cfbf96915c6ced`
- Git SHA: `db3653ab484fde183e2ecf08cd1cf76480458588`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.14 on Windows-11-10.0.26200-SP0
- Runs: 3
- Warm-up seconds (excluded): 3.62

## Per-run metrics

| Metric | Run 1 | Run 2 | Run 3 | Mean |
|---|---:|---:|---:|---:|
| Pass rate | 97.5% | 97.5% | 97.5% | 97.5% |
| Answer Success Rate | 95.0% | 95.0% | 95.0% | 95.0% |
| False Refusal Rate | 5.0% | 5.0% | 5.0% | 5.0% |
| Unanswerable Refusal Rate | 100.0% | 100.0% | 100.0% | 100.0% |
| Refusal Mechanism Match | 100.0% | 100.0% | 100.0% | 100.0% |
| Boundary Message Accuracy | 100.0% | 100.0% | 100.0% | 100.0% |
| Citation Presence Rate | 100.0% | 100.0% | 100.0% | 100.0% |
| Citation Index Validity | 100.0% | 100.0% | 100.0% | 100.0% |
| Required Source Coverage | 100.0% | 100.0% | 100.0% | 100.0% |
| Expected Fact Hit Rate | 95.0% | 95.0% | 95.0% | 95.0% |
| Expected Fact Group Hit Rate | 91.7% | 91.7% | 91.7% | 91.7% |
| Route Accuracy | 100.0% | 100.0% | 100.0% | 100.0% |

## Latency

| Run | p50 | p95 | max |
|---|---:|---:|---:|
| 1 | 2.84s | 10.21s | 10.48s |
| 2 | 2.83s | 10.18s | 10.51s |
| 3 | 2.79s | 10.33s | 10.43s |
| all | 2.83s | 10.29s | 10.51s |

## Stability

- Stable pass (all runs): 39
- Unstable (some runs): 0
- Stable fail (no run): 1
- Consistency rate: 100.0%

Stable failures: `answer_document_h008`

## Failures

| Run | ID | Expected | Actual | Route | Reason | Answer |
|---|---|---|---|---|---|---|
| 1 | `answer_document_h008` | answer | policy_refuse | document_only→document_only | policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_document_h008` | answer | policy_refuse | document_only→document_only | policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_document_h008` | answer | policy_refuse | document_only→document_only | policy/refusal | 根据现有资料无法确定。 |

## Gates

| Gate | Value | Threshold | Result |
|---|---:|---:|---|
| Answer Success Rate | 95.0% | >= 90% | PASS |
| False Refusal Rate | 5.0% | <= 10% | PASS |
| Unanswerable Refusal Rate | 100.0% | >= 90% | PASS |
| Boundary Message Accuracy | 100.0% | >= 90% | PASS |
| Citation Index Validity | 100.0% | >= 95% | PASS |
| Expected Fact Hit Rate | 95.0% | >= 90% | PASS |
| Cross-run Stability | 100.0% | >= 90% | PASS |
