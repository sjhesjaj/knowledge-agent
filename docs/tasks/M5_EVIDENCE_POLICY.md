# M5 - Evidence Policy

## Status

Ready for Coding Agent implementation after reviewer approval.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `dae1c229b2b4ca72a444b98bb6cf12bbf5e1f8a5`
- Baseline: Python compile passes; 243/243 unit/API tests pass.

Read `AGENTS.md`, `docs/V1_IMPLEMENTATION_PLAN.md`, the M1-M4 task documents,
`orchestration/contracts.py`, `orchestration/planner.py`,
`orchestration/document_adapter.py`, `orchestration/wiki_adapter.py`, and
`orchestration/system_provider.py` completely before editing.

## Goal

Decide whether the evidence gathered for a plan is good enough to answer with,
and which evidence may be used for what:

```text
Plan  +  {ToolName: ToolResult}  +  explicit FactAssertions
                  |
          evaluate_evidence
                  |
   PolicyDecision(outcome, usable evidence, resolutions, reasons)
```

M5 returns a **structured decision only**. It generates no answer text, no
citation numbering, and no refusal wording; it executes no tool; it touches no
API. It is pure, offline, and deterministic: no clock, no randomness, no
network, no database, no model.

## Allowed Files

Modify or create only:

```text
orchestration/__init__.py
orchestration/evidence_policy.py
tests/test_evidence_policy.py
```

Do not modify `orchestration/contracts.py`, `orchestration/planner.py`,
`orchestration/document_adapter.py`, `orchestration/wiki_adapter.py`,
`orchestration/wiki_schema.py`, `orchestration/system_provider.py`, existing
tests, `rag.py`, `agent.py`, `api.py`, `storage.py`, README, or this task
document.

`orchestration/__init__.py` may only gain re-exports and must remain
side-effect free.

## No Fact Guessing

`Evidence` carries content, not a parsed fact. M5 must **never** parse
`Evidence.content`, call a model, or use domain keywords to infer what a piece
of evidence asserts. If the caller wants a fact compared, the caller states it.

### FactScope

A string enum with exactly:

```text
policy
current_operational_state
```

`current_operational_state` matches the value the System provider already writes
into `metadata["authority_scope"]`.

### FactAssertion

Frozen, keyword-only dataclass:

```text
evidence: Evidence
scope: FactScope
subject: str
fact_key: str
fact_value: str
```

Rules:

- `subject`, `fact_key`, and `fact_value` must be non-empty after trimming, and
  must be `str` (check the type before trimming, as in M3/M4);
- `scope` must be a `FactScope` member; a bare string is rejected;
- `evidence` must be an `Evidence` instance;
- provide a JSON-friendly `to_dict()`. It serializes the assertion's fields and
  the referenced evidence's `to_dict()`.

An assertion must reference an `Evidence` object that is actually present in the
supplied `results` (compared by **object identity**, not equality - two equal
Evidence values from different tools are different provenance). An assertion
referencing anything else is an integration error: raise `ValueError`.

Conflict resolution happens **at the assertion level**. Losing one claim must
never remove the underlying `Evidence` from `usable_evidence`; the same evidence
may still support other facts.

### Duplicate assertions

**Duplicates are rejected, not deduplicated.** Within a single `Evidence`
object, the triple `(scope, subject, fact_key)` may appear at most once. Two
assertions violating this raise `ValueError`, whether they carry the same
`fact_value` (a redundant duplicate) or different ones (one source contradicting
itself). Both are caller bugs, and M5's posture everywhere else is to fail
loudly on integration errors rather than silently repair them.

Two *different* Evidence objects asserting the same triple are **not**
duplicates - that is either corroboration or a conflict, and is exactly what
resolution is for.

## Policy API

```python
def evaluate_evidence(
    plan: Plan,
    results: Mapping[ToolName, ToolResult],
    *,
    assertions: tuple[FactAssertion, ...] = (),
) -> PolicyDecision:
    ...
```

Rules:

1. Treat every input as read-only. `plan`, `results`, every `ToolResult`, every
   `Evidence`, and every assertion must be unchanged after the call - including
   nested `metadata` and `trace` dicts. Copy before deriving anything.
2. Process planned steps in `plan.steps` order, which the planner has already
   canonicalized to Wiki, Document, System.
3. All output ordering is fully determined by the rules below. No `set`
   iteration order may reach the output.
4. A `direct` plan needs no evidence.
5. **Integration errors raise `ValueError`** (see below).
6. **Unanswerable states return `REFUSE`** (see below).
7. Neither `ToolResult.error_message` nor `ToolResult.error_code` may appear in
   `reason_codes`, in any field of `PolicyDecision`, anywhere in `to_dict()`, or
   in any `ValueError` message this module raises.

### Integration errors (raise `ValueError`)

These indicate the caller wired something up wrong, not that the request cannot
be answered.

**Types are checked before use.** Validate the shape of `results` before
reading `.tool_name`, before any set comparison, and before any sorting, so a
malformed mapping cannot escape as `AttributeError`, `TypeError`, or `KeyError`:

- `plan` must be a `Plan`; `results` must be a `Mapping`; `assertions` must be a
  `tuple` of `FactAssertion`;
- **every key of `results` must be a `ToolName` member** - the bare string
  `"wiki_query"` is rejected;
- **every value of `results` must be a `ToolResult`** - `object()`, `None`, and
  a dict are all rejected.

Messages must identify the offending entry by its key's value (for a valid
`ToolName` key), by its position for an assertion, or by field path. They must
never render the offending payload: no `repr(result)`, no `repr(evidence)`, and
no error text from a `ToolResult`.

Remaining integration errors:

- `results` contains a `ToolName` that is not in `plan.steps` (including any
  result at all for a `direct` plan);
- a `ToolResult.tool_name` does not equal its key's value;
- a `ToolResult` contains `Evidence` whose `source_type` does not match its
  tool (`wiki_query`/`WIKI`, `document_search`/`DOCUMENT`, `system_query`/`SYSTEM`);
- an assertion references an `Evidence` object not present in `results`;
- an assertion violates the duplicate rule;
- `plan` is not a `Plan`, `results` is not a mapping, or `assertions` is not a
  tuple of `FactAssertion`.

Error messages must name the offending tool, field, or assertion index. They
must not contain `error_message` text.

## Decision Contract

### PolicyOutcome

A string enum with exactly:

```text
direct
ready
refuse
```

### ToolFailure

Frozen, keyword-only, so the three unanswerable causes stay distinguishable
without leaking exception text:

```text
tool: ToolName
kind: str        # "missing" | "empty" | "error"
```

These are the **only** two fields. `kind` must be one of those three literals,
declared as module constants.

Neither `ToolResult.error_code` nor `ToolResult.error_message` may enter
`ToolFailure`, `PolicyDecision`, `reason_codes`, any `to_dict()`, or any
`ValueError` message raised by this module. `kind` already carries everything a
policy decision needs; the payload of a failure belongs to the layer that can
act on it.

### PolicyDecision

Frozen, keyword-only:

```text
outcome: PolicyOutcome
usable_evidence: tuple[Evidence, ...]
exact_citation_evidence: tuple[Evidence, ...]
resolutions: tuple[FactResolution, ...]
missing_tools: tuple[ToolName, ...]
tool_failures: tuple[ToolFailure, ...]
reason_codes: tuple[str, ...]
```

Rules:

- `usable_evidence` is the evidence from every planned step that returned `OK`,
  in `plan.steps` order, then in each `ToolResult.evidence` order. It is empty
  for `direct`.
- `exact_citation_evidence` is **conditional**: it is `()` whenever
  `plan.signals.requires_exact_citation` is false, and only when that flag is
  true does it hold the Document evidence carrying both a non-empty `source` and
  a non-empty `locator`, in `usable_evidence` order. This is M5's authorization
  output - it says which evidence *may* be cited exactly - and it never produces
  a citation label, number, or answer text.
- `missing_tools` lists planned steps that produced no usable evidence, in
  `plan.steps` order. `tool_failures` gives the reason for each, same order.
- `reason_codes` are unique, non-empty, and emitted in the fixed order below.
- Provide a JSON-friendly `to_dict()` with enums as strings and tuples as lists.
- All dataclasses in this module are `frozen=True, kw_only=True`.

M5 returns **no natural-language refusal text**. Rendering the refusal is a
later milestone's job.

## Route Sufficiency

- `direct`: no tool result is required, and none is accepted. `outcome` is
  `DIRECT` with empty evidence and the single reason `plan_direct`.
- Every non-direct planned step must have a corresponding `ToolResult`.
- Each planned step must be `OK` **and** carry at least one `Evidence` of the
  matching `SourceType`.
- If any planned step is missing, `EMPTY`, or `ERROR`, the whole decision is
  `REFUSE`.
- **One path never substitutes for another.** A `wiki_document` plan whose
  Document step is empty refuses even though Wiki evidence exists; the Wiki
  result does not become "good enough".
- The three causes stay distinguishable through `ToolFailure.kind`, and through
  the reason codes `missing_tool_result`, `empty_tool_result`, and `tool_error`.
  No exception text is exposed.

## Freshness Boundary

M5 V1 verifies that freshness is **traceable**, not that it is recent. It
defines no business SLA and computes no expiry, because that would require a
clock and a policy M5 has no basis to invent.

- Every System `Evidence` must have a non-empty `observed_at`; otherwise
  `REFUSE` with `system_evidence_missing_observed_at`.
- Every System `Evidence` must carry
  `metadata["authority_scope"] == "current_operational_state"`; otherwise
  `REFUSE` with `system_evidence_missing_authority_scope`.
- When `plan.signals.requires_freshness` is true and the plan includes
  `document_search`, at least one Document `Evidence` must have a non-empty
  `version` or a non-empty `observed_at`; otherwise `REFUSE` with
  `freshness_unsupported`.
- A Wiki page `version` describes the compiled page, not the source document, so
  it can never on its own show that the underlying document is current.
  Therefore: when `requires_freshness` is true and the plan contains
  `wiki_query` but neither `document_search` nor `system_query`, `REFUSE` with
  `freshness_unsupported`.
- Do not call the system clock, and do not compare dates or durations. The
  module must import no `datetime` and no `time`.

## Exact-source Boundary

When `plan.signals.requires_exact_citation` is true:

- the plan must contain a `document_search` step, and that step must be `OK`
  with at least one Document `Evidence`; otherwise `REFUSE` with
  `exact_citation_missing_document`;
- at least one Document `Evidence` must carry both a non-empty `source` and a
  non-empty `locator`; otherwise `REFUSE` with
  `exact_citation_missing_locator`;
- Wiki and System evidence never satisfy this requirement, regardless of
  authority. Wiki is derived knowledge, and System describes state rather than
  wording.

M5 only authorizes which evidence may serve as exact support, via
`exact_citation_evidence`. It does not number, format, or render citations.

## Conflict Detection

Only explicit `FactAssertion`s are considered. Evidence with no assertion is
never inspected for facts.

- Group key is the tuple `(scope, subject, fact_key)`.
- `fact_value` comparison is **exact string equality** on the caller's
  canonical strings. No synonym matching, no numeric parsing, no unit
  conversion, no case folding, no whitespace normalization. If the caller wants
  `5` and `5 天` treated as the same fact, the caller normalizes before calling.
- A group with one distinct `fact_value` is corroboration, not a conflict. It
  still produces a `FactResolution` recording the agreeing assertions.
- A group with more than one distinct `fact_value` goes to authority resolution.
- Group ordering in the output is sorted by `(scope.value, subject, fact_key)`.
- Within a group, assertions are ordered by `(-evidence.authority, index)`,
  where `index` is the assertion's position in the input `assertions` tuple.
  This is a total order, so ties are fully deterministic.

## Authority Resolution

Authority is **contextual**. The V1 plan is explicit that a single global score
is wrong, so `SYSTEM 100` must not simply outrank `DOCUMENT 80` in every scope.

### scope = `current_operational_state`

- System assertions participate, and their evidence must carry the correct
  `authority_scope` (already enforced by the freshness checks).
- Document and Wiki assertions may participate as lower-authority historical or
  derived evidence.
- Nothing is excluded, so `eligible` is the whole group.
- Bucketing then follows the algorithm below.

### scope = `policy`

- **System evidence is excluded from winning.** A live record does not
  reinterpret what a policy says. Every System assertion in a `policy` group
  goes to `ignored_assertions`, with reason `system_excluded_from_policy`.
- Among the remaining (eligible) assertions, the highest `Evidence.authority`
  wins. There is no second ranking rule: under the trusted adapters' fixed
  contract Document is `80` and Wiki is `60`, so this *manifests* as
  Document beating Wiki, but M5 reads only `Evidence.authority` and never
  re-derives authority from the tool or `source_type`.
- M5 does not validate or repair a caller-forged authority/`source_type`
  combination. Adapters own those values; the policy layer trusts them.
- Bucketing then follows the algorithm below. A `policy` group containing only
  System assertions therefore has an empty `eligible` set and is unresolved.

### Bucketing algorithm

This is the complete, binding assignment. It is written out because prose rules
like "lower authority with a different value is superseded" leave two cases
homeless: a lower-authority assertion that *agrees* with the winner, and a
lower-authority assertion in a group whose top tier could not be resolved.

```text
ignored  = assertions not eligible in this scope
eligible = the rest

if eligible is empty:
    resolved      = False
    winning_value = None
    winning       = ()
    contending    = ()
    superseded    = ()

else:
    top_authority = max(a.evidence.authority for a in eligible)
    top           = [a for a in eligible if a.evidence.authority == top_authority]
    top_values    = distinct fact_value in top

    if len(top_values) > 1:
        resolved      = False
        winning_value = None
        winning       = ()
        contending    = top
        superseded    = [a for a in eligible if a not in top]

    else:
        resolved      = True
        winning_value = the single value in top_values
        winning       = [a for a in eligible if a.fact_value == winning_value]
        superseded    = [a for a in eligible if a.fact_value != winning_value]
        contending    = ()
```

Consequences worth stating explicitly:

- a lower-authority assertion that **agrees** with the winner is a corroborating
  winner, not a superseded one - it did not lose anything;
- when the top tier cannot be resolved, lower-authority assertions take no part
  in the arbitration at all and are uniformly `superseded`, regardless of which
  contested value they happen to support. Promoting one of them would let a
  weaker source break a tie between stronger ones;
- eligibility is applied **before** the single-value check, so exclusion is
  never masked by agreement. A `policy` group where Document and System assert
  the *same* value still records the System assertion as ignored: it agreed by
  coincidence and was never entitled to decide policy meaning.

### Derived semantics

- `observed_values` contains the distinct values of **every** assertion in the
  group - ignored, superseded, and contending included - sorted. It describes
  what was seen, not what was allowed to count.
- `fact_conflict_resolved` is emitted only when the **eligible** assertions held
  more than one distinct value *and* the top authority tier settled it.
  Disagreement that exists only between an excluded System assertion and an
  eligible one is not a resolved conflict and must not emit this code.
- `fact_conflict_unresolved` is emitted whenever any group ends with
  `resolved is False`, including the empty-eligible case.

## Conflict Output

### FactResolution

Frozen, keyword-only. Records at least:

```text
scope: FactScope
subject: str
fact_key: str
observed_values: tuple[str, ...]        # distinct, sorted
resolved: bool
winning_value: str | None
winning_assertions: tuple[FactAssertion, ...]
contending_assertions: tuple[FactAssertion, ...]
superseded_assertions: tuple[FactAssertion, ...]
ignored_assertions: tuple[FactAssertion, ...]
```

`contending_assertions` exists because an unresolved top-tier disagreement has
no home in the other three buckets: those assertions did not win, were not
outranked, and were not excluded. Without it the "no assertion is dropped"
invariant is unsatisfiable.

Rules:

- every assertion in the group appears in **exactly one** of winning,
  contending, superseded, or ignored - none is dropped and none is duplicated;
- when `resolved` is `True`: `contending_assertions == ()`;
- when `resolved` is `False` because the top eligible tier held more than one
  value: `winning_assertions == ()` and every assertion of that tier is in
  `contending_assertions`;
- `winning_value` is `None` **if and only if** `resolved` is `False`;
- a `policy` group containing only System assertions is unresolved with every
  assertion in `ignored_assertions`; `contending_assertions` is then legitimately
  empty;
- ordering inside each tuple follows the group ordering rule above;
- `to_dict()` is JSON-friendly and contains no infrastructure exception text and
  no natural-language explanation.

## Reason Codes

Stable, machine-readable, unique, emitted in this fixed order:

```text
plan_direct
missing_tool_result
empty_tool_result
tool_error
system_evidence_missing_observed_at
system_evidence_missing_authority_scope
freshness_unsupported
exact_citation_missing_document
exact_citation_missing_locator
system_excluded_from_policy
fact_conflict_resolved
fact_conflict_unresolved
evidence_sufficient
```

Rules:

- every check runs; the decision is diagnostic rather than short-circuited, so a
  caller sees all the reasons a request failed, not just the first;
- `outcome` is `REFUSE` if any blocking code fired, `DIRECT` for a direct plan,
  otherwise `READY`;
- `evidence_sufficient` is emitted only for a `READY` outcome;
- `plan_direct` is emitted only for a `DIRECT` outcome and appears alone;
- `system_excluded_from_policy` and `fact_conflict_resolved` are informational
  and do not by themselves cause `REFUSE`.

## Import and Integration Boundary

- `orchestration.evidence_policy` may import only the standard library
  (`dataclasses`, `enum`, `typing`) plus `orchestration.contracts` and
  `orchestration.planner`.
- It must not import `rag`, `agent`, `api`, `storage`, `sqlite3`, `datetime`,
  `time`, FastAPI, or `requests`.
- It must not import the M1/M3/M4 adapters. It reads authority from
  `Evidence.authority`, which the adapters already set; it must not re-derive
  authority from the tool name.
- Existing production files must not import the policy module in M5.
- `orchestration/__init__.py` may re-export the new public symbols and must
  remain side-effect free.

## Required Tests

Create `tests/test_evidence_policy.py` using `unittest`. Build `Evidence`,
`ToolResult`, and `Plan` objects directly - do not call the adapters, a
database, or a model. Cover at least:

1. **Route sufficiency matrix**: all eight routes. For each non-direct route,
   a fully satisfied case returns `READY` with `usable_evidence` in
   `plan.steps` order.
2. `direct` returns `DIRECT`, empty evidence, and exactly `("plan_direct",)`.
3. Passing any result for a `direct` plan raises `ValueError`.
4. A planned step with no result returns `REFUSE`, lists the tool in
   `missing_tools`, and reports `kind == "missing"`.
5. An `EMPTY` step returns `REFUSE` with `kind == "empty"`.
6. An `ERROR` step returns `REFUSE` with `kind == "error"`. Give that
   `ToolResult` a **distinctive** `error_code` and `error_message` (for example
   `"E_UNIQ_4711"` and `"unique-failure-text-4711"`) and assert neither string
   appears anywhere in `reason_codes`, in `repr(decision)`, or in
   `json.dumps(decision.to_dict())`. Assert `ToolFailure` exposes only `tool`
   and `kind`.
7. An `OK` step whose evidence tuple is empty is impossible under the
   `ToolResult` invariant; instead assert that a step whose evidence is all of
   the wrong `SourceType` raises `ValueError`.
8. An unplanned extra result raises `ValueError`.
9. A `ToolResult.tool_name` that does not match its key raises `ValueError`.
9a. **`results` type boundaries** raise `ValueError`, asserted to be `ValueError`
    specifically and never `AttributeError`/`TypeError`/`KeyError`:
    a key that is the bare string `"wiki_query"` instead of the enum member;
    a key that is `1`, `None`, or a tuple;
    a value that is `object()`, `None`, a dict, or a bare string;
    `results` that is not a mapping; `plan` that is not a `Plan`;
    `assertions` that is a list instead of a tuple, or contains a non-
    `FactAssertion`.
9b. The `ValueError` message for a bad value does not contain `repr` of that
    value: give the offending object a distinctive `__repr__` and assert the
    marker string is absent.
10. **No substitution**: a `wiki_document` plan with rich Wiki evidence and an
    `EMPTY` Document step still returns `REFUSE`.
11. System evidence missing `observed_at` returns `REFUSE` with the matching
    code; same for a missing or wrong `metadata["authority_scope"]`.
12. `requires_freshness` combinations: Document with `version` passes; Document
    with `observed_at` passes; Document with neither refuses; Wiki-only refuses;
    Wiki + System passes; Wiki + Document passes.
13. `requires_exact_citation`: Document with `source` and `locator` passes; a
    plan without a Document step refuses; Document evidence lacking `locator`
    refuses; Wiki or System evidence does not satisfy it.
14. `exact_citation_evidence` is `()` whenever `requires_exact_citation` is
    false - including a plan that has Document evidence with a perfectly good
    `source` and `locator` - and contains exactly the qualifying Document
    evidence, in `usable_evidence` order, when the flag is true.
15. No assertions: `resolutions` is empty and the outcome is unaffected.
16. A single-value group produces a resolved `FactResolution` with all
    assertions winning and no conflict code.
17. **System resolves a current-state conflict**: System and Document assert
    different values for the same `current_operational_state` triple; System
    wins, Document is superseded, outcome stays `READY`.
18. **System cannot resolve a policy conflict**: System and Document assert
    different values in `policy` scope; the System assertion is ignored with
    `system_excluded_from_policy`, Document wins, and the outcome is `READY`.
19. A `policy` group containing only System assertions refuses: every assertion
    is in `ignored_assertions`, `contending_assertions == ()`, and
    `winning_value is None`.
19a. Resolution reads only `Evidence.authority`: a Wiki-`source_type` evidence
    carrying authority `80` beats a Document-`source_type` evidence carrying
    `60`. This documents that M5 trusts adapter-set authority and does not
    re-derive it from the tool. Use canonical `60`/`80`/`100` values everywhere
    else.
19b. **Corroborating lower authority wins, it is not superseded**:
    Document `80` = `A`, Wiki `60` = `A`, Wiki `60` = `B`. Assert
    `resolved is True`, `winning_value == "A"`, the first two assertions are in
    `winning_assertions`, the third is in `superseded_assertions`, and
    `contending_assertions == ()`.
19c. **Lower authority takes no part in an unresolved top tier**:
    Document `80` = `A`, Document `80` = `B`, Wiki `60` = `A`, Wiki `60` = `C`.
    Assert `resolved is False`, `winning_value is None`,
    `winning_assertions == ()`, both Document assertions are in
    `contending_assertions`, and **both** Wiki assertions are in
    `superseded_assertions` - including the one whose value matches a contested
    value, which must not be promoted.
19d. `observed_values` for 19c is `("A", "B", "C")`, and for a `policy` group
    with an ignored System assertion it still includes the System value.
19e. `fact_conflict_resolved` is **not** emitted when the only disagreement is
    between an excluded System assertion and a single eligible value; it **is**
    emitted when eligible assertions themselves held multiple values and the top
    tier settled it.
20. **Document resolves a Wiki policy conflict**: Document wins, Wiki is
    superseded, and the Wiki assertion still appears in
    `superseded_assertions`.
20a. **Agreement does not excuse ineligibility**: a `policy` group where a
    Document and a System assertion carry the *same* `fact_value` still records
    the System assertion in `ignored_assertions`, with only the Document
    assertion winning and `system_excluded_from_policy` emitted.
21. **Same authority, different values** refuses with
    `fact_conflict_unresolved`, in both scopes. Assert
    `winning_assertions == ()`, `winning_value is None`, and that every
    top-tier assertion is in `contending_assertions`.
21a. `contending_assertions` is `()` for every resolved group, and
    `winning_value is None` if and only if `resolved is False`, checked across
    all resolution tests.
22. Every input assertion appears in **exactly one** of
    winning/contending/superseded/ignored, for every test group, with no
    duplication across buckets. Assert this as a partition over the group's
    assertions, not merely as membership.
23. Losing a conflict does not remove the evidence: the superseded assertion's
    `Evidence` is still in `usable_evidence`.
24. An assertion referencing an `Evidence` object not present in `results`
    raises `ValueError`, including an equal-but-not-identical copy.
25. Duplicate assertions raise `ValueError`: same evidence and triple with the
    same value, and with a different value.
26. Blank, whitespace-only, and non-`str` `subject`/`fact_key`/`fact_value`
    raise `ValueError`; a bare string `scope` raises `ValueError`.
27. **Determinism**: shuffling the input `assertions` order does not change
    `resolutions` group order; repeated identical calls produce equal decisions.
28. **Immutability**: `plan`, `results`, every `ToolResult`, every `Evidence`,
    every nested `metadata`/`trace` dict, and `assertions` compare equal to
    pre-call snapshots after the call.
29. `PolicyDecision`, `FactResolution`, `FactAssertion`, and `ToolFailure` are
    frozen; assignment raises.
30. `to_dict()` on the decision is JSON-serializable via `json.dumps`, renders
    enums as strings and tuples as lists, and contains no `error_message`.
31. `reason_codes` are unique and follow the fixed order; `evidence_sufficient`
    appears only on `READY`; `plan_direct` appears alone.
32. The module imports no clock and no infrastructure: assert against the module
    source for `import datetime`, `from datetime`, `import time`, `sqlite3`,
    `import rag`, `import api`, `import storage`, `requests`.
33. All tests are offline: no database, no network, no Ollama, no clock.

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
  orchestration\system_provider.py `
  orchestration\evidence_policy.py

.\.venv\Scripts\python.exe -m unittest discover -v

.\.venv\Scripts\python.exe -m unittest tests.test_evidence_policy -v

git diff --check
git diff --name-only HEAD
git status --short --untracked-files=all
```

Acceptance conditions:

- all existing 243 tests pass with no regression;
- all new policy tests pass;
- changed paths are exactly the three allowed files and nothing else;
- policy tests are fully offline and deterministic;
- no Ollama, no product database, and no network is required to run M5;
- `data/knowledge_agent.db` is not referenced, opened, or modified by M5 source
  or tests, and no new `.db` file is created;
- current API, SSE, storage, RAG, planner, Wiki, System, Streamlit, and Vue
  behavior is unchanged;
- no commit or push is performed.

### Path gate

`git status --short --untracked-files=all` must list only:

```text
 M orchestration/__init__.py
?? orchestration/evidence_policy.py
?? tests/test_evidence_policy.py
```

Any other path is an immediate rejection. As in M4, the pre-existing gitignored
`data/knowledge_agent.db` never appears here and is not a gate failure; M5 must
simply never touch it, and it needs no hash gate because M5 opens no database at
all.

## Explicit Non-Goals

Do not implement:

- tool execution, an executor, or an Agent loop;
- natural-language fact extraction, content parsing, or keyword inference;
- answer generation, citation numbering, or refusal wording;
- API, SSE, or frontend integration;
- changes to the M1-M4 adapters, planner, or contracts;
- changes to `rag.py`, `agent.py`, `api.py`, or `storage.py`;
- any expiry threshold computed from the current time;
- M6 work;
- new third-party dependencies;
- commits, pushes, or any remote operation.

## Required Handoff

Stop after M5 and report:

- files changed;
- the sufficiency, freshness, and exact-source rules implemented, and what each
  refuses on;
- the conflict grouping key, comparison rule, and the duplicate-assertion
  decision;
- the authority resolution table per scope and the eligibility-before-single-value
  ordering: in `policy`, System is removed by the **eligibility** filter, not
  outranked, and arbitration among the remaining eligible assertions then uses
  `Evidence.authority` alone;
- the four assertion buckets and proof that they partition every group;
- confirmation that no `error_code` or `error_message` reaches the decision,
  its serialization, or any raised message;
- the full reason-code list and its emission order;
- exact commands, exit codes, and total test count;
- `git diff --stat` and `git status --short --untracked-files=all`;
- confirmation that inputs are unmodified, that no clock or database is used,
  and that production files were untouched;
- confirmation that no commit or push occurred;
- any ambiguity discovered in the sufficiency, freshness, or authority rules.
