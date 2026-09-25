# Stage 3 · temporal / freshness 专项开发集：标注规则

数据集：`eval_temporal_freshness_dev.json`（29 条）。

- 这是一个**开发集**：Stage 3 修改 Planner 时会反复查看，所以它衡量的是开发进度，**不能用来衡量泛化能力**。
- 标签在任何 Planner 修改**之前**编写并单独提交。编写标签时没有运行当前 Planner，标签不参考 Planner 的输出。

## 要回答的问题

一个带时间词的请求，是否需要**实时读取一个会随时间变化的业务状态**？这就是 `requires_freshness` 的含义。

| `temporal_intent` | 含义 | `expected_requires_freshness` |
|---|---|---|
| `live_state` | 问的是一条业务记录此刻的值，比如库存、审批进度、个人余额或额度。这个值与制度文本无关，会随时间变化，只能从业务系统读取 | **True** |
| `current_knowledge` | 问的是当前有效的制度或规定写了什么。"目前 / 现行 / 最新 / 当前"等词修饰的是制度本身 | **False** |
| `mixed` | 同一个请求里既问制度，又问实时业务状态 | **True**（由实时业务状态那部分决定） |
| `incidental` | 句子里有时间词，但它的意思不是"当前是否有效"。例如：期限（"截至每年什么时候"）、紧迫（"我现在就要出差"）、名词的一部分（"实时通讯软件"）、"最近的工作日"（指距离最近），或者描述提问人自己的处境（"当前阶段是试用期"） | **False** |

## 为什么"当前有效知识"不算 freshness

- **知识库本身就代表当前生效的版本。** 旧文档被新文档替代后，会同时从检索索引和 Wiki 中退役（见 `docs/APP_DETAILS.md` 的 Wiki 生命周期）。所以"目前的制度规定了什么"只是一个制度内容问题；制度是不是最新的，由知识库的维护流程保证，不需要在每次请求时实时读取。
- **如果把它标成 freshness，这类问题就永远答不上来。** 文档证据不带版本号或观测时间（`document_adapter` 固定写 `version=None`、`observed_at=None`），Evidence Policy 一定会以 `freshness_unsupported` 拒答。这正是 validation 集里 h008 和 dev 集里 `answer_document_008` 失败的原因。
- **"最新 / 最近调整后"指的是版本，但问的仍然是规则内容，所以同样标 False。** 只有当请求要的是一个业务记录此刻的值时，才标 True。

## 路由标签

`expected_route` 是次要指标，用来确认修改 freshness 时没有把路由改坏。标注原则如下：

- 只问具体规则、数值或流程：`document_only`。
- 只要概览或介绍：`wiki_only`。
- 要业务记录的值：`system_only`。即使 demo 业务系统目前只开放库存查询，个人余额、审批进度之类在运行时会得到能力边界文案，但规划层面它仍然是 system 请求。
- 同时问制度和业务状态：按请求的组成部分组合路由。

## 覆盖的时间表达

目前、当前、现在、最新、今天、最近、现行、截至目前；另有当下、眼下、实时、此刻、截至。

前 8 个主要表达在 `current_knowledge` 和 `live_state` 下都至少出现一次，所以同一个词在两种意图下的判定都能被测到。其中当前、现在、最近还出现在 `incidental` 下。

当下、眼下、此刻、截至各只出现在一种意图下，它们只是补充覆盖。"截至"用于"截至每年……"（期限）；"截至目前"单独算一个表达。

## 已知的标签冲突（必须在修改前说明）

现有的冻结数据集里，有 2 条路由标签按"出现时间词就是 freshness"来标：

- dev：`route_document_only_004`，"现在这版考勤制度对迟到是怎么规定的？"，`expected_requires_freshness=True`
- validation_v1：`route_document_only_h005`，"目前生效的这版保密制度是怎么写的？"，`expected_requires_freshness=True`

按本规则，这两条应该标 False。与此同时，dev 的 answerability 数据集把 `answer_document_008`（"现在这版制度里，迟到多少分钟不扣款？"）标为应该回答，这和本规则一致。

冻结的数据集不会被原地修改。如果 Stage 3 采用本规则，会通过一份单独的、带理由的 label 修订文件来记录这两条的变更，并在报告里单独列出，不会把它们算作 Planner 的改进。
