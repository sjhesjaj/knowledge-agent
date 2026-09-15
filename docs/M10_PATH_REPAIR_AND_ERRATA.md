# M10 路径修复与报告更正（勘误）

记录日期：2026-09-08。记录人：实现 agent。

本文是 `docs/M10_IMPLEMENTATION_REPORT.md` 的**外挂勘误**。按用户要求，
**报告原文件保持不动**，不回改、不重写；这里单独记录一处错误判断、修复过程和复验结果。
阅读报告第 9 节「给验收方的锁定要点」时，请以本文为准。

---

## 1. 报告里的错误判断（已确认为错）

**报告原文的说法**（第 9 节）：

> 磁盘迁移方案用的是 Windows 已知文件夹重定向，注销重登录后
> `C:\Users\h000_\Documents` 会指向 D 盘，两个路径随之自动恢复有效。
> **不需要改这两个文件**。

**这个判断是错的。** 已知文件夹重定向（`SHSetKnownFolderPath` / User Shell Folders
注册表项）改变的是**外壳层**对 `FOLDERID_Documents` 的解析结果，它**不会**在
`C:\Users\h000_\Documents` 处创建任何文件系统链接。Git 读取 `.git` 里的路径字符串后
直接做文件系统打开，不经过外壳的已知文件夹 API，因此那两个写死的 C 盘路径
**不会随注销重登录自动恢复**——它们会一直坏下去。

按错误建议行事的后果：验收方会一直等一个永远不会发生的自动修复，
而 `git status` 会持续报 `fatal: not a git repository`。

**正确做法**：用 Git 官方的 `git worktree repair` 重建关联（已执行，见第 3 节）。

错误定性：这是实现方的判断错误，与本轮代码改动无关，也不影响任何已测数据。

---

## 2. 修复前的备份

修复动作会改写 Git 元数据，所以先做了全量备份，落在 C 盘。

> **更正（2026-09-08）**：本文初稿把这份备份描述为落在「另一块物理硬盘」，**这是错的**。
> 用户实测：C 与 D **是同一块物理硬盘（Disk 0）上的两个分区**（分别为分区 3 和 4）。
> 正确表述是「**同一硬盘的另一个分区**」。
>
> 后果必须讲清楚：这份备份**能**用于撤销误删、误改、误执行一类的操作事故，
> **不能**防止这块 SSD 本身故障——盘坏了，两个分区一起没。
> 因此**还需要再复制一份到外置硬盘**，这一条尚未完成，见第 8 节。
>
> 记录口径：本次我没能独立复核磁盘拓扑（本机 PowerShell 调用返回空，见第 8 节），
> 上述 Disk 0 / 分区 3、4 是**用户实测的结果**，不是我验证的。

| 项 | 内容 |
|---|---|
| 备份位置 | `C:\Users\h000_\m10-backup\pre-repair-20260907-235429\`（同盘另一分区） |
| `worktree/` | D 盘工作树全部内容，**含未提交修改、全部新增文件、`data/`、`.cache/`** |
| `main-repo-git/.git/` | 主仓库 `.git` 全量（442 个文件） |
| 排除项 | `.venv`（422 MB，可重建）、`frontend/node_modules`（55 MB，可重建） |
| 大小 | 14 MB |

**备份保真度已逐项核验**：

```text
工作树文件数   源 260  备份 260   MATCH
主仓库 .git    源 442  备份 442   MATCH
diff -r -q     无差异输出（逐字节一致）
knowledge_agent.db  SHA-256 两侧相同
                    c801e487e7776199024285288a6760c995c64fc19a3b2fe4ae4476057a3fb4d2
```

`data/knowledge_agent.db` 当时的状态：3 条知识块、0 条对话。**没有事故前的备份**，
所以这份备份只能保证「从此刻起不再丢」，**不能证明历史数据齐全**。

---

## 3. 执行的修复：`git worktree repair`

两个文件写着迁移前的 C 盘路径，两个 C 盘位置都已是空壳：

| 文件 | 修复前 | 修复后 |
|---|---|---|
| `<worktree>/.git` | `gitdir: C:/…/n-h-2/knowledge-agent/.git/worktrees/knowledge-agent-wiki-v1` | 同路径但 `D:/…` |
| `<主仓库>/.git/worktrees/knowledge-agent-wiki-v1/gitdir` | `C:/…/2026-08-26/knowledge-agent-wiki-v1/.git` | 同路径但 `D:/…` |

执行的命令（在**主工作树**里运行，只给出被链接工作树的路径）：

```bash
git -C "D:/Users/h000_/Documents/Codex/2026-07-11/n-h-2/knowledge-agent" \
    worktree repair "D:/Users/h000_/Documents/Codex/2026-08-26/knowledge-agent-wiki-v1"
```

输出与退出码：

```text
repair: gitdir incorrect: D:/…/n-h-2/knowledge-agent/.git/worktrees/knowledge-agent-wiki-v1/gitdir
exit=0
```

**没有执行**：`clone`、`reset --hard`、`clean`、`worktree prune`，也没有覆盖任何目录。
`worktree repair` 只改上表那两个指针文件，不碰内容、索引或引用。

修复前 `git worktree list` 把这个工作树标为 `prunable`；修复后该标记消失。

---

## 4. 修复后的完整性复验

```text
HEAD                7480ba842ae5ea1e07d0125bd9422f9193d3c20b   （未移动）
branch              codex/iterative-wiki                        （未变）
git status          原生可用，不再需要 --git-dir
                    7 个修改 + 47 个未跟踪文件，与交付时一致（见下方计数说明）
git diff --check    干净
git diff --stat     7 files changed, 826 insertions(+), 75 deletions(-)  （与报告一致）
git fsck            无输出（对象库健康）
冻结题集/评测器      4 个 SHA-256 全部与计划一致，未变
工作树内容           与修复前备份逐字节比对无差异——只有 .git 指针变了
```

`git worktree list` 修复后：

```text
D:/…/2026-07-11/n-h-2/knowledge-agent            622a4af [codex/local-rag-baseline-20260825]
D:/…/2026-08-26/knowledge-agent-wiki-v1          7480ba8 [codex/iterative-wiki]
```

---

## 5. 其余旧路径的排查结果

| 位置 | 结论 |
|---|---|
| `start.ps1`、`start_api.ps1`、`start_frontend.ps1`、`run_tests.ps1`、`run_agent_evaluations.ps1` | **全部使用相对路径，无绝对 C 盘路径，无需修改** |
| `.venv/pyvenv.cfg` | 仍写着 C 盘的 `home`/`executable`/`command`——但那些位置**已被恢复**，见第 6 节，因此可用，未改动 |
| `docs/tasks/M*.md`、`docs/m10-selftest/*.console.txt`、报告正文 | 含 C 盘路径，但都是**历史记录与说明文本**，不是可执行配置，按原样保留 |
| `evaluation_runs/` 旧记录 | 同上，未改动 |

---

## 6. 环境更正：原 Python 3.12 环境已恢复可用

报告第 1.3 节与 8.3 节记录「`.venv` 的基础解释器已随磁盘清理消失，故改用另建的
Python 3.14 环境重测基线与候选」。**该记录如实反映了当时的情况，但情况后来变了**：
磁盘迁移方在把 `.cache` 还原回 C 盘时，一并恢复了
`C:\Users\h000_\.cache\codex-runtimes\codex-primary-runtime\dependencies\python`，
所以项目自带的 `.venv` 现在完全可用。

```text
.venv\Scripts\python.exe --version        Python 3.12.14
import fastapi, jieba, requests, pypdf, httpx, uvicorn   deps OK
site-packages 条目数                       125
```

**已在项目自带的 3.12 环境下重做了确定性复验**（隔离副本，161 个文件，
非开发工作树）：

| 检查 | Python 3.14（报告所用） | Python 3.12.14（项目自带） |
|---|---|---|
| `py_compile`（14 个改动/新增文件） | exit 0 | **exit 0** |
| `unittest discover` | Ran 734  OK | **Ran 734  OK** |
| 通道判定 `blind_v2` | 100.0%  exit 0 | **100.0%  exit 0** |
| 通道判定 `dev` | 100.0%  exit 0 | **100.0%  exit 0** |
| 通道判定 `validation_v1` | 100.0%  exit 0 | **100.0%  exit 0** |
| 通道判定 `holdout` | 97.5%  exit 0 | **97.5%  exit 0** |

两个环境**结果完全一致**。

**仍需说明的口径**：依赖模型的三项（回答集 3 轮、检索相关性、真实 API/SSE）
报告里的数字仍然是 **Python 3.14 环境**下测的，本次没有重跑——重跑基线与候选两侧
约需 40 分钟模型时间，而上表已证明两个环境在确定性部分逐项一致，
基线通过率此前也交叉验证过（3.12 与 3.14 均为每轮 32/40）。
如果验收方要求模型相关指标也在 3.12 下复现，这是一项**尚未完成的验证**，
不应默认它已完成。

---

## 7. 本次没有改动的东西

- **冻结的候选代码**：4 个产品文件、4 个新增测试文件，一字未动（第 4 节已逐项核验）。
- **`docs/M10_IMPLEMENTATION_REPORT.md`**：原文保持不动，包括第 1 节仍然是「未改写任何
  git 元数据」的旧表述——那句话在本次修复后**已不再准确**，正确情况以本文第 3 节为准。
- **冻结题集与评测器**：4 个 SHA-256 未变。
- **`docs/m10-selftest/` 原始结果**：未重新生成，仍是交付时那一份。
- 未提交、未推送、未合并；HEAD 仍是 `7480ba84`。

> **给验收方的提醒**：报告第 9 节关于「注销后自动恢复」的那段是错的，
> 且报告第 9 节称本轮 git 操作全部只读——在本次修复后不再成立。
> 判断以本文为准。清单文件 `docs/m10-selftest/candidate-manifest.json` 生成于修复之前，
> 它覆盖的是代码与文档内容，不含 `.git` 元数据，因此仍然有效。

---

## 8. 计数口径与尚未完成的两件事

### 8.1 文件计数：不要把折叠后的 git 输出行数当文件数

`git status --short` 会把整个未跟踪目录折叠成一行（`?? docs/m10-selftest/`），
所以它的行数**不等于**文件数。本文初稿写的「10 个未跟踪条目」就是这么数错的。

准确口径（截至 2026-09-08）：

```text
git diff --name-only | wc -l                    ->  7   个修改文件
git ls-files --others --exclude-standard | wc -l -> 47   个未跟踪文件
git status --short | wc -l                      -> 18   行（折叠后，不是文件数）
```

47 个未跟踪文件的构成：

| 组成 | 个数 |
|---|---:|
| 新增产品/测试/评测代码（4 个测试 + `evaluate_retrieval_relevance.py` + `eval_retrieval_relevance_m10.json` + `verify_api_sse.py`） | 7 |
| 新增文档（实现报告、本勘误、任务说明） | 3 |
| `docs/m10-selftest/` 自测原始结果 | 37 |
| **合计** | **47** |

（初稿说「9 个新增文件」时本勘误还不存在；加上本文即为 10 个代码与文档文件。）

### 8.2 待办一：把备份再复制一份到外置硬盘

当前唯一的备份与源数据在**同一块 SSD** 上，防不了硬盘故障。本机目前
**没有挂载任何外置卷**（只有 C: 和 D:），所以我无法代做这一步。插入外置盘后：

```bash
# 把 <E:> 换成外置盘盘符
cp -r "C:/Users/h000_/m10-backup/pre-repair-20260907-235429" "E:/m10-backup-offdevice/"
# 复制后核验
diff -r -q "C:/Users/h000_/m10-backup/pre-repair-20260907-235429" "E:/m10-backup-offdevice/pre-repair-20260907-235429"
```

同时建议把 D 盘工作树里当前的未提交内容也一并带上——那 47 个未跟踪文件和 7 处修改
**尚未提交到任何 Git 历史里**，Git 保护不到它们。

### 8.3 待办二：3.12 环境下依赖模型的三项验证

见第 6 节末尾。回答集 3 轮、检索相关性、真实 API/SSE 目前只有 Python 3.14 环境下的
结果；3.12 下**未跑过**，不应默认等价。

### 8.4 本次未能自证的一项

磁盘拓扑（C、D 同属 Disk 0）是**用户实测**并告知的。我尝试用
`Get-Partition` / `Get-Disk` / `wmic` 复核，本机 PowerShell 调用均返回空输出
（与本轮早前观察到的 PowerShell 响应异常一致），因此**这一条我没有独立验证**，
按用户提供的测量结果记录。
