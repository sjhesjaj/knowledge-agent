# Answerability evaluation

- Dataset: `eval_answerability_holdout.json`
- Dataset SHA-256: `5f55f703502a703b6453bb6e08e42ebc70015b9f06f03b98d17163dd7a64a104`
- Git SHA: `5c580c1f56b5d7c28a484dc9d3b318559d3b238c`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.13 on Windows-11-10.0.26200-SP0
- Runs: 1
- Warm-up seconds (excluded): 96.67

## Per-run metrics

| Metric | Run 1 | Mean |
|---|---:|---:|
| Pass rate | 82.5% | 82.5% |
| Answer Success Rate | 85.0% | 85.0% |
| False Refusal Rate | 5.0% | 5.0% |
| Unanswerable Refusal Rate | 100.0% | 100.0% |
| Refusal Mechanism Match | 91.7% | 91.7% |
| Boundary Message Accuracy | 62.5% | 62.5% |
| Citation Presence Rate | 100.0% | 100.0% |
| Citation Index Validity | 100.0% | 100.0% |
| Required Source Coverage | 95.0% | 95.0% |
| Expected Fact Hit Rate | 85.0% | 85.0% |
| Expected Fact Group Hit Rate | 91.8% | 91.8% |
| Route Accuracy | 80.0% | 80.0% |

## Latency

| Run | p50 | p95 | max |
|---|---:|---:|---:|
| 1 | 20.47s | 52.77s | 61.25s |
| all | 20.47s | 52.77s | 61.25s |

## Stability

- Stable pass (all runs): 33
- Unstable (some runs): 0
- Stable fail (no run): 7
- Consistency rate: 100.0%

Stable failures: `answer_holdout_002`, `answer_holdout_015`, `answer_holdout_016`, `answer_holdout_028`, `answer_holdout_034`, `answer_holdout_036`, `answer_holdout_040`

## Failures

| Run | ID | Expected | Actual | Route | Reason | Answer |
|---|---|---|---|---|---|---|
| 1 | `answer_holdout_002` | answer | answer | document_only→document_only | generation miss | 报销申请应在费用发生后30天内提交，单笔金额超过2000元需部门负责人审批。[来源1] |
| 1 | `answer_holdout_015` | answer | answer | system_only→system_only | generation miss | SKU_B200 现在没有货。[来源 1] |
| 1 | `answer_holdout_016` | answer | boundary | system_only→document_system | route marker gap; unsupported boundary | 当前一次支持查询一个 SKU，请拆分后分别查询。 |
| 1 | `answer_holdout_028` | generation_refuse | policy_refuse | document_only→wiki_document | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_holdout_034` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 1 | `answer_holdout_036` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据提供的资料，我无法确定您提交的转正审批是否通过。资料中描述了转正流程，包括试用期员工应在期满前10个工作日提交转正总结，以及审批通过后转正结果自试用期结束次日起生效，但没有具体… |
| 1 | `answer_holdout_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |

## Gates

| Gate | Value | Threshold | Result |
|---|---:|---:|---|
| Answer Success Rate | 85.0% | >= 90% | FAIL |
| False Refusal Rate | 5.0% | <= 10% | PASS |
| Unanswerable Refusal Rate | 100.0% | >= 90% | PASS |
| Boundary Message Accuracy | 62.5% | >= 90% | FAIL |
| Citation Index Validity | 100.0% | >= 95% | PASS |
| Expected Fact Hit Rate | 85.0% | >= 90% | FAIL |
| Cross-run Stability | 100.0% | >= 90% | PASS |
