# M0 - Product-Trunk Baseline

## Purpose

Record the verified starting point for the Wiki Agent V1 product line before
any orchestration code is added.

## Repository State

- Worktree: `C:\Users\h000_\Documents\Codex\2026-08-26\knowledge-agent-wiki-v1`
- Branch: `codex/wiki-agent-v1`
- Base: `origin/main@0a9a80c715aa316444edcf5ab2346d3c6c005267`
- Upstream: `origin/main`
- Frozen RAG reference: `codex/local-rag-baseline-20260825@622a4af`

The product branch and reference baseline have unrelated Git history. The
reference is not a merge base and must not be merged or cherry-picked wholesale.

## Existing Product Surface

- FastAPI REST and SSE chat endpoints;
- Vue 3 conversation client;
- SQLite knowledge, message, source, and trace persistence;
- atomic knowledge replacement;
- knowledge-version protection against stale answer persistence;
- per-conversation generation locks;
- Streamlit prototype;
- Ollama-backed RAG and current single-step Agent routing.

## Verified Environment

A worktree-local `.venv` was created from `requirements-dev.txt`. It is ignored
by Git and is not part of the product diff.

The first attempted test run reused the older reference environment and could
not import FastAPI. That was an environment mismatch, not a product failure.
After installing the product-trunk dependencies in the local `.venv`, the
baseline completed successfully.

## Verification Results

```powershell
.\.venv\Scripts\python.exe -m py_compile agent.py api.py app.py rag.py storage.py
# exit 0

.\.venv\Scripts\python.exe -m unittest discover -v
# Ran 25 tests in 0.834s
# OK
# exit 0
```

One dependency-level warning was observed:

```text
StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated;
install httpx2 instead.
```

This warning does not fail the current tests and is outside M1. Do not change
dependencies during M1 merely to remove it.

## Baseline Gate

M1 may start only if:

- its implementation is based in this worktree;
- existing production files remain unchanged;
- the 25 baseline tests remain green;
- new tests are offline and isolated;
- no code is copied wholesale from the frozen RAG reference.

## Not Verified in M0

- Ollama connectivity or model availability;
- full retrieval/answer evaluation suites;
- frontend dependency installation or production build;
- live browser behavior;
- remote CI.

Those checks are unnecessary for the sidecar-only M1 and will be introduced
when a milestone touches the corresponding surface.
