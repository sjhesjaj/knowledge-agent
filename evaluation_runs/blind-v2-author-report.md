# Blind Holdout V2 作者报告

作者角色：Blind Holdout V2 Author
成文日期：2026-08-28

---

## 1. 实际读取的全部文件（绝对路径）

写作期间只打开过以下三个事实源，全文读取，无其他来源：

1. `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1\sample_company_rules.md`
2. `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1\wiki_pages\sample_company_wiki.json`
3. `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1\system_fixtures\sample_business_system.sql`

另外创建并运行了三个仅用于静态自检的临时脚本，它们只读取上述三个事实源与我自己产出的两个 JSON：

- `...\scratchpad\selfcheck.py`（静态校验）
- `...\scratchpad\diag.py`（措辞重复诊断）
- `...\scratchpad\patch.py` / `patch2.py`（对我自己写的问题做措辞替换）

这些脚本位于会话临时目录，不属于交付物。

### 事实源要点（写作依据）

- **Document**：员工手册共 20 节（工作时间、考勤与迟到、请假制度、加班与调休、远程办公、薪资发放、绩效考核、转正流程、差旅申请、费用报销、设备领用、软件安装、账号与权限、信息安全、数据备份、培训学习、保密义务、离职交接、办公区域管理、员工建议与申诉）。
- **Wiki**：仅 4 个主题页 —— `wiki-leave`（请假与年假）、`wiki-remote-work`（远程办公）、`wiki-information-security`（信息安全）、`wiki-account-access`（账号与权限）。
- **System fixture**：`inventory` 表只有三个 SKU —— `sku-a100` = 42 件、`sku-b200` = 0 件、`sku-c300` = 7 箱。`orders` 与 `approvals` 表虽存在，但 V1 未开放，故仅用于构造"未开放能力"边界题。

由于 Wiki 只有 4 个主题页，所有需要 `wiki_query` 的题目（`wiki_only`、`wiki_document`、`wiki_system`、`wiki_document_system` 共 40 题）的主题都限制在这 4 页之内，避免"该不该走 Wiki"本身产生争议。

---

## 2. 未读取禁止文件的确认

我确认**没有**读取、检索、列举或以任何方式查看以下内容：

- `orchestration/**`、`chat_orchestration.py`、`agent.py`、`rag.py`、`api.py`
- `tests/**`、`docs/**`、`README.md`
- `evaluate*.py`、`check_evaluation_overlap.py`
- `eval_*.json`（任何既有评测集）、`evaluation_runs/**`
- `git log`、`git diff`、`git show`
- 其他 Agent 的工作记录或对话记录

我也没有列出或搜索过项目目录。

### 需要如实披露的自动注入上下文

以下内容**不是我主动读取的**，而是会话启动时由运行环境自动注入到系统上下文中的。为保证盲测可信度，如实列出：

1. **用户记忆索引 `MEMORY.md` 的 5 行摘要**。其中两行涉及本项目，字面内容为：检索缺陷与 `nomic→bge-m3` 的向量模型更换、摘要器故障、`think:False` 与 JSON schema 同时使用会抑制推理；以及"盲测集得分 22.5%、自出题得分 95%"这一聚合结论。
   我**没有**打开这两个记忆文件本身，索引行中也不包含任何路由规则、marker、提示词或既有评测题目。我在写作中未使用其中任何信息。
2. **git 状态快照**：当前分支名 `codex/local-rag-baseline-20260825` 与两条提交标题（`chore: freeze local RAG evaluation baseline`、`feat: build local enterprise knowledge base agent`）。仅为标题，无 diff、无文件清单。

结论：上述注入内容不包含实现细节中可被"针对性适配"的部分（路由判定逻辑、marker、既有题面）。我判断盲测独立性成立，但把它记录在案，由使用方自行判断。

---

## 3. 两个数据集的数量与分布

### 3.1 `eval_orchestrated_routes_blind_v2.json` —— 80 题

| Route | 题数 | expected_steps |
|---|---|---|
| `direct` | 10 | `[]` |
| `wiki_only` | 10 | `["wiki_query"]` |
| `document_only` | 10 | `["document_search"]` |
| `system_only` | 10 | `["system_query"]` |
| `wiki_document` | 10 | `["wiki_query","document_search"]` |
| `wiki_system` | 10 | `["wiki_query","system_query"]` |
| `document_system` | 10 | `["document_search","system_query"]` |
| `wiki_document_system` | 10 | `["wiki_query","document_search","system_query"]` |

ID `route_v2_001` … `route_v2_080`，唯一且连续。工具顺序一律为 Wiki → Document → System，与用户在问句中提及的先后无关（`route_v2_055`、`route_v2_065`、`route_v2_080` 三题刻意把 System 需求放在句首）。

标注分布：`expected_requires_freshness` 为 true 的 25 题；`expected_requires_exact_citation` 为 true 的 36 题。

### 3.2 `eval_answerability_blind_v2.json` —— 40 题

| `expected_behavior` | `category` | 题数 |
|---|---|---|
| `answer` | `document` | 8 |
| `answer` | `wiki` | 4 |
| `answer` | `system` | 4 |
| `answer` | `multi_channel` | 4 |
| `policy_refuse` | `policy_refuse` | 4 |
| `generation_refuse` | `generation_refuse` | 8 |
| `boundary` | `boundary` | 8 |

`expected_behavior` 最终分布：`answer` 20、`policy_refuse` 4、`generation_refuse` 8、`boundary` 8。取值域恰为这四个，**不存在 `"refuse"`**（见第 11 节的计分前修正记录）。拒答题的 `expected_behavior` 与其 `category` 逐题相同。

ID `answer_v2_001` … `answer_v2_040`，唯一且连续（全文件统一前缀，便于连续性校验）。

`multi_channel` 四题覆盖：`wiki_system`（017）、`document_system`（018、020）、`wiki_document_system`（019），满足最低覆盖要求。

boundary 八题按固定文案分布：缺少 SKU 3 题（033/034/035）、多 SKU 2 题（036/037）、未开放能力 3 题（038/039/040）。

**格式取舍说明（两处需要使用方知晓）**

- 两个文件均为**顶层 JSON 数组**。规范只给出了单题格式，我未查看既有评测集，故采用最小忠实解释；如评测器需要 `{"cases": [...]}` 之类外层包装，请由使用方套壳，不要改题面。
- 拒答与边界题的期望由不同字段承载，不可混为一谈：
  - `policy_refuse` 题通过 `expected_behavior = "policy_refuse"` 表达；
  - `generation_refuse` 题通过 `expected_behavior = "generation_refuse"` 表达；
  - **只有 `boundary` 题使用 `expected_message`**，承载三条固定文案之一；两类 refuse 题都没有 `expected_message` 字段。
  - 三类题（两种 refuse 与 boundary）的 `required_source_types` 与 `expected_fact_patterns` **均为空数组** —— 正确行为是拒答或返回固定文案，不应引用任何来源，也不存在必须命中的事实。

---

## 4. boundary 数量

**Route 数据集：45 / 80 条为 `boundary`**，满足"至少 28 条"的要求。逐路由分布：

| Route | normal | boundary |
|---|---|---|
| `direct` | 5 | 5 |
| `wiki_only` | 5 | 5 |
| `document_only` | 5 | 5 |
| `system_only` | 3 | 7 |
| `wiki_document` | 5 | 5 |
| `wiki_system` | 4 | 6 |
| `document_system` | 4 | 6 |
| `wiki_document_system` | 4 | 6 |

**Answerability 数据集：15 / 40 条为 `boundary`**（008、014、016、019、020、023、024、028、029、030、031、034、035、037、040）。该文件未设 boundary 下限，此处按题目实际难度标注。

boundary 覆盖的困难维度：社交开头夹带真实问题、否定式排除（"不用引用原文""细节先不用"）、指代与省略（"那条""那批货""那个货"）、裸 SKU、多 SKU、缺少 SKU、用户语序与工具顺序相反、时效词错位、制度词与实时状态分处不同子句、极简罗列、工作场景铺垫与紧迫感诱导。

### 刻意设置的标注陷阱

- `route_v2_006`（"辛苦啦，今天就先到这儿吧"）含时效词"今天"，但修饰的是结束对话本身，`freshness` 必须为 false —— 全套 120 题中唯一一处"有时效词却标 false"的非 System 题。
- `route_v2_031`（"SKU-A100 还有多少"）、`route_v2_038`（"apr-3001 的审批走完了没"）走 System 但无时效词，`freshness` 为 false，用于检验是否把"走 System"直接等同于"要求实时"。
- `route_v2_035` 与 `route_v2_036` 都缺少 SKU，仅"目前"一词之差导致 `freshness` 一 false 一 true，构成对照。
- `route_v2_015`、`route_v2_019` 句中出现"条款""原文""引用"，但均处于否定语境，`exact_citation` 必须为 false。
- `route_v2_010`（"你比我们那套旧系统好用多了"）含比较句式，但比较对象不是制度，`exact_citation` 为 false。
- `answer_v2_023`（`SKU_F600 目前有货吗`）问法与零库存题几乎相同，正确行为是"查不到该 SKU"，而非"没货"。

---

## 5. 事实 grounding 自检结果

**结论：20 条 answer 题全部通过，逐项可在三个允许事实源中定位。**

自检方式为三重校验，全部通过：

1. **来源定位**：为每条 answer 题写入若干条必须存在于事实源中的字面片段，逐条断言存在。例如 `answer_v2_007` 断言 `设备遗失应在 2 小时内报告直属主管和信息技术部门` 存在于手册；`answer_v2_013` 断言 `'sku-a100', 42, '件'` 存在于 SQL fixture。全部 20 题、共 **39 条断言**全部命中（本轮为 `answer_v2_008` 新增 `进行提醒但不扣款` 一条；此前版本报告写作 39 条，实为 38 条，此处一并订正）。
2. **正例校验**：为每条 answer 题手写一条正确的模型回答，断言其 `expected_fact_patterns` 的**每个外层组**都能命中，确认正则不会误伤正确答案。
3. **反例校验**：构造 5 条似是而非的错误答案（SKU-A100 答成 7 箱、SKU-C300 答成 42 件、零库存答成货很充足、2500 元只需主管审批、调休期限答成 6 个月），断言它们**无法**同时满足全部事实组，确认正则不至于宽到错误答案也能过。

其余分类的 grounding 自检：

- **system answer（4 题）**：只使用 fixture 中真实存在的**单个** SKU，且数量与单位严格对应（a100→42 件、b200→0 件、c300→7 箱）。`answer_v2_015` 用"箱"而非"件"，用于检验单位是否被照抄错。
- **policy_refuse（4 题）**：脚本从 SQL 中解析出 fixture 实际 SKU 集合 `{sku-a100, sku-b200, sku-c300}`，再从题面正则提取 SKU 并归一化，逐条确认 `sku-d400`、`sku-e500`、`sku-f600`、`sku-h800` **均不在**该集合中；同时断言每题只含一个 SKU（不与多 SKU 边界题混淆）。四种合法书写格式各用一次：`SKU-D400`、`sku e500`、`SKU_F600`、`Sku-H800`。
- **generation_refuse（8 题）**：脚本断言相关关键词在 Document 与 Wiki 合并文本中**完全不出现** —— 五险/公积金/社保/社会保险、婚假、年终奖/奖金/年终、加班费/倍工资/倍、住宿/宿费/差旅标准、病假、保险/医疗，全部确认缺席。
  `answer_v2_030`（试用期时长）无法用关键词缺席证明，改用结构化断言：全文提及"试用期"的行共 1 行，其中陈述时长（`\d+|一二三…` + 个月/天/周/年）的行为 **0** 行 —— 即章节存在但时长从未写明。这是最容易被"合理推断"污染的一题，故单独验证。
  另有三题的干扰项已在 notes 中写明：028 的 1:1 是调休折算比例而非工资倍数；029 的 2000 元是报销审批阈值而非住宿标准；031 的请假与薪资两节都存在却都不覆盖病假工资。
- **boundary（8 题）**：三条固定文案逐字比对通过（含 `SKU` 前的半角空格与全角标点），确认集合恰好等于规范给出的三条，且每种类型至少覆盖一题；同时断言 `expected_message` 只出现在 boundary 题上、boundary 题一律路由 `system_only`。

---

## 6. regex 编译结果

- 事实正则总数：**114 条**（分布在 **52 个外层组**中），**全部编译通过**，无空组。（计分前修正新增 3 组、8 条正则，见第 11 节。）
- **裸数字检查通过**：脚本对每条含数字的正则断言其同时包含单位或上下文锚点（件/箱/天/日/元/分钟/小时/次/个月/工作日/库存/零/周/倍/年）。**0 条违规**。示例：`(?:42|四十二)\s*件`、`(?:2|两)\s*(?:个)?\s*小时`、`(?:2000|2,000|两千)\s*元`、`(?:1|一)\s*个工作日`。
- **零库存组**：`answer_v2_014` 与 `answer_v2_019` 使用同一组表达，脚本断言它能命中规范要求的 `0 件`、`无库存`、`没有库存`、`没货`、`没有货`、`缺货`、`售罄`，并额外断言它**不会**命中"这批零件已经到货"这类句子。
  取舍说明：规范提示"零件"需谨慎。裸 `零\s*件` 会把名词"零件"误当数量，因此该组不收录裸"零件"，改为收录有锚定的零表达 —— `库存为零`、`零库存`、`(?:剩余|还有|仅剩|数量为)\s*零`。这是有意的收紧，在此记录以免被误读为遗漏。
- 同义表达接受度：如"失效|作废|清零|不再有效|无法使用"、"直属主管|主管"、"信息技术部|信息技术部门|IT\s*部"、"公共网盘|网盘|云盘"，在接受合理措辞与不放过错误答案之间取平衡，并已由第 5 节的反例校验验证。

---

## 7. 模板重复自检结果

初稿的重复度检查发现了真实问题，已修正后复检通过。

**最终结果：**

- 120 条问题**互不相同**。
- 任意两题的字符 4-gram Jaccard 相似度**均低于 0.45**（两个文件各自 0 对超阈值）。
- **没有任何"自撰措辞"的 4-gram 或 5-gram 出现在 3 题及以上**，即同一句式最多使用两次。

判定方法说明：单纯统计重复 n-gram 会把领域词汇误判为模板 —— Wiki 只有 4 个主题页、手册只有 20 节，"远程办公""信息安全""境外出差""培训预算"必然反复出现，这是题材而非句式。因此校验器先从两个事实源正文中抽取全部 4/5-gram 建立**领域词汇白名单（3007 条）**，只对白名单之外、即完全由我撰写的措辞执行"最多两次"的硬约束。SKU 与单据号在比对前统一替换为占位符，避免删除后产生虚假相邻。

**首轮检出并修正的模板（共 34 处改写）：**

| 重复措辞 | 初稿次数 | 处理 |
|---|---|---|
| `整体是怎么` | 6 | 改写 6 题，全部消除 |
| `还剩多少` / `还有多少` | 各 6 | 各压到 2 次 |
| `大概讲讲` | 5 | 压到 2 次 |
| `现在的库存` / `§现在的` | 3 / 6 | 压到 2 次 |
| `整体讲讲`、`整体介绍`、`梳理一下`、`怎么回事`、`什么情况`、`准确说法`、`要准确的`、`一字不差`、`多长时间`、`有货吗` | 3–4 | 各压到 ≤2 次 |

改写时同步更新了受影响题目的 `notes`，使标注理由与新题面一致（例如 `route_v2_069` 的时效词由"现在"改为"当前"，notes 同步修改）。

连接词分布也已分散，避免"十个实体套同一模板"的观感：routes 中"顺便/顺手/顺带/对了/同时/以及/另外/最后/再帮我"等各出现 1–2 次。

---

## 8. 两个 JSON 的 SHA-256

**当前值（第三版，独立性门禁返修后）：**

```
c7ad0231567fd98630cdb39e48306803a8b0aa749920bbc48b3e3cf116649efb  eval_orchestrated_routes_blind_v2.json   (39708 bytes)
7ae1c0af4f82e0179ad377a6d433605da6e673d2e1d0ed5a689bcf3fd1bc9d89  eval_answerability_blind_v2.json         (23762 bytes)
```

编码 UTF-8，无 BOM，LF 换行。

**修订历史（旧值如实保留，两个文件都确实变化过）：**

| 版本 | 文件 | SHA-256 | 字节数 |
|---|---|---|---|
| v1 初稿 | routes | `24f960335fe8c417f1bdbf8c8f343b1b521a02992aaeea3957e59aff7a9022b4` | 38563 |
| v1 初稿 | answerability | `aaa350a0b32e1cbbc93e3aa5f97ed725e3b6d404a031c7633f3064dc20777f78` | 22698 |
| v2 计分前修正（第 11 节） | routes | 未改动，仍为 `24f960…22b4` | 38563 |
| v2 计分前修正（第 11 节） | answerability | `e8cfcd2c8fb21548eb013ad700059481fed2193dacb3ad5f178b858aa5f35727` | 22948 |
| **v3 独立性门禁返修（第 12 节）** | **routes** | **`c7ad0231567fd98630cdb39e48306803a8b0aa749920bbc48b3e3cf116649efb`** | **39708** |
| **v3 独立性门禁返修（第 12 节）** | **answerability** | **`7ae1c0af4f82e0179ad377a6d433605da6e673d2e1d0ed5a689bcf3fd1bc9d89`** | **23762** |

第 11 节所述"Route JSON 完全未变"仅适用于 v2 那一轮；v3 返修确实修改了 Route 文件中 8 道题的题面与标注理由，其 SHA-256 相应变化。

---

## 9. 未运行或观察任何产品结果的确认

我确认在整个写作过程中**没有**执行以下任何一项：

- 未运行 Planner，未运行应用或 API，未启动 Ollama 或任何模型
- 未运行既有 evaluator，未运行 overlap checker
- 未观察任何产品输出、路由结果、检索结果或评分
- 未根据任何产品行为反向调整题面或标注
- 未为任何已知实现规则或 marker 调整措辞
- 未对代码提出任何修复建议

所有 `expected_route` 与标注均依据任务书给出的产品语义**人工判定**，所有事实正则依据三个事实源**人工编写**。凡是正确标注可能存在明显争议的题目，我按要求直接换题，而不是猜测实现会如何路由（例如：需要 `wiki_query` 的题目一律限制在 4 个真实 Wiki 主题页内；`document_only` 中"找哪个部门修电脑""代码放哪儿"这类既非概览、也不索取数字或原文的题，`exact_citation` 明确标 false 并在 notes 中说明理由）。

执行的自检全部为静态检查：JSON 解析、计数与分布、ID 唯一性与连续性、steps 顺序、正则编译、裸数字、事实字面定位、关键词缺席、固定文案逐字比对、措辞重复统计。

**最终自检结果：全部通过，0 项失败。**

---

## 10. 实际创建的文件列表

目录：`C:\Users\h000_\Documents\ChatGPT\agent项目改进\blind-v2-authoring`

1. `eval_orchestrated_routes_blind_v2.json` —— 80 题路由盲测集
2. `eval_answerability_blind_v2.json` —— 40 题可答性盲测集
3. `BLIND_V2_AUTHOR_REPORT.md` —— 本报告

未向项目仓库复制任何文件，未 `git add`、未 commit、未 push。

---

## 11. 计分前修正记录（本轮）

本轮为**计分前的静态标注修正**，仅改动 `eval_answerability_blind_v2.json`，共触及 **14 个字段、14 道题**。未读取任何新增来源，未运行任何产品组件。

### 11.1 `expected_behavior` schema 修正（12 题）

上一版把 12 条拒答题的 `expected_behavior` 统一写成 `"refuse"`，而评测契约没有该取值。按每题**已有的 `category`** 机械改写：

| 题目 | 原值 | 新值 |
|---|---|---|
| `answer_v2_021` … `answer_v2_024` | `"refuse"` | `"policy_refuse"` |
| `answer_v2_025` … `answer_v2_032` | `"refuse"` | `"generation_refuse"` |

这 12 题的 `id`、`question`、`expected_route`、`required_source_types`、`expected_fact_patterns`、`category`、`difficulty`、`notes` **全部逐字未动**。

### 11.2 `answer_v2_008` 补全"只提醒、不扣款"（+2 个外层组）

题面「每月迟到多久、累计几次以内只提醒不扣款？超过之后要做什么」明确要求"只提醒"与"不扣款"两个事实，原先未被校验。新增两个**独立**外层组（不能合并成一组——同一外层组只要求命中其中一个候选，而本题要求两个事实都出现）：

```json
["提醒"],
["不扣款", "不扣钱", "不扣工资", "不会扣款", "不予扣款"]
```

修正后共 5 个外层组，顺序为：10 分钟 → 2 次 → 提醒 → 不扣款 → 异常说明。原有的 10 分钟、2 次、异常说明三组**未删除、未放宽**。事实依据：手册「考勤与迟到」一节原文 `进行提醒但不扣款`。

### 11.3 `answer_v2_018` 补全上报对象（+1 个外层组）

题面「设备遗失几小时内必须上报、报给谁？…」中的"报给谁"有两个对象，原先只校验了信息技术部门，漏掉直属主管。新增独立外层组：

```json
["直属主管", "主管"]
```

修正后共 4 个外层组，顺序为：2 小时 → 直属主管 → 信息技术部门 → 7 箱，与原文 `设备遗失应在 2 小时内报告直属主管和信息技术部门` 的行文顺序一致。原有三组**未删除、未放宽**。

### 11.4 计数变化

| 指标 | 修正前 | 修正后 |
|---|---|---|
| `expected_fact_patterns` 外层组总数 | 49 | **52** |
| 事实正则总数 | 106 | **114** |
| Answerability 字节数 | 22698 | **22948** |
| Route 外层组 / 正则 / 字节 / SHA | — | **完全未变** |

### 11.5 本轮静态自检结果（全部通过，0 项失败）

- 两个 JSON 均可解析。
- Route JSON 的 SHA-256 仍为 `24f960…22b4`，且与修正前快照**逐字节相同**。
- `expected_behavior` 分布恰为 answer 20 / policy_refuse 4 / generation_refuse 8 / boundary 8；取值域恰为这四个。
- 文件中已不存在字符串 `"expected_behavior": "refuse"`；每条拒答题的 `expected_behavior` 等于其自身 `category`。
- `answer_v2_008` 同时含 10 分钟、2 次、提醒、不扣款、异常说明五个事实组，且"提醒"与"不扣款"确认位于**不同**外层组。
- `answer_v2_018` 同时含 2 小时、直属主管、信息技术部门、7 箱四个事实组。
- 114 条正则全部编译通过，0 条裸数字。
- **改动范围校验**：将修正后的文件与修正前快照逐题逐字段对比，差异恰为 14 处，全部落在 `expected_behavior`（12 题）与 `expected_fact_patterns`（008、018）之内；`question` 与 `notes` 全部未变，无任何越界改动。
- 上一版的全部校验项（分布、ID 连续性、grounding、policy_refuse SKU 缺席、generation_refuse 主题缺席、边界文案逐字、措辞重复）重跑后仍全部通过。

### 11.6 本轮独立性确认

本轮未运行 Planner、未运行应用或 API、未运行 Ollama、未运行既有 evaluator、未运行 overlap checker，未观察任何产品输出或评分，未读取任何项目代码或既有评测集。所有修正均为按指令执行的机械改写，未借机调整任何其他题目、措辞、route 或事实要求。

---

## 12. 独立性门禁返修（本轮）

本轮为**计分前的独立性返修**，尚未运行任何产品计分，因此改写题面不破坏盲测资格。

### 12.1 我收到什么、没收到什么

- **收到**：14 个 V2 题目 ID，以及每个 ID 的失败类型描述（极短表达自然重合、只替换 SKU 的同构句式、Route V2 与 Answerability V2 内部一对多 SKU 题高度相似）。
- **未收到、也未读取**：评测 Agent 的 overlap report、**任何历史参考题原文**、历史 `eval_*.json`、`evaluation_runs/**`、项目代码、`tests/**`、Planner、evaluator、任何产品输出或当前得分。

因此本轮**无法也没有**与历史题目做相似度比较。所有改写只依据"把这道题写得更自然、更有工作场景、句法与本数据集其他题不同"这一目标，以及我此前已读取的三个事实源。

### 12.2 改动范围

只修改了这 14 个 ID 的 `question` 与 `notes`，共 **28 个字段**。`id`、`expected_route`、`expected_steps`、`expected_requires_freshness`、`expected_requires_exact_citation`、`expected_behavior`、`required_source_types`、`expected_fact_patterns`、`expected_message`、`category`、`difficulty` **一律未动**；其余 106 道题逐字段未变。本轮不改变任何能力语义、预期 route、事实或边界。

| ID | 失败类型 | 改写方向 | 保持不变的约束 |
|---|---|---|---|
| `route_v2_001` | 极短问候 | 加入"刚到工位"的自然上班场景，仍为纯问候 | direct，无任何请求 |
| `route_v2_006` | 常见短告别 | 扩成完整收尾场景（收拾东西、准备下班、明天再说） | direct；保留"今天"作为 freshness=false 的陷阱 |
| `route_v2_008` | 逐字重复的极短问候 | 扩成隔壁组新调岗同事的自然招呼加自我介绍 | direct，不附带任何真实请求 |
| `route_v2_028` | 短"数字周期查询"句式 | 加入安全自查背景，改为先陈述情境再问周期 | document_only，仍问高权限账号复核周期，f=false / c=true |
| `route_v2_031` | 极短裸 SKU 查询 | 加入仓库核对存货场景，改为"把 X 调出来看看" | system_only，单个 SKU-A100，**刻意不含任何时效词**，f=false |
| `route_v2_032` | "现在+SKU+库存多少"模板 | 改为客服催办场景 + "把…报给我"祈使句 | system_only，单个 sku b200，保留"目前"，f=true |
| `route_v2_034` | 与 `answer_v2_036` 高度相似 | 改为配单场景 + "跟…都核一下" | system_only，两个不同合法 SKU，f=true；不使用"现在各有多少"结构 |
| `route_v2_037` | 常见订单状态模板 | 改为客户来电追进度的业务背景 | system_only，保留 ord-1001，仍是订单查询而非库存，f=true |
| `answer_v2_006` | "境外和国内有什么不同"比较模板 | 改为一次境内、一次境外的真实行程，把比较隐含在场景里 | document_only，仍要求比较两级审批人，facts/sources 不变 |
| `answer_v2_013` | 常见 SKU-A100 库存模板 | 改为月底盘点账面对不上、需以系统数据为准 | system_only，仅 SKU-A100，仍需答 42 件，无第二个 SKU |
| `answer_v2_022` | 只替换 SKU 的 policy_refuse 模板 | 改为采购报新料号要备货的场景与句法 | policy_refuse / system_only，保留唯一合法但不存在的 `sku e500` |
| `answer_v2_026` | 与历史题逐字重复 | 改为"下个月办婚礼、想跟主管排开时间"的个人场景 | generation_refuse / document_only，仍只问事实源中缺席的婚假天数 |
| `answer_v2_028` | "加班费几倍工资"模板 | 改为发薪前自己核账的场景 | generation_refuse，仍问工作日加班费倍数；**未改成调休折算比例** |
| `answer_v2_036` | 实体替换模板 + 与 `route_v2_034` 相似 | 改为补货计划场景，以陈述需求代替并列发问 | boundary / system_only，仍含 SKU-A100 与 SKU_B200，`expected_message` 逐字不变；不使用"现在各有多少""一起报给我" |

改写过程中触发了两处新的措辞撞车，已在本轮内解决，且解决方式仍限于这 14 个 ID：
- `在系统里` 一度出现在 `answer_v2_006/013/022` 三题 → 改写 `answer_v2_013`（"以系统数据为准"）。
- `route_v2_032` 引入的 `§目前的` 与 `route_v2_063`、`route_v2_072` 撞车，而后两者不在允许改动范围 → 改写 `route_v2_032`（"目前库存数"）。

### 12.3 内部静态检查结果（全部通过，共 90 项，0 失败）

改动范围校验（与返修前快照逐题逐字段比对）：

- 变化的题目恰为 14 个指定 ID，无遗漏、无多余。
- 变化的字段恰为 28 个：`question` 14 个、`notes` 14 个。
- `question`/`notes` 之外的字段变化数为 **0**；其余 106 题逐字段不变。
- Route 八类仍各 10 条；`expected_steps`、`expected_requires_freshness`、`expected_requires_exact_citation` 全部未变（由"仅 question/notes 变化"直接蕴含，并单独复验：freshness=true 仍为 25/80，exact_citation=true 仍为 36/80，boundary 仍为 45 条）。
- `expected_behavior` 分布仍为 answer 20 / policy_refuse 4 / generation_refuse 8 / boundary 8。
- 114 条正则全部编译通过，0 条裸数字；20 条 answer 题的 grounding 断言全部重跑通过。

**内部交叉相似度**（120 道题两两比较，取 `difflib` 序列相似度与字符 4-gram Jaccard 的较大值，先做非字母数字归一化）：

| 指标 | 结果 |
|---|---|
| 120 个 question 是否唯一 | 是，无逐字重复 |
| 最高内部相似度 | **0.7586**（`route_v2_038` vs `answer_v2_039`） |
| 相似度 ≥ 0.85 的问题对 | **0** |
| 相似度 ≥ 0.80 的问题对 | **0** |
| 实体掩码后完全相同的模板 | **0**（不存在"同一句子只换 SKU"） |
| 掩码后最高相似度 | 0.7143（`route_v2_021` vs `answer_v2_001`） |
| `route_v2_034` vs `answer_v2_036` 掩码相似度 | 0.2500（返修前为高度相似，现已分离） |
| 同一自撰措辞（4/5-gram）复用上限 | ≤ 2 次，无违规 |

需要提请注意的残余近邻（**均低于门槛，且都不在本轮允许改动的 14 个 ID 之内**，故本轮未动）：

- `route_v2_038`（"apr-3001 的审批走完了没"）与 `answer_v2_039`（"apr-3001 的审批到哪一步了"）= 0.7586；
- `route_v2_040` 与 `answer_v2_037`（两道多 SKU 题）= 0.7556；
- `route_v2_021` 与 `answer_v2_001`（两道报销期限题）= 0.7143。

这三对分属两个数据集、考察目标不同（路由标注 vs 可答性行为），若外部门禁采用更严的度量或更低的阈值而判失败，请把对应 ID 连同失败类型发回，我按同样流程改写。

### 12.4 本轮独立性确认

本轮未运行 Planner、未运行应用或 API、未运行 Ollama、未运行既有 evaluator、未运行 overlap checker、未运行项目测试，未观察任何产品输出或评分，未读取任何历史参考题原文、项目代码或既有评测集。改写完全依据给定的 14 个 ID 与失败类型，未为猜测产品实现而调整任何措辞。

---

## 交付说明

两个数据集为冻结的盲测集。请勿交给实现 Agent 修改题面、标注或正则；如评测器需要不同的外层结构，请在评测侧适配。本作者未进行任何计分。
