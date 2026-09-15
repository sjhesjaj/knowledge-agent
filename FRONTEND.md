# Vue 3 + FastAPI 前后端分离版

项目保留原有 Streamlit 页面，并新增 Vue 3 + FastAPI 实现。FastAPI 直接复用 `agent.py` 和 `rag.py`，不会维护两套 RAG 逻辑。

## 已实现功能

- PDF、TXT、Markdown 多文件上传与知识库构建
- ChatGPT 风格聊天页面
- 基于 `fetch + ReadableStream` 的 SSE 流式回答
- JSON Schema 约束的结构化流，页面只展示最终答案，不暴露模型分析过程
- Agent 路由、检索、生成阶段状态提示
- Markdown 安全渲染
- 检索来源、章节、片段、分数和运行耗时展示
- SQLite 持久化知识块、向量、会话和消息，后端重启后可以恢复
- 会话列表、新建、切换、恢复和删除
- 浏览器生成匿名 `client_id`，按客户端逻辑隔离会话
- 流中错误、意外断流和失败不写入历史保护
- 省略主语的连续问题会继承前一问上下文，减少无关制度误召回

## 1. 准备本地模型

```powershell
ollama pull qwen3:4b
ollama pull nomic-embed-text
```

启动 Ollama，并确认 `http://localhost:11434` 可以访问。

## 2. 启动后端

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_api.ps1
```

API 文档：`http://127.0.0.1:8000/docs`

## 3. 启动前端

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问：`http://127.0.0.1:5173`

## 4. 使用顺序

1. 确认左侧显示“Ollama 已连接”。
2. 上传 PDF、TXT 或 Markdown 文件。
3. 点击“建立知识库”。
4. 输入问题，观察路由、检索和生成状态。
5. 展开回答下方的“检索依据”查看引用片段。
6. 点击“新对话”创建独立会话；在左侧会话列表中可以切换、恢复或删除历史会话。

## SQLite 持久化与会话隔离

后端会把知识块、向量、会话和消息保存到：

```text
data/knowledge_agent.db
```

`data/` 已加入 Git 忽略列表。数据库保存的是解析后的知识块和向量，不是用户上传的原始文件。正常重启 FastAPI 后，知识库和聊天记录会自动恢复，不需要重新上传文档。

前端首次打开时会在浏览器本地生成匿名 `client_id`，所有会话接口都携带这个标识，从而只显示当前浏览器创建的会话。它仅用于本地演示中的逻辑隔离，并不是登录、身份认证或权限控制；正式部署仍需接入账号体系和服务端鉴权。

重新上传按文档身份更新：同名文档的旧知识块被替换，未涉及的其他文档保留，并提交后台 Wiki 编译任务。索引版本变化会清除历史会话，避免旧回答与新知识混用。等待 Wiki 发布后才能确认两条知识路径均已更新。清空知识库会清除知识块、Wiki 构建和全部历史会话。

## API

| 方法 | 地址 | 用途 |
|---|---|---|
| GET | `/api/health` | 查询模型连接和知识库状态 |
| GET | `/api/conversations?client_id=...` | 查询当前浏览器的会话列表 |
| POST | `/api/conversations` | 新建会话，正文包含 `client_id` 和可选 `title` |
| GET | `/api/conversations/{conversation_id}/messages?client_id=...` | 恢复指定会话的消息 |
| DELETE | `/api/conversations/{conversation_id}?client_id=...` | 删除指定会话及其消息 |
| POST | `/api/knowledge/upload` | 上传文件并建立索引 |
| DELETE | `/api/knowledge` | 清空知识库和全部聊天记录 |
| POST | `/api/chat` | 非流式 Agent 问答，正文包含 `question`、`session_id` 和 `client_id` |
| POST | `/api/chat/stream` | SSE 流式 Agent 问答，正文字段同上 |

流式接口依次返回以下事件：

```text
status  -> Agent 当前阶段
sources -> 检索依据
delta   -> 新生成的文本片段
done    -> 完成及耗时轨迹
error   -> 流中错误
```

## 测试

后端测试在隔离源码副本运行；API导入会初始化源目录下的SQLite，仅切换工作目录不能隔离真实数据。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -v

cd frontend
npm run build
```

最终后端共有991项自动化测试通过（Python 3.12.14，2026-09-15）。覆盖结构化分片、跨分片JSON转义、传输错误、缺少结束标记、会话隔离，以及相同脚本化模型响应下普通/SSE的完整正文、终态、调用次数与持久化一致性。传输/协议错误不落库；交付校验产生的正常拒答按done完成并保存。

正文在整段生成、复查和交付校验后才发出，首段delta不保证对应模型的第一个token。最终配对实测首段正文p95为1.389→3.299秒，普通/SSE总耗时p95增幅4.09%/13.09%。该批两侧使用同一IPv4 loopback模型连接环境；失败批次、语义遗漏及完整限制见 [M10收尾验收报告](docs/M10_CLOSEOUT_REPORT.md)。

前端仍然没有测试框架，`npm run build` 是唯一的前端门禁。上面这些都是后端测试。

## 重启恢复验证

1. 启动 Ollama、FastAPI 和 Vue 页面，上传文档并完成一次问答。
2. 记住左侧会话标题和页面显示的知识块数量。
3. 只停止并重新启动 FastAPI，保持同一个浏览器不清理本地数据。
4. 刷新页面，确认知识块数量没有归零，并能从左侧列表重新打开刚才的会话和消息。
5. 新建另一会话并切换回来，确认两组消息互不混合；删除测试会话后刷新页面，确认它不再出现。

## 当前运行边界

SQLite 已负责知识库和聊天记录的持久化，但运行中的知识块快照、会话锁和知识库更新锁仍在当前 FastAPI 进程内。因此本项目适合本地展示和单进程运行，启动 Uvicorn 时请保持单 Worker。若要多实例部署，需要把共享状态和分布式锁迁移到外部服务，并使用正式的用户认证与权限控制。
