# M6A - Controlled Executor

## Status

Draft for reviewer approval. No implementation has been written.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `a5946e887529f99de7a7e9be8b596291c95e8f43`
- Baseline: Python compile passes; 311/311 unit/API tests pass.

Read `AGENTS.md`, `docs/V1_IMPLEMENTATION_PLAN.md`, the M1-M5 task documents,
`orchestration/contracts.py`, `orchestration/planner.py`,
`orchestration/document_adapter.py`, `orchestration/wiki_adapter.py`,
`orchestration/system_provider.py`, `orchestration/evidence_policy.py`,
`agent.py`, `api.py`, `storage.py`, `tests/test_streaming.py`, and
`tests/test_knowledge_consistency.py` completely before editing.

## Goal

Run a validated `Plan` against the three read-only tools, exactly once each, in
plan order, and hand back everything a later milestone needs to answer:

```text
Plan (M2)  +  ExecutionContext (injected)
                    |
        execute_plan  - one pass, fixed order, no retry
                    |
   {ToolName: ToolResult}   (M1/M3/M4 adapters)
                    |
        FactAssertions from a caller-supplied provider
                    |
        evaluate_evidence (M5)
                    |
   ExecutionBundle(plan, results, decision, trace)
```

M6A remains a **sidecar**. It does not touch `api.py`, SSE, `agent.py`,
`storage.py`, or the Vue client. It produces no answer text, no citations, and
no refusal wording.

- **M6B** connects this to the chat endpoints and produces answers/citations.
- **M6C** adds the frontend evidence-path display.

## Allowed Files

Modify or create only:

```text
orchestration/__init__.py
orchestration/executor.py
tests/test_executor.py
```

Do not modify `agent.py`, `api.py`, `rag.py`, `storage.py`, the `frontend/`
tree, any existing test, any M1-M5 orchestration module, README, or this task
document.

`orchestration/__init__.py` may only gain re-exports and must remain
side-effect free.

## Public API

```python
def execute_plan(
    question: str,
    plan: Plan,
    context: ExecutionContext,
    *,
    assertion_provider: AssertionProvider | None = None,
) -> ExecutionBundle:
    ...
```

### Preflight

**Every static error is raised before any adapter is called, before the
assertion provider is called, and before timing starts.** A request that cannot
possibly succeed must not half-run: partial execution produces misleading
traces, wastes a database round trip, and can leave a step's evidence in a
bundle that was never viable.

Preflight validates, in this order:

1. `question` is a non-empty, non-blank `str`; `plan` is a `Plan`; `context` is
   an `ExecutionContext`; `assertion_provider` is `None` or callable.
2. `len(plan.steps) <= 3`, and every step is a `ToolName`.
3. `knowledge_version` is `None` or a non-`bool` `int` that is `>= 0`. It is
   validated unconditionally, not per-planned-tool, because it always reaches
   the trace.
4. Dependencies for **every** planned step:
   - `document_search` planned: `chunks` is a non-empty `tuple` and every
     element is a `Chunk`;
   - `wiki_query` planned: `wiki_pages` is a non-empty `tuple` and every element
     is a `WikiPage`;
   - `system_query` planned: `system_connection` and `system_request` are both
     present.
5. `document_top_k` / `wiki_top_k` are non-`bool` `int` values `>= 1`, checked
   only for tools that are actually planned.
6. `SystemRequest` shape: it is a `SystemRequest`; `operation` is a
   `SystemOperation` member; `parameters` is a `Mapping`; every key is a `str`;
   every value is a **non-blank** `str`; no key is `subject_id`.
7. **Subject before merge**: if the operation's M4 whitelist includes
   `subject_id`, `context.subject_id` must be a non-blank `str`. This is checked
   **before** the merge, so a missing identity is reported as a missing identity
   rather than as a confusing "parameter set does not match" error one step
   later.
8. Merged parameter names equal `system_provider.OPERATION_PARAMETERS[operation]`
   exactly, after `subject_id` injection into the executor's own copy.

Only after all of that does the executor start `perf_counter` and invoke the
first tool.

`SystemRequest.parameters` is declared as
`field(default_factory=dict)` - never a shared mutable default.

**Composite-route consequence, and a required test:** a `wiki_document_system`
plan whose `SystemRequest` is malformed must call **Wiki and Document zero
times**. Preflight failure means nothing ran.

`SystemRequest.parameters` is copied into an executor-owned `dict` before
`subject_id` is merged. The caller's mapping is never mutated and never passed
onward by reference.

## Execution Model

Binding, and the whole point of the milestone:

- **One pass.** Every tool in `plan.steps` is invoked **exactly once**.
- **Fixed order.** Steps run in `plan.steps` order, which the planner already
  canonicalized to Wiki, Document, System. The executor never reorders.
- **No retry**, no backoff, no fallback tool, no supplementary query, and no
  second plan. A failed step stays failed for this request.
- **No loop.** The executor does not consult a model, does not re-plan, and has
  no notion of "try something else". There is no agent loop to bound because
  there is no loop.
- **At most three steps**, guaranteed by `Plan`'s own invariant; the executor
  additionally asserts `len(plan.steps) <= 3` rather than trusting it, since
  `Plan` is frozen but was built elsewhere.
- **Failures do not stop the pass.** If Wiki fails, Document and System still
  run. A complete result set makes the policy decision diagnostic instead of
  reporting only the first problem, and matches M5's all-checks-run posture.

## Injected Context

Nothing is discovered, opened, loaded, or read from a global. Everything the
tools need arrives in one frozen, keyword-only object:

```python
@dataclass(frozen=True, kw_only=True)
class ExecutionContext:
    chunks: tuple[Chunk, ...] = ()
    wiki_pages: tuple[WikiPage, ...] = ()
    system_connection: sqlite3.Connection | None = None
    subject_id: str | None = None
    system_request: SystemRequest | None = None
    document_top_k: int = 4
    wiki_top_k: int = 3
    knowledge_version: int | None = None
```

Rules:

- the executor **never** calls `load_wiki_pages()`, `sqlite3.connect()`,
  `SQLiteStorage`, or reads any file. The caller loads the Wiki collection and
  owns the connection's lifecycle; the executor neither opens nor closes it.
- `chunks` is the document corpus snapshot. `api.py` already takes such a
  snapshot under `state_lock` before answering; M6A assumes the caller passes
  that snapshot rather than a live list, and the field is a tuple so it cannot
  be mutated mid-execution.
- `knowledge_version` is carried through to the trace only. M6A does not
  implement the version-consistency check; that is `api.py`'s existing
  `commit_exchange` responsibility and stays in M6B.
- A planned step whose dependency is absent is a **configuration error**, not a
  runtime failure, and raises `ValueError` before anything executes:
  `document_search` planned with empty `chunks`; `wiki_query` planned with empty
  `wiki_pages`; `system_query` planned with `system_connection is None` or
  `system_request is None`. The caller controls both the plan and the context,
  so a mismatch is a wiring bug and must not be laundered into
  `ToolStatus.ERROR`.
- Context fields for tools that are **not** planned are ignored, not validated.

## System Operation and Parameters

This is the sharpest safety boundary in the milestone.

### Who produces them

The **caller** does, explicitly and structurally:

```python
@dataclass(frozen=True, kw_only=True)
class SystemRequest:
    operation: SystemOperation
    parameters: Mapping[str, str] = field(default_factory=dict)
```

- `operation` must be a `SystemOperation` member. A bare string is rejected.
- **M6A calls no model and accepts no raw model text.** The planner (M2) decides
  *whether* the System path is needed; it never names an operation or a
  parameter. M6A does not translate natural language into an operation and ships
  no such translator. The executor cannot *prove* what the caller's
  `SystemRequest` was derived from, so the obligation is stated plainly: **the
  caller is responsible for the trustworthiness of `SystemRequest`.** If a
  product wants question-to-operation mapping, that is a separate,
  separately-reviewed milestone with its own whitelist.
- **No SQL of any kind crosses this boundary.** M4 owns the SQL templates; the
  executor passes an enum member and a parameter mapping and nothing else.

### subject_id comes from a trusted identity resolver

- `subject_id` is supplied through `ExecutionContext` and must originate from a
  **trusted server-side identity resolver**. M6A deliberately does not define
  what that resolver is; it defines only that one is required.
- **`client_id` is not an identity.** It is a client-submitted request field
  ([api.py:45](../../api.py)), validated for shape and nothing else, and V1
  explicitly excludes authentication productization. **M6B must not use
  `payload.client_id` as a business `subject_id`.** Doing so would let any
  caller read any subject's records by typing a different string.
- Until a trusted mapping exists, **subject-scoped System operations must be
  unavailable** - the product does not offer them rather than offering them
  unsafely. `get_inventory_level` is unaffected: stock is not subject-scoped.
- `subject_id` is never taken from the question, from a model, or from
  `SystemRequest.parameters`.
- If `SystemRequest.parameters` contains a `subject_id` key, that is an
  integration error: raise `ValueError`. Allowing the caller's parameter map to
  carry it would let an untrusted layer choose whose records to read.
- For operations whose M4 whitelist requires `subject_id`, the executor merges
  `context.subject_id` into **its own copy** of the parameter mapping. If the
  operation requires it and `context.subject_id` is `None`, not a `str`, or
  blank, raise `ValueError`.
- For operations that do not take `subject_id` (`get_inventory_level`), the
  executor must **not** inject it - M4 rejects extra parameters, and inventory
  is deliberately not subject-scoped.

### Whitelist validation

- The executor validates `operation` against `SystemOperation` and validates the
  final merged parameter names against M4's published
  `OPERATION_PARAMETERS[operation]` **before** calling `system_query`, so a
  contract violation is a `ValueError` from the executor rather than a partially
  executed step.
- The executor must import M4's parameter table rather than restating it. Two
  copies of a whitelist will drift.
- `system_query` re-validates everything anyway; the executor's check is a
  second layer, not a replacement.

## Tool Invocation

Each step calls exactly one adapter, with arguments derived only from the plan
and the context:

| Step | Call |
|---|---|
| `wiki_query` | `wiki_query(question, context.wiki_pages, top_k=context.wiki_top_k, trace=<fresh dict>)` |
| `document_search` | `document_search(question, list(context.chunks), top_k=context.document_top_k, trace=<fresh dict>)` |
| `system_query` | `system_query(context.system_connection, request.operation, merged_parameters, trace=<fresh dict>)` |

Rules:

- `question` is the executor's `question` argument, unmodified. The executor
  does not rewrite, expand, translate, or summarize the query. M2 already
  decided what is needed; query rewriting is not in scope.
- Each call receives its **own fresh trace dict**, never a shared one, so one
  tool cannot overwrite another's trace fields.
- Blank `question` raises `ValueError` before any step runs.

## Error Boundary

A tool that fails must produce a `ToolResult` with `ToolStatus.ERROR`, not an
exception that aborts the request and not a silent empty result.

### Which exceptions become ERROR

- `ValueError` and `TypeError` raised by an adapter **propagate unchanged.**
  Those mean the executor called the adapter wrongly - a programmer error that
  must not be laundered into a runtime failure the user sees as "the system was
  unavailable".
- Every other exception (`sqlite3.Error`, `requests` failures, `OSError`,
  `RuntimeError`, `KeyError`, and so on) is converted into an `ERROR`
  `ToolResult` for that step, and the pass continues to the next step.
- `BaseException` subclasses that are not `Exception` (`KeyboardInterrupt`,
  `SystemExit`) are never caught.

### What the ERROR result may contain

`ToolResult` requires a non-empty `error_code` and `error_message`, and M5
forbids either from entering a `PolicyDecision`. Both constraints hold at once:

- `error_code` is a **stable taxonomy code**, not the exception's own code. Use
  a module constant such as `tool_execution_failed`.
- `error_message` is a **fixed template naming the tool and the exception class
  name only**, pinned exactly as:

  ```python
  message = tool.value + " failed with " + type(exc).__name__
  ```

  Use `type(exc).__name__`, never `repr(type(exc))` or a
  `__module__`-qualified name: module prefixes drift between Python versions and
  vendored packages, and the bare class name is the stable part.
  `ToolExecutionError.exception_type` uses the same `type(exc).__name__` value.
- **`str(exc)` is never captured**, anywhere: not in `error_message`, not in the
  trace, not in `ExecutionBundle`. Exception text can contain a query, a
  parameter value, a row, or a path, and this bundle is designed to be
  serialized and persisted.
- The exception class name is not sensitive and is the one detail that makes a
  failure actionable; it is the deliberate line between "diagnosable" and
  "leaky".

Whether to log the original exception at the process boundary is M6B's decision,
not M6A's. M6A drops it.

### Every ERROR result is sanitized

An adapter may also return a `ToolStatus.ERROR` result without raising. Passing
that result through verbatim would defeat the whole boundary: `to_dict()`
serializes `results`, and an adapter is free to put `str(exc)` into its own
`error_message`. **The executor therefore replaces the error payload of every
`ERROR` result that enters the bundle, whichever path produced it.**

| Origin | `error_code` | `error_message` | `tool_errors` |
|---|---|---|---|
| Exception caught by the executor | `tool_execution_failed` | `tool.value + " failed with " + type(exc).__name__` | one `ToolExecutionError` appended |
| `ERROR` returned by the adapter | `tool_reported_error` | `tool.value + " returned an error"` | **nothing appended** |

- The adapter's original `error_code`, `error_message`, **and trace** are
  **discarded, not copied anywhere**. An adapter is free to put exception text
  or a parameter value into its own trace, so carrying that trace through would
  route straight around the sanitization this section exists to perform.
- The replacement is a **new `ToolResult`**; the executor does not mutate the
  object the adapter returned.

**Validate the shape before sanitizing.** Sanitization sets `evidence=()` and
`trace={}`, which would silently *repair* a malformed adapter result and hide
the contract violation. So the executor checks first, and rejects rather than
launders:

- `tool_name` equals the expected tool;
- `evidence` is empty;
- `error_code` and `error_message` are both non-empty `str`.

A violation raises `ValueError` naming the tool and the offending field, and
**without echoing the payload**. This deliberately moves the check earlier than
M5: leaving it to the policy layer would mean sanitization had already erased
the evidence of the violation.

Only after that check does the executor build the replacement:

```text
ToolResult(
    tool_name = tool.value,
    status    = ToolStatus.ERROR,
    evidence  = (),
    error_code    = "tool_reported_error",
    error_message = tool.value + " returned an error",
    trace     = {},
)
```
- `tool_errors` keeps its narrow meaning: **only exceptions the executor caught
  and converted.** It is a log of executor-performed conversions, not a list of
  failed steps. The list of failed steps is `results`.
- Either way M5 sees an `ERROR` `ToolResult` and returns `REFUSE` with the
  `tool_error` reason code. The user-visible outcome is identical; only the
  provenance differs.

Required test: an adapter returning `ERROR` with **three** unique secrets - one
in `error_code`, one in `error_message`, and one inside its returned `trace` -
yields `REFUSE`, an empty `tool_errors`, a result whose `trace` is `{}`, and
none of the three secrets anywhere in `results`, the executor trace,
`repr(bundle)`, or `json.dumps(bundle.to_dict())`. A separate test asserts a
malformed adapter-returned `ERROR` (wrong `tool_name`, non-empty `evidence`, or
a blank error field) raises `ValueError` from the **executor**, and that the
message does not echo the payload.

## Fact Assertions

M5 refuses to guess facts, and M6A must not reintroduce guessing.

- The executor **never parses `Evidence.content`**, never applies keyword rules
  to it, and never calls a model to extract a fact.
- Assertions come from a caller-supplied provider, invoked **once**, after
  execution and before the policy call:

  ```python
  AssertionProvider = Callable[
      [Mapping[ToolName, ToolResult]], tuple[FactAssertion, ...]
  ]
  ```

- A provider is necessary rather than a plain tuple because a `FactAssertion`
  references an `Evidence` **object** that only exists after the tools have run.
- The default provider yields `()`. With no provider, the bundle simply carries
  no fact resolutions - which is the honest V1 state, since nothing in the
  product yet produces structured claims.
- **Consequence to record explicitly:** while M6B uses the default provider, the
  product chain performs **no conflict adjudication at all**. M5's authority
  resolution exists and is tested, but nothing exercises it in production. V1
  cannot be called complete until a structured assertion source is added and
  separately reviewed; until then, "conflicts are resolved deterministically" is
  a statement about the policy layer, not about the shipped pipeline, and must
  not be claimed as a product capability.
- **M6A ships no content-parsing provider**, not even a "simple" one. A provider
  must be deterministic and offline; that requirement is stated for the caller
  and is not enforceable from here.
- If the provider raises, the exception **propagates**. A broken provider is an
  integration error, not a tool failure, and must not silently degrade into "no
  facts asserted" - that would turn a bug into a quietly weaker answer.
- The returned assertions are passed to `evaluate_evidence` unchanged. M5
  already rejects assertions referencing evidence outside this execution, so
  M6A does not duplicate that check.

## ExecutionBundle

```python
@dataclass(frozen=True, kw_only=True)
class ExecutionBundle:
    plan: Plan
    results: Mapping[ToolName, ToolResult]
    decision: PolicyDecision
    trace: Mapping[str, object]
    tool_errors: tuple[ToolExecutionError, ...] = ()
```

```python
@dataclass(frozen=True, kw_only=True)
class ToolExecutionError:
    tool: ToolName
    exception_type: str      # class name only, never str(exc)
```

Rules:

- frozen and keyword-only, like every orchestration dataclass since M2;
- `tool_errors` is in `plan.steps` order;
- provide a JSON-friendly `to_dict()` that serializes plan, results, decision,
  trace, and tool errors, with enums as strings.

### What "immutable" actually means here

State the boundary honestly rather than implying more than Python delivers:

- `results` is a `types.MappingProxyType` over a dict the executor owns. That
  prevents **adding, removing, or replacing top-level keys**. It does **not**
  prevent `bundle.results[tool].status = ...` or
  `bundle.results[tool].evidence[0].content = ...`, because `ToolResult` and
  `Evidence` are ordinary mutable dataclasses. **Do not claim a consumer cannot
  modify the record of what happened.**
- `trace` is likewise exposed as a top-level `MappingProxyType`. Nested values
  inside it remain mutable and shared.
- Per-tool traces are **shallow** copies: top-level keys are independent,
  nested values are shared - the same promise M3 and M4 make.
- `list(context.chunks)` copies the **container only**. The `Chunk` objects are
  shared, and the adapters are trusted to treat them as read-only. M6A does not
  deep-copy a corpus per request.
- Tests must assert exactly these properties and must not assert deep
  immutability anywhere.

### Privacy boundary

A blanket "no parameter value anywhere" rule is **unsatisfiable and wrong**: M4
deliberately writes the business lookup key into Evidence, so
`get_order_status` produces `content = "订单 ord-1001 的当前状态为 已发货。"`,
`locator = "orders:ord-1001"`, and `metadata["record_id"] = "ord-1001"`. That
order number *is* the evidence for "which order". Banning it would ban the
answer.

The line runs between the **identity** and the **record being asked about**:

| Value | Evidence | executor trace / adapter trace | Elsewhere in bundle |
|---|---|---|---|
| `subject_id` value | **forbidden** | **forbidden** | **forbidden** |
| `order_id` / `approval_id` / `sku` value | **allowed** (M4 puts it there) | **forbidden** | allowed only via Evidence |
| SQL text | forbidden | forbidden | forbidden |
| Raw `str(exc)` | forbidden | forbidden | forbidden |
| Adapter's original error payload | forbidden | forbidden | forbidden |

Why `subject_id` is the exception: M4 never `SELECT`s it, so it appears in no
Evidence today. Keeping it out of everything is therefore both achievable and
worth enforcing - it is the one value that identifies *a person* rather than
*the thing they asked about*.

Test consequences, stated so the assertions stay satisfiable:

- the `subject_id` sentinel (for example `"subject-sentinel-7742"`) is asserted
  absent from `results`, `trace`, every `Evidence`, `repr(bundle)`, and
  `json.dumps(bundle.to_dict())`;
- a non-subject parameter sentinel (for example an `order_id` of
  `"ord-sentinel-8853"`) is asserted absent **from the executor trace and every
  adapter trace only**. It must **not** be asserted absent from Evidence or from
  the full `to_dict()`;
- the parameter *name* `"subject_id"` legitimately appears in M4's
  `system_parameter_names` and must never be asserted against;
- the executor's own fixed error messages and the exception *class name* are
  deliberately serialized - they are the safe diagnostic surface - and must not
  be asserted against either.

## Trace

The bundle's trace is a **new dict** the executor owns, exposed as a top-level
`MappingProxyType`. Fixed fields, all prefixed `executor_`. **Every nested
mapping is keyed by `tool.value`, the plain string - never by the `ToolName`
member**, so the trace is JSON-serializable without a conversion pass:

```python
{
    "executor_route": plan.route.value,
    "executor_planned_steps": [t.value for t in plan.steps],
    "executor_executed_steps": [...],           # tool.value, in invocation order
    "executor_step_status": {tool.value: status.value},
    "executor_step_seconds": {tool.value: float},
    "executor_tool_traces": {tool.value: <that tool's returned trace, copied>},
    "executor_total_seconds": float,
    "executor_outcome": decision.outcome.value,
    "executor_knowledge_version": context.knowledge_version,
}
```

Rules:

- per-tool traces are **shallow copies** of what each adapter returned, not
  aliases;
- the trace must never contain the **`subject_id` value**, **any** system
  parameter value (including `order_id` / `approval_id` / `sku`, which *are*
  allowed in Evidence - see the **Privacy boundary** table), SQL text, raw
  `str(exc)`, or an adapter's original error payload. The parameter *name*
  `"subject_id"` may legitimately appear inside `executor_tool_traces` via M4's
  `system_parameter_names`, and must not be asserted against;
- timings come from `time.perf_counter`, which is a monotonic duration source,
  not a wall clock. M6A still records no timestamps: `observed_at` semantics
  belong to the adapters.

## Behavior by Outcome

- **direct plan** (`plan.steps == ()`): no tool is invoked, `results` is empty,
  `tool_errors` is empty, and the bundle carries M5's `DIRECT` decision. A test
  asserts every adapter mock was called zero times.
- **partial tool failure**: remaining steps still run. **Every** failed step
  appears as a sanitized `ERROR` result in `results`; only steps whose failure
  the executor caught **as an exception** additionally appear in `tool_errors`.
  An adapter that returned `ERROR` itself is in `results` but not in
  `tool_errors`. M5 turns either into `REFUSE` with the distinguishing reason
  codes. The bundle is complete either way.
- **REFUSE**: returned normally, not raised. M6A produces no refusal text; the
  bundle carries `decision.outcome`, `missing_tools`, `tool_failures`, and
  `reason_codes` for M6B to render.
- **READY**: the bundle carries `usable_evidence` and `exact_citation_evidence`
  as M5 authorized them. M6A neither filters nor re-ranks them.

## Import and Integration Boundary

- `orchestration.executor` may import the standard library (`dataclasses`,
  `enum`, `sqlite3` for typing, `time`, `types`, `typing`) plus
  `orchestration.contracts`, `.planner`, `.document_adapter`, `.wiki_adapter`,
  `.system_provider`, `.evidence_policy`, and `rag.Chunk` for typing.
- It must not import `agent`, `api`, `storage`, `requests`, or FastAPI.
- No production file may import the executor in M6A.
- `orchestration/__init__.py` may re-export the new public symbols and must
  remain side-effect free.

## Required Tests

Create `tests/test_executor.py` using `unittest` and `unittest.mock`. Patch the
adapter functions **as imported into `orchestration.executor`**, so no test
reaches Ollama, the network, or a database. Cover at least:

1. Each of the eight routes: planned steps are invoked exactly once, in
   `plan.steps` order, and `results` keys match the plan.
2. `direct`: no adapter is called; bundle carries the `DIRECT` decision.
3. Call arguments: `question`, `top_k`, chunks, and Wiki pages arrive as
   injected; each adapter receives a distinct trace dict object.
4. No retry: an adapter that fails is called exactly once, and the remaining
   steps still run.
5. Ordering is preserved even when an earlier step fails.
6. `ValueError`/`TypeError` from an adapter propagate unchanged.
7. Any other exception becomes an `ERROR` `ToolResult` with the taxonomy
   `error_code`, and the pass continues.
8. A unique exception message (for example `"secret-9931"`) appears nowhere in
   `error_message`, `trace`, `tool_errors`, `repr(bundle)`, or
   `json.dumps(bundle.to_dict())`.
9. `KeyboardInterrupt` from an adapter is not caught.
7a. An adapter that **returns** `ToolStatus.ERROR` without raising, with unique
    secrets in its `error_code`, its `error_message`, **and its returned
    trace**: the bundle carries `tool_reported_error` /
    `tool.value + " returned an error"` / `trace == {}`, `tool_errors` stays
    empty, M5 yields `REFUSE`, and none of the three secrets appears in
    `results`, the executor trace, `repr(bundle)`, or
    `json.dumps(bundle.to_dict())`. The adapter's own returned object is not
    mutated.
7c. A **malformed** adapter-returned `ERROR` raises `ValueError` from the
    executor, before sanitization: wrong `tool_name`; non-empty `evidence`;
    blank or non-`str` `error_code`/`error_message`. The message names the tool
    and field but does not echo the payload.
7b. `error_message` equals `tool.value + " failed with " + type(exc).__name__`
    exactly, and `ToolExecutionError.exception_type` equals
    `type(exc).__name__` - no module prefix.
10. Missing dependencies raise `ValueError` before any adapter runs: Document
    planned with no chunks; Wiki planned with no pages; System planned with no
    connection; System planned with no `SystemRequest`.
10a. **Preflight is total**: for a `wiki_document_system` plan with a malformed
    `SystemRequest`, the Wiki and Document adapters are called **zero** times.
    Repeat for each preflight failure class (bad `top_k`, non-`str` parameter
    value, forbidden `subject_id` key, blank `subject_id`).
10b. Invalid `top_k` raises only when that tool is planned; an invalid
    `wiki_top_k` on a Document-only plan is ignored.
10d. **Zero-call preflight coverage** on a `wiki_document_system` plan, each
    asserting all three adapters were called zero times: a blank/whitespace
    system parameter value; `chunks` as a list instead of a tuple; a `chunks`
    element that is not a `Chunk`; `wiki_pages` as a list; a `wiki_pages`
    element that is not a `WikiPage`; `knowledge_version` as a `str`, a `bool`,
    or a negative `int`.
10e. `knowledge_version` is validated even when no tool needs it - for example
    on a Document-only plan - because it always reaches the trace.
10c. The caller's `SystemRequest.parameters` mapping is not mutated and is not
    the object handed to `system_query`.
11. Blank `question` raises `ValueError` before any adapter runs.
12. `subject_id` injection: a subject-scoped operation receives
    `context.subject_id` in its parameters; `get_inventory_level` does **not**
    receive one.
13. A `subject_id` key inside `SystemRequest.parameters` raises `ValueError`.
14. A subject-scoped operation with `subject_id=None` or blank raises
    `ValueError`.
15. A non-`SystemOperation` operation raises `ValueError`; parameter names are
    validated against M4's table before `system_query` is called.
16. The executor's parameter whitelist is M4's table, not a copy: assert the
    executor validates against `system_provider.OPERATION_PARAMETERS`.
17. Assertion provider is called exactly once, after execution, with the results
    mapping, and its output reaches `evaluate_evidence` unchanged.
18. Default provider yields no assertions and no resolutions.
19. A provider that raises propagates; the exception is not converted into an
    empty assertion set.
20. The executor never reads `Evidence.content`: assert the module source
    contains no `.content` access, and that a provider returning `()` produces
    no resolutions even when evidence contains fact-like text.
21. `ExecutionBundle` is frozen. `results` and `trace` reject **top-level** key
    assignment. Do **not** assert that nested `ToolResult`/`Evidence` objects are
    immutable - they are not, and a test claiming otherwise would be false.
22. `to_dict()` is JSON-serializable, and per the **Privacy boundary** table:
    the `subject_id` sentinel appears nowhere in the bundle; SQL text, raw
    `str(exc)`, and the adapter's original error payload appear nowhere; the
    non-subject parameter sentinel (`order_id`) is asserted absent from the
    executor trace and every adapter trace **only**, and is expected to appear
    in Evidence. Do not assert against the parameter *name* `"subject_id"`, the
    executor's fixed error messages, or the exception class name.
23. Trace contains the fixed `executor_*` fields and executed steps in order.
    Per-tool traces are shallow copies: a top-level key added to the adapter's
    returned trace afterwards does not appear in the bundle, while a nested
    mutable value is shared.
24. Inputs are unmodified: `plan`, `context`, `context.chunks`,
    `context.wiki_pages`, and the caller's `SystemRequest.parameters` compare
    equal to pre-call snapshots.
24a. Subject-scoped operations are unavailable without a trusted `subject_id`:
    `get_order_status` and `get_approval_status` raise `ValueError` when
    `context.subject_id` is `None`, blank, or not a `str`, while
    `get_inventory_level` succeeds unaffected.
25. The executor opens nothing: patch `sqlite3.connect` and
    `wiki_adapter.load_wiki_pages` to fail the test if called.
26. Existing 311 tests do not regress.
27. All tests offline: no Ollama, no network, no database file, no clock
    dependence beyond `perf_counter` durations.

## Acceptance Commands

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  agent.py api.py app.py rag.py storage.py `
  orchestration\__init__.py `
  orchestration\contracts.py `
  orchestration\document_adapter.py `
  orchestration\planner.py `
  orchestration\wiki_schema.py `
  orchestration\wiki_adapter.py `
  orchestration\system_provider.py `
  orchestration\evidence_policy.py `
  orchestration\executor.py

.\.venv\Scripts\python.exe -m unittest discover -v

.\.venv\Scripts\python.exe -m unittest tests.test_executor -v

git diff --check
git diff --name-only HEAD
git status --short --untracked-files=all
```

Acceptance conditions:

- all existing 311 tests pass with no regression;
- all new executor tests pass;
- changed paths are exactly the three allowed files;
- executor tests are fully offline and deterministic;
- `api.py`, SSE, `agent.py`, `storage.py`, and `frontend/` behavior is unchanged;
- `data/knowledge_agent.db` is not referenced, opened, or modified, and no new
  `.db` file is created;
- no commit or push is performed.

### Path gate

```text
 M orchestration/__init__.py
?? orchestration/executor.py
?? tests/test_executor.py
```

Any other path is an immediate rejection.

## Explicit Non-Goals

Do not implement:

- any `api.py`, SSE, endpoint, or Vue change (M6B / M6C);
- answer generation, citation numbering, or refusal wording;
- natural-language to operation/parameter translation;
- query rewriting, expansion, or translation;
- retries, backoff, supplementary queries, re-planning, or an agent loop;
- a content-parsing `FactAssertion` provider;
- Wiki loading, database connection management, or file IO inside the executor;
- knowledge-version consistency enforcement (stays in `api.py`);
- changes to M1-M5 modules or to `rag.py` / `agent.py` / `storage.py`;
- new third-party dependencies;
- commits, pushes, or remote operations.

## Required Handoff

- files changed;
- the execution order/one-pass guarantees and how they are tested;
- how context is injected and what raises on a missing dependency;
- who produces the System operation and parameters, how `subject_id` is
  injected, and how the whitelist is validated without duplicating M4's table;
- the exception taxonomy: what propagates, what becomes `ERROR`, and how
  exception text is kept out of every output;
- where `FactAssertion`s come from and the proof that `Evidence.content` is
  never parsed;
- the `ExecutionBundle` and trace field list;
- exact commands, exit codes, and total test count;
- `git diff --stat` and `git status --short --untracked-files=all`;
- confirmation that no commit or push occurred;
- any ambiguity discovered.

## Reviewer Rulings (settled)

These were raised as ambiguities and have been decided. They are binding.

1. **No supplementary query.** When an additional channel is needed, the Planner
   decides it in the *first* plan. The executor never runs a second pass.
2. **One `system_query` step, one `SystemRequest`.** A future route needing two
   system reads changes `Plan`, not just the executor.
3. **No question rewriting.** Wiki and Document both receive the original
   `question` unchanged.
4. **`list(context.chunks)` stays**, with a container-only copy promise. The
   `Chunk` objects are shared and the adapters are trusted to be read-only.
5. **`knowledge_version` is trace-only in M6A.** Version consistency stays with
   the existing `api.py` mechanism and is M6B's concern.

## Open Product Dependency

**A trusted `subject_id` resolver does not exist yet.** `client_id` is a
client-submitted string, not an authenticated identity, and V1 excludes
authentication productization. Until a resolver exists:

- `get_order_status` and `get_approval_status` cannot be offered in the product
  chain;
- `get_inventory_level` is unaffected and can ship;
- M6B must not paper over the gap by passing `payload.client_id` through.

This is a product decision, not an implementation detail, and blocks the
subject-scoped half of the System path regardless of how well M6A is built.
