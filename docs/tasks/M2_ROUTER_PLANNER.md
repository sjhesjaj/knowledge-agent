# M2 - Controlled Router / Planner

## Status

Ready for Coding Agent implementation after reviewer approval.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `ca8d39f54118a09666f732a1e89c0e80c8d3fc9d`
- Baseline: 50/50 unit/API tests pass.

Read `AGENTS.md`, `docs/V1_IMPLEMENTATION_PLAN.md`, and this file completely
before editing.

## Goal

Add a pure planning layer that decides which read-only evidence paths a request
requires. It returns a validated plan and never executes a tool:

```text
question
   |
extract deterministic signals
   |
map required tools to a canonical route
   |
optional one-shot fallback only if no signal decided the route
   |
Plan(route, steps, signals, reason_codes)
```

M2 does not replace or integrate the current `agent.decide_action()` path.

## Allowed Files

Modify or create only:

```text
orchestration/__init__.py
orchestration/planner.py
tests/test_planner.py
```

Do not modify contracts, adapters, production files, existing tests, README, or
this task document.

## Required Enums

In `orchestration/planner.py`, define string enums.

### ToolName

Exactly:

```text
wiki_query
document_search
system_query
```

### Route

Exactly:

```text
direct
wiki_only
document_only
system_only
wiki_document
wiki_system
document_system
wiki_document_system
```

## Canonical Route Matrix

Route is derived from steps; callers and fallback classifiers must not set it
independently.

| Steps | Route |
|---|---|
| `()` | `direct` |
| `(wiki_query,)` | `wiki_only` |
| `(document_search,)` | `document_only` |
| `(system_query,)` | `system_only` |
| `(wiki_query, document_search)` | `wiki_document` |
| `(wiki_query, system_query)` | `wiki_system` |
| `(document_search, system_query)` | `document_system` |
| `(wiki_query, document_search, system_query)` | `wiki_document_system` |

The canonical step order is always Wiki, Document, System. A plan may contain
zero through three steps, with no duplicates.

## Required Data Contracts

Use standard-library dataclasses. They must be keyword-only and frozen so a
plan cannot be mutated after validation.

### RequestSignals

Required boolean fields, all defaulting to `False`:

```text
is_direct
needs_wiki
needs_document
needs_system
requires_freshness
requires_exact_citation
```

Provide a JSON-friendly `to_dict()`.

### Plan

Required fields:

```text
route: Route
steps: tuple[ToolName, ...]
signals: RequestSignals
reason_codes: tuple[str, ...]
fallback_used: bool
```

Rules enforced in `__post_init__`:

- at most three steps;
- no duplicate steps;
- steps are in canonical order;
- `route` exactly matches the route matrix;
- `direct` has no steps;
- every non-direct route has at least one step;
- reason codes are non-empty strings;
- `fallback_used` is a real boolean.

Provide a JSON-friendly `to_dict()` with enum values serialized as strings and
steps/reason codes serialized as lists.

## Public Planner API

Implement:

```python
def plan_request(
    question: str,
    *,
    fallback: Callable[[str], tuple[ToolName, ...] | None] | None = None,
) -> Plan:
    ...
```

Rules:

1. Blank questions raise `ValueError` before the fallback is called.
2. Normalize case and whitespace for matching, but do not rewrite the question
   passed to the fallback.
3. Extract deterministic signals first.
4. If deterministic signals require tools, do not call the fallback.
5. If the input is a direct utterance, return `direct` and do not call fallback.
6. Only when no rule selects a tool may the optional fallback be called.
7. Call fallback at most once with the original question.
8. A valid fallback result is a non-empty tuple of unique `ToolName` values,
   with at most three items. Canonicalize its order before deriving the route.
9. If fallback is absent, returns `None`/empty, returns invalid data, or raises
   an exception, safely default to `document_only`.
10. Do not expose exception text in `reason_codes`.
11. Never allow fallback to choose `direct`; an ambiguous knowledge request
    defaults to the authoritative source-document path.
12. Return data only. Do not call Wiki, RAG, System, Ollama, API, or storage.

## Deterministic Signal Semantics

Marker tables must be module constants, grouped by intent. Keep them
domain-neutral: no student-only, school-only, or one-company vocabulary.

### Direct

Direct applies only to short standalone social utterances such as greetings,
thanks, or goodbye. Matching must be whole-utterance after trimming surrounding
punctuation.

`你好，请问年假有几天？` is not direct.

### Wiki overview

Wiki is required for explicit overview/synthesis intent, including markers such
as:

```text
概述 概览 总结 介绍一下 大概讲什么 整体说明 主要内容
关系 脉络 历史沿革 影响分析
```

Do not treat a generic `是什么` alone as a strong Wiki signal; it causes exact
clause questions such as `第三条是什么` to over-route.

### Source document

Document is required for exact wording, policy meaning, conditions, numbers,
exceptions, comparison, or document-version change. Marker groups include:

```text
原文 条款 第X条 依据 引用 页码 具体怎么写 明确规定
条件 要求 是否允许 多少天 比例 金额 上限 期限
区别 比较 差异 例外
制度 政策 办法 规则 手册 SOP 公告 通知
```

Policy/document nouns are context markers. When the question explicitly asks
only for an overview (for example `介绍一下退款政策`), they must not force a
second Document step. Strong exact markers still produce Wiki + Document.

Version/change intent such as `变化`, `变更`, `新旧`, or `版本对比` requires
both Wiki and Document: Wiki synthesizes; source documents verify.

### Current system state

System is required for the caller's or business system's current operational
state, not merely because a question contains `现在` or an entity such as
`订单`.

Use a conjunction of current/personal-state intent and system objects, plus a
small group of unambiguous state phrases.

Current/personal markers include:

```text
我的 本人 当前 现在 实时 目前 剩余 进度 到哪一步
是否到账 是否通过 我当前是否符合
```

System objects include:

```text
订单 库存 余额 物流 审批 工单 账户 积分 额度
排班 考勤 申请记录
```

Examples:

- `我的订单现在什么状态` -> System;
- `订单管理制度是什么` -> Document, not System;
- `现在的请假制度怎么规定` -> Document, not System;
- `制度怎么规定，我当前是否符合` -> Document + System;
- `最新公告是什么` -> Document with freshness, not System.

### Freshness

Set `requires_freshness` for explicit temporal intent such as `当前`, `现在`,
`实时`, `目前`, `最新`, or `截至`. This flag does not by itself select System.
For example, `最新公告` is a fresh Document request.

### Exact citation

Set `requires_exact_citation` for original wording, clauses, explicit citations,
pages, conditions, policy numbers, limits, deadlines, exceptions, or comparisons.
It implies Document unless the same numeric language is clearly describing a
current system value, such as `当前库存还有多少`.

## Reason Codes

Use stable machine-readable reason codes. Required codes include:

```text
direct_utterance
wiki_overview
document_exact
document_policy_context
document_version_change
system_current_state
freshness_requested
exact_citation_requested
fallback_selected
fallback_empty
fallback_invalid
fallback_exception
default_document
```

Rules:

- deterministic reason codes describe the signals that actually fired;
- no duplicate reason codes;
- preserve a stable order;
- when fallback selects tools, include `fallback_selected`;
- when fallback fails, include the corresponding fallback reason followed by
  `default_document`;
- when no fallback is provided for an ambiguous request, include only
  `default_document`.

## Fallback Boundary

M2 defines an injected callable boundary only. It does not implement an Ollama
classifier.

The planner must treat fallback output as untrusted:

- reject non-tuples;
- reject strings or arbitrary iterables;
- reject unknown/non-`ToolName` items;
- reject empty tuples, duplicates, or more than three items;
- canonicalize valid tuples before route lookup;
- catch fallback exceptions and use the safe Document default.

## Required Route Tests

Use table-driven tests. Cover at least these cases:

| Question | Expected route |
|---|---|
| `你好` | `direct` |
| `谢谢！` | `direct` |
| `你好，请问年假有几天？` | `document_only` |
| `这个制度大概讲什么` | `wiki_only` |
| `介绍一下退款政策` | `wiki_only` |
| `原文第三条具体怎么写` | `document_only` |
| `年假最多可以休多少天` | `document_only` |
| `最新公告是什么` | `document_only` |
| `我的订单现在什么状态` | `system_only` |
| `当前库存还有多少` | `system_only` |
| `订单管理制度是什么` | `document_only` |
| `现在的请假制度怎么规定` | `document_only` |
| `概述制度并引用关键条款` | `wiki_document` |
| `总结制度变化并分析影响` | `wiki_document` |
| `制度怎么规定，我当前是否符合` | `document_system` |
| `介绍审批流程，再看我的审批进度` | `wiki_system` |
| `总结制度、引用条款并查询我当前审批状态` | `wiki_document_system` |

Also test:

- every route matrix entry and canonical step order;
- Plan invariant failures;
- RequestSignals and Plan serialization;
- blank input;
- deterministic and direct requests never call fallback;
- ambiguous input with no fallback defaults to Document;
- valid fallback is called exactly once with the original question;
- fallback order is canonicalized;
- `None`, empty, invalid, duplicate, oversized, and exception fallback cases;
- no exception message leaks into reason codes;
- freshness and exact-citation flags for the examples above;
- marker tables contain no student-domain terms.

Tests must not call Ollama, network services, API endpoints, or a database.

## Import and Integration Boundary

- `orchestration.planner` may use only the Python standard library.
- It must not import `agent`, `api`, `rag`, `storage`, FastAPI, or requests.
- Existing production files must not import `orchestration.planner` in M2.
- `orchestration/__init__.py` may re-export the new public symbols and must
  remain side-effect free.

## Acceptance Commands

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  agent.py api.py app.py rag.py storage.py `
  orchestration\__init__.py `
  orchestration\contracts.py `
  orchestration\document_adapter.py `
  orchestration\planner.py

.\.venv\Scripts\python.exe -m unittest discover -v

git diff --check
git diff --name-only HEAD
git status --short --untracked-files=all
```

Acceptance conditions:

- all existing 50 tests pass;
- all M2 tests pass;
- changed paths are exactly the three allowed files;
- planner tests are fully offline;
- current API/Router/RAG/storage/SSE/Streamlit/Vue behavior is unchanged;
- no commit or push is performed.

## Explicit Non-Goals

Do not implement:

- an Ollama/LLM fallback adapter;
- tool execution or an Agent loop;
- Wiki or System adapters;
- sufficiency, conflict, citation, or authority resolution;
- modifications to `agent.decide_action()`;
- API/SSE/SQLite/frontend integration;
- RAG improvements from the frozen reference branch;
- evaluation dataset changes;
- commits or remote operations.

## Required Handoff

Stop after M2 and report:

- files changed;
- route table and signal behavior implemented;
- fallback behavior;
- exact commands, exit codes, and total test count;
- `git diff --stat` and `git status --short --untracked-files=all`;
- confirmation that production files were untouched;
- confirmation that no commit or push occurred;
- any ambiguity discovered in the route rules.
