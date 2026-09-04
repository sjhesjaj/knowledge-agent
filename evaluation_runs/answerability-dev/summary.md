# Answerability evaluation

- Dataset: `eval_answerability_dev.json`
- Dataset SHA-256: `a50596cd934c9b05ef65c198e6b8550d1f8d8447dedfd322f4f1a5783541cbac`
- Git SHA: `cf96fa2bfcb8b2ceffba169e226dcf3cabf65f05`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.13 on Windows-11-10.0.26200-SP0
- Runs: 1
- Warm-up seconds (excluded): 8.13

## Per-run metrics

| Metric | Run 1 | Mean |
|---|---:|---:|
| Pass rate | 97.5% | 97.5% |
| Answer Success Rate | 95.0% | 95.0% |
| False Refusal Rate | 5.0% | 5.0% |
| Unanswerable Refusal Rate | 100.0% | 100.0% |
| Refusal Mechanism Match | 100.0% | 100.0% |
| Boundary Message Accuracy | 100.0% | 100.0% |
| Citation Presence Rate | 100.0% | 100.0% |
| Citation Index Validity | 100.0% | 100.0% |
| Required Source Coverage | 100.0% | 100.0% |
| Expected Fact Hit Rate | 95.0% | 95.0% |
| Expected Fact Group Hit Rate | 95.7% | 95.7% |
| Route Accuracy | 100.0% | 100.0% |

## Latency

| Run | p50 | p95 | max |
|---|---:|---:|---:|
| 1 | 3.34s | 12.11s | 12.91s |
| all | 3.34s | 12.11s | 12.91s |

## Stability

- Stable pass (all runs): 39
- Unstable (some runs): 0
- Stable fail (no run): 1
- Consistency rate: 100.0%

Stable failures: `answer_document_008`

## Failures

| Run | ID | Expected | Actual | Route | Reason | Answer |
|---|---|---|---|---|---|---|
| 1 | `answer_document_008` | answer | policy_refuse | document_only→document_only | policy/refusal | 根据现有资料无法确定。 |

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
