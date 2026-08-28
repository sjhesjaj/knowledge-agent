# Answerability evaluation

- Dataset: `eval_answerability_holdout.json`
- Dataset SHA-256: `0be35413352610d19008ca7de10c88c57982f9ee7d2731b77e1357aa9734fc07`
- Git SHA: `cf96fa2bfcb8b2ceffba169e226dcf3cabf65f05`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.13 on Windows-11-10.0.26200-SP0
- Runs: 3
- Warm-up seconds (excluded): 3.97

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
| 1 | 3.34s | 12.03s | 12.71s |
| 2 | 3.23s | 11.66s | 11.79s |
| 3 | 3.24s | 11.68s | 11.80s |
| all | 3.28s | 11.78s | 12.71s |

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
