# M1 - Evidence Contracts and Document Adapter

## Status

Ready for Coding Agent implementation after reviewer approval.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `0a9a80c715aa316444edcf5ab2346d3c6c005267`
- Baseline: Python compile passes; 25/25 unit/API tests pass.

Do not work in the frozen reference worktree and do not copy its files wholesale.

## Goal

Introduce the domain-neutral evidence boundary without changing production
behavior:

```text
retrieve_fast(question, chunks)
        |
document_search adapter
        |
ToolResult[Evidence]
```

Nothing in `api.py`, `agent.py`, `rag.py`, `storage.py`, Streamlit, Vue, or SSE
uses the adapter in M1. Integration is deliberately deferred.

## Allowed Files

Create only:

```text
orchestration/__init__.py
orchestration/contracts.py
orchestration/document_adapter.py
tests/test_evidence_adapter.py
```

Do not modify any existing file, including this task document.

## Required Contracts

### SourceType

A string enum with exactly:

```text
wiki
document
system
```

### ToolStatus

A string enum with exactly:

```text
ok
empty
error
```

### Evidence

Implement as a standard-library dataclass. Required fields:

```text
content: str
source_type: SourceType
source: str
locator: str | None
version: str | None
observed_at: str | None
authority: int
confidence: float | None
metadata: dict[str, object]
```

Rules:

- `content` and `source` must be non-empty after trimming;
- `authority` must be an integer from 0 through 100;
- `confidence`, when present, must be from 0.0 through 1.0;
- default optional values are `None`;
- default metadata is a new empty dictionary per instance;
- provide a JSON-friendly `to_dict()` method whose enum values are strings;
- do not auto-fill `observed_at` with query time: that would falsely describe
  source freshness.

### ToolResult

Implement as a standard-library dataclass. Required fields:

```text
tool_name: str
status: ToolStatus
evidence: tuple[Evidence, ...]
error_code: str | None
error_message: str | None
trace: dict[str, object]
```

Rules:

- `tool_name` must be non-empty;
- `ok` requires at least one evidence item and no error fields;
- `empty` requires no evidence and no error fields;
- `error` requires no evidence plus non-empty `error_code` and
  `error_message`;
- default evidence is an empty tuple;
- default trace is a new dictionary per instance;
- provide a JSON-friendly `to_dict()` method.

Use `__post_init__` for these invariant checks. Do not add Pydantic or another
dependency.

## Document Adapter

In `orchestration/document_adapter.py`, implement:

```python
def document_search(
    question: str,
    chunks: list[Chunk],
    *,
    top_k: int = 4,
    trace: dict | None = None,
) -> ToolResult:
    ...
```

Behavior:

1. Reject blank `question` with `ValueError`.
2. Reject `top_k < 1` with `ValueError`.
3. Call the existing `rag.retrieve_fast(question, chunks, top_k=top_k,
   trace=trace)` exactly once.
4. Preserve result ordering.
5. Return `ToolStatus.EMPTY` when retrieval returns no items.
6. Map every `(Chunk, score)` to one `Evidence`:
   - `content = chunk.text`
   - `source_type = SourceType.DOCUMENT`
   - `source = chunk.source`
   - `locator = f"chunk:{chunk.index}"`
   - `version = None`
   - `observed_at = None`
   - `authority = 80`
   - `confidence = None`
   - metadata contains `rank`, `chunk_index`, and raw `retrieval_score`
7. Rank starts at 1.
8. Use tool name `document_search`.
9. The returned trace must be a shallow copy of the supplied trace after
   retrieval, so later caller mutations do not change the ToolResult.
10. Let retrieval exceptions propagate. M1 must not invent an error policy or
    convert infrastructure failure into empty evidence.

Important: a BM25/vector/RRF score is not a calibrated probability. It belongs
in `metadata["retrieval_score"]`; it must never be copied into `confidence`.

## Import Boundary

- `orchestration.document_adapter` may import `Chunk` and `retrieve_fast` from
  `rag`.
- `rag.py` must not import anything from `orchestration`.
- `api.py` must not import the new adapter in M1.
- `orchestration/__init__.py` may re-export contracts and `document_search`, but
  it must not perform work at import time.

## Required Tests

Use `unittest` and `unittest.mock`. Tests must cover at least:

1. Evidence validation for empty text/source, authority bounds, and confidence
   bounds.
2. Evidence `to_dict()` enum and metadata serialization shape.
3. ToolResult invariant validation for `ok`, `empty`, and `error`.
4. ToolResult `to_dict()` nested evidence shape.
5. Two retrieval results map in original order with ranks 1 and 2.
6. Raw retrieval score is metadata and confidence remains `None`.
7. Empty retrieval maps to `ToolStatus.EMPTY`.
8. Blank questions and invalid `top_k` raise `ValueError` before retrieval.
9. `question`, `chunks`, `top_k`, and the original trace object reach
   `retrieve_fast` exactly once.
10. ToolResult trace is copied after retrieval.
11. Retrieval exceptions propagate unchanged.

Patch the adapter module's imported `retrieve_fast`; do not call Ollama,
network services, or a real database.

## Acceptance Commands

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  agent.py api.py app.py rag.py storage.py `
  orchestration\__init__.py `
  orchestration\contracts.py `
  orchestration\document_adapter.py

.\.venv\Scripts\python.exe -m unittest discover -v

git diff --check
git diff --name-only
git status --short
```

Acceptance conditions:

- all existing 25 tests pass;
- all new adapter tests pass;
- changed paths are exactly the four allowed files;
- no Ollama or network calls occur in new tests;
- production API, SSE, storage, RAG, Router, Streamlit, and Vue behavior is
  unchanged;
- no commit or push is performed.

## Explicit Non-Goals

Do not implement:

- `wiki_query` or Wiki storage;
- `system_query`;
- Router/Planner changes;
- evidence merging, conflict resolution, or sufficiency checks;
- API/source payload integration;
- database migrations;
- frontend changes;
- bge-m3 migration;
- timeout/grounding ports from the reference branch;
- changes to existing evaluation datasets or thresholds.

## Required Handoff

Stop after M1 and report:

- files changed;
- exact tests and exit codes;
- total test count;
- `git diff --stat`;
- `git status --short`;
- confirmation that production files were untouched;
- confirmation that no commit or push occurred;
- any ambiguity discovered in the contract.
