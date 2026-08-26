# Knowledge Agent V1 Product-Trunk Plan

> Status: product-trunk plan ready for implementation
>
> Canonical base: `origin/main@0a9a80c`
>
> Reference RAG baseline: `622a4af` (read-only, unrelated history)

## 1. Product Goal

Build the first controlled version of:

```text
User request
     |
Router / Planner
     |
     +-- wiki_query ------> compiled knowledge pages
     +-- document_search -> source documents and exact clauses
     +-- system_query ----> current read-only business state
     |
Evidence merge -> sufficiency/conflict checks -> cited answer
```

This preserves the original simple architecture while using the remote
FastAPI/Vue/SQLite implementation as the product shell.

## 2. Why the Product Trunk Is Canonical

The remote main branch already provides product capabilities that should not be
rebuilt inside the older Streamlit-oriented line:

- FastAPI REST and SSE endpoints;
- Vue conversation UI;
- SQLite knowledge and conversation persistence;
- knowledge-version consistency checks;
- per-conversation concurrency protection;
- 25 fast unit/API regression tests.

The local RAG baseline remains valuable for retrieval, answer grounding,
timeouts, and evaluation cases. Those improvements will be ported selectively,
not by replacing whole files.

## 3. V1 Design Boundaries

### Included

- a shared `Evidence` contract;
- a shared `ToolResult` contract;
- adapters for wiki, source documents, and one read-only system provider;
- deterministic routing signals plus bounded LLM fallback;
- plans containing at most three read steps;
- evidence sufficiency and conflict checks;
- citations in non-streaming and SSE answers;
- trace fields that show route, steps, evidence types, and fallback reason;
- fast unit/API tests for every orchestration rule.

### Explicitly excluded

- write tools and human approval flows;
- multi-domain plug-in architecture;
- autonomous open-ended loops;
- vector database migration;
- multi-instance locking;
- authentication/authorization productization;
- OCR and ingestion queues;
- wholesale migration of `622a4af`;
- a Wiki editing UI.

## 4. Authority Model

Authority is contextual, not a single universal score:

```text
current operational state: system > current source document > wiki
policy meaning:            current source document > wiki > system notes
model prior:               never accepted as evidence
```

Retrieval score remains metadata. It is not evidence confidence.

## 5. Milestones

### M0 - Product-trunk baseline

- Base: `0a9a80c`
- Python compile: pass
- Unit/API tests: 25/25 pass
- No Ollama evaluation required for this baseline.

### M1 - Evidence contracts and document adapter

Add a sidecar orchestration package with `Evidence`, `ToolResult`, and a thin
adapter over `retrieve_fast`. Do not connect it to API, Router, storage, or UI.
This proves the conversion boundary without changing behavior.

### M2 - Controlled Router / Planner

Define route labels and a table-driven planner for:

- `wiki_only`;
- `document_only`;
- `system_only`;
- `document_system`;
- `wiki_document`.

Use deterministic rules for freshness, exact citation, and user-state signals;
use one bounded LLM classification fallback only for ambiguous requests. The
planner returns data and never executes tools.

### M3 - Read-only Wiki

Add a versioned Wiki-page schema and query adapter. Start with committed sample
pages derived from the repository's distributable sample policy. Each claim
must carry source locators. No automatic publication and no Wiki UI.

### M4 - Read-only System Provider

Add one demonstrable system query provider backed by isolated sample tables or
fixtures. Use allowlisted operations and parameterized SQL. Do not expose an
arbitrary SQL tool and do not write business state.

### M5 - Evidence policy

Implement:

- minimum evidence sufficiency by route;
- freshness requirements for system-state questions;
- exact-source requirements for clauses and numbers;
- conflict detection by subject and fact key;
- deterministic authority resolution;
- refusal when required evidence is absent.

### M6 - API/SSE integration

Connect the planner and executor behind the existing chat endpoints while
preserving conversation locks and knowledge-version consistency. Extend trace
and source payloads compatibly; then add a small Vue evidence-path display.

## 6. Migration Policy for the RAG Reference Branch

Every port from `622a4af` is a separate, named task. Each task must state:

- the exact behavior being ported;
- why the product trunk needs it;
- which current API/storage/SSE contracts must remain stable;
- unit/API regression tests;
- Ollama evaluation requirements, if any.

Do not copy the reference `rag.py`, `agent.py`, README, or evaluation data as a
single change.

## 7. Quality Gates

Every milestone must satisfy:

```powershell
.\.venv\Scripts\python.exe -m py_compile agent.py api.py app.py rag.py storage.py
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

Additional rules:

- existing 25 tests must remain green;
- new orchestration tests must not call Ollama or the network;
- no persistent database writes in unit tests;
- no frontend build is required until a milestone edits `frontend/`;
- full local-model evaluations are run only for milestones that modify RAG,
  answer generation, routing, or production orchestration.

## 8. Completion Definition

V1 is complete when the API can answer representative requests through the
three read-only evidence paths, expose a bounded plan and trace, resolve or
surface conflicts deterministically, and cite the evidence used, while the
existing product tests and milestone-specific evaluations remain green.
