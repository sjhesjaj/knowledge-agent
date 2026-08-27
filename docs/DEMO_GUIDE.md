# V1 演示指南

三通道知识 Agent 的固定演示流程：Wiki、原文检索、只读实时状态。
按本文顺序执行，全部问题都经过实测，路由与证据类型可复现。

---

## 1. 启动前检查

| 检查项 | 命令 | 期望 |
|---|---|---|
| Python 环境 | `.\.venv\Scripts\python.exe --version` | Python 3.12.x |
| 后端测试 | `.\.venv\Scripts\python.exe -m unittest discover` | `OK`，394 项 |
| 前端构建 | `cd frontend; npm run build` | `✓ built` |
| Wiki 页面 | `.\.venv\Scripts\python.exe -c "from orchestration import load_wiki_pages; print(len(load_wiki_pages()))"` | `4` |
| 演示 fixture | `.\.venv\Scripts\python.exe -m json.tool wiki_pages\sample_company_wiki.json > $null` | 无输出即通过 |

## 2. Ollama 与模型

```powershell
ollama serve
ollama pull qwen3:4b
ollama pull nomic-embed-text
```

模型名以 `rag.py` 的 `CHAT_MODEL` / `EMBED_MODEL` 为准。本分支的 Embedding 模型是
`nomic-embed-text`（见「已知限制」）。

确认服务在线：

```powershell
curl http://127.0.0.1:11434/api/tags
```

## 3. 启动后端

```powershell
.\start_api.ps1
```

健康检查应返回 `"ollama_connected": true`：

```powershell
curl http://127.0.0.1:8000/api/health
```

## 4. 启动前端

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173

## 5. 上传样例文档（必做）

通过左侧「导入知识」上传仓库根目录的 **`sample_company_rules.md`**，
等待侧栏「知识片段」数量大于 0。

**为什么必须上传**：

- Wiki 页面已经从这份样例规则派生，随仓库加载，无需上传；
- Document 通道依赖上传后建立的检索索引，不上传就是空的；
- 用同一份语料，能保证 Wiki 摘要与原文条款内容一致；
- 跳过这一步，只能演示 Wiki 与库存，涉及原文的问题会返回
  「当前没有可用的文档知识库。」

**两者是不同的知识形态，但来源一致**：上传的文件只用于建立 Document 检索索引；
Wiki 页面是仓库内已编译好的样例页面。

## 6. 打开「三通道模式」

右上角勾选 **三通道模式**。

- 关闭时走原有 legacy 链路（`decide_action` + 单一检索），行为与之前完全一致；
- 打开后走 Planner → Executor → Evidence Policy，回答下方会显示
  **证据路径**（route 与 steps）和带来源类型标签的证据卡片。

> 打开该模式后，即使知识库为空也可以输入——Wiki 与库存问题本来就不依赖文档上传。

---

## 7. 固定演示问题

主演示按顺序 1 → 7，第 8 题为可选边界演示。

| # | 问题 | route | steps | Evidence 类型 |
|---|---|---|---|---|
| 1 | `你好` | `direct` | 无 | 无（固定问候，不调用模型） |
| 2 | `介绍一下远程办公` | `wiki_only` | `wiki_query` | Wiki ×3 |
| 3 | `年假原文具体怎么规定？` | `document_only` | `document_search` | 原文 |
| 4 | `概述请假制度并引用关键条款` | `wiki_document` | `wiki_query`, `document_search` | Wiki + 原文 |
| 5 | `帮我查一下 SKU-A100 的库存` | `system_only` | `system_query` | 实时状态（42 件） |
| 6 | `SKU-A100 现在还有多少库存？请附原文依据。` | `document_system` | `document_search`, `system_query` | 原文 + 实时状态 |
| 7 | `帮我查一下 SKU-Z999 的库存` | `system_only` | `system_query` | 无 → **REFUSE** |
| 8 | `我的订单现在什么状态？` | `system_only` | `system_query` | 无 → 边界提示（可选） |

### 逐题讲解要点

**1. `你好`** — Planner 判定为 `direct`，不执行任何工具，也**不调用回答模型**，直接返回固定问候。展示「不需要证据的请求不会浪费一次推理」。

**2. `介绍一下远程办公`** — 只走 Wiki。证据卡片显示蓝色 `Wiki` 标签，locator 形如 `section:远程办公`，可追溯回 `sample_company_rules.md` 的对应章节。展示 Wiki 是**派生知识**，但保留了回原文的路径。

**3. `年假原文具体怎么规定？`** — 「原文」是强精确标记，只走 Document。绿色 `原文` 标签，卡片带章节标题、片段编号和检索分数。

**4. `概述请假制度并引用关键条款`** — 「概述」触发 Wiki，「引用/条款」触发 Document，两条通道同时执行。证据路径显示 `Wiki + 原文`。展示**导航 + 精确依据**的组合。

**5. `帮我查一下 SKU-A100 的库存`** — 走只读 System 通道，返回 `42 件`，数据来自隔离的演示 fixture。橙色 `实时状态` 标签，locator 为 `inventory:sku-a100`。SKU 由确定性正则从问题中提取，**不经过模型**。

**6. `SKU-A100 现在还有多少库存？请附原文依据。`** — 三通道里最能说明设计的一题：制度事实来自原文，当前状态来自 System，两者合并后回答。证据路径显示 `原文 + 实时状态`。

**7. `帮我查一下 SKU-Z999 的库存`** — 格式合法但不存在的 SKU。System 返回 `EMPTY`，Evidence Policy 判定 `REFUSE`，直接返回固定文案「根据现有资料无法确定。」，**不调用模型去圆场**。展示「证据不足时说不知道，而不是猜」。

**8.（可选边界演示）`我的订单现在什么状态？`** — 返回「当前版本仅支持库存查询。」

> 讲解口径：当前没有可信的身份解析器，因此系统**主动不开放**主体范围的订单和审批查询。
> 这不是查不到，而是避免用客户端自报的身份去读取业务数据。
> 前七题展示产品能力，这一题展示设计克制。

### 可选补充：两种不同的「拒答」

如果时间允许，可以对比问一个手册里没写的普通问题，例如 `公司班车几点发车？`：

- 它走 `document_only`，检索**会返回**若干片段，Policy 判定为 `READY`；
- 拒答由既有的生成层防幻觉链完成（模型基于证据判断无法回答）；
- 与第 7 题不同：第 7 题是**策略层**在调用模型之前就拒答。

两种机制都存在，层次不同，不要混为一谈。

---

## 8. 已知限制（如实说明）

**复合路由会被政策语义压制。** 这句话不要用于主演示：

```
库存管理制度怎么规定，SKU-A100 还有多少？
```

实测结果是 `document_only`，System 通道被抑制。原因是 Planner 的确定性规则里，
「制度 / 规定」这类政策语义会压制弱实时意图（「现在」「多少」「查一下」），
以避免把「现在的请假制度怎么规定」这类纯制度问题误路由到实时查询。
代价是「制度 + 实时状态」的自然问法有时也会被一并压制。

第 6 题的问法（`请附原文依据`）用的是强精确标记而非政策名词，因此能稳定走
`document_system`。

**改进方向**：让 Planner 规则演进，或引入一次受控的 LLM 路由兜底。已列入 backlog。

其他限制：

- **Embedding 模型仍是 `nomic-embed-text`**。`bge-m3` 仅在参考分支评估过，尚未在
  当前产品主干的相同语料、切分和评测条件下验证，因此不作为本次演示结论，后续单独
  评估迁移。演示中 Document 通道的检索质量以当前模型为准，问法越接近原文措辞越稳。
- 订单与审批查询未开放，需要可信身份解析器（见第 8 题）；
- 生产链路暂未接入结构化事实断言，因此**不进行冲突裁决**——
  Evidence Policy 的权威裁决已实现并有测试，但当前没有生产数据触发它；
- `client_id` 仅用于演示级会话隔离，不等同于登录认证；
- 前端没有测试框架，只有生产构建作为门禁。

---

## 9. 演示失败时的快速排查

| 现象 | 排查 |
|---|---|
| 输入框灰掉 | 未打开「三通道模式」且知识片段为 0。打开开关，或先上传 `sample_company_rules.md` |
| 涉及原文的问题回「当前没有可用的文档知识库。」 | 没上传样例文档，或上传后知识片段仍为 0 |
| Wiki 问题回「当前没有可用的 Wiki 页面。」 | `wiki_pages/sample_company_wiki.json` 缺失或非法，用第 1 节的命令检查 |
| 库存问题回「请提供需要查询的 SKU。」 | 问题里没有合法 SKU。格式为字母 + 三位数字，如 `SKU-A100` |
| 库存问题回「当前版本仅支持库存查询。」 | 问的是订单/审批/账户，属于未开放范围，见第 8 题 |
| 回答一直转圈或报错 | 检查 `ollama serve` 是否在跑，`/api/health` 的 `ollama_connected` 是否为 `true` |
| SSE 报「回答生成失败，请稍后重试。」 | orchestrated 模式的固定错误文案，不含内部细节。查看后端终端日志定位真实原因 |
| 路由与本文不一致 | 确认「三通道模式」已打开；关闭时走的是 legacy 链路，没有 route/steps |
| 页面白屏 | 前端未构建或依赖未安装，执行 `npm install` 后重试 |

演示可用的库存 SKU：`SKU-A100`（42 件）、`SKU-B200`（0 件，展示「存在且为零」）、`SKU-C300`（7 箱）。
