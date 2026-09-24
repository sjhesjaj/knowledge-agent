# eval/ — 评测结果管理规则

## 进 Git 的（小、需要复现和对比）

| 内容 | 位置 | 说明 |
|---|---|---|
| 基线 / 回归 / 任一带标签的运行 | `eval/<label>.json` | 环境（commit、模型 digest、量化、Ollama 版本、Wiki build）、检索配置、system prompt 哈希、请求形态、每次运行的 summary、aggregate、gates，以及**每个 case 每次运行的结果**（用于翻转分析） |
| 对比结论 | `eval/stage0_comparison.json`（以及以后的同类文件） | `compare_stage0.py` 的输出 |
| 冒烟 / 真实调用记录 | `eval/deepseek_smoke.json` | 逻辑与实际 prompt 的差异、token、耗时；不含请求头和 Key |
| Trace 开销 A/B | `eval/stage1_trace_overhead.json` | `trace_overhead.py` 的结果（TRACE on/off 各 200 次 mock 请求） |
| 诊断标签 overlay | `eval/diagnostic_labels/<dataset>.labels.json` | Stage 2 起。人工编写，锁定数据集 sha256 和它所针对的 wiki 语料；冻结数据集本身不改 |
| 诊断报告 | `eval/diagnostics/<label>.diagnostic.json` / `.md` | `python -m diagnostic_eval --eval eval/<label>.json --labels <overlay>` 的输出：case 级阶段诊断，以及 root cause 分布 |
| 脚本 | `eval/*.py` | harness、对比、冒烟、开销基准 |

每个 case 的每次运行在 `eval/<label>.json` 里都带有 `trace_run_id`。某个 case 失败时，用这个 id 去对应 label 的 Trace 库里查：

```powershell
.\.venv\Scripts\python.exe -m agent_trace --db eval\artifacts\<label>\traces.sqlite show <trace_run_id>
```

## 不进 Git 的（大、原始、可再生成）

`eval/artifacts/`，已在 `.gitignore` 中忽略：

- `eval/artifacts/<label>/run-N.json`、`summary.json`、`summary.md`：`evaluate_answerability.py` 的原始逐次输出。`run_stage0_eval.py` 会写到这里，再把需要留档的部分汇总进 `eval/<label>.json`
- 控制台日志：用 `> eval\artifacts\<label>.console.txt` 重定向到这里
- `eval/artifacts/<label>/traces.sqlite`：Stage 1 起，这次评测所有 case 的 Trace。Eval 的 Trace 不截断，保留完整的 prompt、证据和工具结果，所以体积较大；它可以再生成，因此不提交

## 历史例外

Stage 0（commit `416fd0d`）的原始输出在这条规则制定之前就已经提交，位置是 `eval/runs/baseline_qwen/`、`eval/runs/regression_qwen/`、`eval/*.console.txt`。**它们保留在原处，不移动也不改写**，因为 `stage0_comparison.json` 和 HANDOFF 都引用了这些路径。新的运行不要再往 `eval/runs/` 里写。
