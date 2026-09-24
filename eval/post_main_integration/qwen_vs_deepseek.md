# Post-main-integration baseline — Qwen vs DeepSeek on eval-env-v1

- series `pmi` — post-main-integration baseline
- environment `eval-env-v1` (manifest `a6ecd2b4ae9dbede…`), commit `b8c81971c4bf`, 3 runs per arm
- only variable: provider/model — qwen `qwen3:4b` (ollama) vs deepseek `deepseek-flash`; DeepSeek thinking disabled (pinned by OpenAICompatibleProvider)

## Official results

| metric | qwen | deepseek |
|---|---|---|
| passed per run | 39 / 39 / 39 | 39 / 39 / 39 |
| pass rate (mean) | 97.5% | 97.5% |
| answer success (mean) | 95.0% | 95.0% |
| false refusal (mean) | 5.0% | 5.0% |
| cross-run stability | 100.0% (unstable 0) | 100.0% (unstable 0) |

## Diagnostic (all case runs; primary counted once per failed run)

| | qwen | deepseek |
|---|---|---|
| primary routing_error | 0 | 0 |
| primary planning_error | 3 | 3 |
| primary tool_error | 0 | 0 |
| primary retrieval_error | 0 | 0 |
| primary evidence_error | 0 | 0 |
| primary generation_error | 0 | 0 |
| secondary_effects | 3 {'evidence_error': 3} | 3 {'evidence_error': 3} |
| latent_issues | 0  | 0  |
| unattributed | 0  | 0  |

Per run:

- qwen run 1: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}
- qwen run 2: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}
- qwen run 3: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}
- deepseek run 1: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}
- deepseek run 2: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}
- deepseek run 3: primary {'planning_error': 1}, secondary {'evidence_error': 1}, latent {}, unattributed {}

## Case transitions (qwen -> deepseek, over 3 runs each)

stable_pass 39 · unchanged_failure 1 · fixed 0 · newly_failed 0 · unstable 0

| case | transition | qwen | deepseek | behaviours (qwen -> deepseek) |
|---|---|---|---|---|
| `answer_document_h008` | unchanged_failure | 0/3 | 0/3 | ['policy_refuse'] -> ['policy_refuse'] |

## Latency, tokens, calls, cost

| | qwen | deepseek |
|---|---|---|
| task latency mean / p95 (s) | 3.60 / 10.47 | 1.96 / 5.89 |
| LLM latency per task (s) | 3.13 | 1.46 |
| prompt / completion tokens (total) | 55219 / 3891 | 58837 / 3101 |
| prompt / completion tokens per task | 460.2 / 32.4 | 490.3 / 25.8 |
| DeepSeek cache hit / miss tokens | - | 29169 / 29668 |
| LLM calls total (per task) | 132 (1.10) | 132 (1.10) |
| tool calls total (per task) | 108 (0.90) | 108 (0.90) |
| API cost total | $0 (local) | $0.012797 |
| cost / task | $0 | $0.00010664 (120 task runs) |
| cost / successful task | $0 | $0.00010937 (117 passed) |

_Token counts come from each provider's own tokenizer and are descriptive only; they are not comparable as context size and say nothing about context optimisation._

_Qwen runs locally; its API cost is 0 and hardware is not costed._ Cost basis: list price x recorded usage (cache hit/miss split, peak by call time); not reconciled with the invoice; 132 of 132 DeepSeek calls fell in peak hours.

## Prompt adaptation (DeepSeek)

DeepSeek's effective prompts are not byte-identical to Qwen's: schema requests are sent as json_object with an appended JSON Schema instruction (see prompt_adaptations). Logical prompts are identical by construction.

- qwen: calls whose effective prompt differs from the logical one: 0; adaptations {}
- deepseek: calls whose effective prompt differs from the logical one: 105; adaptations {'schema_not_supported_by_json_object': 105}

## answer_document_h008 trace comparison (run 1)

### qwen — 0/3 passed

```
run 727e20f6c30442dc889420885e53159a  completed
  eval eval:eval-env-v1 mode=orchestrated streaming=False  2026-09-24T05:55:16.906+00:00  28.7ms
  commit=b8c81971c4bf  provider=ollama/qwen3:4b  llm_calls=0 tokens=0+0
  question: 目前的制度里，核心协作时间是几点到几点？
  eval: eval_answerability_validation_v1.json case=answer_document_h008 run=1
    · [planner] plan_request ok 0.1ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 4.0ms
      · [tool_call] document_search ok 4.0ms
    · [evidence] evaluate_evidence ok 0.0ms
```
- route `document_only`, requires_freshness `True`, evidence `refuse` ['freshness_unsupported'], llm_calls 0
- retrieved ['chunk:1', 'chunk:17', 'chunk:4', 'chunk:7']
- answer: 根据现有资料无法确定。

### deepseek — 0/3 passed

```
run 3f622f304d84434896c5b0e935f1dcac  completed
  eval eval:eval-env-v1 mode=orchestrated streaming=False  2026-09-24T06:02:41.705+00:00  24.6ms
  commit=b8c81971c4bf  provider=deepseek/deepseek-flash  llm_calls=0 tokens=0+0
  question: 目前的制度里，核心协作时间是几点到几点？
  eval: eval_answerability_validation_v1.json case=answer_document_h008 run=1
    · [planner] plan_request ok 0.2ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 8.1ms
      · [tool_call] document_search ok 7.9ms
    · [evidence] evaluate_evidence ok 0.1ms
```
- route `document_only`, requires_freshness `True`, evidence `refuse` ['freshness_unsupported'], llm_calls 0
- retrieved ['chunk:1', 'chunk:17', 'chunk:4', 'chunk:7']
- answer: 根据现有资料无法确定。


## qwen: stage25_qwen_env_v1 -> pmi_qwen_env_v1 (code change, same env and model)

> code_change: 58f2611 -> b8c8197 (main integration); same environment, same model

- passed per run: [39, 39, 39] -> [39, 39, 39]
- cases: {'unchanged': 29, 'answer_changed': 11}

| case | change | pass | behaviours | distinct answers (old / new) | primary (old -> new) |
|---|---|---|---|---|---|
| `answer_document_h003` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 1 / 1 | None -> None |
| `answer_document_h004` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 1 / 1 | None -> None |
| `answer_document_h005` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_document_h007` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_multi_h001` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 1 / 1 | None -> None |
| `answer_multi_h002` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_multi_h004` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_wiki_h001` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 2 | None -> None |
| `answer_wiki_h002` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `refuse_missing_h006` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h007` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |

## deepseek: stage25_deepseek_env_v1 -> pmi_deepseek_env_v1 (code change, same env and model)

> code_change: 58f2611 -> b8c8197 (main integration); same environment, same model

- passed per run: [39, 39, 39] -> [39, 39, 39]
- cases: {'unchanged': 26, 'answer_changed': 14}

| case | change | pass | behaviours | distinct answers (old / new) | primary (old -> new) |
|---|---|---|---|---|---|
| `answer_document_h004` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 1 / 1 | None -> None |
| `answer_document_h005` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_document_h006` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 1 | None -> None |
| `answer_multi_h001` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 2 | None -> None |
| `answer_multi_h002` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 2 / 3 | None -> None |
| `answer_multi_h003` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 1 / 2 | None -> None |
| `answer_wiki_h003` | answer_changed | 3/3 -> 3/3 | ['answer'] -> ['answer'] | 3 / 2 | None -> None |
| `refuse_missing_h001` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h002` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h003` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h004` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h005` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h006` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 1 / 1 | None -> None |
| `refuse_missing_h007` | answer_changed | 3/3 -> 3/3 | ['generation_refuse'] -> ['generation_refuse'] | 2 / 1 | None -> None |
