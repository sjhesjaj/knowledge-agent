# 交接文档：Stage 0（LLMProvider）+ Stage 1（Agent Trace）

> 本文件按阶段累积。**Stage 1 — Agent Trace 见 §10**（实现 commit `8899d66`）。§0–§9 是 Stage 0 和它的 housekeeping 部分，保留当时的原文。§0–§9 里说"没有进入 Trace 阶段"，指的是 Stage 0 结束时的状态。

# Stage 0 交接：统一 LLMProvider（本地 Ollama/Qwen + DeepSeek API）

- 日期：2026-09-23
- 分支：`stage0-llm-provider`（本地分支，**未 push**）
- 基线 tag：`stage0-baseline` → `db3653ab484fde183e2ecf08cd1cf76480458588`（干净的 `main`）
- 实现 commit：`416fd0d890243dd321d3e301fc67f83ec63e3395`
- 本文件单独放在一个 docs commit 里（在实现 commit 之后）
- OpenViking POC 的未提交改动已保存到本地分支 `wip/openviking-poc@6adfe31`（未 push），Stage 0 没有包含其中任何内容
- 按要求在此停止，**没有进入 Trace 阶段**

## 0. 验收标准逐条结论

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | 基线冻结：tag、Qwen Eval ≥2 次、记录环境 | ✅ 完成（3 次） | `stage0-baseline` tag，`eval/baseline_qwen.json` |
| 2 | 统一 Provider 返回文本、token、耗时；业务层最小改动 | ✅ 完成 | `llm_provider.LLMResponse`；业务层 `git diff -w` 为 +37 / −113 行，公开函数签名不变 |
| 3 | 模型差异在 Provider 层处理 | ✅ 完成 | Qwen 的 `<think>` 和 `message.thinking`、DeepSeek 的 `reasoning_content` 都进入 `reasoning` 字段，业务层只拿到 `content` |
| 4 | 模型通过配置切换，DeepSeek 模型 ID 查官方文档 | ✅ 完成 | `.env.example`；模型 ID 于 2026-09-23 从官方文档核实（见 §2.4） |
| 5 | Qwen 重跑 Eval 与基线一致 | ✅ 完成 | 3 次都是 39/40，逐题 0 翻转，prompt 和请求体完全相同（§4） |
| 6 | DeepSeek 通过同一接口跑通至少 1 条真实 Query | ✅ 完成 | `eval/deepseek_smoke.json`（§6） |
| 7 | Key 只从 .env 读；有 .env.example；.gitignore 含 .env；git 历史无 Key | ✅ 完成，但有风险（见 §7 R1） | §5.3 扫描结果 |
| 8 | 单元测试 mock、不联网；真实调用测试没有 Key 时跳过 | ✅ 完成 | 33 个 mock 测试；2 个真实调用测试没有 Key 时 `skipIf` 跳过 |
| 9 | 一个清晰的 commit | ✅ 完成 | 实现只有一个 commit `416fd0d`，HANDOFF 另外一个 docs commit |

基线里本来就有 1 个失败的测试，不是 Stage 0 引入的。它已在后续的 housekeeping 中通过测试隔离修复（§8.2）；housekeeping 之后，全量 665 个测试全部通过。

## 1. 修改的文件

**业务代码（最小改动）**

| 文件 | 改动 |
|---|---|
| `rag.py` | 7 处手写的 `requests.post(.../api/chat)` 换成 `llm_provider.get_provider().chat()` / `.chat_stream()`；调用点里剥 think 的代码删掉，改由 Provider 处理；`CHAT_MODEL` 改为从配置读取。Prompt、检索参数、Chunk 结构、公开函数签名都没动。`answer_stream` 只把 NDJSON 读取换成了 `chat_stream()`，提取 JSON 的状态机原样保留。diff 行数看起来多，是因为函数主体整体少了一级缩进 |
| `agent.py` | `decide_action`（工具调用）和 `summarize_knowledge_base` 两处调用改走 Provider；`clean_content()` 保留 |
| `.gitignore` | 加一行 `.env` |
| `tests/__init__.py` | 测试启动时关闭 `.env` 加载，并固定用 Ollama provider，让单元测试不受开发者本地配置影响 |

**新增**

| 文件 | 用途 |
|---|---|
| `llm_provider.py` | `LLMProvider` 接口、`OllamaProvider`、`OpenAICompatibleProvider`（DeepSeek）、配置加载、缓存和 `reset_provider()` |
| `.env.example` | 配置模板，不含任何 Key |
| `tests/test_llm_provider.py` | 33 个单元测试，全部 mock |
| `tests/test_llm_provider_live.py` | 2 个真实 DeepSeek 调用测试，`.env` 里没有完整 DeepSeek 配置时自动跳过 |
| `eval/run_stage0_eval.py` | 包一层 `evaluate_answerability.py`（被包的脚本本身没改），补记环境元数据、被动监听每个 `/api/chat` 请求体、整理出逐题结果 |
| `eval/compare_stage0.py` | 基线和回归的对比；判定标准在拿到回归数据之前就写死了 |
| `eval/deepseek_smoke.py` | 跑一条真实 Query，记录逻辑 prompt 和实际发出的 prompt 的差异 |
| `eval/baseline_qwen.json`、`eval/regression_qwen.json` | 基线和回归结果：环境、配置、prompt 哈希、聚合指标、每题 × 3 次的明细 |
| `eval/stage0_comparison.json` | 对比结论 |
| `eval/deepseek_smoke.json` | 冒烟测试的完整记录（不含请求头，也就不含 Key） |
| `eval/*.console.txt`、`eval/runs/**` | 评测器的原始输出，作为留档 |

**没有改的**：`api.py`、`chat_orchestration.py`、`orchestration/*`、`evaluate_*.py`、`wiki_maintenance/*`、所有 Prompt、所有检索和 RAG 参数。

## 2. 关键设计说明

### 2.1 接口

```python
@dataclass(frozen=True)
class LLMResponse:
    content: str                 # 最终文本，已去掉思考内容
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_seconds: float
    provider: str; model: str
    reasoning: str | None        # 思考内容，只供调试，业务层不用
    tool_calls: tuple[ToolCall, ...]   # ToolCall(name, arguments: dict)
    finish_reason: str | None
    prompt_adaptations: tuple[dict, ...]  # 实际 prompt 与逻辑 prompt 的每一处差异

provider.chat(messages, *, response_format=None | "json" | schema_dict,
              tools=None, temperature=None, max_tokens=None) -> LLMResponse
provider.chat_stream(messages, *, response_format=None, temperature=None,
                     max_tokens=None) -> LLMStream   # 迭代得到文本增量；迭代结束后 .response 带用量和耗时
llm_provider.get_provider()    # 按配置构造，进程内缓存
llm_provider.reset_provider()  # 清掉缓存；测试切换环境变量后要调用
```

### 2.2 为什么这样设计

1. **`OllamaProvider` 发出的请求体和原来逐字段一致**，连字段顺序都一样：`think:false`、`format`、`options.temperature`、`tools`、超时（普通请求 180 秒；流式是连接 10 秒 + 读取 180 秒）。"Qwen 无回归"最强的证据不是指标对得上，而是**发出去的请求本来就没变**。回归时的请求监听证实了这一点（§4.3）。
2. **仍然直接调用 `requests.post`，不引入 openai SDK。** 现有测试是 patch 全局的 `requests.post`，还按顺序数调用次数，这样做才能不改一个旧测试就全部通过。DeepSeek 用的是 OpenAI 兼容的 HTTP 接口，用 `requests` 就够了，不增加依赖。
3. **网络和 HTTP 异常不包装，原样抛给调用方。** `rerank` 等函数里 `except requests.RequestException` 这类降级逻辑因此保持不变。DeepSeek 的 HTTPError 会带上响应体里的错误原因，但绝不带请求头，所以不会带出 Key。
4. **模型差异只在 Provider 层处理**：
   - Qwen3（本机实际是 Qwen3-4B-Thinking-2507）：`content` 里的 `<think>` 按原 `answer()` 的规则剥离（有 `</think>` 就取它后面的部分；否则删掉完整的 think 块和没闭合的尾部 think 块），`message.thinking` 字段的内容放进 `reasoning`。
   - DeepSeek：思考模式默认是**开启**的，Stage 0 固定发送 `thinking: {"type": "disabled"}`，与 Qwen 的 `think:false` 语义对齐；`reasoning_content` 只放进 `reasoning`。
   - 用量：Ollama 取 `prompt_eval_count` / `eval_count`，DeepSeek 取 `usage.prompt_tokens` / `usage.completion_tokens`。
5. **DeepSeek 的 JSON 模式适配（按你的决定执行）**：DeepSeek 只支持 `{"type": "json_object"}`，并要求 prompt 里出现 "json"。
   - 传入 schema 时：改用 `json_object`，并在第一条 system 消息末尾追加一段根据 schema 生成的格式说明（没有 system 消息就新建一条）。
   - 传入 `"json"` 且 prompt 里已经有 "json" 时（比如 rerank 的 prompt）：不改。
   - 每一处改动都记在 `prompt_adaptations`，冒烟记录里也同时保存了逻辑 messages 和实际 messages。
   - 调用方传入的 messages 不会被改动（有单元测试覆盖）。Qwen 路径上没有任何适配。
6. **配置按 provider 分前缀**：`LLM_PROVIDER` 选择后端，`OLLAMA_*` / `DEEPSEEK_*` 各自配置自己的参数。这样切换只需要改一个变量，真实调用测试也可以不受 `LLM_PROVIDER` 影响、单独读 DeepSeek 的配置。
   - Ollama 在代码里保留了原来的默认值（`localhost:11434` / `qwen3:4b`），没有 `.env` 时行为和原来一样。
   - **DeepSeek 在代码里没有任何默认值**，缺 base_url、model 或 key 就直接报错，错误信息里会列出缺哪些。
   - 读取顺序是进程环境变量优先，然后是 `.env`。**唯一的例外是 API Key：只从 `.env` 读**，严格按验收标准 7 字面执行，环境变量 `DEEPSEEK_API_KEY` 会被忽略。如果以后要接 CI，可以放宽这一条。
7. **缓存和清理**：`get_provider()` 在进程内缓存一个实例；`reset_provider()` 清掉缓存。`tests/__init__.py` 设置了 `LLM_DOTENV=""` 并固定 `LLM_PROVIDER=ollama`，这样开发者本地 `.env` 里的 `LLM_PROVIDER=deepseek` 也不会让现有测试去访问 DeepSeek。
8. **Embedding 不走 LLMProvider**：DeepSeek 没有 embedding 接口，所以 `rag.OLLAMA_URL` 这个常量保留，继续给 embedding 和健康检查用。

### 2.3 有意保留的边界行为变化

下面几种情况只有在极端输出下才会出现，本次 Eval 里一次都没有触发（逐题 0 翻转，请求形态完全相同）：

1. `answer_structured` 原来不剥 think 就直接 `json.loads`。如果 content 里混进了 `</think>`，原来会解析失败并退回 `answer()`；现在 Provider 已经先剥掉了，会直接解析成功。这正是验收标准 3 要的效果。
2. `rerank` / `select_for_subquestions` 原来只处理带 `</think>` 的情况，现在也会删掉没闭合的 `<think>…`。
3. Ollama 返回的 message 里如果完全没有 `content` 字段：原来是 `KeyError`，现在当作空字符串，后面的 JSON 解析失败后走原来的降级分支。

### 2.4 DeepSeek 官方信息（2026-09-23 在 api-docs.deepseek.com 查证）

- base_url：`https://api.deepseek.com`（OpenAI 格式）。
- 模型：`deepseek-flash`（DeepSeek-V4.1-Flash）和 `deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。旧名 `deepseek-v4-flash` 仍然能用，但对应的模型已下线。本次使用 `deepseek-flash`。
- 思考模式默认开启，用 `{"thinking": {"type": "enabled/disabled"}}` 切换；开启时 `temperature` 不生效，思考内容在 `reasoning_content` 字段。
- JSON Output：`response_format: {"type": "json_object"}`，prompt 里必须出现 "json"；官方说明偶尔会返回空 content。

## 3. 测试命令和输出原文

```powershell
.\.venv\Scripts\python.exe -m py_compile agent.py api.py app.py rag.py storage.py llm_provider.py
```
```
py_compile exit=0
```

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover
```
这是 `.env` 已配置 DeepSeek Key 时的结果，真实调用测试也跑了：
```
======================================================================
FAIL: test_missing_wiki_is_a_fixed_answer (tests.test_orchestrated_chat.FixedAnswerTests.test_missing_wiki_is_a_fixed_answer)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\h000_\Documents\ChatGPT\agent项目改进\knowledge-agent\tests\test_orchestrated_chat.py", line 218, in test_missing_wiki_is_a_fixed_answer
    self.assertEqual(body["answer"], MESSAGE_NO_WIKI)
AssertionError: '年假为 5 天。[来源 1]' != '当前没有可用的 Wiki 页面。'
- 年假为 5 天。[来源 1]
+ 当前没有可用的 Wiki 页面。


----------------------------------------------------------------------
Ran 665 tests in 18.925s

FAILED (failures=1)
```
- 在 `stage0-baseline`（改动前）上跑同一条命令：`Ran 630 tests ... FAILED (failures=1)`，失败的是**同一个测试**。
- 在 Stage 0 代码上、没有 `.env` 时：`Ran 665 tests ... FAILED (failures=1, skipped=2)`。

所以 Stage 0 新增 35 个测试，没有引入任何新的失败。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_llm_provider tests.test_llm_provider_live -v
```
（以下输出截掉了前面 33 行 `... ok`）
```
test_plain_chat_returns_text_and_usage (tests.test_llm_provider_live.DeepSeekLiveTests.test_plain_chat_returns_text_and_usage) ... ok
test_schema_request_returns_parseable_json (tests.test_llm_provider_live.DeepSeekLiveTests.test_schema_request_returns_parseable_json) ... ok

----------------------------------------------------------------------
Ran 2 tests in 1.746s

OK
```
没有 Key 时同一条命令的结果：`Ran 35 tests in 0.026s  OK (skipped=2)`，跳过原因是 `DeepSeek not configured in .env: ... 缺少配置：DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_API_KEY`。

Eval：
```powershell
.\.venv\Scripts\python.exe -X utf8 eval\run_stage0_eval.py --label baseline_qwen --runs 3     # 在 stage0-baseline 上跑
.\.venv\Scripts\python.exe -X utf8 eval\run_stage0_eval.py --label regression_qwen --runs 3   # 在 Stage 0 代码上跑
.\.venv\Scripts\python.exe -X utf8 eval\compare_stage0.py eval\baseline_qwen.json eval\regression_qwen.json
.\.venv\Scripts\python.exe -X utf8 eval\deepseek_smoke.py
```

## 4. 基线 Eval 与回归 Eval 对比

### 4.1 环境（两次运行相同）

| 项 | 值 |
|---|---|
| 数据集 | `eval_answerability_validation_v1.json`，40 题，sha256 见 `eval/*.json` 的 `dataset_sha256` 字段；`blind_v2` 没有碰 |
| 基线代码 | `db3653a`（`stage0-baseline`），已跟踪文件没有改动 |
| 回归代码 | 当时还没提交的 Stage 0 代码。`regression_qwen.json` 里的 `code_sha256` 已核对，和 `416fd0d` 中的 `rag.py`、`agent.py`、`llm_provider.py`、`chat_orchestration.py`、`evaluate_answerability.py` 完全一致 |
| Chat 模型 | `qwen3:4b`，digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`，权重 blob `sha256-3e4cb141…4e4f`（和 registry 上当前的 `qwen3:4b` 一致），Qwen3-4B-Thinking，**Q4_K_M**，4.0B |
| Embedding | `nomic-embed-text`，F16，137M |
| Ollama | 0.32.15 |
| Wiki | `data/wiki` 已发布的 `build-0001`，`current.json` sha256 `e349bd55…4f61`（这个目录被 gitignore，属于环境输入） |
| Prompt | 4 个 system prompt，sha256 前缀分别是 `38999f71`（rerank）、`519efb55`（子问题选择）、`b20d9a0b`（证据复查）、`f3c643ca`（回答）；全文存在 `observed_llm_requests.system_prompts` |
| 检索配置 | chunk 220/40；BM25 k1=1.5、b=0.75；RRF k=60、向量权重 1.0、关键词权重 1.2；top_k=4、candidate_k=8；BM25 快速路径阈值 ≥3.0 且 ≥1.5× 第二名 |
| Python / OS | 3.12.14 / Windows-11-10.0.26200 |

### 4.2 判定标准（在回归运行之前写进 `eval/compare_stage0.py`）

1. **聚合指标**：回归每一次的每个指标，都落在基线 3 次的 [最小值, 最大值] 区间内，允许上下各多 1 题（按该指标的分母折算）。
2. **逐题**：
   - 基线 3/3 通过、回归 ≤1/3 通过，算**硬回归 → 失败**。
   - 基线 3 次行为完全一致、回归里一次都没出现这个行为，算**行为翻转 → 失败**。
   - 其他通过次数的变化只报告，不判失败。
3. **请求一致性**：回归发出的每个 system prompt、每种请求形态都必须在基线里出现过，出现新的就算失败。

### 4.3 结果（`eval/compare_stage0.py` 输出原文）

```
## Aggregate (per run)
| metric | baseline runs | regression runs | allowed | ok |
|---|---|---|---|---|
| passed_cases | 39 / 39 / 39 | 39 / 39 / 39 | 38 – 40 | ✅ |
| pass_rate | 97.5% / 97.5% / 97.5% | 97.5% / 97.5% / 97.5% | 95.0% – 100.0% | ✅ |
| answer_success_rate | 95.0% / 95.0% / 95.0% | 95.0% / 95.0% / 95.0% | 90.0% – 100.0% | ✅ |
| false_refusal_rate | 5.0% / 5.0% / 5.0% | 5.0% / 5.0% / 5.0% | 0.0% – 10.0% | ✅ |
| unanswerable_refusal_rate | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 91.7% – 100.0% | ✅ |
| refusal_mechanism_match | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 91.7% – 100.0% | ✅ |
| boundary_accuracy | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 87.5% – 100.0% | ✅ |
| citation_presence_rate | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 95.0% – 100.0% | ✅ |
| citation_index_validity | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 95.0% – 100.0% | ✅ |
| required_source_coverage | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 95.0% – 100.0% | ✅ |
| expected_fact_hit_rate | 95.0% / 95.0% / 95.0% | 95.0% / 95.0% / 95.0% | 90.0% – 100.0% | ✅ |
| expected_fact_group_hit_rate | 91.7% / 91.7% / 91.7% | 91.7% / 91.7% / 91.7% | 86.7% – 96.7% | ✅ |
| route_accuracy | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 97.5% – 100.0% | ✅ |

## Per-case flips
- hard_regression: 0
- behaviour_flip: 0
- hard_improvement: 0
- soft_flip: 0

## Request identity
- system prompts identical: True (no new: True)
- request shapes identical: True (no new: True)
- /api/chat calls baseline vs regression: 133 vs 133
    - format=json options=None tools=False prompt=38999f71e2bf: 24 vs 24
    - format=json options=None tools=False prompt=519efb557691: 3 vs 3
    - format=schema:75d4773336812270 options={'temperature': 0} tools=False prompt=b20d9a0b473b: 24 vs 24
    - format=schema:75d4773336812270 options={'temperature': 0} tools=False prompt=f3c643ca9fc4: 82 vs 82

VERDICT: NO REGRESSION (aggregate=True, flips=True, requests=True)
```

其他指标：

| | 基线 | 回归 |
|---|---|---|
| 跨次稳定性 | 100%（39 题 3/3 通过，1 题 0/3） | 100%（同样的 39 + 1） |
| 3 次都失败的题 | `answer_document_h008` | `answer_document_h008` |
| 耗时 p50 / p95 / max | 2.83 / 10.52 / 10.94 秒 | 2.83 / 10.29 / 10.51 秒 |
| 评测器自带的 7 项门禁 | 全部通过 | 全部通过 |

`answer_document_h008` 3 次都判成 `policy_refuse`，这是 evidence policy 的判定，**没有调用模型**，所以和 Provider 无关。按 scope 要求不处理 Badcase。

## 5. git 信息

### 5.1 实现 commit 的 `git diff --stat stage0-baseline 416fd0d`

```
 .env.example                           |   25 +
 .gitignore                             |    1 +
 agent.py                               |   52 +-
 eval/baseline_qwen.console.txt         |  701 ++++++++++
 eval/baseline_qwen.json                | 2260 +++++++++++++++++++++++++++++++
 eval/compare_stage0.py                 |  175 +++
 eval/deepseek_smoke.console.txt        |   11 +
 eval/deepseek_smoke.json               |  133 ++
 eval/deepseek_smoke.py                 |  122 ++
 eval/regression_qwen.console.txt       |  701 ++++++++++
 eval/regression_qwen.json              | 2278 ++++++++++++++++++++++++++++++++
 eval/run_stage0_eval.py                |  277 ++++
 eval/runs/baseline_qwen/run-1.json     | 2072 +++++++++++++++++++++++++++++
 eval/runs/baseline_qwen/run-2.json     | 2072 +++++++++++++++++++++++++++++
 eval/runs/baseline_qwen/run-3.json     | 2072 +++++++++++++++++++++++++++++
 eval/runs/baseline_qwen/summary.json   |  159 +++
 eval/runs/baseline_qwen/summary.md     |   65 +
 eval/runs/regression_qwen/run-1.json   | 2072 +++++++++++++++++++++++++++++
 eval/runs/regression_qwen/run-2.json   | 2072 +++++++++++++++++++++++++++++
 eval/runs/regression_qwen/run-3.json   | 2072 +++++++++++++++++++++++++++++
 eval/runs/regression_qwen/summary.json |  159 +++
 eval/runs/regression_qwen/summary.md   |   65 +
 eval/stage0_comparison.json            |  318 +++++
 llm_provider.py                        |  528 ++++++++
 rag.py                                 |  229 ++--
 tests/__init__.py                      |    8 +
 tests/test_llm_provider.py             |  404 ++++++
 tests/test_llm_provider_live.py        |   44 +
 28 files changed, 20964 insertions(+), 183 deletions(-)
```

业务层忽略空白后的真实改动量（`git diff -w --stat`）：

```
 .gitignore        |  1 +
 agent.py          | 46 +++++++++------------------
 rag.py            | 95 ++++++++-----------------------------------------------
 tests/__init__.py |  8 +++++
 4 files changed, 37 insertions(+), 113 deletions(-)
```

新增的 2 万多行里，绝大部分是 Eval 留档（`eval/runs/**` 和 JSON 结果），代码只占少数。

### 5.2 分支和提交

- `stage0-llm-provider`：`db3653a` → `416fd0d`（实现）→ docs commit（本文件）。
- `wip/openviking-poc`：`db3653a` → `6adfe31`，保存了原来未提交的 POC 改动（`rag.py`、`.gitignore` 的修改和 18 个未跟踪文件）。
- 仓库本来没有配置 git 提交身份，所以每次提交都用 `git -c user.name=sjhesjaj -c user.email=184739250+sjhesjaj@users.noreply.github.com` 临时指定（和仓库历史里的作者身份一致），**没有修改全局 git 配置**。
- 没有 push，没有改动任何远程分支。
- 本地 `.git/info/exclude` 里加了一行 `.venv-openviking/`（只在本机生效，不会提交）。原因是 POC 的 `.gitignore` 改动已经移到 wip 分支，`main` 上这个目录会显示为未跟踪。

### 5.3 Key 安全检查

- `git log --all -p | grep -cE 'sk-[A-Za-z0-9]{20,}'` → `0`
- 拿 `.env` 里的 Key 值在 `git log --all -p` 中做精确匹配（没有打印值）→ `0`
- `git log --all --oneline -- .env` → 空，`.env` 从来没有被提交过
- `git check-ignore .env` → 命中 `.gitignore:17:.env`；`.env.example` 没有被忽略，里面的 `DEEPSEEK_API_KEY=` 是空的

## 6. DeepSeek 真实调用的输入和输出

命令：`.\.venv\Scripts\python.exe -X utf8 eval\deepseek_smoke.py`。完整记录在 `eval/deepseek_smoke.json`，里面有逻辑 messages、实际 messages、实际请求参数（不含请求头）和统一格式的响应。

**配置**：`{'provider': 'deepseek', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'timeout': 180.0, 'api_key_set': True}`

**链路**：和 Eval 相同。`chat_orchestration.prepare`（路由 `document_only`，BM25 快速路径，embedding 走本地 Ollama）→ `rag.answer_structured`。整条链路只发生了 1 次 LLM 调用：这条问题走的是 BM25 快速路径，没有触发 rerank；首轮也没有拒答，所以没有触发证据复查。

**输入：逻辑 messages（业务层构造的，没有改动）**

system：
```
你是严谨的企业知识库助手。请直接阅读用户消息中“资料”部分并回答“问题”。只能使用资料里的事实，不得使用外部知识。若资料明确包含答案，必须作答；只有资料确实没有相关信息时才说“根据现有资料无法确定”。最终答案控制在3句话以内，不要展示分析过程、推理步骤或自我检查。在每个关键结论后使用[来源1]这样的编号标注依据。 /no_think
```
user：
```
以下是检索到的资料：

[来源 1：sample_company_rules.md，片段 1]
# 星河科技员工手册（演示资料）
## 请假制度
正式员工入职满一年后，每年享有 5 天带薪年假；工作满三年后增加至 8 天。实习生不享有带薪年假，但每月可申请 1 天事假。请假应提前在系统提交申请，1 天以内由直属主管审批，超过 1 天还需部门负责人审批。

[来源 2：sample_company_rules.md，片段 2]
# 星河科技员工手册（演示资料）
## 薪资发放
公司于每月 10 日发放上一个自然月的工资；遇法定节假日则提前至最近的工作日。工资条通过人力资源系统发送，员工如有疑问应在 5 个工作日内反馈。

[来源 3：sample_company_rules.md，片段 3]
# 星河科技员工手册（演示资料）
## 远程办公
员工每周最多申请 2 天远程办公，须至少提前一个工作日获得直属主管批准。涉及客户现场支持、机房值守的岗位不适用远程办公政策。

[来源 4：sample_company_rules.md，片段 4]
# 星河科技员工手册（演示资料）
## 账号与权限
系统账号仅限本人使用，不得共享密码或验证码。岗位调整时，直属主管应发起权限变更；高权限账号每 90 天复核一次。发现账号异常应立即联系信息安全团队。

请回答问题：员工年假有多少天？
/no_think
```

**实际发出的内容和逻辑 prompt 的差异**：只有一处。`messages[0]`（system）从 167 字变成 312 字，末尾追加了：
```


请只输出一个合法的 JSON 对象，不要输出 JSON 以外的任何内容。该 JSON 必须符合以下 JSON Schema：{"type":"object","required":["answer"],"properties":{"answer":{"type":"string"}}}
```
`prompt_adaptations` 里的记录：`[{'kind': 'json_format_instruction', 'reason': 'schema_not_supported_by_json_object', 'position': 'appended_to_messages[0]', ...}]`

实际请求参数：`{'model': 'deepseek-flash', 'stream': False, 'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'}, 'temperature': 0}`

**输出（控制台原文）**
```
Question : 员工年假有多少天？
Provider : deepseek / deepseek-flash @ https://api.deepseek.com
Route    : document_only (ready)
LLM call 1: format=True prompt_tokens=504 completion_tokens=44 latency=1.11s finish=stop adaptations=['schema_not_supported_by_json_object']
  content: {"answer":"正式员工入职满一年后每年享有5天带薪年假，工作满三年后增加至8天[来源1]；实习生不享有带薪年假[来源1]。"}
Answer   : 正式员工入职满一年后每年享有5天带薪年假，工作满三年后增加至8天[来源1]；实习生不享有带薪年假[来源1]。
```
- 统一格式的响应：`content` 是 JSON 字符串，`reasoning=None`（思考已关闭），prompt_tokens=504，completion_tokens=44，latency=1.106 秒，finish_reason=`stop`。
- 业务层 `answer_structured` 解析出来的最终答案就是上面的 `Answer`。
- 端到端耗时 1.53 秒（包含本地检索）。

## 7. 未解决的问题和风险

**R1 · API Key 已暴露（需要你处理）。** 过程中出现过两个 DeepSeek Key：一个贴在了聊天里，另一个一度写在 `.env.example` 里（在提交之前，已原样改名为 `.env`，并用不含 Key 的模板重新生成了 `.env.example`；上面的扫描确认 git 历史里没有它）。两个 Key 都已经出现在对话记录里，**建议都去 DeepSeek 控制台轮换**。

**R2 · ~~`wip/openviking-poc` 分支上的 `.gitignore` 里没有 `.env`。~~ 已在 housekeeping 中解决（§8.1）**：那个分支已补上 `.env` / `.env.*` / `!.env.example`（commit `8463f33`）。

**R3 · POC 以后合并时会和 Stage 0 冲突。** POC 修改过 `rag.answer`、`answer_structured`、`answer_stream`、`build_answer_messages`（加了 `user_memory` 参数），这些正是 Stage 0 改动过的调用点。另外，`scripts/openviking_probe.py` 靠修改 `rag.CHAT_MODEL` 来切换模型、靠 patch `requests.post` 来打开 think；Stage 0 之后前者对 chat 调用已经不起作用（要改用 `OLLAMA_CHAT_MODEL` 配置加 `reset_provider()`）。

**R4 · `wiki_maintenance/ollama_compiler.py` 没有迁移（遗留项，按你的决定）。** Wiki 编译仍然直接请求 Ollama，模型写死为 `qwen3:4b`。所以 `LLM_PROVIDER=deepseek` 时，后台 Wiki 维护**仍然用本地 Qwen**。它已经有 `WikiModel` 注入接口，后续可以写一个适配器接到 Provider 上。

**R5 · ~~基线里本来就失败的测试。~~ 已在 housekeeping 中解决（§8.2）。** `tests.test_orchestrated_chat.FixedAnswerTests.test_missing_wiki_is_a_fixed_answer` 在 `stage0-baseline` 上就失败。原因是测试把 `WIKI_PAGES` 置空了，但本地 gitignored 的 `data/wiki/current.json`（build-0001）被 `wiki_runtime` 优先读取，覆盖了置空的效果。§3 里的测试输出保留的是修复之前的原文。

**R6 · DeepSeek 模式的覆盖面很窄。**
- 真实调用只验证了 `answer_structured` 这一条路径（1 条 Query 加 2 个真实调用测试）。
- `rerank`、`select_for_subquestions`、`decide_action`（工具调用）、`summarize_knowledge_base`、流式 `answer_stream` 走 DeepSeek 的情况**只有 mock 测试，没有真实调用验证**。
- DeepSeek 模式没有跑 Eval，按要求也不要求效果更好。

**R7 · DeepSeek 模式下 prompt 里有只对 Qwen 有意义的内容。**
- Prompt 里的 `/no_think` 会原样发给 DeepSeek。当前没有观察到影响；按"不改 Prompt"的要求保留了。
- 格式说明是 Provider 追加的，所以 DeepSeek 实际收到的 prompt 和 Qwen 不同，差异已经记录在 `prompt_adaptations`。
- 官方文档说 JSON 模式偶尔会返回空 content。遇到这种情况，`answer_structured` 会走原来的降级分支 `answer()`，多一次调用。

**R8 · 思考模式固定关闭。** 如果以后打开思考并同时使用工具调用，DeepSeek 要求在后续请求里回传 `reasoning_content`，否则返回 400。本阶段按要求没有实现这部分。

**R9 · DeepSeek 模式仍然依赖本地 Ollama。** Embedding（`nomic-embed-text`）和 `/api/health` 的健康检查继续走 `rag.OLLAMA_URL` 这个写死的常量；`OLLAMA_BASE_URL` 配置只影响 chat 调用。

**R10 · Ollama 流式输出里的内联 `<think>` 不会被过滤。** 流式模式下只丢弃了单独的 `message.thinking` 字段，没有过滤写在 `content` 里的 `<think>` 标签。目前流式请求都带 JSON Schema，没有观察到这种输出。

**R11 · 真实调用测试会在常规测试运行中访问网络。** 只要 `.env` 里有 Key，`unittest discover` 就会真实调用 DeepSeek 两次（每次约 1 秒，费用很低）。这符合验收标准 8，但如果不希望常规测试联网，可以再加一个显式开关。

**R12 · Eval 的局限。**
- `validation_v1` 是仓库里定位为回归基线的数据集，曾被开发者看过，所以这次的结论只能说明"没有回归"，**不能当作泛化能力的指标**。
- `rerank` 调用没有设置 temperature，本来有随机性。这次 3 次运行的结果完全一致，但并不代表它是确定性的。
- 基线 harness（`eval/run_stage0_eval.py`）是打 tag 之后才写的，没有包含在 tag 的代码树里。它不改任何产品代码，也不改评测逻辑，只做被动记录。

**R13 · 本机 Ollama 的奇怪状态。** `ollama list` 没有列出 `qwen3:4b`，但 `/api/show` 和本地 manifest 都在，Eval 也正常使用了它。精确的 digest 和权重 blob 已经记录在基线里，将来可以核对。

## 8. Stage 0 housekeeping（进入 Stage 1 之前）

本节只做清理，不扩展功能，**没有改动任何生产代码**（`rag.py`、`agent.py`、`llm_provider.py`、`api.py`、`chat_orchestration.py`、`wiki_runtime.py` 都没动），也没有删除或移动任何真实数据。

| 分支 | commit | 内容 |
|---|---|---|
| `wip/openviking-poc` | `8463f338085c21bddbd3109c4b0f7d715de0f896` | 只改 `.gitignore`：忽略 `.env`、`.env.*`，保留 `.env.example` 可提交 |
| `stage0-llm-provider` | `225d228fee12b838dca334e453678b11bf73154e` | 测试隔离、`.gitignore`、eval artifacts 规则 |
| `stage0-llm-provider` | 本文件更新所在的 docs commit | HANDOFF §0 / §7 R2、R5 / §8 / §9 |

### 8.1 .gitignore 规则

两个分支现在都有下面这组规则：

```gitignore
# Local secrets: never commit .env or its variants; the template stays tracked.
.env
.env.*
!.env.example
```

`stage0-llm-provider` 上另外加了：

```gitignore
# Raw eval run output (per-run dumps, console logs). Condensed results stay in eval/.
eval/artifacts/
```

`git check-ignore --no-index` 的核对结果：

| 路径 | 结果 |
|---|---|
| `.env`、`.env.local`、`.env.production` | 忽略 |
| `.env.example` | 不忽略 |
| `eval/artifacts/x/run-1.json`、`eval/artifacts/x.console.txt` | 忽略 |
| `eval/runs/baseline_qwen/run-1.json`、`eval/baseline_qwen.json`、`eval/README.md` | 不忽略 |

Stage 0 已提交的 `eval/runs/` 下 10 个文件仍然被跟踪。

### 8.2 修复 `test_missing_wiki_is_a_fixed_answer`：怎么隔离的

- **根因**：`chat_orchestration.current_wiki_pages()` 优先读取 `wiki_runtime.RUNTIME.published_pages()`，这个模块级单例的根目录是真实的 `data/wiki`。本机上那里有一个已发布的 build-0001，它覆盖了测试里 `patch.object(chat_orchestration, "WIKI_PAGES", ())` 的效果。换一台没有本地 Wiki build 的机器（比如 CI），这个测试就会通过，所以它是一个依赖环境的测试缺陷。
- **修复方式**：在 `OrchestratedChatTests.setUp` 里执行 `patch.object(wiki_runtime, "RUNTIME", wiki_runtime.WikiRuntime(root=<本测试的临时目录>/wiki))`。
  - 这个目录不存在，等同于"还没有发布过 build"，也就是全新 checkout 的状态。
  - `_has_published_build()` 只检查文件是否存在，不会创建目录；临时目录在 `tearDown` 时清理，patch 通过 `addCleanup(patch.stopall)` 恢复。
  - 用的是仓库里已有的隔离写法，和 `test_upload_upsert.py`、`test_cross_document_supersede.py`、`test_wiki_runtime.py` 一样。
- **作用范围**：`OrchestratedChatTests` 和继承它的 7 个测试类（RouteScenario、FixedAnswer、EmptyKnowledgeBase、LegacyCompatibility、Stream、ConcurrencyAndVersion、Isolation）。没有做全局替换，因为 `ImportPurityTests` 要断言全局 `RUNTIME` 指向默认目录。
- **确认影响范围**：写了一个只用于诊断的 runner，把整个测试套件放在空的临时 Wiki 根目录下跑。除了诊断脚本本身预期会触发的 `ImportPurityTests`，唯一受影响的就是这个目标测试。也就是说，全套件只有它依赖本地 `data/wiki`。
- **数据没有被改动**：修复前后 `data/wiki/current.json` 的 sha256 都是 `e349bd55ac1bc534…`，目录下都是 4 个文件。

### 8.3 Eval artifacts 规则

- 写在 `eval/README.md` 里。
- **进 Git**：`eval/<label>.json`（环境、配置、prompt 哈希、每次运行的 summary、aggregate、gates、每个 case 每次运行的结果）、对比结论、冒烟记录、脚本。
- **不进 Git**：`eval/artifacts/`，也就是评测器的原始 `run-N.json` / `summary.*` 和控制台日志。
- `eval/run_stage0_eval.py` 的原始输出目录从 `eval/runs/<label>` 改成了 `eval/artifacts/<label>`，condensed 的 `eval/<label>.json` 不变。用 `--runs 1` 实际跑了一次核对（39/40）：原始文件落在 `eval/artifacts/` 下且被忽略，condensed 文件照常生成。这次核对的产物是一次性的，核对后已删除，没有提交。
- **历史例外**：Stage 0 已提交的 `eval/runs/**` 和 `eval/*.console.txt` 保留在原处，没有移动或改写。

### 8.4 测试结果（在 `225d228` 上，`.env` 已配置 DeepSeek Key）

```
py_compile exit=0
unittest discover exit=0
Ran 665 tests in 16.782s
OK
```
```
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_orchestrated_chat tests.test_llm_provider tests.test_llm_provider_live
Ran 67 tests in 3.407s
OK
```
修复之后单独跑目标测试：`Ran 1 test ... OK`；单独跑 `tests.test_orchestrated_chat`：`Ran 32 tests ... OK`。

### 8.5 diff 摘要（`git diff --stat 34663aa 225d228`）

```
 .gitignore                      |  5 +++++
 eval/README.md                  | 21 +++++++++++++++++++++
 eval/run_stage0_eval.py         |  9 ++++++++-
 tests/test_orchestrated_chat.py |  8 ++++++++
 4 files changed, 42 insertions(+), 1 deletion(-)
```

## 9. Tech Debt

- **TD1 · 拆分 `llm_provider.py`（本阶段按要求不重构）。** 这个文件现在 528 行，把几类职责放在了一起。将来可以拆成：
  - `llm/config.py`：`LLMConfig`、`load_config`、`.env` 解析、`LLMConfigError`
  - `llm/types.py`：`LLMResponse`、`ToolCall`、`LLMStream`、`LLMProvider` Protocol
  - `llm/providers/ollama.py`、`llm/providers/openai_compatible.py`：两个实现，以及 `split_think`、`adapt_json_prompt` 这类只属于某个 provider 的适配逻辑
  - `llm/__init__.py`：`get_provider` / `reset_provider` / `create_provider`，保持现有的导入路径兼容

  拆分时要注意：两个 provider 必须继续在调用时直接使用 `requests.post`，现有测试是 patch 全局 `requests.post` 的。
- **TD2 · Wiki 编译器接入 Provider**（原来的 R4）：给 `WikiModel` 写一个接到 `llm_provider` 的适配器，替代写死的 `OllamaWikiModel`。
- **TD3 · Embedding 与 chat 的配置不统一**（原来的 R9）：`rag.OLLAMA_URL` 仍然是写死的常量，`OLLAMA_BASE_URL` 只影响 chat 调用。
- **TD4 · `run_stage0_eval.py` 只支持 answerability 评测器**，而且名字带着 Stage 0。以后如果要评测别的数据集或 provider，可以把它泛化，并同步更新 `eval/README.md`。
- **TD5 · 真实调用测试没有显式开关**（原来的 R11）：只要 `.env` 里有 Key，常规测试就会联网。

按要求在这里停止，没有进入 Trace 阶段。

---

## 10. Stage 1 — Agent Trace

- 分支：`stage0-llm-provider`（在 Stage 0 之后继续提交，本地，**未 push**）
- 实现 commit：`8899d66184a5017ff713ae4b9b18fc15436a8b65`
- 本节所在的 docs commit 在它之后
- 目标：一个 Agent 请求或 Eval case 失败时，**只看持久化的 Trace**，就能还原它经过了哪些阶段、调用了什么工具、拿到了什么证据、消耗了多少 Token 和时间，以及具体在哪一步出的错
- 按要求在此停止，**没有进入 Stage 2（Eval / Badcase 分类）**

### 10.1 数据模型

两张新表。API 的 Trace 存在 `api.storage.path`（默认 `data/knowledge_agent.db`）；Eval 的 Trace 存在 `eval/artifacts/<label>/traces.sqlite`。表在第一次使用时创建（`IF NOT EXISTS`），`storage.py` 没有改。

**`trace_runs`**：一个请求或一个 Eval case 对应一行。

| 字段 | 含义 |
|---|---|
| `run_id` | uuid4 hex；API 通过响应头 `X-Run-Id` 返回 |
| `schema_version`、`kind`（api/eval）、`entrypoint`、`mode`、`streaming` | 请求形态。流式和非流式共用这张表，用 `streaming` 区分 |
| `session_id`、`client_id`、`question` | 请求输入 |
| `status` | `running` / `completed` / `failed`。**completed 只表示请求正常结束，不代表答案正确** |
| `failed_stage`、`failed_span_id` | failed_stage 取最外层的业务阶段（router/planner/tool_call/evidence/generation/commit；如果异常发生在所有阶段之外，就是 `request`）；具体失败位置看 `failed_span_id` 指向的子 span |
| `error_type`、`error_message`、`error_traceback` | 导致请求中断的异常；已脱敏；消息截断到 1000 字，traceback 截断到 4000 字（保留尾部） |
| `started_at`、`finished_at`、`duration_ms` | 时间 |
| `git_commit`、`git_dirty` | 进程启动后第一次用到时获取，之后缓存 |
| `provider`、`model` | 实际使用的 Provider |
| `prompt_hashes_json` | 本次运行实际用到的 system prompt 的 sha256 列表（只是汇总；逐次调用的信息在 llm_call span 里）。可以和 Stage 0 基线的哈希直接对比 |
| `retriever_config_json` | chunk、BM25、RRF、top_k、candidate_k、快速路径阈值、embed 模型、执行器的 top_k |
| `dataset`、`dataset_sha256`、`case_id`、`eval_run_index` | 只有 Eval 有 |
| `knowledge_version` | API 请求开始时的知识库版本 |
| `llm_calls`、`prompt_tokens`、`completion_tokens`、`llm_latency_ms`、`span_count`、`error_span_count` | 汇总 |
| `trace_overhead_ms` | recorder 自己计时的开销，**只用于内部诊断**（原因见 §10.7） |
| `attributes_json` | 其他事实，例如 `sse_error_event` |

**`trace_spans`**：一个步骤对应一行。字段有 `span_id`、`run_id`、`parent_span_id`、`seq`、`stage`、`name`、`status`（ok/empty/error）、`offset_ms`、`latency_ms`、`input_json`、`output_json`、`error_type`、`error_code`、`error_message`、`error_traceback`，以及只有 llm_call 才有的 `provider`、`model`、`prompt_tokens`、`completion_tokens`，最后是 `attributes_json`。

| stage / name | 记录的内容 |
|---|---|
| `router` / `decide_action`（legacy） | 输入：问题、历史轮数。输出：type、tool、arguments、seconds。走 LLM 分支时下面挂一个 llm_call |
| `planner` / `plan_request` | 输出 `Plan.to_dict()`（route、steps、signals、reason_codes、fallback_used） |
| `planner` / `availability_check` | 输入：steps、chunks 数、wiki 页数、SKU 数。输出：固定答案或 null |
| `tool_call` / `execute_plan` | 执行器整体，下面挂每个工具的子 span |
| `tool_call` / `document_search`、`wiki_query`、`system_query` | name、arguments、result（`ToolResult.to_dict()`，包括 evidence 和检索 trace）、latency（执行器记录的单步耗时）、status、error_code、error_type（执行器捕获的异常类名） |
| `tool_call` / `search_knowledge_base`、`list_knowledge_sources`、`summarize_knowledge_base`（legacy） | arguments 和结果 |
| `evidence` / `evaluate_evidence`（legacy 模式是 `retrieved_sources`） | `PolicyDecision.to_dict()`：outcome、usable_evidence、missing_tools、tool_failures、reason_codes |
| `generation` / `answer_structured`、`answer_stream` | 输入：问题、证据来源、历史轮数。输出：答案 |
| `llm_call` / 调用方函数名（`answer_structured`、`rerank`、`select_for_subquestions`、`decide_action`…） | 逻辑 messages；有适配时还有实际发送的 messages；`logical_prompt_sha256`、`effective_prompt_sha256`、`system_prompt_sha256`、`prompt_adaptations`；content、reasoning、tool_calls、finish_reason；**Stage 0 Provider 给出的 prompt/completion token** 和 latency。流式还有 `delta_count` 和 `first_delta_ms` |
| `commit` / `commit_exchange` | 知识库版本和来源数。知识库版本冲突导致的 409 会在这里记为失败 |

用 `python -m agent_trace --db <库> list [--status failed]` 列出运行记录，用 `show <run_id> [--json]` 把一个 run 还原成文本树。

### 10.2 决策是怎么落实的（对照你给的 11 条约束）

1. **存储位置**：API 的 Trace 跟着 `api.storage.path` 走，测试把 storage 换成临时库，Trace 也就跟着进临时库，所以测试不会污染真实库（已确认：跑完全量测试后，真实库里依然没有 `trace_*` 表）。Eval 的 Trace 写到 `eval/artifacts/<label>/traces.sqlite`，已被 gitignore。
2. **统一的 sanitizer**：key 名匹配 `api_key`、`authorization`、`token`、`password`、`secret`、`client_secret`、`private_key`、`subject_id`、`cookie` 等的字段会被脱敏。`prompt_tokens`、`max_tokens` 这类 token 计数字段不受影响。文本里形如 `sk-…`、`Bearer …` 的值，以及进程已知的 DeepSeek Key 的原值，都会被替换掉。普通业务参数（例如 SKU）保留真实值。**执行器没有改**，所以工具报错时只有它给出的脱敏信息（error_code 和异常类名）。
3. **非工具异常**：记录 error_type、截断后的 message 和 traceback，写库之前统一脱敏。
4. **status 取值**：`running | completed | failed`。工具出错、系统正常给出拒答的请求记为 completed，同时计入 `error_span_count`。
5. **`TRACE_ENABLED`**：默认开启；设为 `0/false/no/off` 时不写任何行、也不返回 header，业务行为不变（有测试覆盖）。
6. **`run_id` 只通过 `X-Run-Id` header 返回**，HTTPException 的响应也带；响应体、SSE 事件和客户端 trace 一个字节都没变（原有的隐私测试照常通过）。
7. **大文本截断**：API 的 Trace 里每个字符串最多 4000 字、每个列表最多 50 项；Eval 的 Trace 完整保留（`truncate=False`）。
8. **failed_stage 取最外层业务阶段**，具体位置看 `failed_span_id`。**失败点按"导致中断的那个异常对象"来匹配，而且这个异常必须一路逃出了该 span 的所有祖先**。被调用方处理掉的错误（例如 rerank 失败后降级）仍然记为 error span，但不会被认定为失败点。这条规则是在一次真实运行中发现问题后改的，见 §10.9 R1。
9. **每个 llm_call** 都记录 logical/effective prompt 的哈希和 prompt adaptation；run 级别的 `prompt_hashes` 只做汇总。
10. **开销以 200 次 mock A/B 为主要指标**，`trace_overhead_ms` 只用于诊断。
11. **没有扩大范围**：没有 Dashboard，没有接 Langfuse/OTel，没有做 Badcase 分类，没有改 Prompt、Retriever 参数和业务决策。

另外两条设计原则：

- **Trace 绝不能让请求失败**：开始记录和写库时的错误都会被记日志后吞掉；recorder 在请求过程中执行的代码（`_trace_bundle`、`_prompt_facts`、`_record_response`）都包在 `agent_trace.safely()` 里。有测试覆盖。
- **流式接口**：Starlette 每次调用 sync generator 的 `next()` 时都会重新复制 context，在生成器里 `set` 的 contextvar 过了第一个 `yield` 就会丢失（我写了一个探测脚本验证过：`step1 v=None`）。所以流式 generator 由 `Run.iterate()` 驱动，每次 `next()` 都在同一个 Context 里执行。客户端提前断开时，run 会被记为 failed，错误类型是 `ClientDisconnected`。

### 10.3 改动的文件

| 文件 | 改动 |
|---|---|
| `agent_trace.py`（新增） | recorder、SQLite 存储、sanitizer、provider 包装层、`Run.iterate`、CLI |
| `api.py` | 4 个入口各开一个 run，记录 router、tool_call、evidence、generation、commit，并设置 `X-Run-Id`；业务逻辑没变（忽略空白后 +131/−24 行，大部分是 `with` 缩进） |
| `chat_orchestration.py` | 在 `prepare()` 里对 planner、availability、execute_plan 做记录，并加了 `_trace_bundle()`。只读，决策不变 |
| `llm_provider.py` | `get_provider()` 在有活动 run 时返回包装过的 provider，没有 run 时返回原对象（+3 行） |
| `eval/run_stage0_eval.py` | 每个 case 开一个 eval run，写入 `trace_run_id`、`trace_db`、`trace_summary`；`code_sha256` 里加上了 `agent_trace.py`。评测器本身没改 |
| `eval/trace_overhead.py`（新增） | TRACE 开/关的 A/B 基准 |
| `eval/README.md` | 补充 Trace 产物的说明，以及如何从失败 case 的 `trace_run_id` 查到 Trace |
| `tests/test_agent_trace.py`（新增） | 28 个测试 |
| `eval/stage1_trace_qwen.json`、`eval/stage1_trace_comparison.json`、`eval/stage1_trace_overhead.json`（新增） | 回归结果、对比结论、开销数据 |

**没有改的**：`orchestration/*`、`rag.py`、`agent.py`、`storage.py`、`evaluate_answerability.py`、`wiki_*`、前端、所有 Prompt、所有检索参数。

### 10.4 新增测试（`tests/test_agent_trace.py`，28 个，全部 mock，不联网）

所有断言都通过**新开的 SQLite 连接**读取，不看内存里的对象。

| 场景 | 测试 |
|---|---|
| 普通成功请求 | `test_plain_request_replays_every_stage_from_sqlite`：阶段顺序、元数据、tool 参数和结果、evidence、llm_call 的 token 和哈希、`X-Run-Id` 与库中记录一致、响应体的 key 不变 |
| 多步 Tool Calling | `test_multi_step_tool_calls_are_recorded_in_order`（先 document_search 再 system_query，SKU 保留真实值，subject_id 为 null）、`test_wiki_and_document_route_records_both_tools` |
| Tool 失败 | `test_tool_exception_is_an_error_span_in_a_completed_run`（run 为 completed，error span 数为 1，evidence 显示 refuse + tool_error）、`test_executor_programmer_error_fails_the_run_at_tool_call`（failed，failed_stage=tool_call，有 traceback） |
| 生成阶段失败 | `test_model_failure_fails_the_run_at_generation`（Bearer token 已脱敏）、`test_error_handled_inside_a_tool_is_not_blamed_for_a_later_failure`（复现 §10.9 R1 的真实场景）、`test_commit_conflict_fails_at_commit` |
| Streaming | `test_streaming_run_uses_the_same_model`、`test_stream_failure_mid_generation_is_finalized`、`test_legacy_stream_is_traced` |
| legacy 模式 | 规则路由、LLM 路由（router 下挂 llm_call）、锁冲突返回的 409 响应也带 `X-Run-Id` |
| 开关和健壮性 | `TRACE_ENABLED=0` 时不写任何行、业务不变；写库失败时请求照常完成；recorder 自身出 bug 时请求照常完成；没有活动 run 时 provider 不被包装；CLI 能还原 |
| recorder 单元测试 | sanitizer（3 个）、截断（API 截断、Eval 完整）、finish 幂等以及外层 stage 的判定、按异常对象身份匹配失败点、跨线程池的流式 generator、流提前关闭记为 failed、关闭开关时返回 null run |

### 10.5 全量测试结果（在 `8899d66` 的代码上）

```
.\.venv\Scripts\python.exe -m py_compile agent.py api.py app.py rag.py storage.py llm_provider.py agent_trace.py chat_orchestration.py
py_compile exit=0
.\.venv\Scripts\python.exe -X utf8 -m unittest discover
Ran 693 tests in 24.126s
OK
```

665 个原有测试 + 28 个新测试。`.env` 里配有 DeepSeek Key，所以 2 个真实调用测试也一起跑了。跑完之后真实的 `data/knowledge_agent.db` 里仍然只有 `knowledge_chunks`、`conversations`、`messages`、`sqlite_sequence`、`app_meta` 这几张表。

### 10.6 validation_v1 回归（带 Trace，和 Stage 0 的 `regression_qwen.json` 对比）

```
.\.venv\Scripts\python.exe -X utf8 eval\run_stage0_eval.py --label stage1_trace_qwen --runs 3
.\.venv\Scripts\python.exe -X utf8 eval\compare_stage0.py eval\regression_qwen.json eval\stage1_trace_qwen.json
```

- **VERDICT: NO REGRESSION**：3 次运行都是 39/40，12 项指标每一次都落在允许区间内；逐题：硬回归 0、行为翻转 0、硬改善 0、软翻转 0；system prompt 和请求形态完全一致，没有出现新的。完整结果在 `eval/stage1_trace_comparison.json`。
- `/api/chat` 调用次数是 133 对 135，多出来的 2 次都是 rerank（24 → 26）。**原因是只用 Trace 查出来的**：`answer_multi_h001` 在第 1、2 次运行时，`select_for_subquestions` 让模型返回了不在候选集里的 ID（`["6","3"]`）或空列表，触发了已有的降级逻辑，多调一次 rerank。这个调用本来就没设 temperature，属于模型输出的随机波动，和 instrumentation 无关；这个 case 的判定结果也没变。
- Trace 覆盖情况：120 次 case 运行都有各自不同的 `trace_run_id`，Trace 库里 120 个 run 全部是 completed，共 755 个 span，库文件 1.5MB（Eval 不截断，平均每个 run 约 12.5KB）。
- `code_sha256`（rag、agent、llm_provider、chat_orchestration、evaluate_answerability、agent_trace）和 `8899d66` 中的文件逐一一致。
- 端到端耗时仅供参考，因为 LLM 本身的波动会淹没 Trace 的开销：p50 / p95 / max 在 Stage 0 是 2.83 / 10.29 / 10.51 秒，Stage 1 是 2.80 / 10.37 / 11.72 秒。

### 10.7 Instrumentation 开销（主要指标：TRACE 开/关 A/B，每组 200 次）

`eval/trace_overhead.py`：真实的请求路径（FastAPI → planner → executor → evidence → answer_structured/answer_stream → provider → SQLite commit），只把检索和模型换成瞬间返回的 mock；两组请求逐个交替执行，每组先预热 20 次。

| 场景 | 关闭 平均 / p50 / p95 | 开启 平均 / p50 / p95 | 平均差值（95% CI） | p50 差值 | p95 差值 |
|---|---|---|---|---|---|
| orchestrated `/api/chat` | 26.03 / 22.79 / 44.60 ms | 45.38 / 43.38 / 60.05 ms | **+19.35 ms** [+17.97, +20.74] | +20.59 | +15.45 |
| orchestrated `/api/chat/stream` | 27.27 / 24.43 / 48.41 ms | 46.85 / 44.54 / 63.75 ms | **+19.58 ms** [+18.07, +21.08] | +20.12 | +15.33 |

- **怎么理解**：每个请求固定多出约 20ms。在 mock 请求上，这相当于 +74%；在真实请求上（validation 的 p50 是 2.8 秒），大约是 **+0.7%**。
- **时间花在哪里**（用 cProfile 看的）：几乎全部花在每个请求多出来的 2 次 SQLite 事务上（启动时插入 run，结束时写入 span 并更新 run），每次 connect、commit、close 合起来约 5ms（Windows 上 WAL 在最后一个连接关闭时会做 checkpoint）。Python 这边的 span、sanitize 和哈希每个请求不到 1ms。
- recorder 自计时的 `trace_overhead_ms`（p50 9.0 / p95 13.1 ms）比 A/B 测出来的差值小，因为 Trace 写入会让 WAL 变大，拖慢随后会话存储关闭连接时的 checkpoint，而这部分时间算在了存储层头上。**所以它只能作为诊断，不能当开销指标。**
- 按要求只做测量，不做优化；优化方向记在 §10.10。

### 10.8 Trace 样例（真实请求：真实 API、真实检索、真实 Qwen，用的是临时库）

**成功的 run**（document_system 路线，两个工具）：

```
run 1f8305f785ad4b59811cfdcaf248172c  completed
  api /api/chat mode=orchestrated streaming=False  2026-09-23T10:43:07.912+00:00  4752.8ms
  commit=5baf106ace40+dirty  provider=ollama/qwen3:4b  llm_calls=1 tokens=489+109
  question: 请假制度原文怎么写的，另外 SKU-A100 还有多少库存？
    · [planner] plan_request ok 0.1ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 392.6ms
      · [tool_call] document_search ok 391.9ms
      · [tool_call] system_query ok 0.1ms
    · [evidence] evaluate_evidence ok 0.1ms
    · [generation] answer_structured ok 4144.8ms
      · [llm_call] answer_structured ok 4144.7ms tokens=489+109
    · [commit] commit_exchange ok 12.4ms
```

答案：`…请假应提前在系统提交申请…[来源 1] SKU-A100 当前库存为 42 件。[来源 5]`。这些样例是在代码提交之前生成的，所以 commit 显示为 `5baf106+dirty`。

**失败的 run**：chat provider 指向一个不存在的端口，embedding 仍然走真实的 Ollama，于是出现真实的 `ConnectionError`。

```
run 5a51d396f01e4255a3852403c083f5dc  failed  failed_stage=generation
  api /api/chat mode=orchestrated streaming=False  2026-09-23T10:43:12.569+00:00  6201.5ms
  commit=5baf106ace40+dirty  provider=ollama/qwen3:4b  llm_calls=2 tokens=0+0
  question: 年假最多可以休多少天
  error: ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=1): Max retries exceeded with url: /api/chat (Caused by NewConnectionError(...[WinError 10061]...))
    · [planner] plan_request ok 0.1ms
    · [planner] availability_check ok 0.0ms
    · [tool_call] execute_plan ok 4133.2ms
      · [tool_call] document_search ok 4133.1ms
        ✗ [llm_call] rerank error 2041.2ms tokens=None+None error=ConnectionError
    · [evidence] evaluate_evidence ok 0.0ms
    ✗ [generation] answer_structured error 2047.7ms error=ConnectionError
      ✗ [llm_call] answer_structured error 2045.8ms tokens=None+None error=ConnectionError  <= failed here
```

这棵树本身就能说明发生了什么：检索阶段的 rerank 连不上模型，但 `rag.rerank` 捕获了异常并降级为未重排的候选，所以 `document_search` 仍然是 ok；证据判定通过之后，真正让请求中断的是生成阶段的模型调用。`failed_span_id` 指向的 span 里保存了完整的 traceback（已截断和脱敏）。客户端收到的是 500。

### 10.9 实现过程中发现的问题

- **R1（已修复）**：第一版实现是"第一个出现异常的 span 就是失败点"。在上面这个真实的失败请求里，它把已经被 `rag.rerank` 处理掉的 rerank 错误误判成了失败点（`failed_stage=tool_call`）。mock 测试没发现这个问题，因为测试里检索是 mock 的，根本没走到 rerank。修复后改为：在 run 失败时，按"导致中断的那个异常对象"匹配，并且要求这个异常逃出了该 span 的所有祖先。同时补了 API 层和 recorder 层两个回归测试。**修复之后重跑了全量测试、validation、A/B 基准和样例，§10.5–§10.8 的数字都来自最终代码。**
- **R2（已修复）**：检查 diff 时发现，recorder 在请求过程中执行的代码（`_trace_bundle`、`_prompt_facts`、`_record_response`）如果自己出错，会让请求失败。现在这几处都包在 `agent_trace.safely()` 里，并加了测试。

### 10.10 已知限制、风险和 Tech Debt

- **L1 · 未处理异常导致的 500 响应不带 `X-Run-Id`**（HTTPException 的 404/409 会带）。遇到这种情况，用 `python -m agent_trace list --status failed`，或按 session_id 和时间去 `trace_runs` 里查。
- **L2 · 没有保留期限，也没有清理机制**：API 的 Trace 会一直增长。基准测试里一共发了 880 个请求（其中 440 个开了 Trace，另外还有全部请求的会话记录），库文件是 4.1MB，也就是每个 API run 最多约 9KB。以后需要一个清理策略。
- **L3 · 进程被直接杀掉时**，run 会一直停在 `running`，没有 finished_at。这本身也是一个可以查到的事实。
- **L4 · 每个工具的 span 是事后重建的**：执行器在一次调用里跑完所有工具，所以工具 span 的 offset 是按执行器记录的单步耗时累加出来的（工具按顺序执行）；evidence span 的耗时是用总耗时减去各工具耗时推算的（`attributes.timing` 里标明了推算方式）。执行器运行期间发生的 llm_call 都挂在 `document_search` 下面，因为在当前代码里只有它会调用模型；**如果以后别的工具也开始调模型，这条归属规则就要改**（TD4）。
- **L5 · llm_call 的 name 是直接调用方的函数名**（通过 `sys._getframe` 取）；流式 llm_call 的 latency 包含了 consumer 在两个 delta 之间占用的时间。
- **L6 · 工具错误只有执行器给出的脱敏信息**（异常类名和固定的 message），这是执行器的设计决定的，本阶段按要求没有改执行器。
- **L7 · sanitizer 基于 key 名和值的模式匹配**：问题和答案里的业务文本不会被脱敏（这是有意的，因为要还原请求就需要它们）；Eval 的 Trace 保存了完整的 prompt 和证据（已 gitignore）。
- **L8 · Wiki 后台编译不在 Trace 范围内**，因为它不属于任何一个请求，而且不走 Provider。
- **L9 · 在 legacy 流式接口里**，"模型未返回可显示的答案"这个 RuntimeError 是在所有 span 之外抛出的，所以 `failed_stage=request`；orchestrated 流式的同一个检查放在 generation span 里面，因此会记为 generation。
- **L10 · `messages.trace_json`（客户端 trace）和新的 Trace 没有直接关联**：`messages` 表里没有 run_id，只能靠 session_id 和时间对上。
- **L11 · `git_commit` 在进程里只取一次**；代码改了但服务没重启时，记录的 commit 会过时。`git_dirty` 只看已跟踪的文件。
- **TD1**（沿用 Stage 0）：拆分 `llm_provider.py`。
- **TD2 · 写入开销的优化方向（本阶段不做）**：复用 SQLite 连接、把开始行和结束行合并成一次写入、改成后台异步写入、调整 checkpoint 策略。依据是 §10.7 的 cProfile 结论。
- **TD3 · `agent_trace.py` 大约 1000 行**，可以拆成 store、recorder、sanitize、provider 包装层和 CLI 几个部分。
- **TD4 · llm_call 的归属规则**（见 L4）：更准确的做法是在执行器里给每个工具开一个 span，但那需要改执行器。

按要求在这里停止，没有进入 Stage 2。
