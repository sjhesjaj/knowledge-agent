# Answerability evaluation

- Dataset: `eval_answerability_blind_v2.json`
- Dataset SHA-256: `7ae1c0af4f82e0179ad377a6d433605da6e673d2e1d0ed5a689bcf3fd1bc9d89`
- Git SHA: `65b402c203fe389742b6bf87b81bf32cba081e73`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.13 on Windows-11-10.0.26200-SP0
- Runs: 3
- Warm-up seconds (excluded): 8.87

## Per-run metrics

| Metric | Run 1 | Run 2 | Run 3 | Mean |
|---|---:|---:|---:|---:|
| Pass rate | 80.0% | 80.0% | 80.0% | 80.0% |
| Answer Success Rate | 85.0% | 85.0% | 85.0% | 85.0% |
| False Refusal Rate | 0.0% | 0.0% | 0.0% | 0.0% |
| Unanswerable Refusal Rate | 91.7% | 91.7% | 91.7% | 91.7% |
| Refusal Mechanism Match | 91.7% | 91.7% | 91.7% | 91.7% |
| Boundary Message Accuracy | 50.0% | 50.0% | 50.0% | 50.0% |
| Citation Presence Rate | 95.2% | 95.2% | 95.2% | 95.2% |
| Citation Index Validity | 100.0% | 100.0% | 100.0% | 100.0% |
| Required Source Coverage | 90.0% | 90.0% | 90.0% | 90.0% |
| Expected Fact Hit Rate | 90.0% | 90.0% | 90.0% | 90.0% |
| Expected Fact Group Hit Rate | 90.4% | 90.4% | 90.4% | 90.4% |
| Route Accuracy | 82.5% | 82.5% | 82.5% | 82.5% |

## Latency

| Run | p50 | p95 | max |
|---|---:|---:|---:|
| 1 | 3.77s | 11.86s | 13.14s |
| 2 | 3.73s | 11.64s | 12.77s |
| 3 | 3.80s | 11.84s | 13.42s |
| all | 3.77s | 11.79s | 13.42s |

## Stability

- Stable pass (all runs): 32
- Unstable (some runs): 0
- Stable fail (no run): 8
- Consistency rate: 100.0%

Stable failures: `answer_v2_006`, `answer_v2_018`, `answer_v2_019`, `answer_v2_028`, `answer_v2_034`, `answer_v2_035`, `answer_v2_038`, `answer_v2_040`

## Failures

| Run | ID | Expected | Actual | Route | Reason | Answer |
|---|---|---|---|---|---|---|
| 1 | `answer_v2_006` | answer | answer | document_only→document_only | citation mismatch; generation miss | 下个月先去深圳、再飞一趟新加坡，这两趟差在系统里提申请，要过的审批人是不是不一样，分别得谁签 |
| 1 | `answer_v2_018` | answer | answer | document_system→system_only | route marker gap; retrieval miss; generation miss | 根据资料，设备遗失几小时内必须上报、报给谁的信息未在资料中提及。SKU sku-c300 的当前库存为 7 箱。[来源 1] |
| 1 | `answer_v2_019` | answer | answer | wiki_document_system→wiki_system | route marker gap; retrieval miss | 高权限账号每 90 天复核一次。[来源 1] 系统账号仅限本人使用，不得共享密码或验证码。[来源 3] SKU sku-b20:0 的当前库存为 0 件。[来源 4] |
| 1 | `answer_v2_028` | generation_refuse | answer | document_only→document_only | generation miss | 工作日加班的加班费按1倍计算。[来源1] |
| 1 | `answer_v2_034` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_v2_035` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 1 | `answer_v2_038` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_v2_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_v2_006` | answer | answer | document_only→document_only | citation mismatch; generation miss | 下个月先去深圳、再飞一趟新加坡，这两趟差在系统里提申请，要过的审批人是不是不一样，分别得谁签 |
| 2 | `answer_v2_018` | answer | answer | document_system→system_only | route marker gap; retrieval miss; generation miss | 根据资料，设备遗失几小时内必须上报、报给谁的信息未在资料中提及。SKU sku-c300 的当前库存为 7 箱。[来源 1] |
| 2 | `answer_v2_019` | answer | answer | wiki_document_system→wiki_system | route marker gap; retrieval miss | 高权限账号每 90 天复核一次。[来源 1] 系统账号仅限本人使用，不得共享密码或验证码。[来源 3] SKU sku-b20:0 的当前库存为 0 件。[来源 4] |
| 2 | `answer_v2_028` | generation_refuse | answer | document_only→document_only | generation miss | 工作日加班的加班费按1倍计算。[来源1] |
| 2 | `answer_v2_034` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_v2_035` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_v2_038` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_v2_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_v2_006` | answer | answer | document_only→document_only | citation mismatch; generation miss | 下个月先去深圳、再飞一趟新加坡，这两趟差在系统里提申请，要过的审批人是不是不一样，分别得谁签 |
| 3 | `answer_v2_018` | answer | answer | document_system→system_only | route marker gap; retrieval miss; generation miss | 根据资料，设备遗失几小时内必须上报、报给谁的信息未在资料中提及。SKU sku-c300 的当前库存为 7 箱。[来源 1] |
| 3 | `answer_v2_019` | answer | answer | wiki_document_system→wiki_system | route marker gap; retrieval miss | 高权限账号每 90 天复核一次。[来源 1] 系统账号仅限本人使用，不得共享密码或验证码。[来源 3] SKU sku-b20:0 的当前库存为 0 件。[来源 4] |
| 3 | `answer_v2_028` | generation_refuse | answer | document_only→document_only | generation miss | 工作日加班的加班费按1倍计算。[来源1] |
| 3 | `answer_v2_034` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_v2_035` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_v2_038` | boundary | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_v2_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |

## Gates

| Gate | Value | Threshold | Result |
|---|---:|---:|---|
| Answer Success Rate | 85.0% | >= 90% | FAIL |
| False Refusal Rate | 0.0% | <= 10% | PASS |
| Unanswerable Refusal Rate | 91.7% | >= 90% | PASS |
| Boundary Message Accuracy | 50.0% | >= 90% | FAIL |
| Citation Index Validity | 100.0% | >= 95% | PASS |
| Expected Fact Hit Rate | 90.0% | >= 90% | PASS |
| Cross-run Stability | 100.0% | >= 90% | PASS |
