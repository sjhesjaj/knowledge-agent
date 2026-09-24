# Stage 2.5 — Qwen vs DeepSeek on eval-env-v1

- environment `eval-env-v1` (manifest `a6ecd2b4ae9dbede…`), commit `58f261175913`, 3 runs per arm
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
| task latency mean / p95 (s) | 3.69 / 10.48 | 1.73 / 5.52 |
| LLM latency per task (s) | 3.20 | 1.24 |
| prompt / completion tokens (total) | 52211 / 4386 | 57764 / 3200 |
| prompt / completion tokens per task | 435.1 / 36.5 | 481.4 / 26.7 |
| DeepSeek cache hit / miss tokens | - | 21750 / 36014 |
| LLM calls total (per task) | 132 (1.10) | 134 (1.12) |
| tool calls total (per task) | 108 (0.90) | 108 (0.90) |
| API cost total | $0 (local) | $0.007387 |
| cost / task | $0 | $0.00006156 (120 task runs) |
| cost / successful task | $0 | $0.00006314 (117 passed) |

_Token counts come from each provider's own tokenizer and are descriptive only; they are not comparable as context size and say nothing about context optimisation._

_Qwen runs locally; its API cost is 0 and hardware is not costed._ Cost basis: list price x recorded usage (cache hit/miss split, peak by call time); not reconciled with the invoice; 0 of 134 DeepSeek calls fell in peak hours.

## Prompt adaptation (DeepSeek)

DeepSeek's effective prompts are not byte-identical to Qwen's: schema requests are sent as json_object with an appended JSON Schema instruction (see prompt_adaptations). Logical prompts are identical by construction.

- qwen: calls whose effective prompt differs from the logical one: 0; adaptations {}
- deepseek: calls whose effective prompt differs from the logical one: 105; adaptations {'schema_not_supported_by_json_object': 105}

## answer_document_h008 trace comparison (run 1)

### qwen — 0/3 passed

```
run 22e6ef4b8d8445729f53ce9edcf44354  completed
  eval eval:eval-env-v1 mode=orchestrated streaming=False  2026-09-24T05:13:52.366+00:00  25.6ms
  commit=58f261175913  provider=ollama/qwen3:4b  llm_calls=0 tokens=0+0
  question: 目前的制度里，核心协作时间是几点到几点？
  eval: eval_answerability_validation_v1.json case=answer_document_h008 run=1
    · [planner] plan_request ok 0.1ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 4.1ms
      · [tool_call] document_search ok 4.0ms
    · [evidence] evaluate_evidence ok 0.0ms
```
- route `document_only`, requires_freshness `True`, evidence `refuse` ['freshness_unsupported'], llm_calls 0
- retrieved ['chunk:1', 'chunk:17', 'chunk:4', 'chunk:7']
- answer: 根据现有资料无法确定。

### deepseek — 0/3 passed

```
run 22397653ce154b918c1826d1ac662333  completed
  eval eval:eval-env-v1 mode=orchestrated streaming=False  2026-09-24T05:21:17.093+00:00  14.6ms
  commit=58f261175913  provider=deepseek/deepseek-flash  llm_calls=0 tokens=0+0
  question: 目前的制度里，核心协作时间是几点到几点？
  eval: eval_answerability_validation_v1.json case=answer_document_h008 run=1
    · [planner] plan_request ok 0.1ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 4.6ms
      · [tool_call] document_search ok 4.5ms
    · [evidence] evaluate_evidence ok 0.0ms
```
- route `document_only`, requires_freshness `True`, evidence `refuse` ['freshness_unsupported'], llm_calls 0
- retrieved ['chunk:1', 'chunk:17', 'chunk:4', 'chunk:7']
- answer: 根据现有资料无法确定。

