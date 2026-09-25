# Stage 3 · freshness baseline review（Planner 修改前）

- 标签：`e74ee62`，数据集 sha256 `322471b5…`。规则文档的措辞修正在 `48fa696`，标签没有变。
- baseline 运行在 `9751c10`，工作区干净，Planner 与 `main@224f2e7` 相同。
- 原始结果：[`freshness_baseline.json`](freshness_baseline.json) / [`.md`](freshness_baseline.md)。

## 1. 当前的 `requires_freshness` 是怎么生成的

1. `planner._clause_signals`：只要子句里出现 `FRESHNESS_MARKERS` 中的任意一个词（当前、现在、实时、目前、最新、截至、此时、此刻、眼下、这会儿、当下），就把 `requires_freshness` 设为 True。判断是纯子串匹配，**不看这个词修饰的是什么**。
2. `_extract_signals`：任意一个子句为 True，整个请求就是 True。
3. `evidence_policy`：freshness 只有两种方式能被满足，要么文档证据带 `version` 或 `observed_at`，要么有带 `observed_at` 的系统证据。但 `document_adapter` 固定写 `version=None`、`observed_at=None`，Wiki 的页面版本又被明确排除在外。**所以只要一个请求被标了 freshness、计划里又没有系统步骤，它就一定会以 `freshness_unsupported` 拒答，而且不会调用模型。**

## 2. Baseline

| 指标 | 值 |
|---|---|
| freshness accuracy | **0.483**（14/29） |
| TP / FP / FN / TN | 10 / **13** / 2 / 4 |
| precision / recall | 0.435 / 0.833 |
| false positive rate / false negative rate | 0.765（13/17）/ 0.167（2/12） |
| route accuracy（次要指标） | 0.931（27/29） |
| **应该能回答、却会因 freshness 被拒答** | **13/17** |

| intent | n | TP | FP | FN | TN |
|---|---|---|---|---|---|
| current_knowledge | 12 | 0 | 9 | 0 | 3 |
| incidental | 5 | 0 | 4 | 0 | 1 |
| live_state | 9 | 7 | 0 | 2 | 0 |
| mixed | 3 | 3 | 0 | 0 | 0 |

"会被拒答"这一列不是推断出来的：脚本用真实的 `evaluate_evidence`，喂给它和各 adapter 输出同样形状的证据，逐条核实过。

## 3. 错误模式

**P1 · 只要出现时间词就判为 freshness，不看它修饰什么（13 个误报，全部会导致拒答）。**
- **时间词修饰的是制度**（9 条）：目前的弹性到岗时间、当前的报销时限、现在公司规定、最新的年假规定、截至目前的培训预算上限、简单介绍目前的信息安全要求、当下的复核周期、目前生效的保密制度、眼下这版考勤制度。这与 h008 以及 dev 集的 `answer_document_008` 属于同一类问题。
- **时间词根本不是时效的意思**（4 条）：
  - "截至每年什么时候"表示期限。
  - "我现在就要出差"表示紧迫。
  - "实时通讯软件"里的"实时"是名词的一部分。
  - "我当前还在试用期"只是提问人给出的前提。

**P2 · 词表缺漏（2 个漏报）。** "今天"和"最近"不在词表里，所以 `今天 SKU-C300 的库存` 和 `我最近一次的报销申请审批到哪一步` 被漏判。
- 路由本身是对的（system_only）。系统证据总是带 `observed_at`，所以这两个漏报在下游几乎没有实际影响。

**P3 · 有 3 条判对了，但原因是那几个词恰好不在词表里。** tf_ck_05（现行）、tf_ck_07（今天）、tf_ck_08（最近）。
- **如果只往词表里加词，这 3 条会立刻变成误报。** 所以单纯扩充词表会让结果变差。

**P4 · 与 freshness 相关的路由缺口（2 条）。** tf_ls_05 "截至目前我的年假还剩几天" 和 tf_mx_01 的后半句，被路由成了 document_only。
- 原因："年假"是制度名词，句子里又没有系统对象词，所以没有识别出这是一个个人业务状态请求。freshness 本身判对了。
- 现在它们会被拒答；如果只改 freshness，它们会变成**用制度条文回答个人余额问题**，这比拒答更糟。

## 4. 候选修改方案

所有模拟都是只读的：调用 planner 现有的内部函数重新组合判定逻辑，**planner 代码没有改动**。模拟只覆盖见过的数据集，没有读 holdout 和 blind。

| 方案 | 规则 | temporal dev | 路由集 dev / validation_v1 的 freshness 标签 | answerability dev / validation_v1 中会变的 case |
|---|---|---|---|---|
| 当前 | 出现时间词 | 14/29 | 80/80、80/80 | – |
| **A** | 子句**同时**满足：出现时间词（词表加上今天、今日、最近、近期、这几天），并且这个子句本身需要读系统（`needs_system`） | 27/29 | 79/80、79/80 | `answer_document_008`、h008：freshness 从 True 变 False |
| **A′（推荐）** | 在 A 的基础上，第一人称加"还剩 / 剩余 / 余额 / 还有多少"这类余额词，也算实时状态。**这一条只影响 freshness，不改路由** | 29/29 | 79/80、79/80 | 同上，只有这 2 条 |
| B | 在 A 的基础上，把"个人余额"识别为系统请求，也就是改 `needs_system` | 未模拟 | – | 会改变路由，范围超出本阶段 |
| C | 不改 Planner，改 Evidence Policy：让文档证据携带知识构建版本，从而满足 freshness | – | – | 所有误报都不再被拒答，但 freshness 这个信号本身仍然不准确，"实时通讯软件"之类仍会被标 freshness |

**推荐 A′。**

- **收益：** 修掉 P1、P2 和 h008 这一类问题，不改变任何路由。P4 的两条仍然会得到安全的拒答，不会变成误导性回答。
- **代价：** 冻结路由集里有 2 条 freshness 标签与新规则冲突（dev 的 `route_document_only_004`、validation_v1 的 `route_document_only_h005`），在这两个集合上的 freshness 信号准确率会各从 80/80 降到 79/80。按 `LABELING.md` 的规则，这两条应该改标 False。
- **标签冲突的处理：** 不原地修改冻结数据集，而是提交一份带理由的 label 修订文件，报告里把这 2 条单独列出，不算作 Planner 的改进。

**需要注意：**

- A′ 在 temporal dev 上得到 29/29，**不代表泛化能力**。这些标签是看过失败模式之后写的，规则也是对着这些标签设计的。
- 要衡量泛化能力，需要**在实现之前**另写一份专项 held-out 集（比如 20 条），开发期间不看，实现完成后只跑一次。
- 实现后还要跑全量测试、路由评测，以及 eval-env-v1 上的 Qwen 3 轮回答评测和 Diagnostic Eval，确认 h008 转为通过，并且没有新增失败。
