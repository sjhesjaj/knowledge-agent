# Environment diff: eval/stage1_trace_qwen.json -> eval/env_v1_validation_qwen.json

> Every difference is attributed to the environment change; this is not a regression or improvement judgement of the agent.

- old wiki: `unpinned (live data/wiki published_build:build-0001)` (commit 5baf106ace40)
- new wiki: `committed_sample` in `eval-env-v1` (commit 38afa41a3b53)
- passed per run: old [39, 39, 39] -> new [39, 39, 39]
- cases: {'answer_changed': 11, 'unchanged': 29}

`distinct answers` counts different answer texts within each side's own runs; `wiki` says whether either side used wiki evidence. Both are facts shown for context; the attribution stays environment_change.

| case | change | pass (old -> new) | distinct answers (old / new) | wiki | what changed |
|---|---|---|---|---|---|
| `answer_document_h001` | answer_changed | 3 -> 3 | 2 / 1 | no | answer text differs |
| `answer_document_h005` | answer_changed | 3 -> 3 | 1 / 2 | no | answer text differs |
| `answer_document_h007` | answer_changed | 3 -> 3 | 1 / 1 | no | answer text differs |
| `answer_multi_h002` | answer_changed | 3 -> 3 | 1 / 2 | no | answer text differs |
| `answer_multi_h003` | answer_changed | 3 -> 3 | 1 / 1 | yes | answer text differs |
| `answer_multi_h004` | answer_changed | 3 -> 3 | 1 / 2 | yes | answer text differs |
| `answer_wiki_h001` | answer_changed | 3 -> 3 | 1 / 2 | yes | answer text differs |
| `answer_wiki_h002` | answer_changed | 3 -> 3 | 1 / 2 | yes | answer text differs |
| `answer_wiki_h003` | answer_changed | 3 -> 3 | 1 / 1 | yes | answer text differs |
| `answer_wiki_h004` | answer_changed | 3 -> 3 | 1 / 1 | yes | answer text differs |
| `refuse_missing_h007` | answer_changed | 3 -> 3 | 1 / 2 | no | answer text differs |

Unchanged (29): `answer_document_h002`, `answer_document_h003`, `answer_document_h004`, `answer_document_h006`, `answer_document_h008`, `answer_inventory_h001`, `answer_inventory_h002`, `answer_inventory_h003`, `answer_inventory_h004`, `answer_multi_h001`, `boundary_approval_h001`, `boundary_approval_h002`, `boundary_balance_h001`, `boundary_balance_h002`, `boundary_missing_sku_h001`, `boundary_missing_sku_h002`, `boundary_order_h001`, `boundary_order_h002`, `refuse_missing_h001`, `refuse_missing_h002`, `refuse_missing_h003`, `refuse_missing_h004`, `refuse_missing_h005`, `refuse_missing_h006`, `refuse_missing_h008`, `refuse_policy_h001`, `refuse_policy_h002`, `refuse_policy_h003`, `refuse_policy_h004`
