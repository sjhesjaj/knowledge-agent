# Answerability evaluation

- Dataset: `eval_answerability_holdout.json`
- Dataset SHA-256: `5f55f703502a703b6453bb6e08e42ebc70015b9f06f03b98d17163dd7a64a104`
- Git SHA: `cf96fa2bfcb8b2ceffba169e226dcf3cabf65f05`
- Chat model: `qwen3:4b`
- Embedding model: `nomic-embed-text`
- Python: 3.12.13 on Windows-11-10.0.26200-SP0
- Runs: 3
- Warm-up seconds (excluded): 13.04

## Per-run metrics

| Metric | Run 1 | Run 2 | Run 3 | Mean |
|---|---:|---:|---:|---:|
| Pass rate | 52.5% | 55.0% | 55.0% | 54.2% |
| Answer Success Rate | 45.0% | 45.0% | 45.0% | 45.0% |
| False Refusal Rate | 20.0% | 20.0% | 20.0% | 20.0% |
| Unanswerable Refusal Rate | 83.3% | 91.7% | 91.7% | 88.9% |
| Refusal Mechanism Match | 58.3% | 66.7% | 66.7% | 63.9% |
| Boundary Message Accuracy | 62.5% | 62.5% | 62.5% | 62.5% |
| Citation Presence Rate | 100.0% | 100.0% | 94.1% | 98.0% |
| Citation Index Validity | 100.0% | 100.0% | 100.0% | 100.0% |
| Required Source Coverage | 50.0% | 50.0% | 50.0% | 50.0% |
| Expected Fact Hit Rate | 75.0% | 75.0% | 75.0% | 75.0% |
| Expected Fact Group Hit Rate | 82.0% | 82.0% | 80.3% | 81.4% |
| Route Accuracy | 57.5% | 57.5% | 57.5% | 57.5% |

## Latency

| Run | p50 | p95 | max |
|---|---:|---:|---:|
| 1 | 7.01s | 13.29s | 16.21s |
| 2 | 6.99s | 11.87s | 15.13s |
| 3 | 7.85s | 12.21s | 15.26s |
| all | 7.85s | 13.10s | 16.21s |

## Stability

- Stable pass (all runs): 21
- Unstable (some runs): 1
- Stable fail (no run): 18
- Consistency rate: 97.5%

Unstable cases: `answer_holdout_026`

Stable failures: `answer_holdout_002`, `answer_holdout_009`, `answer_holdout_010`, `answer_holdout_011`, `answer_holdout_012`, `answer_holdout_015`, `answer_holdout_016`, `answer_holdout_017`, `answer_holdout_018`, `answer_holdout_019`, `answer_holdout_020`, `answer_holdout_024`, `answer_holdout_029`, `answer_holdout_030`, `answer_holdout_031`, `answer_holdout_034`, `answer_holdout_036`, `answer_holdout_040`

## Failures

| Run | ID | Expected | Actual | Route | Reason | Answer |
|---|---|---|---|---|---|---|
| 1 | `answer_holdout_002` | answer | answer | document_only→document_only | generation miss | 报销申请应在费用发生后30天内提交，单笔金额超过2000元需部门负责人审批。[来源1] |
| 1 | `answer_holdout_009` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技远程办公政策规定员工每周最多申请2天远程办公，需提前一个工作日获得直属主管批准，但涉及客户现场支持、机房值守的岗位不适用此政策。[来源 1] |
| 1 | `answer_holdout_010` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 根据资料，星河科技员工年假规定：正式员工入职满一年后每年享5天带薪年假，满三年后增至8天；实习生不享有带薪年假，但每月可申请1天事假。请假需提前在系统提交，1天内由直属主管审批，超… |
| 1 | `answer_holdout_011` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技要求内部资料不得上传至未经公司批准的公共网盘或外部人工智能服务，敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具。[来源 1] |
| 1 | `answer_holdout_012` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技的账号和权限管理设计思路是：系统账号仅限本人使用，不得共享密码；岗位调整时由直属主管发起权限变更；高权限账号每90天复核一次；发现异常立即联系信息安全团队。[来源1] 员工… |
| 1 | `answer_holdout_015` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_holdout_016` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_holdout_017` | answer | policy_refuse | wiki_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_holdout_018` | answer | policy_refuse | document_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 1 | `answer_holdout_019` | answer | answer | wiki_document→document_only | route marker gap; retrieval miss | 年假整体逻辑是：正式员工入职满一年后每年享有5天带薪年假，工作满三年后增加至8天。原文：正式员工入职满一年后，每年享有 5 天带薪年:年假；工作满三年后增加至 8 天。[来源 1] |
| 1 | `answer_holdout_020` | answer | answer | wiki_document_system→document_system | route marker gap; retrieval miss | 根据资料，信息安全要求：内部资料不得上传至未经公司批准的公共网盘或外部人工智能服务。敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具[来源 1]。SKU-B200 … |
| 1 | `answer_holdout_024` | generation_refuse | answer | document_only→document_only | generation miss | 根据提供的资料，工作日加班的加班费是按1倍算的。[来源1] |
| 1 | `answer_holdout_026` | generation_refuse | answer | document_only→document_only | generation miss | 根据提供的资料，问题'在家办公公司给报宽带费或者话费补贴吗？'的答案需要从[来源1]中查找相关信息。我将仔细检查[来源1]的内容。 |
| 1 | `answer_holdout_029` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 1 | `answer_holdout_030` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 1 | `answer_holdout_031` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定。 |
| 1 | `answer_holdout_034` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 1 | `answer_holdout_036` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定。 |
| 1 | `answer_holdout_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_holdout_002` | answer | answer | document_only→document_only | generation miss | 报销申请应在费用发生后30天内提交，单笔金额超过2000元需部门负责人审批。[来源1] |
| 2 | `answer_holdout_009` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技远程办公政策规定员工每周最多申请2天远程办公，需提前一个工作日获得直属主管批准，但涉及客户现场支持、机房值守的岗位不适用此政策。[来源 1] |
| 2 | `answer_holdout_010` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 根据资料，星河科技员工年假规定：正式员工入职满一年后每年享5天带薪年假，满三年后增至8天；实习生不享有带薪年假，但每月可申请1天事假。请假需提前在系统提交，1天内由直属主管审批，超… |
| 2 | `answer_holdout_011` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技要求员工不得将内部资料上传至未经公司批准的公共网盘或外部人工智能服务，敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具。[来源 1] |
| 2 | `answer_holdout_012` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技的账号和权限管理设计思路是：系统账号仅限本人使用，不得共享密码；岗位调整时由直属主管发起权限变更；高权限账号每90天复核一次；发现异常立即联系信息安全团队。[来源1] 员工… |
| 2 | `answer_holdout_015` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_holdout_016` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_holdout_017` | answer | policy_refuse | wiki_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_holdout_018` | answer | policy_refuse | document_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 2 | `answer_holdout_019` | answer | answer | wiki_document→document_only | route marker gap; retrieval miss | 年假整体逻辑是：正式员工入职满一年后每年享有5天带薪年假，工作满三年后增加至8天。原文：正式员工入职满一年后，每年享有 5 天带薪年:年假；工作满三年后增加至 8 天。[来源 1] |
| 2 | `answer_holdout_020` | answer | answer | wiki_document_system→document_system | route marker gap; retrieval miss | 根据资料，信息安全要求：内部资料不得上传至未经公司批准的公共网盘或外部人工智能服务。敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具[来源 1]。SKU-B200 … |
| 2 | `answer_holdout_024` | generation_refuse | answer | document_only→document_only | generation miss | 根据提供的资料，工作日加班的加班费是按1倍算的。[来源1] |
| 2 | `answer_holdout_029` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_holdout_030` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_holdout_031` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_holdout_034` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 2 | `answer_holdout_036` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定。 |
| 2 | `answer_holdout_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_holdout_002` | answer | answer | document_only→document_only | generation miss | 报销申请应在费用发生后 30 天内提交，单笔金额超过 2001 元需部门负责人审批。[来源 1] |
| 3 | `answer_holdout_009` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技远程办公政策规定员工每周最多申请2天远程办公，需提前一个工作日获得直属主管批准，但涉及客户现场支持、机房值守的岗位不适用此政策。[来源 1] |
| 3 | `answer_holdout_010` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 根据资料，星河科技员工年假规定：正式员工入职满一年后每年享5天带薪年假，满三年后增至8天；实习生不享有带薪年假，但每月可申请1天事假。请假需提前在系统提交，1天内由直属主管审批，超… |
| 3 | `answer_holdout_011` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技要求员工不得将内部资料上传至未经公司批准的公共网盘或外部人工智能服务，敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具。[来源 1] |
| 3 | `answer_holdout_012` | answer | answer | wiki_only→document_only | route marker gap; retrieval miss | 星河科技的账号和权限管理设计思路是：系统账号仅限本人使用，不得共享密码；岗位调整时由直属主管发起权限变更；高权限账号每90天复核一次；发现异常需立即联系信息安全团队。[来源1] 员… |
| 3 | `answer_holdout_015` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_holdout_016` | answer | policy_refuse | system_only→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_holdout_017` | answer | policy_refuse | wiki_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_holdout_018` | answer | policy_refuse | document_system→document_only | route marker gap; policy/refusal | 根据现有资料无法确定。 |
| 3 | `answer_holdout_019` | answer | answer | wiki_document→document_only | route marker gap; retrieval miss | 年假整体逻辑是：正式员工入职满一年后每年享有5天带薪年假，工作满三年后增加至8天。原文：正式员工入职满一年后，每年享有 5 天带薪年:年假；工作满三年后增加至 8 天。[来源 1] |
| 3 | `answer_holdout_020` | answer | answer | wiki_document_system→document_system | route marker gap; retrieval miss | 根据资料，信息安全要求：内部资料不得上传至未经公司批准的公共网盘或外部人工智能服务。敏感文件对外发送前须获得部门负责人批准，并使用公司指定的加密工具[来源 1]。SKU-B200 … |
| 3 | `answer_holdout_024` | generation_refuse | answer | document_only→document_only | generation miss | 根据提供的资料，我需要查找关于工作日加班的加班费计算标准的信息。我将仔细阅读每个来源片段，看是否包含相关细节。 |
| 3 | `answer_holdout_029` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_holdout_030` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_holdout_031` | policy_refuse | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定。 |
| 3 | `answer_holdout_034` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |
| 3 | `answer_holdout_036` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定。 |
| 3 | `answer_holdout_040` | boundary | generation_refuse | system_only→document_only | route marker gap | 根据现有资料无法确定 |

## Gates

| Gate | Value | Threshold | Result |
|---|---:|---:|---|
| Answer Success Rate | 45.0% | >= 90% | FAIL |
| False Refusal Rate | 20.0% | <= 10% | FAIL |
| Unanswerable Refusal Rate | 88.9% | >= 90% | FAIL |
| Boundary Message Accuracy | 62.5% | >= 90% | FAIL |
| Citation Index Validity | 100.0% | >= 95% | PASS |
| Expected Fact Hit Rate | 75.0% | >= 90% | FAIL |
| Cross-run Stability | 97.5% | >= 90% | PASS |
