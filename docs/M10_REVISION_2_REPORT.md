# M10 返修第 2 轮报告

日期：2026-09-08。作者：实现 agent。

针对第二次复验的四处残留（`revision1/M10_REVISION_1_REVIEW.md`）与第二轮返修单
（`revision1/M10_REWORK_2_BRIEF.md`）。原实现报告、路径勘误、第一轮返修报告
**均保持原样未动**。

**这不是验收结论。** 真实模型、三路径检索、API/SSE、动态更新、性能，以及替换曝光题
后的独立复验仍待完成，不拿历史结果充数。

---

## 0. 摘要

| 项 | 本轮前 | 本轮后 |
|---|---|---|
| R2 复查决定未共享 | 普通接口复查后答对并保存；SSE 只生成一次、保存拒答 | 两接口共用生成后复查与最终决定，正文/调用次数/落库一致 |
| R5 修饰关系作用于整段 | 别处的「如果」或有依据数值可为错误断言免责 | 按数值所在分句判定；先否定后肯定也会判出 |
| R4 提取正则缺「百」 | `二百三十四件` 提取成 `34 件` | 提取到 `234 件`；`千` 一并支持 |
| R1 合法信封回退 | 整个 `{"answer":…}` 当正文发送并保存 | 统一提取正文，两种回退形态都覆盖 |
| 空证据提前发正文的例外 | 保留 | **已按裁定取消** |
| 单元/接口测试 | 745 通过 | **749 通过** |
| 公开路由 v2/dev/validation/holdout | 100/100/100/97.5% | **100/100/100/97.5%**（无回归） |

修改 8 个文件（含被裁定允许调整的 `tests/test_streaming.py`），未跟踪文件 51 个（含本报告）。
HEAD 仍 `7480ba84`，四个冻结哈希未变。

---

## 1. 本次动用的裁定：允许调整的原测试

返修单已裁定，**允许调整** `tests/test_streaming.py::test_streams_visible_content_and_requires_done`
中「逐块列表形状」的断言。这是本轮唯一改动的原有测试文件。

| | 改动前 | 改动后 |
|---|---|---|
| 断言 | `assertEqual(result, ["Hello", ", world"])` | `assertEqual("".join(result), "Hello, world")` |
| 仍然验证 | 完整正文、需要 `done` 标记 | **完整正文、需要 `done` 标记（未变）** |

**没有放宽的部分**：Unicode/代理对/转义错误、格式错误、传输中断、缺少结束标记、
拼接一致性、会话隔离、失败不落库——这些断言一字未动。逐块 JSON 解析仍由同文件的
`test_decodes_escaped_content_across_chunks` 覆盖。测试没有被删除或跳过；
函数里加了注释说明改动依据。

### 契约变化

| | 改动前 | 改动后 |
|---|---|---|
| `answer_stream` 何时产出 | 边解析边逐段发出；空证据时也逐段发出 | **整段解析 + 收到 `done` + 通过交付判定之后**才产出 |
| 空证据 | 例外：逐段放行 | **无例外**，同样缓冲 |
| 交付判定失败 | 抛异常（调用方发 error、不落库） | 产出与普通接口逐字相同的拒答（调用方发 `done`、正常落库） |
| 传输层错误 | 抛异常 | **抛异常（未变）** |
| 最终正文由谁决定 | 各自决定 | `decide_delivery`，两接口共用 |

---

## 2. R2　两接口共用复查后的决定

### 复现（修复前）

证据只有 5 天。两接口都注入同一组响应：首轮「99 天」，复查「5 天」。

| 普通接口 | SSE |
|---|---|
| 调用模型 2 次，返回并保存正确的 5 天 | 只调用 1 次，发送并保存拒答 |

首轮为误拒、复查为正确答案时同样分叉。**共用 `validate_answer` 不等于共用决策**——
第一轮返修只共用了校验函数，生成后的复查留在了 `answer_structured` 里面。

### 修复

把「生成后复查 → 正文提取 → 交付判定」整条链抽成 `rag.decide_delivery()`，
两条接口都调用它；复查请求本身抽成 `_evidence_recheck()`，也是同一个。

```python
def decide_delivery(question, results, first_answer, *, allow_recheck=True):
    result = first_answer
    if allow_recheck and results and needs_evidence_recheck(result, results, question):
        fallback = result if is_refusal(result) else UNGROUNDED_ANSWER_MESSAGE
        retry = _evidence_recheck(question, results[0][0])      # 既有的那一次
        result = retry if retry is not None and is_valid_evidence_retry(retry, question) else fallback
    ok, _ = validate_answer(result, results, question)
    return result if ok else UNGROUNDED_ANSWER_MESSAGE
```

`allow_recheck=False` 只用于「结构化解析失败 → 纯文本回退」那一支，它已经用掉第二次
生成调用。**单题生成调用上限仍是 2**，两接口相同。

普通接口的有效复查**没有被取消**，也没有靠统一拒答来制造假一致——下表第 2、3 行就是
复查真的把错误答案纠正过来的情形。

### 修复后（六种情形，两接口逐项比对）

| 情形 | 首轮 → 复查 | 两接口最终正文 |
|---|---|---|
| 首轮正确 | 5 天 → （不触发） | 5 天，1 次调用 |
| 首轮错误后复查正确 | 99 天 → 5 天 | **5 天**，2 次调用 |
| 首轮误拒后复查正确 | 拒答 → 5 天 | **5 天**，2 次调用 |
| 两次都不可交付 | 99 天 → 99 天 | 拒答，2 次调用 |
| 空证据 | — | 缓冲后交付，无例外 |
| 传输故障 | — | `error` 收尾、**不落库** |

测试 `ApiDeliveryTests.test_both_interfaces_share_the_recheck_and_its_decision`
逐项断言：全部 delta 拼出的正文、终态事件、**两侧模型调用次数相等且 ≤2**、
两个会话数据库里最后一条消息的正文相同。传输故障单独由
`test_a_transport_failure_still_errors_and_persists_nothing` 守住。

---

## 3. R5　修饰关系必须对应到具体数量

### 复现（修复前）

来源 5 天，问「是否 99 天」。以下三种都被放行并保存：

- `每年享有 99 天带薪年假，其中可以先休 5 天。` —— 另一分句里有依据的 `5 天` 替它免责
- `每年享有 99 天带薪年假。如果需要申请，请提交表单。` —— 另一分句的「如果」替它免责
- `每年享有 99 天带薪年假，没有特殊限制。` —— 另一分句的「没有」替它免责

`grounded_units` 和 `hedged` 都是整段判断，没有问「这个修饰是不是在修饰这个数」。

### 修复

改为**按数值出现位置、逐个分句判定**：

- 只有**本分句内**出现、且证据支持的同量词数值，才算「在纠正」；
- 只有**本分句内**的假设/否定词，才算「在假设」；
- 逐次判定而不是集合去重，所以同一个数字先被否定、后又被肯定时，
  **后一次肯定仍然判出**。

### 修复后

| 答案 | 期望 | 结果 |
|---|---|---|
| `99 天…，其中可以先休 5 天` | 拦下 | ✔ |
| `99 天…。如果需要申请，请提交表单` | 拦下 | ✔ |
| `99 天…，没有特殊限制` | 拦下 | ✔ |
| `不是 99 天，是 5 天；不过公司确实按 99 天执行` | 拦下 | ✔ |
| `不是 99 天。每年享有 5 天` | 放行 | ✔ |
| `如果按你说的 99 天计算…原文写的是 5 天` | 放行 | ✔ |

前两例在真实 API 消费路径上也验证过（两接口都拒答、都不再保存错误断言）。

---

## 4. R4　把百位接入真正的提取链

### 复现（修复前）

第一轮只修了转换函数，没有修**提取正则**：`_NUMBER_WITH_UNIT` 的字符类里没有「百」。

```text
quantities('库存为二百三十四件。')  ->  [('34', '件')]      # 截断
来源 234 件 + 正确答案「二百三十四件」  ->  误拒
来源 34 件 + 错误答案「二百三十四件」  ->  错误放行
```

截断的数字比识别不出来更危险：它变成了另一个看起来合法的数。

### 修复

字符类补入 `百` 与 `千`，转换函数同步支持千位，两者范围对齐（0–9999）。

```text
二百三十四件 -> [('234','件')]      一百零五件 -> [('105','件')]
三十四件     -> [('34','件')]       两千件     -> [('2000','件')]
```

来源 234 时正确答案放行、来源 34 时错误的 234 拦下，两个方向都验证。
测试覆盖的是**完整提取 → 校验 → 交付**，不只是内部转换函数。

---

## 5. R1　回退返回合法 JSON 信封时提取正文

### 复现（修复前）

首轮非 JSON → 纯文本回退；回退这次模型碰巧返回了合法 `{"answer":"5 天年假。[来源1]"}`。
`answer()` 只剥 think 标签，于是**整个信封被当作正文发送并保存**。

### 修复

新增 `extract_answer_text()`：是合法信封就取 `answer` 字段，不是就按纯文本处理并剥
think 标签。`answer()` 与复查解析都走它，两种回退形态汇合到同一个出口，再进交付校验。
生成调用上限不变。

真实 API 回归覆盖两种形态：
`test_a_valid_json_envelope_fallback_is_unwrapped_not_pasted`（信封）与
`test_a_plain_text_fallback_is_delivered_as_prose`（纯文本），都检查返回正文与落库内容。

---

## 6. 测试与命令

隔离源码副本，项目自带 `.venv`，**Python 3.12.14**。

| # | 命令 | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `python -m py_compile`（改动/新增 Python 文件，含 tests） | 0 | 通过 |
| 2 | `python -m unittest discover` | 0 | **Ran 749，失败 0** |
| 3 | `python docs/m10-selftest/repro_rework.py`（第一轮五项） | 0 | R1–R5 全部 PASS |
| 4 | `python docs/m10-selftest/repro_rework_2.py`（本轮四项） | 0 | R1/R2/R4/R5 全部 PASS |
| 5 | `evaluate_orchestrated_routes --dataset …blind_v2` | 0 | 80/80　100% |
| 6 | 同上 `…dev` | 0 | 80/80　100% |
| 7 | 同上 `…validation_v1` | 0 | 80/80　100% |
| 8 | 同上 `…holdout` | 0 | 78/80　97.5% |

两个复现脚本随候选交付在 `docs/m10-selftest/`，可从仓库根目录直接运行。

**仍未完成，不计通过**：Python 3.12 下的三轮回答集与延迟比较、新问答集 40 题 × 3 轮、
三路径检索相关性、真实模型 API/SSE 12 题（**含本轮缓冲改动后的首段可见正文实测**）、
动态编译与更新演练、替换曝光题后的独立复验。

---

## 7. 候选标识

```text
HEAD                7480ba842ae5ea1e07d0125bd9422f9193d3c20b（未移动）
branch              codex/iterative-wiki
修改文件            8（rag.py、orchestration/planner.py、chat_orchestration.py、
                       orchestration/document_adapter.py、README.md、
                       docs/DEMO_GUIDE.md、FRONTEND.md、tests/test_streaming.py）
未跟踪文件          51（含本报告与两个复现脚本）
git diff --stat     8 files changed, 1017 insertions(+), 122 deletions(-)
冻结题集/评测器      4 个 SHA-256 未变
```

本轮实际改动的产品文件只有 `rag.py`（R1/R2/R4/R5 与空证据例外的取消）；
`orchestration/planner.py` 等其余文件本轮未再改动，行数差异来自第一轮。
`tests/test_streaming.py` 的改动即第 1 节所述、经裁定允许的那一处。

逐文件 SHA-256 需重新记录：请以交付时重新生成的
`docs/m10-selftest/candidate-manifest.json` 为准，不要沿用上一候选的清单，
也不要只以未变的 HEAD 标识本候选。

未提交、未推送、未合并。交回主 agent 继续验收；**不宣称最终验收通过**。
