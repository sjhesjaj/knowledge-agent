# M4 - Read-only System Provider

## Status

Ready for Coding Agent implementation after reviewer approval.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `668214cd1a465cd2a13c8c61492a4bbcc8e8a6c6`
- Baseline: Python compile passes; 195/195 unit/API tests pass.

Read `AGENTS.md`, `docs/V1_IMPLEMENTATION_PLAN.md`, `docs/tasks/M1_EVIDENCE_ADAPTER.md`,
`docs/tasks/M2_ROUTER_PLANNER.md`, `docs/tasks/M3_READ_ONLY_WIKI.md`,
`orchestration/contracts.py`, `orchestration/document_adapter.py`,
`orchestration/wiki_adapter.py`, `orchestration/planner.py`, `storage.py`, and
`tests/test_storage.py` completely before editing.

## Goal

Add the third evidence path: a standalone, read-only SQLite provider that
answers **what is true right now** for a business record, and returns it through
the existing `ToolResult[Evidence]` contract.

```text
sqlite3.Connection (caller-owned, fixture-loaded)
        |
system_query(connection, operation, parameters)
        |
   whitelisted SELECT, ? bindings only
        |
   ToolResult[Evidence]
```

System evidence is authoritative for **current operational state only**. It
never explains what a policy means - that remains the source document's job.

Nothing in `api.py`, `agent.py`, `rag.py`, `storage.py`, the planner, Streamlit,
Vue, or SSE uses the provider in M4. Integration is deliberately deferred.

## Allowed Files

Modify or create only:

```text
orchestration/__init__.py
orchestration/system_provider.py
system_fixtures/sample_business_system.sql
tests/test_system_provider.py
```

Do not modify `storage.py`, `orchestration/contracts.py`,
`orchestration/document_adapter.py`, `orchestration/wiki_adapter.py`,
`orchestration/wiki_schema.py`, `orchestration/planner.py`, existing tests,
production files, README, or this task document.

`orchestration/__init__.py` may only gain re-exports and must remain
side-effect free.

## Isolation Boundary

This is a **separate demonstration system**, not the product's own database.

- Do not read, write, extend, or reuse any table in `storage.py`
  (`knowledge_chunks`, `conversations`, `messages`, `app_meta`).
- `data/knowledge_agent.db` already exists in this worktree (gitignored via the
  `data/` rule). It is existing product state: **leave it exactly as it is**. Do
  not delete, move, rewrite, or "clean up" that file or the `data/` directory.
- M4 source, fixture, and tests must never reference, open, or connect to the
  product database. In particular, **`tests/test_system_provider.py` must not
  contain that path as a literal and must not read that file at all** - not even
  to hash it. Proving the file was untouched is done from *outside* the test
  process, by the PowerShell commands in **Path gate**.
- Do not create any **new** `.db` file anywhere - specifically not in the
  repository root, `orchestration/`, `tests/`, `system_fixtures/`, or `data/`.
- Do not import `storage`, `rag`, `agent`, `api`, FastAPI, or `requests`.
- The provider **never creates tables, inserts, updates, or loads the fixture**.
  It receives an already-open, already-populated `sqlite3.Connection`.
- The provider must not open or close connections, and must not mutate
  caller connection state (do not set `row_factory`, do not run `PRAGMA`).
- Tests use `sqlite3.connect(":memory:")` **only**. Unlike
  `tests/test_storage.py`, which uses temporary files, M4 must not create a
  database file at all - not in `data/`, not in a temp directory.

## Whitelisted Operations

Define a string enum `SystemOperation` in `orchestration/system_provider.py`
with exactly:

```text
get_order_status
get_inventory_level
get_approval_status
```

There is no natural-language SQL, no arbitrary table name, no arbitrary column
name, no arbitrary `WHERE`, and no caller-supplied SQL string of any kind.

## Demonstration Fixture

Create `system_fixtures/sample_business_system.sql` containing plain SQLite DDL
and `INSERT` statements for at least:

```sql
CREATE TABLE orders (
    order_id   TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE inventory (
    sku        TEXT PRIMARY KEY,
    quantity   INTEGER NOT NULL CHECK (quantity >= 0),
    unit       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE approvals (
    approval_id  TEXT PRIMARY KEY,
    subject_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_step TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

### Record identity

`order_id`, `sku`, and `approval_id` are **primary keys**. Every other business
column and `updated_at` are `NOT NULL`, so no query can produce a record whose
state is partly unknown. `quantity` is an integer constrained to be
non-negative; `0` is a valid stock level, not a missing one.

Because the lookup key of each operation is that table's primary key, every
whitelisted query returns **at most one row**, which is what makes "one record
produces one Evidence" well defined. The provider must still assert this rather
than assume it: if a SELECT ever returns more than one row, raise a `ValueError`
naming the operation instead of silently taking the first.

Rules:

- all data is **fictional demonstration data**. No real user, customer, order,
  or business record, and no personal data of any kind;
- subject identifiers are obviously synthetic, for example `subject-001`;
- `updated_at` values are fixed ISO 8601 strings written into the file. They are
  never generated at load time, because `observed_at` must be reproducible;
- include at least **two distinct `subject_id` values** with records in both
  `orders` and `approvals`, so cross-subject isolation is testable;
- include at least two `inventory` rows, at least one with quantity `0`, so the
  "record exists but is zero" case is distinguishable from "no record";
- no `PRAGMA`, no `ATTACH`, no triggers, no views;
- the file must load cleanly via `connection.executescript(...)` into a fresh
  `:memory:` database.

The provider module may expose the fixture's location as a `Path` constant
(for example `SAMPLE_FIXTURE_PATH`) for tests and future callers, but **must not
read or execute it**, at import time or ever.

## Query API

```python
SYSTEM_TOOL_NAME = "system_query"
SYSTEM_AUTHORITY = 100
SYSTEM_SOURCE = "demo-business-system"
AUTHORITY_SCOPE = "current_operational_state"

def system_query(
    connection: sqlite3.Connection,
    operation: SystemOperation,
    parameters: dict[str, str],
    *,
    trace: dict | None = None,
) -> ToolResult:
    ...
```

### Parameter contracts

Each operation has a fixed, exact parameter whitelist. These must be module
constants, declared as **ordered tuples**, so SQL binding order comes from the
declaration and never from dict iteration order:

| Operation | Required parameters (exact set) |
|---|---|
| `get_order_status` | `subject_id`, `order_id` |
| `get_inventory_level` | `sku` |
| `get_approval_status` | `subject_id`, `approval_id` |

`get_order_status` and `get_approval_status` **must** take `subject_id` and must
scope the `WHERE` clause by it, so one subject can never read another subject's
record. `get_inventory_level` takes `sku` only: stock level is not owned by a
subject, and accepting a `subject_id` there would imply an access rule that does
not exist.

### Validation order

All of the following raise `ValueError` **before any SQL is prepared or
executed**:

1. `operation` is not a `SystemOperation` member (including a bare string such
   as `"get_order_status"`, which must be rejected - the enum is the contract);
2. `parameters` is not a `dict`;
3. a required parameter is missing;
4. an extra parameter is present (the set must match exactly, not merely
   contain the required names);
5. a parameter value is not a `str` (integers, booleans, `None`, lists, and
   dicts are all rejected; note `isinstance(True, str)` is `False`);
6. a parameter value is empty or whitespace-only.

Error messages must name the operation and the offending parameter, for example
`get_order_status.parameters.subject_id`. They must **not** contain the
parameter's value.

## SQL Safety

- One SQL template per operation, selected from a module-level whitelist keyed
  by `SystemOperation`. The caller cannot influence which template is used
  beyond choosing an enum member.
- Every value is bound through `?` placeholders. **No string concatenation, no
  `%`, no `.format()`, and no f-string may participate in building SQL.**
- Each template is a **single `SELECT`**. The provider executes exactly one
  statement per call and never uses `executescript` or `executemany`.
- No `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `CREATE`, `DROP`, `ALTER`,
  `PRAGMA`, or `ATTACH` appears anywhere in the module.
- Read result columns **positionally**, using a declared column tuple per
  operation. Because `connection.execute()` creates a cursor that inherits the
  caller's `connection.row_factory`, the provider must use its own cursor and
  clear the factory on it:

  ```python
  cursor = connection.cursor()
  try:
      cursor.row_factory = None
      cursor.execute(sql_template, bindings)
      rows = cursor.fetchall()
  finally:
      cursor.close()
  ```

  This never assigns to `connection.row_factory`, so the caller's setting is
  left exactly as it was, and rows are always positional tuples.
- The provider calls `connection.cursor()` **only after** every input check has
  passed. A contract violation must not reach the connection at all.
- The cursor is closed in a `finally`, so it is released even when the database
  raises.
- A SQL-injection string supplied as a parameter value is treated as an ordinary
  value. `get_order_status` with `order_id="' OR '1'='1"` must return
  `ToolStatus.EMPTY`, not every row.
- `sqlite3` exceptions propagate unchanged. A database failure must never be
  reported as `ToolStatus.EMPTY`, and must never be swallowed into
  `ToolStatus.ERROR`.

## Evidence Mapping

One row produces one `Evidence`:

```text
content      = per-operation template, built only from row values
source_type  = SourceType.SYSTEM
source       = SYSTEM_SOURCE ("demo-business-system")
locator      = "<table>:<record_id>"   e.g. "orders:ord-1001"
version      = None
observed_at  = the row's updated_at, verbatim from the database
authority    = SYSTEM_AUTHORITY (100)
confidence   = None
metadata     = {
    "operation": <operation value>,
    "record_id": <order_id | sku | approval_id>,
    "authority_scope": AUTHORITY_SCOPE,
    ... structured business fields for that operation ...
}
```

Content templates must be module constants so the wording cannot drift:

```text
get_order_status      订单 {order_id} 的当前状态为 {status}。
get_inventory_level   SKU {sku} 的当前库存为 {quantity} {unit}。
get_approval_status   审批 {approval_id} 的当前状态为 {status}，当前环节为 {current_step}。
```

Structured business fields per operation:

```text
get_order_status      status
get_inventory_level   quantity, unit
get_approval_status   status, current_step
```

Rules:

- **`observed_at` must come from the row's `updated_at`.** Never use
  `datetime.now`, and do not import a clock into this module: query time
  describes when we looked, not when the state was true. A test must assert the
  value equals the fixture's literal string.
- `version` is `None`. A live record has no document version.
- `confidence` is `None`. A row is not a probability.
- `authority` is `100`, and `metadata["authority_scope"]` is
  `"current_operational_state"`. The module docstring must state that this
  authority applies to live state only and never to policy meaning. Tests must
  assert the ordering `WIKI_AUTHORITY < DOCUMENT_AUTHORITY < SYSTEM_AUTHORITY`
  against the other adapters' constants, not just the literal `100`, so a future
  edit cannot silently invert the authority model.
- **`subject_id` must not appear in `Evidence`** - not in `content`, not in
  `locator`, not in `metadata`. The caller already supplied it; echoing it back
  into an evidence payload that may be logged or persisted spreads the
  identifier for no benefit.

## ToolResult

- `tool_name` is `"system_query"`.
- A matched record yields `ToolStatus.OK` with one Evidence item.
- A valid query that matches no record yields `ToolStatus.EMPTY` with no
  evidence and no error fields.
- Input errors raise `ValueError`; database errors propagate. Neither is
  converted into `EMPTY`, and `ToolStatus.ERROR` is not used to absorb them.

## Trace

The returned `ToolResult.trace` must be a **new dict** built exactly as:

```python
{
    **trace_copy,                                   # {} when trace is None
    "system_operation": operation.value,
    "system_parameter_names": sorted(parameters),   # names only, never values
    "system_rows_matched": <rows returned by the SELECT>,
    "system_returned_evidence": len(evidence),
}
```

Rules:

- these four keys are the complete, fixed set this provider adds; do not add or
  rename fields;
- if the caller's trace already contains one of these keys, **the provider's
  computed value wins** (the spread comes first, as written above);
- the trace must never contain parameter **values**, `subject_id`, any SQL text,
  or any exception text;
- the caller's dict is never mutated;
- the copy is **shallow, and only top-level isolation is promised**, exactly as
  in M3. Nested mutable values stay shared; do not use `deepcopy`, and tests
  must not assert deep isolation.

## Import and Integration Boundary

- `orchestration.system_provider` may import only the standard library
  (`sqlite3`, `enum`, `pathlib`, `typing`) plus `orchestration.contracts`.
- It must not import `storage`, `rag`, `agent`, `api`, `planner`, FastAPI, or
  `requests`.
- Existing production files must not import the provider in M4.
- `orchestration/__init__.py` may re-export the new public symbols and must
  remain side-effect free.

## Required Tests

Create `tests/test_system_provider.py` using `unittest`. Load the fixture into
`sqlite3.connect(":memory:")` in `setUp`; create no files. Cover at least:

1. Each of the three operations returns the expected record with the correct
   `content`, `locator`, and structured metadata.
2. **Subject isolation**: `get_order_status` with subject A and an order owned
   by subject B returns `EMPTY`; same for `get_approval_status`.
3. An unknown `order_id` / `sku` / `approval_id` returns `EMPTY` with no error
   fields and `tool_name == "system_query"`.
4. An inventory row with quantity `0` returns `OK` (present-and-zero is not
   absent).
5. Unknown operation raises `ValueError`, including a bare string
   `"get_order_status"` instead of the enum member.
6. Missing, empty, whitespace-only, and non-`str` parameter values each raise
   `ValueError`; table-driven over every operation and every parameter, crossed
   with the values `1`, `True`, `None`, `[]`, `{}`.
7. **Extra parameters are rejected**, including `subject_id` passed to
   `get_inventory_level`.
8. `parameters` that is not a dict raises `ValueError`.
9. All input errors are raised **before the connection is touched**: patch
   `connection.cursor` and assert it was never called. (Monitor `cursor.execute`
   rather than `connection.execute` - the provider uses its own cursor.)
9a. **Caller `row_factory` is isolated and preserved**: set a custom
   `connection.row_factory` that returns a `dict`, run all three operations, and
   assert they still work and produce Evidence identical to the default-factory
   case - the provider deliberately *ignores* the caller's factory on its own
   cursor so rows stay positional tuples. Then assert `connection.row_factory`
   is still the *same object* after the call: ignored for row shape, never
   overwritten on the connection.
9b. The cursor is closed even when the database raises, and no cursor is opened
   when input validation fails.
10. Error messages name the operation and parameter but never contain the
    parameter's value.
11. **Injection**: `order_id="' OR '1'='1"`, `subject_id="x'; DROP TABLE orders;--"`,
    and `sku="%"` each return `EMPTY`; afterwards all three tables still have
    their original row counts and contents.
12. **Parameterization is real**: capture the SQL passed to `cursor.execute` and
    assert it contains `?`, contains no interpolated parameter value, and that
    the bindings were passed as the second argument; assert the module source
    contains no f-string/`%`/`.format(` SQL construction.
13. **No writes**: snapshot every table before and after a batch of queries and
    assert contents are identical, and assert `connection.total_changes` is
    unchanged across all queries.
14. **No write SQL in the module**: the provider's source contains none of
    `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `CREATE`, `DROP`, `ALTER`,
    `PRAGMA`, `ATTACH`, `executescript`, or `executemany`.
15. `observed_at` equals the fixture's literal `updated_at` for that row, and
    the module source contains no `datetime`/`time` import.
16. `authority == 100`, `metadata["authority_scope"] == "current_operational_state"`,
    and `WIKI_AUTHORITY < DOCUMENT_AUTHORITY < SYSTEM_AUTHORITY`.
17. `confidence is None` and `version is None`.
18. `subject_id` appears nowhere in the returned Evidence - not in `content`,
    `locator`, or `metadata` (check the serialized `to_dict()` too).
19. Trace contains exactly the caller's keys plus the four `system_` fields,
    with `system_parameter_names` sorted; a colliding caller key is overridden;
    the caller's dict is not mutated; top-level isolation holds after the call.
20. Trace leaks nothing sensitive: the serialized trace contains no parameter
    value, no `subject_id`, no `SELECT`, and no table name.
21. **Database errors propagate**: querying a connection whose tables were
    dropped, or a closed connection, raises `sqlite3.Error` (or
    `sqlite3.ProgrammingError`) rather than returning `EMPTY`.
21a. **Multi-row defense**: in a separate `:memory:` connection, create
    same-shaped tables **without** the primary-key constraint, insert two rows
    matching the same lookup key, and assert that the operation raises a
    `ValueError` whose message names the operation. Assert it does **not**
    silently return the first row, and that the cursor was still closed. Cover
    at least one subject-scoped operation and `get_inventory_level`.
22. The provider's module source contains no `import storage`, no
    `sqlite3.connect`, no `.db` string literal, and none of the `storage.py`
    table names (`knowledge_chunks`, `conversations`, `messages`, `app_meta`).
    Checking for the absence of any `.db` literal covers the product database
    without naming its path, keeping that path out of the test file entirely.
23. Fixture hygiene: the fixture loads into `:memory:`, defines the three
    required tables with the required columns, and contains at least two
    distinct `subject_id` values.
23a. **Record identity**: `PRAGMA table_info` (run by the *test*, not the
    provider) confirms `order_id`, `sku`, and `approval_id` are primary keys and
    that every other column is `NOT NULL`; inserting a duplicate primary key
    raises `sqlite3.IntegrityError`; inserting a negative `quantity` raises
    `sqlite3.IntegrityError`.
23b. **Nothing is persisted**, asserted entirely in-process without reading the
    product database:
    every connection the tests open is `":memory:"`;
    the provider itself never calls `sqlite3.connect` (assert against the module
    source, and patch `sqlite3.connect` to fail the test if it is ever invoked);
    after the module's tests run, no `*.db` file exists in the repository root,
    `orchestration/`, `tests/`, or `system_fixtures/`.
    The product database's SHA-256, size, and modification time are **not**
    checked here - that comparison belongs to the external PowerShell commands
    in **Path gate**, outside the test process.
24. All tests are offline: no Ollama, no network, no API endpoint, no file-backed
    database.

## Acceptance Commands

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  agent.py api.py app.py rag.py storage.py `
  orchestration\__init__.py `
  orchestration\contracts.py `
  orchestration\document_adapter.py `
  orchestration\planner.py `
  orchestration\wiki_schema.py `
  orchestration\wiki_adapter.py `
  orchestration\system_provider.py

.\.venv\Scripts\python.exe -m unittest discover -v

.\.venv\Scripts\python.exe -m unittest tests.test_system_provider -v

.\.venv\Scripts\python.exe -c "import sqlite3, pathlib; c = sqlite3.connect(':memory:'); c.executescript(pathlib.Path('system_fixtures/sample_business_system.sql').read_text(encoding='utf-8')); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))"

git diff --check
git diff --name-only HEAD
git status --short --untracked-files=all
```

Acceptance conditions:

- all existing 195 tests pass with no regression;
- all new provider tests pass;
- the fixture loads into `:memory:` and reports the three expected tables;
- changed paths are exactly the four allowed files and nothing else;
- provider tests are fully offline and create no database file;
- current API, SSE, storage, RAG, planner, Wiki, Streamlit, and Vue behavior is
  unchanged;
- no commit or push is performed.

### Path gate

`git status --short --untracked-files=all` must list only:

```text
 M orchestration/__init__.py
?? orchestration/system_provider.py
?? system_fixtures/sample_business_system.sql
?? tests/test_system_provider.py
```

Any other path is an immediate rejection.

The pre-existing `data/knowledge_agent.db` is gitignored, so it never appears in
`git status` and is **not** a gate failure.

**The hash check must wrap the M4-only test run, never `unittest discover`.**
Importing `api.py` executes `storage = SQLiteStorage()` at module scope
(`api.py:37`), whose `_initialize()` runs `PRAGMA journal_mode = WAL`,
`CREATE TABLE IF NOT EXISTS`, and `PRAGMA user_version = 1`. That rewrites the
file's header and modification time on **any** run of the existing API tests.
This is a pre-existing baseline side effect, unrelated to M4, and must not be
used to fail this milestone.

Record the three values, run only the M4 module, then compare:

```powershell
Get-FileHash data\knowledge_agent.db -Algorithm SHA256
(Get-Item data\knowledge_agent.db).Length
(Get-Item data\knowledge_agent.db).LastWriteTimeUtc

.\.venv\Scripts\python.exe -m unittest tests.test_system_provider -v

Get-FileHash data\knowledge_agent.db -Algorithm SHA256
(Get-Item data\knowledge_agent.db).Length
(Get-Item data\knowledge_agent.db).LastWriteTimeUtc
```

All three must be unchanged across the M4-only run. `unittest discover` must
still pass, but its effect on this file is expected and is not an M4 failure.

Separately, confirm no **new** database file was created. Exclude the existing
file by its exact resolved path - a name filter would also hide a new
`knowledge_agent.db` created somewhere else:

```powershell
$existing = (Resolve-Path data\knowledge_agent.db).Path
Get-ChildItem -Recurse -Filter *.db |
  Where-Object { $_.FullName -notlike '*\.venv\*' -and $_.FullName -ne $existing }
```

That listing must be empty. Equivalently, scan the four permitted areas plus
`data/` individually: repository root, `orchestration/`, `tests/`,
`system_fixtures/`, and `data/` excluding only the exact existing path.

## Explicit Non-Goals

Do not implement:

- an arbitrary-SQL tool, a query builder, or caller-supplied SQL of any kind;
- any write operation - approval, refund, cancellation, or status update;
- a productized permission or authentication system;
- changes to `storage.py` or the product database schema;
- Planner, Agent, API, SSE, or frontend integration;
- natural-language parsing into an operation or its parameters;
- evidence sufficiency, conflict resolution, or citation generation (M5);
- a fixture loader, seeding helper, or migration inside the provider;
- new third-party dependencies;
- commits, pushes, or any remote operation.

## Required Handoff

Stop after M4 and report:

- files changed;
- the three operations, their exact parameter whitelists, and their SQL
  templates;
- how SQL injection is neutralized and how read-only-ness is enforced and
  verified;
- the cursor execution contract, and evidence that the caller's
  `connection.row_factory` is isolated from provider row decoding and left
  unchanged;
- the SHA-256, size, and modification time of `data/knowledge_agent.db`
  immediately before and after the **M4-only** test run, showing M4 did not
  touch it (note that `unittest discover` does change this file via
  `api.py:37`, which is a pre-existing baseline side effect, not an M4 result);
- the Evidence mapping, including where `observed_at` comes from and why
  `subject_id` is excluded;
- the trace keys and the top-level-only isolation guarantee;
- exact commands, exit codes, and total test count;
- `git diff --stat` and `git status --short --untracked-files=all`;
- confirmation that `storage.py`, the product database, and all existing
  orchestration modules were untouched, and that no database file was created;
- confirmation that no commit or push occurred;
- any ambiguity discovered in the operation, parameter, or authority rules.
