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

基线里本来就有 1 个失败的测试，不是 Stage 0 引入的，本阶段也没有修（见 §7 R5）。

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

**R2 · `wip/openviking-poc` 分支上的 `.gitignore` 里没有 `.env`。** 如果切换到那个分支，`.env` 会显示为未跟踪文件，有被误加进提交的风险。合并 POC 前应先把 `.env` 规则带过去。

**R3 · POC 以后合并时会和 Stage 0 冲突。** POC 修改过 `rag.answer`、`answer_structured`、`answer_stream`、`build_answer_messages`（加了 `user_memory` 参数），这些正是 Stage 0 改动过的调用点。另外，`scripts/openviking_probe.py` 靠修改 `rag.CHAT_MODEL` 来切换模型、靠 patch `requests.post` 来打开 think；Stage 0 之后前者对 chat 调用已经不起作用（要改用 `OLLAMA_CHAT_MODEL` 配置加 `reset_provider()`）。

**R4 · `wiki_maintenance/ollama_compiler.py` 没有迁移（遗留项，按你的决定）。** Wiki 编译仍然直接请求 Ollama，模型写死为 `qwen3:4b`。所以 `LLM_PROVIDER=deepseek` 时，后台 Wiki 维护**仍然用本地 Qwen**。它已经有 `WikiModel` 注入接口，后续可以写一个适配器接到 Provider 上。

**R5 · 基线里本来就失败的测试。** `tests.test_orchestrated_chat.FixedAnswerTests.test_missing_wiki_is_a_fixed_answer` 在 `stage0-baseline` 上就失败。原因是测试把 `WIKI_PAGES` 置空了，但本地 gitignored 的 `data/wiki/current.json`（build-0001）被 `wiki_runtime` 优先读取，覆盖了置空的效果。这是依赖环境的测试缺陷，不在 Stage 0 范围内，没有修。

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
