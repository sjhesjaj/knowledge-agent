# M6B - Minimal API / SSE Integration

## Status

Draft for reviewer approval. One review round intended.

## Base

- Branch: `codex/wiki-agent-v1`
- Starting commit: `e8b568019aafc33f18c5da7fd48aae0b76e79039`
- Baseline: compile passes; 362/362 tests pass.

Read `AGENTS.md`, `docs/tasks/M6A_CONTROLLED_EXECUTOR.md`, `api.py`,
`rag.py`, `storage.py`, `tests/test_streaming.py`, and
`tests/test_knowledge_consistency.py` before editing.

## Goal

Put the M1-M6A chain behind the existing chat endpoints, opt-in, without
changing any current behavior:

```text
question -> plan_request -> ExecutionContext -> execute_plan
         -> PolicyDecision -> answer + programmatic [来源 N] -> JSON / SSE
```

## Trust Boundary

The goal is a demonstrable V1, not a module hardened against hostile internal
code. `orchestration/` (Planner, adapters, Executor, Policy) and `rag.py` are
**trusted**: M6B relies on their published contracts and does **not** re-mirror
their invariants, re-check their return shapes, or defend against a malicious
internal adapter.

Strict validation applies only where untrusted input crosses in:

- HTTP request fields (`mode`, `question`, ids);
- the user-supplied SKU;
- identity (there is none - see System Scope);
- anything reaching SQL;
- anything that would write to a database;
- anything returned to the client, including exceptions.

M6A already performs deeper internal checks. Those stay because they are
written and passing; M6B does not extend that style outward.

## Allowed Files

Modify or create only:

```text
api.py
chat_orchestration.py
tests/test_orchestrated_chat.py
```

The glue lives in `chat_orchestration.py` so `api.py` gains only wiring.
`orchestration/` stays a pure sidecar and is **not** modified: no changes to the
executor, policy, planner, or any adapter.

## Opt-in Switch

`ChatRequest` gains one backward-compatible field:

```python
mode: Literal["legacy", "orchestrated"] = "legacy"
```

- default `legacy`: byte-identical behavior to today, including `decide_action`,
  the response shape, and the SSE event sequence;
- `orchestrated`: the new chain;
- **no new endpoints.** `/api/chat` and `/api/chat/stream` keep sole ownership of
  the conversation lock, the knowledge-version check, and persistence.
  Duplicating an endpoint would duplicate those three, which is exactly how they
  drift apart.

Everything below applies to `orchestrated` only.

## Planning

- Call `plan_request(question)` with **no fallback**. M2 then defaults an
  ambiguous request to `document_only`; no LLM router is introduced.
- The plan's steps are executed by `execute_plan` unchanged.

## Channel Availability

A planned channel whose dependency is missing must not reach `execute_plan` -
M6A raises `ValueError` for that, which would surface as a 500. `api.py`
therefore checks availability **before** executing and, when a planned channel
is unavailable, returns a fixed refusal instead:

| Channel | Available when | Fixed message when not |
|---|---|---|
| `document_search` | the knowledge snapshot is non-empty | 当前没有可用的文档知识库。 |
| `wiki_query` | the Wiki collection loaded | 当前没有可用的 Wiki 页面。 |
| `system_query` | a SKU was extracted (below) | 请提供需要查询的 SKU。 |

These are returned as a normal answer with `route`, empty `sources`, and no
model call.

### Wiki loading

- `load_wiki_pages()` is called **once at module import** and cached in a
  module-level tuple, mirroring how `api.py` already loads chunks at import.
- A missing or invalid file must not crash startup: catch `OSError` and
  `ValueError`, leave the collection empty, and let the availability rule above
  produce the fixed message. Do not retry per request.

### Demo system connection

- The System channel uses the **isolated demo fixture only**:
  `system_fixtures/sample_business_system.sql`.
- Open a fresh `sqlite3.connect(":memory:")` **per request**, load the fixture
  into it, pass it to the executor, and close it in a `finally`. A per-request
  in-memory database is both thread-safe under FastAPI's threadpool and
  structurally incapable of writing anything persistent.
- **Never** touch `data/knowledge_agent.db`, `storage.SQLiteStorage`, or any
  other database. No new `.db` file is created.

## Snapshot Without Documents

Today both endpoints run, before any planning:

```python
if not local_chunks:
    raise HTTPException(409, "请先上传文档并建立知识库")
```

and `state_snapshot()` returns early when `chunks` is empty - without creating
the conversation, without validating ownership, and without reading history.
A Wiki-only or Inventory-only question would therefore be rejected before the
channel-availability check could ever run.

Rules:

- `legacy` keeps today's behavior exactly, including the 409 and the early
  return. Its error **priority** must not change either: with empty chunks the
  409 still precedes any ownership check.
- `orchestrated` with empty chunks must still validate conversation ownership,
  create the conversation, read history, and take the knowledge version.
- Empty `chunks` becomes an error only when the plan contains
  `document_search`, and then it is the fixed 当前没有可用的文档知识库。 -
  not a 409.
- Wiki-only and Inventory-only do not depend on any document upload.

Implement with a mode-aware helper, defaulting to legacy behavior:

```python
def state_snapshot(conversation_id, client_id, *, require_chunks: bool = True)
```

`require_chunks=True` keeps the early return; `require_chunks=False` always
performs ownership validation, history read, and version capture. Note the
deliberate consequence: in `orchestrated` a foreign conversation now yields 404
even with an empty knowledge base, because ownership is checked before anything
else. That is new-mode behavior only.

Required test: **empty chunks + `orchestrated` + `SKU-A100` -> System executes
and the answer is persisted.**

## System Scope

M6B opens exactly one operation:

```text
get_inventory_level
```

- **Not** `get_order_status`, **not** `get_approval_status`. Both are
  subject-scoped, and no trusted `subject_id` resolver exists; M6A recorded this
  as a product blocker and it still stands.
- No `client_id -> subject_id` mapping is implemented. `client_id` is a
  client-submitted string, not an identity.
- `ExecutionContext.subject_id` stays `None`.

### SKU extraction

Deterministic and offline. **No LLM, no fuzzy matching.**

- Pattern: case-insensitive `sku` , an optional single `-`, `_`, or space, then
  a letter and three digits - i.e. `SKU-A100`, `sku a100`, `SKU_A100`.
- Normalize to lowercase `sku-a100` form; that is the value passed as the `sku`
  parameter.
- Exactly one distinct SKU must be found. Zero, or two different ones, is
  treated as "no SKU" and yields the fixed message 请提供需要查询的 SKU。
- The extractor never invents a SKU and never falls back to a default.
- An unknown-but-well-formed SKU is passed through: M4 returns `EMPTY`, the
  policy returns `REFUSE`, and the fixed insufficient-evidence message is
  produced. Fabricating "not found" text is not the extractor's job.

## Answer Boundary

| Outcome | Behavior |
|---|---|
| `DIRECT` | reuse the existing direct reply; no tools, no evidence, no model call for evidence |
| `REFUSE` | a **fixed** message. Never call the model to guess around missing evidence. |
| `READY` | reuse `answer_structured` / `answer_stream` with Evidence converted to context |

Fixed refusal messages, chosen by the decision's reason codes, in this priority
order (first match wins, so the most actionable cause is shown):

```text
missing SKU              -> 请提供需要查询的 SKU。
tool error / missing     -> 部分信息源暂时不可用，无法给出完整回答。
empty result             -> 根据现有资料无法确定。
exact citation missing   -> 缺少可引用的原文依据，无法回答。
otherwise                -> 根据现有资料无法确定。
```

`REFUSE` still returns `route`, `steps`, and any `usable_evidence` gathered, so
the caller can see what *was* found.

### Evidence to context

`answer_structured(question, results, history)` takes
`list[tuple[Chunk, float]]`. Convert at the boundary:

- one synthetic `Chunk` per Evidence, in `usable_evidence` order:
  `Chunk(text=evidence.content, source=evidence.source, index=<1-based rank>)`;
- paired with a neutral score (the retrieval score is not a probability and is
  not reused for ranking here);
- these synthetic Chunks are **never** indexed, persisted, or written to
  storage. They exist only to build one prompt.

## Citations

- `[来源 N]` is assigned **programmatically**, N being the 1-based position in
  `usable_evidence`. The model never chooses a citation number.
- The same numbering is used for the synthetic Chunk `index`, so a number the
  model emits maps to the same source the API returns.
- M6C renders these. It does not redefine the protocol.

## Response Shape

`legacy` mode is unchanged. `orchestrated` mode returns the existing keys plus
two, so an old client still finds what it expects:

```json
{
  "answer": "...",
  "trace": {...},
  "sources": [...],
  "route": "wiki_document",
  "steps": ["wiki_query", "document_search"]
}
```

Each source is the legacy shape extended with provenance; fields that do not
apply to a channel are `null` rather than absent, so the key set is stable:

```json
{
  "rank": 1,
  "type": "document",
  "source": "sample_company_rules.md",
  "heading": "请假制度",
  "chunk_index": 12,
  "locator": "chunk:12",
  "score": 3.5,
  "content": "..."
}
```

- `type` is the Evidence `source_type` value;
- `heading`, `chunk_index`, and `score` are `null` for wiki and system evidence;
- `locator` carries the Evidence locator for every type.

### Trace

A **trimmed** trace, not the executor's full bundle trace:

```text
mode, route, steps, outcome, reason_codes,
executor_total_seconds, answer_seconds, total_seconds
```

Per-tool traces and per-step timings are **not** exposed. The privacy rules M6A
enforces still hold: no `subject_id` value (there is none), no parameter value,
no SQL, no exception text.

### SSE

Same event names and order as legacy: `status`, `sources`, `delta`, `done`, and
`error` on failure. `route` and `steps` ride on the `done` event's trace.
`sources` is emitted before the first `delta`, exactly as today.

A `REFUSE` or unavailable-channel answer is emitted as a single `delta` followed
by `done` - not as an `error` event. It is a valid answer, not a failure.

### Stream failure

The current stream handler emits `f"Agent 运行失败：{exc}"`, which returns raw
exception text to the client. That is one of the blocking criteria, so:

- `legacy` keeps its current message verbatim - changing it would be an API
  compatibility break, and it is pre-existing behavior;
- `orchestrated` emits a **fixed** payload with no interpolation:

  ```json
  {
    "code": "ORCHESTRATED_STREAM_ERROR",
    "message": "回答生成失败，请稍后重试。"
  }
  ```

- `str(exc)` is never concatenated, formatted, or logged into the event;
- no `done` event is emitted;
- `commit_exchange` is **not** called, so nothing is persisted;
- the conversation lock is still released in `finally`.

Required test: raise an exception carrying a unique secret string and assert the
full orchestrated SSE response body does not contain it.

## Preserved Behavior

Non-negotiable, and each has a test:

- the conversation lock is acquired and released identically in both modes;
- `commit_exchange` runs with the same snapshot version check, so an answer
  generated against a stale knowledge base is still rejected with 409;
- a failed stream still commits nothing;
- client scoping (`client_id`) on conversations is unchanged;
- the existing 362 tests pass untouched.

## Explicit Non-Goals

- assertion provider (the default empty provider stays) and therefore **no
  production conflict adjudication** - M6A recorded this; it is still true and
  must not be claimed as a shipped capability;
- LLM router fallback;
- an identity resolver, order or approval operations;
- Wiki compilation or editing;
- any `frontend/` change (M6C);
- changes to `orchestration/`, `rag.py`, `agent.py`, or `storage.py`.

## Required Tests

`tests/test_orchestrated_chat.py`, offline. Patch `answer_structured` /
`answer_stream` and the Ollama-backed calls; use a temp-file storage as
`tests/test_streaming.py` does, and the real in-memory demo fixture.

Representative scenarios, each asserting `route`, `steps`, and `sources`:

1. Wiki-only.
2. Document-only.
3. Wiki + Document.
4. Inventory System-only (`SKU-A100`).
5. Document + Inventory.
6. Missing SKU -> fixed message, System not executed.
7. Insufficient evidence -> `REFUSE`, fixed message, **no model call**.
8. `legacy` mode unchanged: same answer, same shape, `decide_action` still used.
9. SSE and non-streaming agree on `route`, `steps`, and `sources` for the same
   question.
10. Conversation lock: a second concurrent request gets 409 in both modes.
11. Knowledge-version: a mid-flight version bump still yields 409 and persists
    nothing.
12. Citations `[来源 N]` are assigned by position and match `sources` order.
13. No `.db` file is created; `data/knowledge_agent.db` is untouched by the
    orchestrated path.
14. SKU extraction: `SKU-A100`, `sku a100`, `sku_a100` all normalize; zero SKUs
    and two different SKUs both yield the fixed message.
15. **Empty chunks + `orchestrated` + `SKU-A100`**: System executes, the answer
    is persisted, and no 409 is raised. With `document_search` planned instead,
    the fixed 当前没有可用的文档知识库。 is returned - still not a 409.
16. Empty chunks + `legacy`: still 409, unchanged.
17. Empty chunks + `orchestrated` + a conversation owned by another client: 404.
18. **Orchestrated stream failure**: an exception carrying a unique secret
    yields the fixed `ORCHESTRATED_STREAM_ERROR` payload, the secret appears
    nowhere in the response body, no `done` event is emitted, nothing is
    persisted, and the conversation lock is released (a following request on the
    same session succeeds).
19. Legacy stream failure keeps its existing message.

## Acceptance Commands

```powershell
.\.venv\Scripts\python.exe -m py_compile agent.py api.py app.py rag.py storage.py chat_orchestration.py
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m unittest tests.test_orchestrated_chat -v
git diff --check
git status --short --untracked-files=all
```

Acceptance:

- existing 362 tests pass unchanged;
- new tests pass;
- changed paths are exactly the three allowed files;
- no commit or push.

## Blocking vs Backlog

Only these block acceptance:

1. a crash on normal input;
2. a break in existing API/SSE compatibility;
3. arbitrary SQL, or any business write (anything beyond
   `get_inventory_level` against the per-request in-memory demo database);
4. cross-user reading of real data;
5. a raw exception or obviously sensitive information returned to the client;
6. regression in the existing 362 tests.

**Backlog - recorded, not fixed, and not grounds for another round:**

- richer SKU parsing (multiple SKUs, fuzzy or aliased forms);
- cross-channel ranking or de-duplication of evidence;
- production conflict adjudication (needs a structured assertion source);
- a trusted `subject_id` resolver, and with it order/approval operations;
- trace richness (per-tool traces, per-step timings);
- internal-module hardening: mutated dataclasses, contract-violating internal
  adapters, exotic Mappings or cyclic structures, theoretical trace-encoding
  bypasses, duplicated internal constants such as `SYSTEM_TRACE_FIELDS`;
- multi-domain, distributed, or plugin concerns that do not exist yet.

Process: **one spec review round and one implementation review round.** P2/P3
findings are recorded in this backlog rather than reworked; only P0/P1 blocks
the commit.
