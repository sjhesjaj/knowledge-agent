# M3 - Read-only Wiki

## Status

Ready for Coding Agent implementation after reviewer approval.

## Base

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Starting commit: `653134f906f91e8ccbb8f1f218ed0a269e67b33e`
- Baseline: Python compile passes; 120/120 unit/API tests pass.

Read `AGENTS.md`, `docs/V1_IMPLEMENTATION_PLAN.md`, `docs/tasks/M1_EVIDENCE_ADAPTER.md`,
`docs/tasks/M2_ROUTER_PLANNER.md`, `orchestration/contracts.py`,
`orchestration/document_adapter.py`, and `sample_company_rules.md` completely
before editing.

## Goal

Add a read-only, versioned Wiki page schema and a `wiki_query` adapter that
returns the same `ToolResult[Evidence]` contract M1 established:

```text
sample_company_rules.md
        |
   (hand-authored, reviewed)
        |
wiki_pages/sample_company_wiki.json
        |
   load_wiki_pages -> tuple[WikiPage, ...]
        |
   wiki_query(question, pages)
        |
   ToolResult[Evidence]
```

Wiki content is **derived knowledge, not the source of truth**. Every claim must
carry a locator back to the section of `sample_company_rules.md` it was derived
from, and Wiki evidence must rank below source-document evidence.

Nothing in `api.py`, `agent.py`, `rag.py`, `storage.py`, the planner, Streamlit,
Vue, or SSE uses the adapter in M3. Integration is deliberately deferred.

## Allowed Files

Modify or create only:

```text
orchestration/__init__.py
orchestration/wiki_schema.py
orchestration/wiki_adapter.py
wiki_pages/sample_company_wiki.json
tests/test_wiki_adapter.py
```

Do not modify `orchestration/contracts.py`, `orchestration/document_adapter.py`,
`orchestration/planner.py`, existing tests, production files, README,
`sample_company_rules.md`, or this task document.

`orchestration/__init__.py` may only gain re-exports and must remain
side-effect free.

## Required Schema

Define in `orchestration/wiki_schema.py`. Use standard-library dataclasses that
are **frozen and keyword-only**, matching the M2 `Plan` style, so a page cannot
be mutated after validation. Do not add Pydantic, YAML, or any other dependency.

### WikiClaim

```text
claim_id: str
text: str
source: str
locator: str
```

Rules:

- every field must be non-empty after trimming;
- `source` identifies the originating document, for M3 always
  `sample_company_rules.md`;
- `locator` identifies the exact section it was derived from, in the form
  `section:<heading text>`, where the heading text matches a `## ` heading in
  `sample_company_rules.md` verbatim;
- provide a JSON-friendly `to_dict()`.

### WikiPage

```text
page_id: str
title: str
summary: str
aliases: tuple[str, ...]
version: str
claims: tuple[WikiClaim, ...]
```

Rules:

- `page_id`, `title`, `summary`, and `version` must be non-empty after trimming;
- `aliases` may be empty, but any alias present must be a non-empty string, and
  aliases must not contain duplicates;
- `claims` must contain at least one `WikiClaim`;
- `claim_id` values must be unique within the page;
- `aliases` and `claims` must be tuples, so a page cannot be mutated in place;
- provide a JSON-friendly `to_dict()` whose nested claims are serialized.

### Collection-level invariants

Validated when a collection is loaded or constructed, not only per page:

- `page_id` must be unique across all pages;
- `claim_id` must be unique across **all** pages, not merely within one page, so
  a claim id alone identifies one claim;
- the collection must contain at least one page.

Use `__post_init__` for per-object checks and an explicit validation step for
collection-level checks. Raise `ValueError` with a message naming the offending
id or field. Do not silently drop invalid pages.

### Type boundaries and error reporting

Validation must check **types before use**. A malformed file must never surface
as `AttributeError`, `TypeError`, `KeyError`, or `IndexError` leaking from
inside the parser: those indicate the code trusted the input's shape.

Required checks when loading JSON:

- the top level must be a JSON object; `pages` must be a list; each element of
  `pages` must be a JSON object;
- each claim element must be a JSON object, and `claims` must be a list;
- every string field (`schema_version`, `page_id`, `title`, `summary`,
  `version`, `claim_id`, `text`, `source`, `locator`) must actually be a `str`,
  not a number, boolean, `null`, list, or object;
- `aliases` must be a list, and every alias must be a `str`;
- every claim element must be constructible as a `WikiClaim`;
- **unknown fields are rejected** at all three levels - top level, page, and
  claim. A versioned schema that silently ignores unknown keys cannot detect a
  file written against a newer schema.

Required checks when constructing objects directly in Python:

`WikiClaim` and `WikiPage` are the public schema, so these checks belong in
`__post_init__` and must not live only in the JSON loader. A caller passing an
integer, boolean, `None`, list, or object where a string is required must get a
`ValueError`, never an `AttributeError` leaking from `.strip()`.

- every string field must be an actual `str` **before** it is trimmed or tested
  for emptiness - `WikiClaim.claim_id`, `WikiClaim.text`, `WikiClaim.source`,
  `WikiClaim.locator`, `WikiPage.page_id`, `WikiPage.title`,
  `WikiPage.summary`, `WikiPage.version`;
- `WikiPage.aliases` and `WikiPage.claims` must be `tuple`, not list or any
  other iterable;
- every element of `WikiPage.claims` must be a `WikiClaim` instance;
- every element of `WikiPage.aliases` must be a `str`.

Errors raised from direct construction use a dotted class path rather than a
JSON index path:

```text
WikiClaim.claim_id
WikiClaim.text
WikiPage.page_id
WikiPage.title
```

The JSON loader keeps the indexed paths described above. When the loader
constructs an object and `__post_init__` rejects it, the loader must surface the
indexed path (`pages[1].claims[0].text`), not the bare class path.

Every structural failure must be raised as a `ValueError` whose message
contains a **field path** locating the problem, using zero-based indices:

```text
pages[1].claims[0].text
pages[2].aliases[3]
pages[0].page_id
schema_version
```

A caller must be able to find the offending element in the JSON file from the
message alone. Wrapping an underlying exception is acceptable as long as the
raised type is `ValueError` and the path is present.

## Data File

Create `wiki_pages/sample_company_wiki.json` as standard JSON (UTF-8, no YAML,
no comments):

```json
{
  "schema_version": "1.0",
  "pages": [
    {
      "page_id": "...",
      "title": "...",
      "summary": "...",
      "aliases": ["..."],
      "version": "...",
      "claims": [
        {
          "claim_id": "...",
          "text": "...",
          "source": "sample_company_rules.md",
          "locator": "section:请假制度"
        }
      ]
    }
  ]
}
```

Rules:

- the top level must contain exactly `schema_version` and `pages`;
- provide **at least three** demonstration pages;
- pages must cover at least annual leave, remote work, and information
  security, because the required query tests use them;
- every claim must be derived from `sample_company_rules.md`;
- **do not introduce any fact that is not in `sample_company_rules.md`**, and do
  not paraphrase a number, threshold, or approval level into a different value;
- do not use `student_handbook.md` or any other corpus that is not distributed
  with this repository;
- `version` is the Wiki page version, authored by hand (for example `2026-08-26`
  or `1.0`). It is not the source document's version and must not be invented as
  one.

## Query API

Define in `orchestration/wiki_adapter.py`.

### Loading

```python
DEFAULT_WIKI_PATH = Path(__file__).resolve().parent.parent / "wiki_pages" / "sample_company_wiki.json"
SUPPORTED_SCHEMA_VERSION = "1.0"

def load_wiki_pages(path: str | Path = DEFAULT_WIKI_PATH) -> tuple[WikiPage, ...]:
    ...
```

Behavior:

1. Read the file as UTF-8 and parse it with the standard `json` module.
2. Let `FileNotFoundError` propagate. A missing Wiki file is an infrastructure
   error, not an empty Wiki.
3. Let `json.JSONDecodeError` propagate, or re-raise it wrapped in a `ValueError`
   that preserves the original message. Do not return an empty tuple.
4. Raise `ValueError` when `schema_version` is missing or unsupported, when
   `pages` is missing/empty/not a list, when a required field is missing or
   blank, when any collection-level uniqueness invariant fails, or when any
   check in **Type boundaries and error reporting** fails. That section is
   binding here: no malformed input may escape as `AttributeError`,
   `TypeError`, `KeyError`, or `IndexError`.
5. Preserve file order in the returned tuple.
6. Do not cache globally in a way that hides file changes between calls, and do
   not read the file at import time.

### Query

```python
WIKI_TOOL_NAME = "wiki_query"
WIKI_AUTHORITY = 60

def wiki_query(
    question: str,
    pages: Sequence[WikiPage],
    *,
    top_k: int = 3,
    trace: dict | None = None,
) -> ToolResult:
    ...
```

Behavior:

1. Reject a blank `question` with `ValueError` before any scoring.
2. Reject `top_k < 1` with `ValueError` before any scoring.
3. Reject a `pages` argument that is not a sequence of `WikiPage`, or that
   violates the collection-level uniqueness invariants, with `ValueError` before
   any scoring.
4. Run entirely offline and deterministically. Do not call Ollama, the network,
   `rag`, `storage`, the API, or a database. Do not use randomness, wall-clock
   time, or `set` iteration order in ranking.
5. Return `ToolStatus.EMPTY` with no evidence when nothing scores above zero.
6. Return `ToolStatus.OK` otherwise, with `tool_name = "wiki_query"`.
7. Do not swallow schema or infrastructure errors. `ToolStatus.ERROR` must not be
   used to mask a `ValueError` from invalid input.

## Retrieval Boundary

Implement simple, deterministic, standard-library term matching. No vector
search, no reranker, no LLM summarization, no `jieba` or other tokenizer
dependency.

### Tokenization

One tokenizer is applied to **both** the query and every searched field, so
punctuation is handled identically on both sides.

1. Normalize: lowercase, then collapse all whitespace runs to a single space.
2. Treat every character that is not ASCII alphanumeric and not a CJK ideograph
   as a separator. CJK means `U+4E00`-`U+9FFF` for M3.
3. ASCII tokens: each maximal run matching `[a-z0-9]+` is one token.
4. CJK tokens: for each maximal run of contiguous CJK characters, emit every
   adjacent character bigram of that run. A run of length 1 emits that single
   character as one token, so a one-character term is not silently dropped.
   Bigrams never span a separator, so `年假、调休` yields `年假` and `调休` but
   never `假调`.
5. Deduplicate tokens, preserving first-appearance order.

The query's token list is the **query terms**. Each field's token list is
converted to a set for matching.

### Scoring

- A field's match count is the number of **distinct query terms present in that
  field's token set**. Repetition never accumulates: a term appearing five times
  in a field still counts once, and a duplicated query term counts once because
  query terms are deduplicated.
- `aliases` is scored as **one merged logical field**: all aliases are tokenized
  into a single set, so a term hitting three aliases counts once.
- Fixed weights, which must be module constants:

  ```text
  title       4
  aliases     3
  summary     2
  claim text  1
  ```

- Each claim's score is:

  ```text
  score = title_matches   * 4
        + alias_matches   * 3
        + summary_matches * 2
        + claim_matches   * 1
  ```

  where the first three terms come from the claim's page and the last from the
  claim's own text.

- Scores are integers. Do not normalize, scale, or divide by field length.

### Selection and ordering

- Evidence granularity is **one Evidence per matching claim**.
- `top_k` bounds the number of returned Evidence items (that is, claims), not
  the number of pages.
- Only claims with a total score greater than zero are eligible.
- **Stable ordering**: sort by score descending, then `page_id` ascending, then
  `claim_id` ascending. This is a total order, so ties are fully deterministic.
  Sort ascending strings with plain Python `<` on the raw id, no locale
  collation.
- Ranks in metadata start at 1 and are assigned after ordering, before `top_k`
  truncation is applied to the ordered list.

State the tokenizer and the tie-breaking rule in the module docstring so a
future change is a visible decision rather than an accident.

## Evidence Mapping

For each selected claim, produce one `Evidence`:

```text
content      = claim.text
source_type  = SourceType.WIKI
source       = claim.source          # the originating document
locator      = claim.locator         # the originating section
version      = page.version
observed_at  = None
authority    = WIKI_AUTHORITY (60)
confidence   = None
metadata     = {
    "rank": <1-based rank>,
    "retrieval_score": <raw score>,
    "page_id": page.page_id,
    "page_title": page.title,
    "claim_id": claim.claim_id,
    "wiki_locator": f"page:{page.page_id}#claim:{claim.claim_id}",
}
```

Rules:

- `source` and `locator` carry the **source-document** trail; `page_id`,
  `claim_id`, and `wiki_locator` carry the **Wiki** trail. Both must survive, so
  a reader can always get from Wiki evidence back to the original section.
- `authority` must be lower than the document adapter's `80`. Assert the
  relationship against `document_adapter.DOCUMENT_AUTHORITY` in tests, not just
  the literal `60`, so a future change to either constant cannot silently invert
  the authority model.
- `confidence` must remain `None`. A term-overlap score is not a calibrated
  probability and must never be copied into `confidence`.
- `observed_at` must remain `None`. Wiki pages are compiled artifacts; query
  time does not describe their freshness.
- every Evidence gets its own new `metadata` dict.

### Trace

The returned `ToolResult.trace` must be a **new dict** built exactly as:

```python
{
    **trace_copy,                              # {} when trace is None
    "wiki_scanned_pages": len(pages),
    "wiki_matched_claims": eligible_count,     # claims scoring > 0, before top_k
    "wiki_returned_evidence": len(evidence),   # after top_k truncation
    "wiki_top_k": top_k,
}
```

Rules:

- these four keys are the complete, fixed set this adapter adds; do not add or
  rename fields;
- if the caller's trace already contains one of these keys, **the adapter's
  computed value wins** (the spread comes first, as written above);
- unlike `document_search`, which must hand the live trace object to
  `retrieve_fast`, `wiki_query` has no downstream writer and **must not mutate
  the caller's dict at all**;
- the copy is **shallow, and only top-level isolation is promised**. Adding,
  removing, or replacing a top-level key on either dict must not affect the
  other. A nested mutable value is still shared between the caller's dict and
  the returned trace. Do not claim deep isolation, and do not use `deepcopy`;
  callers that need nested isolation must pass values they do not intend to
  mutate. Tests must assert top-level isolation only.

## Import and Integration Boundary

- `orchestration.wiki_schema` may use only the Python standard library.
- `orchestration.wiki_adapter` may import the standard library and
  `orchestration.contracts` / `orchestration.wiki_schema`.
- Neither module may import `agent`, `api`, `rag`, `storage`, `planner`,
  FastAPI, or `requests`.
- Existing production files must not import the Wiki modules in M3.
- `orchestration/__init__.py` may re-export the new public symbols and must
  remain side-effect free; it must not load the JSON file at import time.

## Required Tests

Create `tests/test_wiki_adapter.py` using `unittest` and, where needed,
`unittest.mock`. Tests must cover at least:

1. `WikiClaim` and `WikiPage` construct normally and serialize through
   `to_dict()` with nested claims and tuples rendered as lists.
2. Blank/whitespace `claim_id`, `text`, `source`, `locator`, `page_id`, `title`,
   `summary`, and `version` each raise `ValueError`.
3. A page with zero claims raises `ValueError`.
4. Duplicate `claim_id` within a page raises `ValueError`.
5. Duplicate `page_id` across the collection raises `ValueError`.
6. Duplicate `claim_id` across two different pages raises `ValueError`.
7. Duplicate aliases raise `ValueError`; empty aliases are allowed.
8. Pages and claims are immutable (assignment raises).
9. `load_wiki_pages()` loads the committed file and returns at least three pages
   in file order.
10. `load_wiki_pages` raises on: missing file, malformed JSON, missing/unsupported
    `schema_version`, missing/empty `pages`, and each collection invariant
    violation. Use temporary files; do not write into `wiki_pages/`.
10a. **Type boundaries** - each of the following raises `ValueError`, and the
    raised type is asserted to be `ValueError` specifically, never
    `AttributeError`, `TypeError`, `KeyError`, or `IndexError`:
    a non-object top level (list, string, number);
    `pages` not a list;
    a page element that is not an object;
    `claims` not a list;
    a claim element that is not an object;
    a string field supplied as a number, boolean, `null`, list, or object at
    each of the three levels;
    `aliases` not a list;
    a non-string alias element.
10b. **Unknown fields** are rejected at the top level, in a page, and in a claim.
10c. **Field paths**: the `ValueError` message contains the failing path, at
    least for `pages[<i>].claims[<j>].<field>`, `pages[<i>].aliases[<j>]`, and a
    top-level field such as `schema_version`.
10d. **Direct construction types**, table-driven over **every** string field of
    both dataclasses (`WikiClaim.claim_id`, `.text`, `.source`, `.locator`;
    `WikiPage.page_id`, `.title`, `.summary`, `.version`) crossed with the
    non-string values `1`, `True`, `None`, `[]`, and `{}`: each combination
    raises `ValueError`, asserted to be `ValueError` specifically and not
    `AttributeError`. Also: `WikiPage(aliases=[...])` and
    `WikiPage(claims=[...])` with lists raise `ValueError`; a `claims` tuple
    containing a non-`WikiClaim` element raises `ValueError`; a non-string alias
    raises `ValueError`.
11. **Source fidelity**: for every claim in the committed file, the
    `locator` heading exists verbatim as a `## ` heading in
    `sample_company_rules.md`, and `source` is `sample_company_rules.md`.
12. **No invented numbers**: for every claim in the committed file, each Arabic
    numeral token appearing in `claim.text` also appears in the text of the
    section named by its `locator`.
13. Representative queries return the expected page: annual leave
    (for example `年假有多少天`), remote work (for example `远程办公可以申请几天`),
    and information security (for example `内部资料可以上传到公共网盘吗`).
13a. **Punctuation boundary**: a bigram never spans a separator. Construct a page
    whose text contains `年假、调休` and assert that a query producing `假调`
    does not match it, while `年假` and `调休` do.
13b. **Repeated query term counts once**: the question `年假、年假、年假` tokenizes
    to exactly one deduplicated `年假` token and scores identically to `年假`.
    Assert the token list, not only the score. Note that the separators are
    required: `年假年假年假` is a single six-character CJK run and yields
    `{年假, 假年}`, so it is **not** a valid repetition example.
13c. **Repeated field occurrence counts once**: a claim repeating a matched term
    many times scores identically to one occurrence.
13d. **Merged aliases**: a term appearing in three different aliases of one page
    contributes `3` once, not `9`; and a page whose only match is an alias
    scores exactly `3` per distinct matched term.
13e. **Weights**: a constructed page and query verify the exact integer score
    from the formula, so the 4/3/2/1 weighting cannot drift silently.
13f. **Single-character CJK run** is emitted as its own token and is matchable.
14. Ordering is stable and deterministic: repeated identical calls return
    identical evidence order, and the documented tie-break by `page_id` then
    `claim_id` is exercised by a constructed tie.
15. `top_k` bounds the number of Evidence items, `top_k` larger than the match
    count returns all matches, and `top_k < 1` raises `ValueError`.
16. A question that matches nothing returns `ToolStatus.EMPTY` with empty
    evidence and no error fields.
17. Blank question raises `ValueError`; invalid `pages` raises `ValueError`.
18. Evidence fields: `source_type` is `SourceType.WIKI`, `version` comes from the
    page, `source`/`locator` carry the source-document trail, and
    `page_id`/`claim_id`/`wiki_locator` carry the Wiki trail.
19. `WIKI_AUTHORITY < document_adapter.DOCUMENT_AUTHORITY`, and evidence
    authority equals `WIKI_AUTHORITY`.
20. `retrieval_score` lives in `metadata` and `confidence` stays `None`.
21. Caller mutation safety, **top-level only**: adding/removing/replacing a
    top-level key on the passed-in `trace` after the call does not change
    `ToolResult.trace`; the caller's `trace` dict is not mutated by the call
    (assert it still equals its pre-call snapshot); mutating a returned
    `metadata` copy from `to_dict()` does not change the Evidence. Do **not**
    assert deep isolation of nested values - nested sharing is the documented
    contract.
21a. **Trace contract**: the returned trace contains exactly the caller's keys
    plus `wiki_scanned_pages`, `wiki_matched_claims`, `wiki_returned_evidence`,
    and `wiki_top_k`, with values matching the documented meanings (including a
    case where `top_k` truncates, so `wiki_matched_claims` exceeds
    `wiki_returned_evidence`).
21b. **Key collision**: a caller trace that already contains `wiki_top_k` is
    overridden by the adapter's computed value.
22. Tests are fully offline: no Ollama, no network, no API endpoint, no database.

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
  orchestration\wiki_adapter.py

.\.venv\Scripts\python.exe -m unittest discover -v

.\.venv\Scripts\python.exe -m unittest tests.test_wiki_adapter -v

.\.venv\Scripts\python.exe -m json.tool wiki_pages\sample_company_wiki.json > $null

git diff --check
git diff --name-only HEAD
git status --short --untracked-files=all
```

Acceptance conditions:

- all existing 120 tests pass with no regression;
- all new Wiki tests pass;
- the JSON data file parses with the standard library;
- changed paths are exactly the five allowed files and nothing else;
- Wiki tests are fully offline and deterministic;
- current API, SSE, storage, RAG, planner, Streamlit, and Vue behavior is
  unchanged;
- no commit or push is performed.

### Path gate

`git status --short --untracked-files=all` must list only:

```text
 M orchestration/__init__.py
?? orchestration/wiki_schema.py
?? orchestration/wiki_adapter.py
?? wiki_pages/sample_company_wiki.json
?? tests/test_wiki_adapter.py
```

Any other path is an immediate rejection.

## Explicit Non-Goals

Do not implement:

- automatic Wiki compilation, generation, or publication;
- Wiki write, edit, review, approval, or UI flows;
- Planner, API, Agent, SSE, or frontend integration;
- the System provider (M4);
- evidence sufficiency, conflict resolution, or citation generation (M5);
- changes to `rag.py`, `agent.py`, `api.py`, `storage.py`, or any existing
  production path;
- changes to `orchestration/contracts.py`, `document_adapter.py`, or
  `planner.py`;
- vector search, rerankers, or LLM summarization;
- new third-party dependencies, including YAML;
- use of `student_handbook.md` or any non-distributed corpus;
- commits, pushes, or any remote operation.

## Required Handoff

Stop after M3 and report:

- files changed;
- schema and collection invariants implemented;
- the JSON data file's pages, and the source section each page was derived from;
- the tokenizer, the exact scoring formula, `top_k` semantics, and the
  tie-breaking rule;
- the returned trace keys and the top-level-only isolation guarantee;
- how malformed schema input is converted into `ValueError` with field paths;
- the Evidence mapping, including how both the Wiki and source-document trails
  are preserved;
- exact commands, exit codes, and total test count;
- `git diff --stat` and `git status --short --untracked-files=all`;
- confirmation that production files and existing orchestration modules were
  untouched;
- confirmation that no commit or push occurred;
- any ambiguity discovered in the schema, scoring, or authority rules.
