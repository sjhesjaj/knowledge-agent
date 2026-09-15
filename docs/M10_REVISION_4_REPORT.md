# M10 返修第 4 轮报告

日期：2026-09-08。作者：实现 agent。

依据 `revision3/M10_REWORK_4_PROMPT.md` 与复验报告 `revision3/M10_REVISION_3_REVIEW.md`。
本轮范围**只有 R5 的两处**：数值豁免的作用域，以及拒答短语对事实校验的短路。
R1–R4 已通过复验，本轮未改动；路由已达标，`orchestration/planner.py` 一字未动。

前五份报告（实现报告、路径勘误、返修 1/2/3）**全部保持字节不变**（第 7 节）。

**这不是验收结论。** 真实模型三轮回答、三路径检索、真实 API/SSE、动态编译更新、
性能与首段可见正文时间仍未执行，不拿历史结果充数。

---

## 0. 摘要

| 项 | 本轮前 | 本轮后 |
|---|---|---|
| R5-a 豁免作用域 | 前缀里出现任意关键词即豁免 | 修饰词必须**管得到这个数**，且数值之后没有背书 |
| R5-b 拒答短路 | `is_refusal()` 命中即提前返回，跳过数量检查 | 拒答短语不再跳过事实校验；纯拒答仍可交付且不强制引用 |
| 复验列出的 5 个反例 | 全部原样发送并落库 | **全部拦截**，两接口一致 |
| 复验列出的 3 个正例 | 正常交付 | **正常交付（未变）** |
| 单元/接口测试 | 787 通过 | **810 通过**（+23，无删除、无跳过、无放宽） |
| 公开路由 v2/dev/validation/holdout | 100/100/100/97.5% | **100/100/100/97.5%**，逐题零新增回归 |
| 撤销验证 | 6/6（第 3 轮） | 第 3 轮 5/5 + 本轮 **4/4** |

修改 8 个文件，本轮实际改动的产品文件**只有 `rag.py`**；未跟踪文件 90 个。
HEAD 仍 `7480ba84`，四个冻结哈希未变。

---

## 1. 候选标识

```text
HEAD                7480ba842ae5ea1e07d0125bd9422f9193d3c20b（未移动）
branch              codex/iterative-wiki
修改文件            8（本轮只动 rag.py）
未跟踪文件          90（含本报告）
git diff --stat     8 files changed, 1305 insertions(+), 123 deletions(-)
工作区 diff SHA-256  de4da9e70bd0c8cd6b7910e443002ac43b4e5d8171daa3bc186ec5a82dee58d1
候选文件总数        242
```

| 文件 | SHA-256 | 说明 |
|---|---|---|
| `rag.py`（LF 归一） | `54566e3a8c58082fb08439662e1f82dcfb2e84d4faa844ff896bff99874cd82d` | 本轮唯一改动的产品文件 |
| `rag.py`（磁盘原始字节） | `89681d47738787923a81aec0af37f639fb280def603bc30304efd13244df6a8f` | 同上，CRLF 检出 |
| `tests/test_quantity_exemptions.py` | `63798342535e18759ce75d99b4c2f60065c39651b17517c2cb3395a338050e36` | 新增 |
| `docs/m10-selftest/repro_rework_4.py` | `2638248b5a02448ead4986a9af7c1e6047f88dd557b1e36208e96442b7f0e28b` | 新增 |
| `docs/m10-selftest/revert_probe_4.py` | `2878ce67fbc0b7bee10acacb394e253425fbdd60339cc5780e144d3d2dc12399` | 新增 |
| `docs/m10-selftest/revert_probe_3.py` | `00a9a1489d51552bb7783e3592799c1244cc145dedb4d718c5485e0cb2129d90` | 修订，见第 6 节 |

**已修改文件按 LF 归一后取哈希，新增文件按磁盘原始字节取哈希**——这是清单生成器的
既有口径（CRLF 检出不应被记成改动）。上一轮报告给的 `rag.py` 是 LF 归一值，这里两个
都列出来，免得两轮之间口径看起来不一致。

`orchestration/planner.py`、`chat_orchestration.py`、`orchestration/document_adapter.py`、
`tests/test_streaming.py` 与验收方冻结的第三轮候选**逐字节相同**。
逐文件哈希以本轮重新生成的 `docs/m10-selftest/candidate-manifest.json` 为准；清单在
本报告定稿后最后生成，除**它自己那一条**外每条都对应交付文件。

---

## 2. R5-a：豁免必须绑定到这一次数值出现

### 复现（修复前）

在验收方冻结的第三轮候选副本上跑本轮复现脚本，五个反例全部被两接口原样发送并落库：

```text
没有特殊限制的正式员工每年享有99天带薪年假。[来源1]      validate=True/ok   1 次调用
如果入职满一年就每年享有99天带薪年假。[来源1]            validate=True/ok   1 次调用
按你提供的入职日期计算每年享有99天带薪年假。[来源1]      validate=True/ok   1 次调用
你说的99天确实是公司规定的年假额度。[来源1]              validate=True/ok   1 次调用
```

### 根因

上一轮的判据是 `any(cue in segment[:index])`——**关键词出现在前缀里**。这只证明了
词在数值前面，没有证明这个词修饰的是这个数：

- `没有` 的宾语是"特殊限制"；
- `如果` 管的是资格条件，99 天是条件成立**之后**的断言，`就` 并没有结束豁免；
- `按你` 引用的是"入职日期"；
- 第四句确实引用了用户的数值，但**随后自己也认可了它**，那就不再是引用。

### 修复

两个条件同时成立才豁免，缺一不可：

**（1）修饰词管得到这个数（`_governs`）。** 修饰词末尾到数值开头之间的原文，只允许
`每年`、`说的`、`的`、`提到` 这类不引入新对象的轻modifier；一旦残留下别的字，或者
中间还有**另一个数量**，就说明修饰词已经有自己的宾语了。

```python
def _governs(span: str) -> bool:
    residue = "".join(span.split())
    if _NUMBER_WITH_UNIT.search(residue):   # 修饰词攀在别的数上
        return False
    for token in _LIGHT_MODIFIERS:
        residue = residue.replace(token, "")
    return not residue
```

**（2）数值之后没有背书（`_ENDORSEMENT_PATTERN`）。** 引用用户的话再点头认可，仍是
事实断言。背书的作用范围从这个数值一直看到**下一个数值**为止——可以跨分句
（`按你说的99天，属实。` 要拦），但到下一个数值就停，否则
`不是99天。5天才对。` 里认可 5 的 `才对` 会反过来把 99 判成断言。这需要按位置遍历
整段答案，所以断言边界改为用下标表示（`_assertion_starts`），不再切成字符串。

`不正确`/`未确认` 里的 `正确`/`确认` 不算背书——模式带否定字的后顾排除。

### 修复后

| 答案 | 语义 | 结果 |
|---|---|---|
| 没有特殊限制的正式员工每年享有99天带薪年假 | 否定的是限制 | 拦截 ✔ |
| 如果入职满一年就每年享有99天带薪年假 | 条件成立后的断言 | 拦截 ✔ |
| 按你提供的入职日期计算每年享有99天带薪年假 | 引用的是入职日期 | 拦截 ✔ |
| 你说的99天确实是公司规定的年假额度 | 引用后背书 | 拦截 ✔ |
| **不是99天而是5天带薪年假** | 明确纠正 | 交付 ✔ |
| **如果按你说的99天计算，结论仍需核对原文** | 明确待核实假设 | 交付 ✔ |
| **根据现有资料无法确定。** | 纯拒答 | 交付 ✔ |

### 变体（每次只改一样东西）

| 变体族 | 覆盖 | 结果 |
|---|---|---|
| 数值替换 | 99 / 88 / 30，同一句式 | 反例全拦、正例全放 ✔ |
| 条件连接词替换 | 如果…就、若…便、假如…那么、倘若…就、要是…那么 | 全拦 ✔ |
| 否定对象变化 | 特殊限制 / 额外条件 / 其他规定 / 附加要求 | 全拦 ✔ |
| 归属对象变化 | 提供的入职日期 / 说的部门口径 / 提到的岗位序列 | 全拦 ✔ |
| 引用后确认 | 确实 / 没错 / 正是 / 的确如此 / 属实 | 全拦 ✔ |
| 背书归属 | `不是99天。5天才对。` | 放行 ✔（背书认可的是 5） |

---

## 3. R5-b：拒答短语不能跳过事实校验

### 复现（修复前）

```text
根据现有资料无法确定奖金金额。正式员工每年享有99天带薪年假。[来源1]

ungrounded_quantities(...) = [('99', '天')]      ← 数值检查早就查出来了
validate_answer(...)       = (True, 'refusal')   ← 但整体校验以 refusal 放行
两接口都发送并保存整段，各 2 次调用，SSE 正常 done
```

### 根因

`validate_answer()` 命中 `is_refusal()` 就 `return True, "refusal"`，后面的引用检查和
数量检查都不再执行。"含有拒答短语"被当成了"整段没有事实断言"——但拒了一件事的同时
编造另一件事，是两件事。这条短路与第 2 节的作用域**完全独立**：把豁免收紧到极致，
也拦不住它。

### 修复

拒答只豁免"缺引用"这一项（纯拒答本来就不该被要求标来源），事实断言的检查照跑：

```python
refusal = is_refusal(answer_text)
if results and not refusal and not citation_indices(answer_text):
    return False, "no_citation"
if ungrounded_quantities(answer_text, results, question):
    return False, "ungrounded_quantity"
return True, "refusal" if refusal else "ok"
```

同时修正 `decide_delivery()` 的回退值：判据从"看起来像不像拒答"改成"首轮本身能不能
交付"，否则夹带假数字的伪拒答会被当成回退值留下来。

未新增"部分成功"协议，未增加生成次数，未改统一拒答文案。

### 修复后

| 输出 | 结果 |
|---|---|
| `根据现有资料无法确定奖金金额。……每年享有99天……[来源1]` | `(False, ungrounded_quantity)`；两接口交付统一拒答，2 次调用，done，落库为拒答 ✔ |
| `根据现有资料无法确定。` | `(True, refusal)`，**不要求引用**，正常交付 ✔ |
| `根据现有资料无法确定奖金金额。正式员工每年享有5天带薪年假。[来源1]` | 拒一问、答一问且有依据 → 交付 ✔ |

---

## 4. 真实 API 消费回归

`tests/test_quantity_exemptions.py::QuantityExemptionApiTests`。确定性测试：只有
Ollama 的 HTTP 响应是脚本化的，路由、生成处理、有界复查、交付判定、SSE 事件与
SQLite 保存都是产品代码。**不能据此推断真实模型出错频率。**

每格都断言：普通接口返回正文、**未经 strip 的全部 delta 拼接**、终态事件、
两侧模型调用次数相等且 ≤2、两个会话数据库里的实际正文。

| 情形 | 首轮 → 复查 | 两接口结果 | 调用 |
|---|---|---|---|
| 1 复查纠正（5 个反例各一次） | 错误断言 → 正确 5 天 | **交付正确 5 天** | 2 |
| 2 两次都错（5 个反例各一次） | 错误 → 同样错误 | **统一拒答**，正文不含 99，两库都不存错误断言 | 2 |
| 3 明确纠正 | 合法 | 原样交付 | 1 |
| 3 明确待核实假设 | 合法 | 原样交付 | 1 |
| 3 纯拒答 | 合法 | 原样交付（拒答仍触发既有那一次复查） | 2 |
| 4 缺 `done` | — | `error`，**不落库** | — |
| 4 流内模型错误 | — | `error`，**不落库** | — |
| 4 传输故障 | — | `error`，**不落库** | — |
| 4 越界引用 | 越界 → 越界 | 统一拒答 | 2 |

有效复查**没有被取消**（情形 1 就是复查把错误答案纠正过来），也没有靠"一律拒答"
制造假一致（情形 3 全部原样交付）。

---

## 5. 测试与命令

隔离源码副本，项目自带 `.venv`，**Python 3.12.14 (MSC v.1944 64 bit)，
Windows-11-10.0.26200-SP0**。所有 `api` 导入与测试都在隔离副本内发生。

| # | 命令 | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `python -m py_compile`（全部改动/新增 Python 文件） | 0 | 通过 |
| 2 | `python -m unittest discover` | 0 | **Ran 810，失败 0** |
| 3 | `docs/m10-selftest/repro_rework.py`（第 1 轮五项） | 0 | 全部 PASS |
| 4 | `docs/m10-selftest/repro_rework_2.py`（第 2 轮四项） | 0 | 全部 PASS |
| 5 | `docs/m10-selftest/repro_rework_3.py`（第 3 轮五项） | 0 | 全部 PASS |
| 6 | `docs/m10-selftest/repro_rework_4.py`（本轮八例） | 0 | 5 反例全拦、3 正例全放 |
| 7 | `docs/m10-selftest/revert_probe_3.py` | 0 | 5/5 撤销后变红 |
| 8 | `docs/m10-selftest/revert_probe_4.py` | 0 | **4/4 撤销后变红** |
| 9 | `evaluate_orchestrated_routes --dataset …blind_v2` | 0 | 80/80　100% |
| 10 | 同上 `…dev` | 0 | 80/80　100% |
| 11 | 同上 `…validation_v1` | 0 | 80/80　100% |
| 12 | 同上 `…holdout` | 0 | 78/80　97.5% |

测试数 787 → 810（+23），全部来自 `tests/test_quantity_exemptions.py`。
**没有删除、跳过或放宽任何既有断言。**

### 公开路由逐题回归

| 数据集 | 上一候选失败 | 本候选失败 | 新增回归 |
|---|---:|---:|---|
| blind_v2 / dev / validation_v1 | 0 | 0 | 无 |
| holdout | 2 | 2 | **无**，仍是 `route_holdout_039`、`route_holdout_055` |

本轮未改动 `orchestration/planner.py`，路由结果按预期完全不变。

### 原始记录

```text
docs/m10-selftest/r4/repro4-before.console.txt    修复前：5 反例全部错误交付
                                                  （在验收方冻结的第三轮候选副本上运行）
docs/m10-selftest/r4/repro4-after.console.txt     修复后：8 例全部符合预期
docs/m10-selftest/r4/repro_rework{,_2,_3}-rerun.console.txt   前三轮复现脚本复跑
docs/m10-selftest/r4/revert-probe-3.console.txt   5/5
docs/m10-selftest/r4/revert-probe-4.console.txt   4/4
docs/m10-selftest/r4/unit-tests.console.txt       810 项完整输出
docs/m10-selftest/r4/route/candidate4-*.json      四套公开路由逐题结果
docs/m10-selftest/candidate-manifest.json         本轮重新生成的候选清单
```

---

## 6. 撤销验证

任务单要求"分别撤销两处变化，对应行为测试都应变红"。`revert_probe_4.py` 在临时副本
里逐个撤销，再跑对应测试：

| 撤销 | 对应测试 | 结果 |
|---|---|---|
| `_governs` 恒真（回到"前缀命中即豁免"） | `ExemptionScopeTests` | 失败 ✔ |
| 忽略数值之后的背书 | `ExemptionScopeTests` | 失败 ✔ |
| 恢复 `is_refusal()` 提前返回 | `RefusalDoesNotSkipFactsTests` | 失败 ✔ |
| 同上，经两条真实接口 | `QuantityExemptionApiTests` | 失败 ✔ |

**`revert_probe_3.py` 的一条目已修订，如实说明**：第 3 轮的 A2 撤销靠改写
`before = segment[:index]` 实现，本轮 `_is_quoted_or_denied()` 被整体替换，该行不复
存在，撤销只会输出 `SKIP` 并让脚本报出虚假的 5/6。该条已移除并写明原因——它保护的
行为现在由 `revert_probe_4.py` 的 `_governs` 撤销覆盖，且覆盖得更准。第 3 轮探针现在
是 5/5，不是"少了一项"，是那一项搬了家。

---

## 7. 受保护文件核对

| 文件 | 状态 |
|---|---|
| 四个冻结题集/评测器 | 未变（哈希见第三轮报告第 8 节） |
| 三个事实源、`storage.py` | 未变 |
| `docs/M10_IMPLEMENTATION_REPORT.md` 等前五份报告 | 未变 |
| `tests/test_streaming.py`、其余 19 个原有跟踪测试文件 | 未变 |
| `orchestration/planner.py`、`chat_orchestration.py`、`orchestration/document_adapter.py` | **未变**（本轮不动路由） |

以上均与验收方冻结的第三轮候选逐字节比对确认。`git diff --check` 退出码 0。
未提交、未推送、未合并；未改动真实数据库与数据生命周期；未迁移项目。

---

## 8. 残余问题与未完成验证

### 8.1 已知的保守误拒（会误拒，不会编造）

收紧之后专门测了 13 种合法的否定/假设写法，**当前 11 种放行、2 种误拒**：

| 误拒的写法 | 为什么识别不了 |
|---|---|
| `不是公司规定的99天，原文写的是5天。[来源1]` | `不是` 与 99 之间隔着 `公司规定的`，无法判断那是 99 的定语还是 `不是` 自己的宾语 |
| `假设按99天计算，也需要核对原文。[来源1]` | `假设` 与 99 之间隔着 `按`，同上 |

两条都写成了测试（`test_known_conservative_refusals_are_recorded_not_forgotten`），
**故意钉住而不是修掉**：把轻modifier词表继续加长直到它们通过，正是复验明确警告的
做法。代价是两种合法说法会被拒答（安全方向，且既有的一次复查仍有机会给出别的措辞），
不是错误事实被交付。

修复过程中也纠正了一处**自相矛盾**：`未提` 本来就是否定词，`原文没有提到99天` /
`资料未提及99天` 却因为中间隔着 `提到`、`及` 被判成管不到。补上这几个轻modifier后，
这类否定恢复正常——这不是词表膨胀，是让既有的否定词真的能用。

### 8.2 本轮不能证明的事

- 冻结独立路由集本轮**未重跑**（`orchestration/planner.py` 未改动，公开四套逐题零回归）。
  上一轮 75/80、边界 36/40、direct 9/10 是主 agent 的测量，不是本轮的新证据。
- 定向 API 测试固定了模型响应，**不能**推断真实模型产生这些串的频率。

### 8.3 仍未完成，不计通过

- Python 3.12 下公开与独立回答题集各 3 轮（含 Answer Success Rate、False Refusal Rate、
  Boundary Message Accuracy、Citation Index Validity、Expected Fact Hit、Cross-run Stability）；
- 三条检索路径（静态 Wiki、动态 Wiki、Document）的 Relevant Hit@3；
- 真实本地模型的 API/SSE 12 题验收；
- 动态编译与资料更新演练；
- 同环境基线/候选 p95、模型调用次数，以及无条件缓冲之后的**首段可见正文时间**。

本轮收紧的是拒答方向，**8.1 的两条误拒会直接影响 False Refusal Rate**，需要在真实
模型三轮回答里实测，不能用确定性测试的结果代替。

---

交回主 agent 继续验收。**不宣称最终验收通过。**
