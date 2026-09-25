# Stage 3 · freshness 评分：after A' (dev, implemented planner)

- 数据集 `eval_temporal_freshness_dev.json`，sha256 `322471b50e25c12d…`，29 条
- 代码 `307a12b`，dirty=False
- Planner 的 freshness 词表：当前、现在、实时、目前、最新、截至、此时、此刻、眼下、这会儿、当下

| 指标 | 值 |
|---|---|
| freshness accuracy | 1.0 (29/29) |
| TP / FP / FN / TN | 12 / 0 / 0 / 17 |
| precision / recall | 1.0 / 1.0 |
| false positive rate / false negative rate | 0.0 / 0.0 |
| route accuracy（次要） | 0.931 |
| 应该能回答、却会因 freshness 被拒答 | 0/17 |

## 按意图

| intent | n | TP | FP | FN | TN | route 正确 |
|---|---|---|---|---|---|---|
| current_knowledge | 12 | 0 | 0 | 0 | 12 | 12 |
| incidental | 5 | 0 | 0 | 0 | 5 | 5 |
| live_state | 9 | 9 | 0 | 0 | 0 | 8 |
| mixed | 3 | 3 | 0 | 0 | 0 | 2 |

## 逐条

| id | intent | 期望 / 实际 freshness | 结果 | 期望 / 实际 route | Planner 命中的词 | 因 freshness 拒答 |
|---|---|---|---|---|---|---|
| `tf_ck_01` | current_knowledge | False / False | TN | document_only / document_only | 目前 | False |
| `tf_ck_02` | current_knowledge | False / False | TN | document_only / document_only | 当前 | False |
| `tf_ck_03` | current_knowledge | False / False | TN | document_only / document_only | 现在 | False |
| `tf_ck_04` | current_knowledge | False / False | TN | document_only / document_only | 最新 | False |
| `tf_ck_05` | current_knowledge | False / False | TN | document_only / document_only | - | False |
| `tf_ck_06` | current_knowledge | False / False | TN | document_only / document_only | 目前、截至 | False |
| `tf_ck_07` | current_knowledge | False / False | TN | document_only / document_only | - | False |
| `tf_ck_08` | current_knowledge | False / False | TN | document_only / document_only | - | False |
| `tf_ck_09` | current_knowledge | False / False | TN | wiki_only / wiki_only | 目前 | False |
| `tf_ck_10` | current_knowledge | False / False | TN | document_only / document_only | 当下 | False |
| `tf_ck_11` | current_knowledge | False / False | TN | document_only / document_only | 目前 | False |
| `tf_ck_12` | current_knowledge | False / False | TN | document_only / document_only | 眼下 | False |
| `tf_ls_01` | live_state | True / True | TP | system_only / system_only | 目前 | - |
| `tf_ls_02` | live_state | True / True | TP | system_only / system_only | 现在 | - |
| `tf_ls_03` | live_state | True / True | TP | system_only / system_only | - | - |
| `tf_ls_04` | live_state | True / True | TP | system_only / system_only | 最新 | - |
| `tf_ls_05` | live_state | True / True | TP | system_only / document_only | 目前、截至 | True |
| `tf_ls_06` | live_state | True / True | TP | system_only / system_only | - | - |
| `tf_ls_07` | live_state | True / True | TP | system_only / system_only | 当前 | - |
| `tf_ls_08` | live_state | True / True | TP | system_only / system_only | 实时 | - |
| `tf_ls_09` | live_state | True / True | TP | system_only / system_only | 此刻 | - |
| `tf_mx_01` | mixed | True / True | TP | document_system / document_only | 目前 | True |
| `tf_mx_02` | mixed | True / True | TP | document_system / document_system | 当前、现在 | - |
| `tf_mx_03` | mixed | True / True | TP | wiki_system / wiki_system | 当前、目前 | - |
| `tf_in_01` | incidental | False / False | TN | document_only / document_only | 截至 | False |
| `tf_in_02` | incidental | False / False | TN | document_only / document_only | 现在 | False |
| `tf_in_03` | incidental | False / False | TN | document_only / document_only | 实时 | False |
| `tf_in_04` | incidental | False / False | TN | document_only / document_only | - | False |
| `tf_in_05` | incidental | False / False | TN | document_only / document_only | 当前 | False |
