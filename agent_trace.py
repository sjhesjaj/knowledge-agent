"""Per-request execution traces, persisted to SQLite (Stage 1).

A *run* is one request (an API call or one Eval case). It owns an ordered list
of *spans*; each span is one step with its input, output, latency, status and
error. Reading `trace_runs` + `trace_spans` for a run_id is enough to replay
which stages ran, which tools were called with what, what evidence came back,
what every model call cost, and where it broke. Nothing here judges whether a
run was *right* - that is Eval's job; a trace only records facts.

Stages (`span.stage`):
    router, planner, tool_call, evidence, generation, commit  - business stages
    llm_call - one provider call, always nested under a business stage

Design rules:
- **Tracing never breaks a request.** Recording errors are logged and swallowed;
  an unwritable trace store degrades to "no trace", not to a failed answer.
- **One data model** for streaming and non-streaming runs (`streaming` flag).
- **The current run lives in a ContextVar.** Streaming generators are stepped
  through `Run.iterate`, which runs every `next()` inside one captured Context -
  Starlette may resume a sync generator on a fresh context each step.
- **Sanitized before storage.** Sensitive keys and secret-looking values are
  redacted everywhere; API traces also truncate long text (Eval keeps it full).
- `TRACE_ENABLED=0` turns all of this into no-ops: no rows, no header.

Run `python -m agent_trace --db <path> list|show <run_id>` to read traces.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent

BUSINESS_STAGES = ("router", "planner", "tool_call", "evidence", "generation", "commit")
LLM_CALL = "llm_call"
#: `failed_stage` when a request aborts outside every business stage.
REQUEST_STAGE = "request"

STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED = "running", "completed", "failed"

#: API traces bound their size; Eval traces keep everything (`truncate=False`).
API_MAX_TEXT = 4000
API_MAX_ITEMS = 50
ERROR_MESSAGE_MAX = 1000
TRACEBACK_MAX = 4000
REDACTED = "[REDACTED]"


def enabled() -> bool:
    return os.environ.get("TRACE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# Sanitizer
# --------------------------------------------------------------------------

# Matched against a lower-cased key with "-" folded to "_". Token *counts*
# (prompt_tokens, max_tokens) end in "tokens" and deliberately do not match.
_SENSITIVE_KEY = re.compile(
    r"(^|_)(api_?key|apikey|authorization|token|password|passwd|secret|secret_key"
    r"|private_key|client_secret|subject_id|cookie|set_cookie)$"
)
_SECRET_VALUES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
)
_known_secrets: tuple[str, ...] | None = None


def _configured_secrets() -> tuple[str, ...]:
    """Secret values this process knows about, redacted literally wherever they appear."""
    global _known_secrets
    if _known_secrets is None:
        found: list[str] = []
        try:
            import llm_provider

            key = llm_provider.load_config("deepseek").api_key
            if key:
                found.append(key)
        except Exception:
            pass
        _known_secrets = tuple(found)
    return _known_secrets


def is_sensitive_key(key: object) -> bool:
    return bool(_SENSITIVE_KEY.search(str(key).lower().replace("-", "_")))


def redact_text(text: str) -> str:
    for secret in _configured_secrets():
        text = text.replace(secret, REDACTED)
    for pattern in _SECRET_VALUES:
        text = pattern.sub(REDACTED, text)
    return text


def _truncate(text: str, limit: int | None, *, keep_tail: bool = False) -> str:
    if limit is None or len(text) <= limit:
        return text
    dropped = len(text) - limit
    if keep_tail:
        return f"…[truncated {dropped} chars]" + text[-limit:]
    return text[:limit] + f"…[truncated {dropped} chars]"


def sanitize(value: Any, *, max_text: int | None = None, max_items: int | None = None) -> Any:
    """JSON-safe copy with secrets redacted and (optionally) long text bounded."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return sanitize(value.value, max_text=max_text, max_items=max_items)
    if isinstance(value, str):
        return _truncate(redact_text(value), max_text)
    if isinstance(value, dict) or hasattr(value, "items"):
        return {
            str(key): (REDACTED if is_sensitive_key(key) and item is not None
                       else sanitize(item, max_text=max_text, max_items=max_items))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        kept = items if max_items is None else items[:max_items]
        result = [sanitize(item, max_text=max_text, max_items=max_items) for item in kept]
        if len(kept) < len(items):
            result.append(f"…[{len(items) - len(kept)} more items]")
        return result
    if hasattr(value, "to_dict"):
        return sanitize(value.to_dict(), max_text=max_text, max_items=max_items)
    return _truncate(redact_text(str(value)), max_text)


def describe_exception(exc: BaseException) -> tuple[str, str, str]:
    """(type, message, traceback) - redacted and truncated, ready to store."""
    message = getattr(exc, "detail", None) or str(exc)
    trace_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return (
        type(exc).__name__,
        _truncate(redact_text(str(message)), ERROR_MESSAGE_MAX),
        _truncate(redact_text(trace_text), TRACEBACK_MAX, keep_tail=True),
    )


# --------------------------------------------------------------------------
# Run metadata (computed once per process)
# --------------------------------------------------------------------------

_git_info: tuple[str | None, bool | None] | None = None
_retriever_config: dict | None = None


def git_info() -> tuple[str | None, bool | None]:
    global _git_info
    if _git_info is None:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, timeout=10).stdout.strip() or None
            dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                        cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip())
            _git_info = (commit, dirty if commit else None)
        except Exception:
            _git_info = (None, None)
    return _git_info


def retriever_config() -> dict:
    """The retrieval knobs a run used, read from the code's own defaults."""
    global _retriever_config
    if _retriever_config is None:
        import inspect

        import rag
        from orchestration import ExecutionContext

        def defaults(function) -> dict:
            return {name: p.default for name, p in inspect.signature(function).parameters.items()
                    if p.default is not inspect.Parameter.empty and p.default is not None}

        context = ExecutionContext()
        _retriever_config = {
            "split_text": defaults(rag.split_text),
            "bm25_rank": defaults(rag.bm25_rank),
            "hybrid_retrieve": defaults(rag.hybrid_retrieve),
            "retrieve_with_rerank": defaults(rag.retrieve_with_rerank),
            "retrieve_fast": defaults(rag.retrieve_fast),
            "bm25_fast_path": _fast_path_thresholds(rag),
            "retrieval_budget": _retrieval_budget(rag, defaults),
            "wiki_weights": _wiki_weights(),
            "embed_model": rag.EMBED_MODEL,
            "executor": {"document_top_k": context.document_top_k, "wiki_top_k": context.wiki_top_k},
        }
    return _retriever_config


def _fast_path_thresholds(rag) -> dict:
    """BM25 fast-path thresholds: named constants (current rag) or, for older code, its source."""
    score, ratio = getattr(rag, "BM25_CONFIDENT_SCORE", None), getattr(rag, "BM25_CONFIDENT_RATIO", None)
    if score is None or ratio is None:
        found = re.search(r"first >= ([\d.]+) and \(second == 0 or first / second >= ([\d.]+)\)",
                          inspect.getsource(rag.retrieve_fast))
        score, ratio = (float(found.group(1)), float(found.group(2))) if found else (None, None)
    return {"min_top_score": score, "min_ratio_to_second": ratio}


def _retrieval_budget(rag, defaults) -> dict:
    """Retrieval budget knobs present in this version of rag (absent ones are omitted)."""
    budget = {name: getattr(rag, name) for name in ("MAX_SUB_QUESTIONS", "PADDING_SCORE_RATIO") if hasattr(rag, name)}
    for name in ("fit_to_budget", "cover_merged_clauses", "drop_padding_results"):
        if hasattr(rag, name) and defaults(getattr(rag, name)):
            budget[name] = defaults(getattr(rag, name))
    return budget


def _wiki_weights() -> dict:
    from orchestration import wiki_adapter

    return {name: getattr(wiki_adapter, name) for name in
            ("TITLE_WEIGHT", "ALIAS_WEIGHT", "SUMMARY_WEIGHT", "CLAIM_WEIGHT") if hasattr(wiki_adapter, name)}


def _provider_identity() -> tuple[str | None, str | None]:
    try:
        import llm_provider

        provider = llm_provider.get_provider()
        return provider.name, provider.model
    except Exception:
        return None, None


def generation_input(question: str, results, history) -> dict:
    """What a generation step was given, without re-storing the evidence text."""
    return {
        "question": question,
        "evidence": [{"source": chunk.source, "index": chunk.index} for chunk, _ in results],
        "history_turns": len(history),
    }


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_runs (
    run_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entrypoint TEXT NOT NULL,
    mode TEXT,
    streaming INTEGER NOT NULL,
    session_id TEXT,
    client_id TEXT,
    question TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    failed_stage TEXT,
    failed_span_id TEXT,
    error_type TEXT,
    error_message TEXT,
    error_traceback TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms REAL,
    git_commit TEXT,
    git_dirty INTEGER,
    provider TEXT,
    model TEXT,
    prompt_hashes_json TEXT,
    retriever_config_json TEXT,
    dataset TEXT,
    dataset_sha256 TEXT,
    case_id TEXT,
    eval_run_index INTEGER,
    knowledge_version INTEGER,
    llm_calls INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    llm_latency_ms REAL,
    span_count INTEGER,
    error_span_count INTEGER,
    trace_overhead_ms REAL,
    attributes_json TEXT
);

CREATE TABLE IF NOT EXISTS trace_spans (
    span_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES trace_runs(run_id) ON DELETE CASCADE,
    parent_span_id TEXT,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    offset_ms REAL,
    latency_ms REAL,
    input_json TEXT,
    output_json TEXT,
    error_type TEXT,
    error_code TEXT,
    error_message TEXT,
    error_traceback TEXT,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    attributes_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_run ON trace_spans(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_trace_runs_started ON trace_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_trace_runs_case ON trace_runs(dataset, case_id);
CREATE INDEX IF NOT EXISTS idx_trace_runs_session ON trace_runs(session_id, started_at);
"""

RUN_COLUMNS = (
    "run_id", "schema_version", "kind", "entrypoint", "mode", "streaming", "session_id",
    "client_id", "question", "status", "failed_stage", "failed_span_id", "error_type",
    "error_message", "error_traceback", "started_at", "finished_at", "duration_ms",
    "git_commit", "git_dirty", "provider", "model", "prompt_hashes_json",
    "retriever_config_json", "dataset", "dataset_sha256", "case_id", "eval_run_index",
    "knowledge_version", "llm_calls", "prompt_tokens", "completion_tokens", "llm_latency_ms",
    "span_count", "error_span_count", "trace_overhead_ms", "attributes_json",
)
SPAN_COLUMNS = (
    "span_id", "run_id", "parent_span_id", "seq", "stage", "name", "status", "offset_ms",
    "latency_ms", "input_json", "output_json", "error_type", "error_code", "error_message",
    "error_traceback", "provider", "model", "prompt_tokens", "completion_tokens",
    "attributes_json",
)


class TraceStore:
    """SQLite persistence for runs and spans. Creates its tables on first use."""

    _initialized: set[str] = set()
    _init_lock = threading.Lock()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def ensure_schema(self) -> None:
        key = str(self.path.resolve())
        if key in self._initialized:
            return
        with self._init_lock:
            if key in self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self.connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(SCHEMA)
            finally:
                connection.close()
            self._initialized.add(key)

    def insert_run(self, row: dict) -> None:
        self.ensure_schema()
        columns = [c for c in RUN_COLUMNS if c in row]
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    f"INSERT INTO trace_runs({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    [row[c] for c in columns],
                )
        finally:
            connection.close()

    def finalize(self, row: dict, spans: list[dict]) -> None:
        self.ensure_schema()
        update = [c for c in RUN_COLUMNS if c in row and c != "run_id"]
        connection = self.connect()
        try:
            with connection:
                connection.executemany(
                    f"INSERT OR REPLACE INTO trace_spans({', '.join(SPAN_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(SPAN_COLUMNS))})",
                    [[span.get(c) for c in SPAN_COLUMNS] for span in spans],
                )
                connection.execute(
                    f"UPDATE trace_runs SET {', '.join(c + ' = ?' for c in update)} WHERE run_id = ?",
                    [row[c] for c in update] + [row["run_id"]],
                )
        finally:
            connection.close()

    def load(self, run_id: str) -> dict | None:
        """One run and its spans, JSON columns decoded, spans in `seq` order."""
        self.ensure_schema()
        connection = self.connect()
        try:
            run = connection.execute("SELECT * FROM trace_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            spans = connection.execute(
                "SELECT * FROM trace_spans WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        finally:
            connection.close()
        return {"run": _decode(dict(run)), "spans": [_decode(dict(span)) for span in spans]}

    def recent(self, limit: int = 20, status: str | None = None) -> list[dict]:
        self.ensure_schema()
        connection = self.connect()
        try:
            query = ("SELECT run_id, status, failed_stage, entrypoint, mode, streaming, case_id, "
                     "started_at, duration_ms, question FROM trace_runs")
            params: list = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            rows = connection.execute(query + " ORDER BY started_at DESC LIMIT ?", params + [limit]).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]


def _decode(row: dict) -> dict:
    for key in list(row):
        if key.endswith("_json") and row[key] is not None:
            row[key[: -len("_json")]] = json.loads(row.pop(key))
        elif key.endswith("_json"):
            row[key[: -len("_json")]] = row.pop(key)
    return row


def load_trace(db_path: str | Path, run_id: str) -> dict | None:
    return TraceStore(db_path).load(run_id)


# --------------------------------------------------------------------------
# Spans and runs
# --------------------------------------------------------------------------


class _NullSpan:
    """Stands in for a span when no run is active. Falsy; ignores everything."""

    def __bool__(self) -> bool:
        return False

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def __setattr__(self, _name, _value) -> None:
        pass

    def __getattr__(self, _name):
        return None


NULL_SPAN = _NullSpan()


class Span:
    def __init__(self, run: "Run", stage: str, name: str, parent_id: str | None, input: Any = None) -> None:
        self.run = run
        self.span_id = uuid.uuid4().hex
        self.parent_id = parent_id
        self.seq = run._next_seq()
        self.stage = stage
        self.name = name
        self.status = "ok"
        self.input = input
        self.output: Any = None
        self.error_type: str | None = None
        self.error_code: str | None = None
        self.error_message: str | None = None
        self.error_traceback: str | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.attributes: dict = {}
        self._exc: BaseException | None = None
        self._started = perf_counter()
        self.offset_ms = (self._started - run._t0) * 1000
        self.latency_ms: float | None = None

    def __bool__(self) -> bool:
        return True

    def __enter__(self) -> "Span":
        self.run._stack.append(self)
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        self.latency_ms = (perf_counter() - self._started) * 1000
        if self.run._stack and self.run._stack[-1] is self:
            self.run._stack.pop()
        if exc is not None:
            # An error span is a fact even when the caller then handles the
            # exception (e.g. rerank falling back); whether it *aborted the run*
            # is decided later, in Run.fail, by exception identity.
            self.status = "error"
            self.error_type = exc_type.__name__
            self._exc = exc
            self.run._on_span_exception(self, exc)
        return False

    def to_row(self, max_text: int | None, max_items: int | None) -> dict:
        clean = lambda value: sanitize(value, max_text=max_text, max_items=max_items)
        return {
            "span_id": self.span_id, "run_id": self.run.run_id, "parent_span_id": self.parent_id,
            "seq": self.seq, "stage": self.stage, "name": self.name, "status": self.status,
            "offset_ms": self.offset_ms, "latency_ms": self.latency_ms,
            "input_json": _json(clean(self.input)), "output_json": _json(clean(self.output)),
            "error_type": self.error_type, "error_code": self.error_code,
            "error_message": self.error_message, "error_traceback": self.error_traceback,
            "provider": self.provider, "model": self.model,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "attributes_json": _json(clean(self.attributes)) if self.attributes else None,
        }


_CURRENT: contextvars.ContextVar["Run | None"] = contextvars.ContextVar("agent_trace_run", default=None)


class Run:
    """One traced request. Use as a context manager, or `iterate()` a stream."""

    def __init__(
        self,
        store: TraceStore,
        *,
        kind: str,
        entrypoint: str,
        question: str,
        mode: str | None = None,
        streaming: bool = False,
        session_id: str | None = None,
        client_id: str | None = None,
        dataset: str | None = None,
        dataset_sha256: str | None = None,
        case_id: str | None = None,
        eval_run_index: int | None = None,
        knowledge_version: int | None = None,
        truncate: bool = True,
    ) -> None:
        started = perf_counter()
        self.store = store
        self.run_id = uuid.uuid4().hex
        self._t0 = started
        self._seq = 0
        self._stack: list[Span] = []
        self.spans: list[Span] = []
        self.status = STATUS_RUNNING
        self.failed_stage: str | None = None
        self.failed_span_id: str | None = None
        self.error: tuple[str, str, str] | None = None
        self.attributes: dict = {}
        self._finished = False
        self._token: contextvars.Token | None = None
        self._max_text = API_MAX_TEXT if truncate else None
        self._max_items = API_MAX_ITEMS if truncate else None
        commit, dirty = git_info()
        provider, model = _provider_identity()
        try:
            retriever = retriever_config()
        except Exception:
            retriever = None
        self.row: dict = {
            "run_id": self.run_id, "schema_version": SCHEMA_VERSION, "kind": kind,
            "entrypoint": entrypoint, "mode": mode, "streaming": int(streaming),
            "session_id": session_id, "client_id": client_id,
            "question": sanitize(question, max_text=self._max_text),
            "status": STATUS_RUNNING, "started_at": utc_now(),
            "git_commit": commit, "git_dirty": None if dirty is None else int(dirty),
            "provider": provider, "model": model,
            "retriever_config_json": _json(retriever),
            "dataset": dataset, "dataset_sha256": dataset_sha256, "case_id": case_id,
            "eval_run_index": eval_run_index, "knowledge_version": knowledge_version,
        }
        self._store_ok = True
        try:
            store.insert_run(self.row)
        except Exception:
            self._store_ok = False
            logger.exception("trace: could not record run start; this run will not be persisted")
        self._overhead = perf_counter() - started

    # -- structure -----------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _by_id(self, span_id: str | None) -> Span | None:
        return next((span for span in self.spans if span.span_id == span_id), None)

    def span(self, stage: str, name: str, input: Any = None) -> Span:
        started = perf_counter()
        parent = self._stack[-1].span_id if self._stack else None
        span = Span(self, stage, name, parent, input)
        self.spans.append(span)
        self._overhead += perf_counter() - started
        return span

    def record(self, stage: str, name: str, *, parent: Span | None = None, status: str = "ok",
               input: Any = None, output: Any = None, latency_ms: float | None = None,
               offset_ms: float | None = None, error_type: str | None = None,
               error_code: str | None = None, error_message: str | None = None,
               attributes: dict | None = None) -> Span:
        """Add a span for a step that already happened (timings supplied by the caller)."""
        started = perf_counter()
        span = Span(self, stage, name, parent.span_id if parent else None, input)
        span.output, span.status, span.latency_ms = output, status, latency_ms
        if offset_ms is not None:
            span.offset_ms = offset_ms
        span.error_type, span.error_code, span.error_message = error_type, error_code, error_message
        span.attributes = dict(attributes or {})
        self.spans.append(span)
        self._overhead += perf_counter() - started
        return span

    def _top_stage(self, span: Span) -> str:
        top = span
        while top.parent_id is not None:
            parent = self._by_id(top.parent_id)
            if parent is None:
                break
            top = parent
        return top.stage if top.stage in BUSINESS_STAGES else REQUEST_STAGE

    def _on_span_exception(self, span: Span, exc: BaseException) -> None:
        # The innermost span an exception passes through carries its details;
        # enclosing spans the same exception escapes through only get the type.
        passed_through_child = any(
            other._exc is exc and other.parent_id == span.span_id for other in self.spans
        )
        if not passed_through_child:
            _type, span.error_message, span.error_traceback = describe_exception(exc)

    def _failure_span(self, exc: BaseException) -> Span | None:
        """Where `exc` - the exception that aborted the run - was first seen.

        Matched by identity along the cause/context chain, so an exception a
        span raised but the caller then handled can never be blamed. With no
        match (e.g. the client disconnected mid-stream) the innermost span still
        open at that moment is where the run was interrupted.
        """
        chain, seen = [], exc
        while seen is not None and seen not in chain:
            chain.append(seen)
            seen = seen.__cause__ or seen.__context__

        def escaped(span: Span, candidate: BaseException) -> bool:
            # The exception left every enclosing span too; one that stopped at
            # an ancestor was handled there and did not abort the run.
            parent = self._by_id(span.parent_id)
            while parent is not None:
                if parent._exc is not candidate:
                    return False
                parent = self._by_id(parent.parent_id)
            return True

        for candidate in chain:
            hits = [s for s in self.spans if s._exc is candidate and escaped(s, candidate)]
            if hits:
                # Innermost = the hit none of whose children also saw it.
                return next(s for s in hits
                            if not any(o._exc is candidate and o.parent_id == s.span_id for o in hits))
        return self._stack[-1] if self._stack else None

    # -- lifecycle -------------------------------------------------------------------

    def fail(self, exc: BaseException, **attributes) -> None:
        """Mark the run failed with the exception that aborted it."""
        if self.error is None:
            self.error = describe_exception(exc)
            span = self._failure_span(exc)
            self.failed_span_id = span.span_id if span else None
            self.failed_stage = self._top_stage(span) if span else REQUEST_STAGE
        self.status = STATUS_FAILED
        self.attributes.update(attributes)
        if self.run_id and hasattr(exc, "status_code") and hasattr(exc, "headers"):
            try:  # HTTPException: let the error response carry the run id too
                exc.headers = {**(exc.headers or {}), "X-Run-Id": self.run_id}
            except Exception:
                pass

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        started = perf_counter()
        try:
            if self.status != STATUS_FAILED:
                self.status = STATUS_COMPLETED
            llm = [s for s in self.spans if s.stage == LLM_CALL]
            prompt_hashes = sorted({s.attributes.get("system_prompt_sha256") for s in llm
                                    if s.attributes.get("system_prompt_sha256")})
            error_type, error_message, error_traceback = self.error or (None, None, None)
            self.row.update({
                "status": self.status, "failed_stage": self.failed_stage,
                "failed_span_id": self.failed_span_id, "error_type": error_type,
                "error_message": error_message, "error_traceback": error_traceback,
                "finished_at": utc_now(), "duration_ms": (perf_counter() - self._t0) * 1000,
                "prompt_hashes_json": _json(prompt_hashes),
                "llm_calls": len(llm),
                "prompt_tokens": sum(s.prompt_tokens or 0 for s in llm),
                "completion_tokens": sum(s.completion_tokens or 0 for s in llm),
                "llm_latency_ms": sum(s.latency_ms or 0 for s in llm),
                "span_count": len(self.spans),
                "error_span_count": sum(s.status == "error" for s in self.spans),
                "attributes_json": _json(sanitize(self.attributes)) if self.attributes else None,
            })
            rows = [span.to_row(self._max_text, self._max_items) for span in self.spans]
            for span in self.spans:
                span._exc = None  # drop exception/traceback references
            self.row["trace_overhead_ms"] = (self._overhead + perf_counter() - started) * 1000
            if self._store_ok:
                self.store.finalize(self.row, rows)
        except Exception:
            logger.exception("trace: could not finalize run %s", self.run_id)

    def __enter__(self) -> "Run":
        self._token = _CURRENT.set(self)
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self.fail(exc)
        self.finish()
        if self._token is not None:
            _CURRENT.reset(self._token)
            self._token = None
        return False

    def abort(self, exc: BaseException) -> None:
        """Fail and finalize a run that never got to its context/iteration."""
        self.fail(exc)
        self.finish()

    def iterate(self, generator: Iterator) -> Iterator:
        """Step `generator` inside one Context bound to this run, then finalize.

        Closing early (client disconnect) marks the run failed.
        """
        context = contextvars.copy_context()
        context.run(_CURRENT.set, self)
        exhausted = False
        try:
            while True:
                try:
                    item = context.run(next, generator)
                except StopIteration:
                    exhausted = True
                    return
                yield item
        except GeneratorExit:
            if self.status != STATUS_FAILED:
                self.fail(ClientDisconnected("stream closed by the client before completion"))
            raise
        except BaseException as exc:
            if not exhausted and self.status != STATUS_FAILED:
                self.fail(exc)
            raise
        finally:
            if not exhausted and self.status != STATUS_FAILED:
                self.fail(ClientDisconnected("stream closed before completion"))
            try:
                context.run(generator.close)
            finally:
                self.finish()


class ClientDisconnected(Exception):
    """Recorded when a stream is closed before it finished."""


class _NullRun:
    run_id = None
    status = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def span(self, *_a, **_k):
        return NULL_SPAN

    def record(self, *_a, **_k):
        return NULL_SPAN

    def fail(self, *_a, **_k) -> None:
        pass

    def finish(self) -> None:
        pass

    def abort(self, _exc) -> None:
        pass

    def iterate(self, generator):
        return generator


NULL_RUN = _NullRun()


def start_run(store_path: str | Path, **metadata) -> Run | _NullRun:
    """A new run writing to `store_path`, or a no-op when tracing is off or broken."""
    if not enabled():
        return NULL_RUN
    try:
        return Run(TraceStore(store_path), **metadata)
    except Exception:
        logger.exception("trace: could not start a run; continuing untraced")
        return NULL_RUN


def current() -> Run | None:
    return _CURRENT.get()


def safely(function, *args, **kwargs):
    """Run trace-recording code; a bug in it is logged and never reaches the request."""
    try:
        return function(*args, **kwargs)
    except Exception:
        logger.exception("trace: recording failed; the request continues untraced for this step")
        return None


def span(stage: str, name: str, input: Any = None):
    run = _CURRENT.get()
    return run.span(stage, name, input) if run is not None else NULL_SPAN


def record(stage: str, name: str, **kwargs):
    run = _CURRENT.get()
    return run.record(stage, name, **kwargs) if run is not None else NULL_SPAN


def fail_current(exc: BaseException, **attributes) -> None:
    run = _CURRENT.get()
    if run is not None:
        run.fail(exc, **attributes)


# --------------------------------------------------------------------------
# Provider instrumentation
# --------------------------------------------------------------------------


def wrap_provider(provider):
    """The provider itself when no run is active; otherwise a recording proxy."""
    run = _CURRENT.get()
    return provider if run is None else TracedProvider(provider, run)


def _messages_hash(messages: list[dict]) -> str:
    return sha256_text(json.dumps(messages, ensure_ascii=False, sort_keys=True))


def _prompt_facts(inner, messages: list[dict], kwargs: dict) -> tuple[dict, dict]:
    """(input, attributes) for an llm_call, including logical vs effective prompt."""
    import llm_provider

    effective, adaptations = messages, ()
    if isinstance(inner, llm_provider.OpenAICompatibleProvider):
        effective, adaptations = llm_provider.adapt_json_prompt(messages, kwargs.get("response_format"))
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    tools = kwargs.get("tools")
    call_input = {
        "messages": messages,
        "response_format": kwargs.get("response_format"),
        "temperature": kwargs.get("temperature"),
        "max_tokens": kwargs.get("max_tokens"),
        "tools": [t.get("function", {}).get("name") for t in tools] if tools else None,
    }
    if effective is not messages and effective != messages:
        call_input["effective_messages"] = effective
    attributes = {
        "logical_prompt_sha256": _messages_hash(messages),
        "effective_prompt_sha256": _messages_hash(effective),
        "system_prompt_sha256": sha256_text(system),
        "prompt_adaptations": list(adaptations),
    }
    return call_input, attributes


def _record_response(span: Span, response, content: str | None = None) -> None:
    if response is None:
        return
    span.prompt_tokens = response.prompt_tokens
    span.completion_tokens = response.completion_tokens
    span.output = {
        "content": response.content if content is None else content,
        "reasoning": response.reasoning,
        "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in response.tool_calls],
        "finish_reason": response.finish_reason,
    }
    if content is None and getattr(response, "raw_content", "") and response.raw_content != response.content:
        span.output["raw_content"] = response.raw_content
    span.attributes["provider_latency_ms"] = response.latency_seconds * 1000
    if response.prompt_adaptations:
        span.attributes["prompt_adaptations"] = list(response.prompt_adaptations)


class TracedProvider:
    """Records every chat / chat_stream call of the wrapped provider as an llm_call span."""

    def __init__(self, inner, run: Run) -> None:
        self._inner = inner
        self._run = run
        self.name = inner.name
        self.model = inner.model

    def _open(self, caller: str, messages, kwargs) -> Span:
        facts = safely(_prompt_facts, self._inner, messages, kwargs)
        call_input, attributes = facts if facts else ({"messages": messages}, {})
        span = self._run.span(LLM_CALL, caller, input=call_input)
        span.provider, span.model = self._inner.name, self._inner.model
        span.attributes.update(attributes)
        return span

    def chat(self, messages, **kwargs):
        span = self._open(sys._getframe(1).f_code.co_name, messages, kwargs)
        with span:
            response = self._inner.chat(messages, **kwargs)
            safely(_record_response, span, response)
        return response

    def chat_stream(self, messages, **kwargs):
        import llm_provider

        caller = sys._getframe(1).f_code.co_name
        inner_stream = self._inner.chat_stream(messages, **kwargs)

        def produce(stream):
            span = self._open(caller, messages, kwargs)
            span.attributes["streaming"] = True
            parts: list[str] = []
            started = perf_counter()
            with span:
                for delta in inner_stream:
                    if not parts:
                        span.attributes["first_delta_ms"] = (perf_counter() - started) * 1000
                    parts.append(delta)
                    yield delta
                stream.response = inner_stream.response
                safely(_record_response, span, inner_stream.response, content="".join(parts))
                span.attributes["delta_count"] = len(parts)

        return llm_provider.LLMStream(produce)


# --------------------------------------------------------------------------
# Reading traces back (CLI)
# --------------------------------------------------------------------------


def format_trace(trace: dict) -> str:
    """A run and its span tree as text - the whole execution, from SQLite alone."""
    run, spans = trace["run"], trace["spans"]
    lines = [
        f"run {run['run_id']}  {run['status']}"
        + (f"  failed_stage={run['failed_stage']}" if run["failed_stage"] else ""),
        f"  {run['kind']} {run['entrypoint']} mode={run['mode']} streaming={bool(run['streaming'])}"
        f"  {run['started_at']}  {run['duration_ms'] or 0:.1f}ms",
        f"  commit={(run['git_commit'] or '-')[:12]}{'+dirty' if run['git_dirty'] else ''}"
        f"  provider={run['provider']}/{run['model']}"
        f"  llm_calls={run['llm_calls']} tokens={run['prompt_tokens']}+{run['completion_tokens']}",
        f"  question: {run['question']}",
    ]
    if run.get("case_id"):
        lines.append(f"  eval: {run['dataset']} case={run['case_id']} run={run['eval_run_index']}")
    if run["error_type"]:
        lines.append(f"  error: {run['error_type']}: {run['error_message']}")
    children: dict[str | None, list[dict]] = {}
    for item in spans:
        children.setdefault(item["parent_span_id"], []).append(item)

    def walk(parent: str | None, depth: int) -> None:
        for item in sorted(children.get(parent, []), key=lambda s: (s["offset_ms"] or 0, s["seq"])):
            mark = "✗" if item["status"] == "error" else "·"
            detail = ""
            if item["stage"] == LLM_CALL:
                detail = f" tokens={item['prompt_tokens']}+{item['completion_tokens']}"
            if item["error_type"]:
                detail += f" error={item['error_type']}" + (f"({item['error_code']})" if item["error_code"] else "")
            latency = f"{item['latency_ms']:.1f}ms" if item["latency_ms"] is not None else "-"
            failed = "  <= failed here" if item["span_id"] == run["failed_span_id"] else ""
            lines.append(f"  {'  ' * depth}{mark} [{item['stage']}] {item['name']} "
                         f"{item['status']} {latency}{detail}{failed}")
            walk(item["span_id"], depth + 1)

    walk(None, 1)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_trace", description="Read persisted agent traces.")
    parser.add_argument("--db", default=str(ROOT / "data" / "knowledge_agent.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="recent runs")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--status", choices=[STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED])
    show = sub.add_parser("show", help="one run as a span tree")
    show.add_argument("run_id")
    show.add_argument("--json", action="store_true", help="print the raw decoded trace")
    args = parser.parse_args(argv)
    store = TraceStore(args.db)
    if args.command == "list":
        for row in store.recent(args.limit, args.status):
            print(f"{row['run_id']}  {row['status']:<9} {row['failed_stage'] or '-':<10} "
                  f"{row['entrypoint']:<30} {row['case_id'] or '-':<24} {row['question'][:40]}")
        return 0
    trace = store.load(args.run_id)
    if trace is None:
        print(f"run {args.run_id} not found in {args.db}", file=sys.stderr)
        return 1
    print(json.dumps(trace, ensure_ascii=False, indent=2) if args.json else format_trace(trace))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    sys.exit(main())
