"""Run a validated Plan against the three read-only tools, exactly once each.

One pass, fixed order, no retry, no supplementary query, no re-planning. There
is no agent loop to bound because there is no loop.

Three boundaries do the real work:

- **Nothing is discovered.** Chunks, Wiki pages, the database connection, the
  subject identity, and the System request all arrive through
  `ExecutionContext`. The executor opens no connection, loads no Wiki, and reads
  no file.
- **Total preflight.** Every static error is raised before timing starts, before
  the assertion provider runs, and before any adapter is called. A request that
  cannot succeed must not half-run.
- **Every ERROR is sanitized.** Whether a step failed by raising or by returning
  `ToolStatus.ERROR`, the payload that reaches the bundle is a fixed, safe
  string. Raw `str(exc)` and an adapter's own error fields and trace are
  discarded, because this bundle is designed to be serialized and persisted.

The System boundary is the sharpest one: the caller supplies a structured
`SystemRequest`, no SQL crosses it, and `subject_id` comes only from the trusted
context - never from the question, a model, or the caller's parameter map.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from time import perf_counter
from types import MappingProxyType
from typing import Callable, Mapping

from rag import Chunk

from .contracts import ToolResult, ToolStatus
from .document_adapter import document_search
from .evidence_policy import FactAssertion, PolicyDecision, evaluate_evidence
from .planner import Plan, ToolName
from .system_provider import OPERATION_PARAMETERS, SystemOperation, system_query
from .wiki_adapter import wiki_query
from .wiki_schema import WikiPage

MAX_STEPS = 3
SUBJECT_ID_PARAMETER = "subject_id"

# Stable taxonomy codes. Never an adapter's or an exception's own code.
ERROR_CODE_EXECUTION_FAILED = "tool_execution_failed"
ERROR_CODE_TOOL_REPORTED = "tool_reported_error"

_WHITESPACE = re.compile(r"\s+")

# SQL must never leave the provider that owns it. Matched against string leaves
# after whitespace is collapsed, so newline-separated SQL cannot slip past.
SQL_MARKERS = (
    "select ",
    "insert ",
    "update ",
    "delete ",
    "replace ",
    "drop ",
    "alter ",
    "create ",
    " from ",
    " where ",
    "pragma ",
    "attach ",
)

# The exact field names M4's system_query publishes in its trace. Restated here
# because M4 exports no constant for them; `test_system_trace_fields_match_m4`
# pins this against a real system_query call, so a change in M4 fails loudly
# instead of silently widening what may be published.
SYSTEM_TRACE_FIELDS = frozenset(
    {
        "system_operation",
        "system_parameter_names",
        "system_rows_matched",
        "system_returned_evidence",
    }
)

AssertionProvider = Callable[
    [Mapping[ToolName, ToolResult]], "tuple[FactAssertion, ...]"
]


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SystemRequest:
    """The System operation the caller wants, stated structurally.

    M6A calls no model and accepts no raw model text. The executor cannot prove
    where this came from, so the obligation is the caller's: it is responsible
    for the trustworthiness of this request.
    """

    operation: SystemOperation
    parameters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ExecutionContext:
    """Everything the tools need, injected. Nothing is read from a global."""

    chunks: tuple[Chunk, ...] = ()
    wiki_pages: tuple[WikiPage, ...] = ()
    system_connection: sqlite3.Connection | None = None
    # Must originate from a trusted server-side identity resolver. A
    # client-submitted field such as api.py's `client_id` is not an identity.
    subject_id: str | None = None
    system_request: SystemRequest | None = None
    document_top_k: int = 4
    wiki_top_k: int = 3
    knowledge_version: int | None = None


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ToolExecutionError:
    """One exception the executor caught and converted.

    Only the class name survives: exception text can carry a query, a parameter
    value, a row, or a path.
    """

    tool: ToolName
    exception_type: str

    def to_dict(self) -> dict[str, object]:
        return {"tool": self.tool.value, "exception_type": self.exception_type}


@dataclass(frozen=True, kw_only=True)
class ExecutionBundle:
    """What happened, and what the policy layer made of it.

    `results` and `trace` are `MappingProxyType`: that prevents adding,
    removing, or replacing top-level keys. It does **not** make the nested
    `ToolResult` and `Evidence` objects immutable - they are ordinary mutable
    dataclasses. Only top-level isolation is promised.
    """

    plan: Plan
    results: Mapping[ToolName, ToolResult]
    decision: PolicyDecision
    trace: Mapping[str, object]
    tool_errors: tuple[ToolExecutionError, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_dict(),
            "results": {
                tool.value: result.to_dict() for tool, result in self.results.items()
            },
            "decision": self.decision.to_dict(),
            "trace": dict(self.trace),
            "tool_errors": [item.to_dict() for item in self.tool_errors],
        }


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def _require_text(path: str, value: object) -> None:
    if not isinstance(value, str):
        raise ValueError(path + " must be a string, got " + type(value).__name__)
    if not value.strip():
        raise ValueError(path + " must not be empty")


def _require_top_k(path: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(path + " must be an integer, got " + type(value).__name__)
    if value < 1:
        raise ValueError(path + " must be at least 1")


def _require_corpus(path: str, value: object, expected: type, tool: ToolName) -> None:
    if not isinstance(value, tuple):
        raise ValueError(path + " must be a tuple, got " + type(value).__name__)
    if not value:
        raise ValueError(path + " must not be empty when " + tool.value + " is planned")
    for index, item in enumerate(value):
        if not isinstance(item, expected):
            raise ValueError(
                path + "[" + str(index) + "] must be a " + expected.__name__
                + ", got " + type(item).__name__
            )


def _merged_system_parameters(context: ExecutionContext) -> dict[str, str]:
    """Copy the caller's parameters, then inject the trusted subject.

    The caller's mapping is never mutated and never handed onward by reference.
    """
    request = context.system_request
    if not isinstance(request, SystemRequest):
        raise ValueError(
            "context.system_request must be a SystemRequest, got "
            + type(request).__name__
        )
    if not isinstance(request.operation, SystemOperation):
        raise ValueError(
            "context.system_request.operation must be a SystemOperation member, got "
            + type(request.operation).__name__
        )
    if not isinstance(request.parameters, Mapping):
        raise ValueError(
            "context.system_request.parameters must be a mapping, got "
            + type(request.parameters).__name__
        )

    # Keys first, before any sort or join touches them.
    for key in request.parameters:
        if not isinstance(key, str):
            raise ValueError(
                "context.system_request.parameters keys must all be strings, got "
                + type(key).__name__
            )

    merged: dict[str, str] = {}
    for key in request.parameters:
        path = "context.system_request.parameters." + key
        if key == SUBJECT_ID_PARAMETER:
            raise ValueError(
                "context.system_request.parameters must not carry "
                + SUBJECT_ID_PARAMETER
                + "; it comes from the trusted context"
            )
        value = request.parameters[key]
        # Names are safe to report; values never are.
        if not isinstance(value, str):
            raise ValueError(
                path + " must be a string, got " + type(value).__name__
            )
        if not value.strip():
            raise ValueError(path + " must not be empty")
        merged[key] = value

    required = OPERATION_PARAMETERS[request.operation]
    # Subject before merge: a missing identity must be reported as a missing
    # identity, not as a confusing parameter-set mismatch one step later.
    if SUBJECT_ID_PARAMETER in required:
        subject = context.subject_id
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError(
                "context.subject_id must be a non-empty string for "
                + request.operation.value
                + "; subject-scoped operations require a trusted identity"
            )
        merged[SUBJECT_ID_PARAMETER] = subject

    missing = sorted(set(required) - set(merged))
    unexpected = sorted(set(merged) - set(required))
    if missing:
        raise ValueError(
            request.operation.value + " is missing parameters: " + ", ".join(missing)
        )
    if unexpected:
        raise ValueError(
            request.operation.value
            + " does not accept parameters: "
            + ", ".join(unexpected)
        )
    return merged


def _preflight(
    question: object,
    plan: object,
    context: object,
    assertion_provider: object,
) -> dict[str, str] | None:
    _require_text("question", question)
    if not isinstance(plan, Plan):
        raise ValueError("plan must be a Plan, got " + type(plan).__name__)
    if not isinstance(context, ExecutionContext):
        raise ValueError(
            "context must be an ExecutionContext, got " + type(context).__name__
        )
    if assertion_provider is not None and not callable(assertion_provider):
        raise ValueError(
            "assertion_provider must be callable or None, got "
            + type(assertion_provider).__name__
        )

    steps = plan.steps
    if not isinstance(steps, tuple):
        raise ValueError("plan.steps must be a tuple, got " + type(steps).__name__)
    if len(steps) > MAX_STEPS:
        raise ValueError(
            "plan.steps must contain at most " + str(MAX_STEPS) + " steps"
        )
    for index, step in enumerate(steps):
        if not isinstance(step, ToolName):
            raise ValueError(
                "plan.steps[" + str(index) + "] must be a ToolName, got "
                + type(step).__name__
            )

    # Validated unconditionally: it always reaches the trace.
    version = context.knowledge_version
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                "context.knowledge_version must be an integer or None, got "
                + type(version).__name__
            )
        if version < 0:
            raise ValueError("context.knowledge_version must not be negative")

    # Step 4: ALL planned dependencies, before any top_k. Interleaving the two
    # would report a top_k problem while a corpus is still missing.
    if ToolName.DOCUMENT_SEARCH in steps:
        _require_corpus(
            "context.chunks", context.chunks, Chunk, ToolName.DOCUMENT_SEARCH
        )
    if ToolName.WIKI_QUERY in steps:
        _require_corpus(
            "context.wiki_pages", context.wiki_pages, WikiPage, ToolName.WIKI_QUERY
        )
    if ToolName.SYSTEM_QUERY in steps:
        if context.system_connection is None:
            raise ValueError(
                "context.system_connection is required when system_query is planned"
            )
        if context.system_request is None:
            raise ValueError(
                "context.system_request is required when system_query is planned"
            )

    # Step 5: ALL planned top_k values.
    if ToolName.DOCUMENT_SEARCH in steps:
        _require_top_k("context.document_top_k", context.document_top_k)
    if ToolName.WIKI_QUERY in steps:
        _require_top_k("context.wiki_top_k", context.wiki_top_k)

    if ToolName.SYSTEM_QUERY in steps:
        if context.system_connection is None:
            raise ValueError(
                "context.system_connection is required when system_query is planned"
            )
        return _merged_system_parameters(context)
    return None


# --------------------------------------------------------------------------
# Invocation and error sanitization
# --------------------------------------------------------------------------


def _invoke(
    tool: ToolName,
    question: str,
    context: ExecutionContext,
    merged_parameters: dict[str, str] | None,
    tool_trace: dict,
) -> ToolResult:
    if tool is ToolName.WIKI_QUERY:
        return wiki_query(
            question, context.wiki_pages, top_k=context.wiki_top_k, trace=tool_trace
        )
    if tool is ToolName.DOCUMENT_SEARCH:
        # A container copy so the adapter cannot retain caller state. The Chunk
        # objects are shared, and the adapters are trusted to be read-only.
        return document_search(
            question,
            list(context.chunks),
            top_k=context.document_top_k,
            trace=tool_trace,
        )
    return system_query(
        context.system_connection,
        context.system_request.operation,
        merged_parameters,
        trace=tool_trace,
    )


def _require_tool_result(tool: ToolName, result: object) -> None:
    """Mirror the whole ToolResult runtime invariant, before anything reads it.

    `ToolResult` validates on construction but is not frozen, so a mutated
    result carries no guarantee by the time it reaches here. This runs before
    the assertion provider is called, so a malformed result can never be shown
    to caller code - and before sanitization, so a violation is reported rather
    than silently repaired.
    """
    prefix = "results[" + tool.value + "]"
    if not isinstance(result, ToolResult):
        raise ValueError(
            prefix + " adapter returned " + type(result).__name__
            + ", expected a ToolResult"
        )
    if result.tool_name != tool.value:
        raise ValueError(prefix + ".tool_name does not match the invoked tool")
    if not isinstance(result.status, ToolStatus):
        raise ValueError(
            prefix + ".status must be a ToolStatus member, got "
            + type(result.status).__name__
        )
    if not isinstance(result.evidence, tuple):
        raise ValueError(
            prefix + ".evidence must be a tuple, got " + type(result.evidence).__name__
        )
    if not isinstance(result.trace, Mapping):
        raise ValueError(
            prefix + ".trace must be a mapping, got " + type(result.trace).__name__
        )

    has_error_fields = result.error_code is not None or result.error_message is not None
    if result.status is ToolStatus.OK:
        if not result.evidence:
            raise ValueError(prefix + " is ok but carries no evidence")
        if has_error_fields:
            raise ValueError(prefix + " is ok but carries error fields")
    elif result.status is ToolStatus.EMPTY:
        if result.evidence:
            raise ValueError(prefix + " is empty but carries evidence")
        if has_error_fields:
            raise ValueError(prefix + " is empty but carries error fields")
    else:
        if result.evidence:
            raise ValueError(prefix + " reported an error but carries evidence")
        for name in ("error_code", "error_message"):
            value = getattr(result, name)
            # Describe the violation only; never echo the payload.
            if not isinstance(value, str) or not value.strip():
                raise ValueError(prefix + "." + name + " must be a non-empty string")


def _assert_trace_is_publishable(
    tool: ToolName, trace: Mapping, secrets: frozenset[str]
) -> None:
    """Enforce the whole privacy matrix on a trace, not just the part we expect.

    Three guarantees, because a trace is copied verbatim into the bundle and the
    bundle is designed to be serialized and persisted:

    1. no system parameter value and no subject identity - the matrix allows a
       business key such as `order_id` inside `Evidence`, since that *is* the
       evidence for which record, but forbids it in any trace;
    2. no SQL text;
    3. for `system_query`, the full published schema - see
       `_assert_system_trace_schema`. A key allowlist alone is not enough: a
       payload can ride *inside* a legitimate field, so the types and values are
       pinned too.

    Only string keys and string leaves are inspected, so an integer count can
    never collide with a value. A pathologically short parameter value could
    still match a substring of unrelated text, which fails closed - the safe
    direction for a privacy check.
    """
    prefix = "results[" + tool.value + "].trace"

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, (list, tuple, set, frozenset)):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            for secret in secrets:
                if secret in node:
                    raise ValueError(
                        prefix + " carries a parameter value or subject identity; "
                        "traces must record names only"
                    )
            # Collapse whitespace first: a newline-separated statement is still
            # SQL, and a space-delimited marker list alone would miss it.
            flattened = _WHITESPACE.sub(" ", node).lower()
            for marker in SQL_MARKERS:
                if marker in flattened:
                    raise ValueError(
                        prefix + " carries SQL text; SQL must not leave the "
                        "provider that owns it"
                    )

    # Value scan first, so the schema messages below cannot echo a secret that
    # was used as a key.
    walk(trace)


def _assert_system_trace_schema(
    trace: Mapping,
    *,
    operation_value: str,
    parameter_names: list[str],
    evidence_count: int,
) -> None:
    """Pin the System trace's whole shape, not just its field names.

    A key allowlist stops an extra `debug_sql` key but not a payload smuggled
    inside a legitimate one - `system_rows_matched: {"raw_row": ...}` uses only
    approved names. Requiring every field, its type, and its value defeats that,
    and also catches a trace that silently disagrees with the call it claims to
    describe.
    """
    prefix = "results[system_query].trace"

    missing = sorted(SYSTEM_TRACE_FIELDS - set(trace))
    if missing:
        raise ValueError(prefix + " is missing required field(s): " + ", ".join(missing))
    unknown = sorted(str(key) for key in trace if key not in SYSTEM_TRACE_FIELDS)
    if unknown:
        raise ValueError(
            prefix + " has unpublished field(s): " + ", ".join(unknown)
            + "; the system trace may carry only "
            + ", ".join(sorted(SYSTEM_TRACE_FIELDS))
        )

    operation = trace["system_operation"]
    if not isinstance(operation, str):
        raise ValueError(
            prefix + ".system_operation must be a string, got "
            + type(operation).__name__
        )
    if operation != operation_value:
        raise ValueError(
            prefix + ".system_operation does not match the invoked operation"
        )

    names = trace["system_parameter_names"]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError(prefix + ".system_parameter_names must be a list of strings")
    if names != parameter_names:
        raise ValueError(
            prefix + ".system_parameter_names does not match the parameters passed"
        )

    for name in ("system_rows_matched", "system_returned_evidence"):
        value = trace[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                prefix + "." + name + " must be an integer, got " + type(value).__name__
            )
        if value < 0:
            raise ValueError(prefix + "." + name + " must not be negative")

    if trace["system_returned_evidence"] != evidence_count:
        raise ValueError(
            prefix + ".system_returned_evidence disagrees with the evidence returned"
        )
    if trace["system_rows_matched"] < trace["system_returned_evidence"]:
        raise ValueError(
            prefix + ".system_rows_matched is less than system_returned_evidence"
        )


def _sanitize_reported_error(tool: ToolName, result: ToolResult) -> ToolResult:
    """Replace an adapter-reported error payload with a fixed, safe one.

    The shape was already checked by `_require_tool_result`, which runs first
    precisely because sanitization blanks evidence and trace and would otherwise
    silently repair a malformed result.
    """
    return ToolResult(
        tool_name=tool.value,
        status=ToolStatus.ERROR,
        evidence=(),
        error_code=ERROR_CODE_TOOL_REPORTED,
        error_message=tool.value + " returned an error",
        trace={},
    )


def _exception_result(tool: ToolName, exc: Exception) -> ToolResult:
    return ToolResult(
        tool_name=tool.value,
        status=ToolStatus.ERROR,
        evidence=(),
        error_code=ERROR_CODE_EXECUTION_FAILED,
        # Class name only. str(exc) is never captured, anywhere.
        error_message=tool.value + " failed with " + type(exc).__name__,
        trace={},
    )


def _default_assertion_provider(
    results: Mapping[ToolName, ToolResult],
) -> tuple[FactAssertion, ...]:
    """No assertions by default.

    M6A ships no content-parsing provider. While the product uses this default,
    the chain performs no conflict adjudication at all - M5's authority
    resolution exists and is tested, but nothing exercises it in production.
    """
    return ()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def execute_plan(
    question: str,
    plan: Plan,
    context: ExecutionContext,
    *,
    assertion_provider: AssertionProvider | None = None,
) -> ExecutionBundle:
    """Execute `plan` once against the injected context and judge the result.

    Static errors raise `ValueError` before anything runs. Tool runtime failures
    become sanitized `ERROR` results and do not stop the pass. A `REFUSE`
    decision is returned normally, never raised: rendering it is M6B's job.
    """
    merged_parameters = _preflight(question, plan, context, assertion_provider)

    # Values that must never survive in any trace: every system parameter value,
    # plus the subject identity even when this operation does not take one.
    secrets = set(merged_parameters.values()) if merged_parameters else set()
    if isinstance(context.subject_id, str) and context.subject_id.strip():
        secrets.add(context.subject_id)
    trace_secrets = frozenset(secrets)

    results: dict[ToolName, ToolResult] = {}
    tool_errors: list[ToolExecutionError] = []
    executed_steps: list[str] = []
    step_status: dict[str, str] = {}
    step_seconds: dict[str, float] = {}
    tool_traces: dict[str, dict] = {}

    started = perf_counter()
    for tool in plan.steps:
        tool_trace: dict = {}
        step_started = perf_counter()
        try:
            result = _invoke(tool, question, context, merged_parameters, tool_trace)
        except (ValueError, TypeError):
            # The executor called the adapter wrongly. That is a programmer
            # error and must not be laundered into "the system was unavailable".
            raise
        except Exception as exc:
            result = _exception_result(tool, exc)
            tool_errors.append(
                ToolExecutionError(tool=tool, exception_type=type(exc).__name__)
            )
        else:
            # Full invariant mirror before anything - including the assertion
            # provider - is allowed to see this result.
            _require_tool_result(tool, result)
            if result.status is ToolStatus.ERROR:
                result = _sanitize_reported_error(tool, result)
            else:
                _assert_trace_is_publishable(tool, result.trace, trace_secrets)
                if tool is ToolName.SYSTEM_QUERY:
                    _assert_system_trace_schema(
                        result.trace,
                        operation_value=context.system_request.operation.value,
                        parameter_names=sorted(merged_parameters or {}),
                        evidence_count=len(result.evidence),
                    )

        step_seconds[tool.value] = perf_counter() - step_started
        executed_steps.append(tool.value)
        step_status[tool.value] = result.status.value
        # Shallow copy: top-level keys are independent, nested values shared.
        tool_traces[tool.value] = dict(result.trace)
        results[tool] = result

    results_view = MappingProxyType(results)

    # `is None`, not truthiness: preflight admits any callable, and a callable
    # that happens to be falsy (it defines __bool__ or __len__) must still be
    # invoked. Selecting with `or` would silently swap it for the default and
    # report "no facts asserted" - the exact silent degradation this layer
    # refuses to perform.
    provider = (
        _default_assertion_provider if assertion_provider is None else assertion_provider
    )
    # A broken provider is an integration error; letting it degrade into "no
    # facts asserted" would turn a bug into a quietly weaker answer.
    assertions = provider(results_view)

    decision = evaluate_evidence(plan, results_view, assertions=assertions)
    total_seconds = perf_counter() - started

    trace: dict[str, object] = {
        "executor_route": plan.route.value,
        "executor_planned_steps": [step.value for step in plan.steps],
        "executor_executed_steps": executed_steps,
        "executor_step_status": step_status,
        "executor_step_seconds": step_seconds,
        "executor_tool_traces": tool_traces,
        "executor_total_seconds": total_seconds,
        "executor_outcome": decision.outcome.value,
        "executor_knowledge_version": context.knowledge_version,
    }

    return ExecutionBundle(
        plan=plan,
        results=results_view,
        decision=decision,
        trace=MappingProxyType(trace),
        tool_errors=tuple(tool_errors),
    )
