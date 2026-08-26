"""Decide whether gathered evidence is good enough, and what it may be used for.

This layer is pure: it returns a structured decision and nothing else. It writes
no answer text, numbers no citation, executes no tool, and reads no clock,
database, or network.

Two ideas do the work:

- **Facts are stated, never guessed.** `Evidence` carries content, not a parsed
  claim, so the caller supplies explicit `FactAssertion`s. This module never
  parses `Evidence.content` or infers meaning from keywords.
- **Authority is contextual.** A live system record is decisive about current
  state and irrelevant to what a policy *means*, so `SYSTEM 100` must not simply
  outrank `DOCUMENT 80` everywhere. In `policy` scope System is removed by the
  eligibility filter - it is never merely outranked - and arbitration among the
  remaining assertions then uses `Evidence.authority` alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .contracts import (
    AUTHORITY_MAX,
    AUTHORITY_MIN,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    Evidence,
    SourceType,
    ToolResult,
    ToolStatus,
)
from .planner import Plan, ToolName


class FactScope(str, Enum):
    POLICY = "policy"
    CURRENT_OPERATIONAL_STATE = "current_operational_state"


class PolicyOutcome(str, Enum):
    DIRECT = "direct"
    READY = "ready"
    REFUSE = "refuse"


FAILURE_MISSING = "missing"
FAILURE_EMPTY = "empty"
FAILURE_ERROR = "error"
FAILURE_KINDS = (FAILURE_MISSING, FAILURE_EMPTY, FAILURE_ERROR)

# The value the System provider writes into metadata for live state.
AUTHORITY_SCOPE_CURRENT_STATE = "current_operational_state"

TOOL_SOURCE_TYPE: dict[ToolName, SourceType] = {
    ToolName.WIKI_QUERY: SourceType.WIKI,
    ToolName.DOCUMENT_SEARCH: SourceType.DOCUMENT,
    ToolName.SYSTEM_QUERY: SourceType.SYSTEM,
}


# --------------------------------------------------------------------------
# Reason codes
# --------------------------------------------------------------------------

REASON_PLAN_DIRECT = "plan_direct"
REASON_MISSING_TOOL_RESULT = "missing_tool_result"
REASON_EMPTY_TOOL_RESULT = "empty_tool_result"
REASON_TOOL_ERROR = "tool_error"
REASON_SYSTEM_MISSING_OBSERVED_AT = "system_evidence_missing_observed_at"
REASON_SYSTEM_MISSING_AUTHORITY_SCOPE = "system_evidence_missing_authority_scope"
REASON_FRESHNESS_UNSUPPORTED = "freshness_unsupported"
REASON_EXACT_CITATION_MISSING_DOCUMENT = "exact_citation_missing_document"
REASON_EXACT_CITATION_MISSING_LOCATOR = "exact_citation_missing_locator"
REASON_SYSTEM_EXCLUDED_FROM_POLICY = "system_excluded_from_policy"
REASON_FACT_CONFLICT_RESOLVED = "fact_conflict_resolved"
REASON_FACT_CONFLICT_UNRESOLVED = "fact_conflict_unresolved"
REASON_EVIDENCE_SUFFICIENT = "evidence_sufficient"

REASON_CODE_ORDER = (
    REASON_PLAN_DIRECT,
    REASON_MISSING_TOOL_RESULT,
    REASON_EMPTY_TOOL_RESULT,
    REASON_TOOL_ERROR,
    REASON_SYSTEM_MISSING_OBSERVED_AT,
    REASON_SYSTEM_MISSING_AUTHORITY_SCOPE,
    REASON_FRESHNESS_UNSUPPORTED,
    REASON_EXACT_CITATION_MISSING_DOCUMENT,
    REASON_EXACT_CITATION_MISSING_LOCATOR,
    REASON_SYSTEM_EXCLUDED_FROM_POLICY,
    REASON_FACT_CONFLICT_RESOLVED,
    REASON_FACT_CONFLICT_UNRESOLVED,
    REASON_EVIDENCE_SUFFICIENT,
)

# Codes that make a request unanswerable. The rest are informational.
BLOCKING_REASON_CODES = frozenset(
    {
        REASON_MISSING_TOOL_RESULT,
        REASON_EMPTY_TOOL_RESULT,
        REASON_TOOL_ERROR,
        REASON_SYSTEM_MISSING_OBSERVED_AT,
        REASON_SYSTEM_MISSING_AUTHORITY_SCOPE,
        REASON_FRESHNESS_UNSUPPORTED,
        REASON_EXACT_CITATION_MISSING_DOCUMENT,
        REASON_EXACT_CITATION_MISSING_LOCATOR,
        REASON_FACT_CONFLICT_UNRESOLVED,
    }
)


def _require_text(path: str, value: object) -> None:
    if not isinstance(value, str):
        raise ValueError(path + " must be a string, got " + type(value).__name__)
    if not value.strip():
        raise ValueError(path + " must not be empty")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class FactAssertion:
    """A claim the caller says a specific Evidence makes.

    M5 never derives one of these from text; stating the fact is the caller's
    job, which is what keeps this layer free of inference.
    """

    evidence: Evidence
    scope: FactScope
    subject: str
    fact_key: str
    fact_value: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, Evidence):
            raise ValueError(
                "FactAssertion.evidence must be an Evidence, got "
                + type(self.evidence).__name__
            )
        if not isinstance(self.scope, FactScope):
            raise ValueError(
                "FactAssertion.scope must be a FactScope member, got "
                + type(self.scope).__name__
            )
        _require_text("FactAssertion.subject", self.subject)
        _require_text("FactAssertion.fact_key", self.fact_key)
        _require_text("FactAssertion.fact_value", self.fact_value)

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.value,
            "subject": self.subject,
            "fact_key": self.fact_key,
            "fact_value": self.fact_value,
            "evidence": self.evidence.to_dict(),
        }


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ToolFailure:
    """Why one planned step yielded nothing usable.

    Deliberately only two fields: an `error_code` or `error_message` would carry
    an infrastructure payload into a policy decision that may be serialized and
    logged. `kind` is all a policy consumer needs.
    """

    tool: ToolName
    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.tool, ToolName):
            raise ValueError(
                "ToolFailure.tool must be a ToolName member, got "
                + type(self.tool).__name__
            )
        if self.kind not in FAILURE_KINDS:
            raise ValueError(
                "ToolFailure.kind must be one of " + ", ".join(FAILURE_KINDS)
            )

    def to_dict(self) -> dict[str, object]:
        return {"tool": self.tool.value, "kind": self.kind}


@dataclass(frozen=True, kw_only=True)
class FactResolution:
    """How one (scope, subject, fact_key) group came out.

    The four assertion buckets partition the group: every assertion lands in
    exactly one, so nothing a caller asserted can silently disappear.
    """

    scope: FactScope
    subject: str
    fact_key: str
    observed_values: tuple[str, ...]
    resolved: bool
    winning_value: str | None
    winning_assertions: tuple[FactAssertion, ...] = ()
    contending_assertions: tuple[FactAssertion, ...] = ()
    superseded_assertions: tuple[FactAssertion, ...] = ()
    ignored_assertions: tuple[FactAssertion, ...] = ()

    def __post_init__(self) -> None:
        if (self.winning_value is None) is not (self.resolved is False):
            raise ValueError(
                "FactResolution.winning_value must be None if and only if "
                "resolved is False"
            )
        if self.resolved and self.contending_assertions:
            raise ValueError(
                "FactResolution: a resolved group must have no contending assertions"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.value,
            "subject": self.subject,
            "fact_key": self.fact_key,
            "observed_values": list(self.observed_values),
            "resolved": self.resolved,
            "winning_value": self.winning_value,
            "winning_assertions": [a.to_dict() for a in self.winning_assertions],
            "contending_assertions": [a.to_dict() for a in self.contending_assertions],
            "superseded_assertions": [a.to_dict() for a in self.superseded_assertions],
            "ignored_assertions": [a.to_dict() for a in self.ignored_assertions],
        }


@dataclass(frozen=True, kw_only=True)
class PolicyDecision:
    outcome: PolicyOutcome
    usable_evidence: tuple[Evidence, ...] = ()
    exact_citation_evidence: tuple[Evidence, ...] = ()
    resolutions: tuple[FactResolution, ...] = ()
    missing_tools: tuple[ToolName, ...] = ()
    tool_failures: tuple[ToolFailure, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "usable_evidence": [item.to_dict() for item in self.usable_evidence],
            "exact_citation_evidence": [
                item.to_dict() for item in self.exact_citation_evidence
            ],
            "resolutions": [item.to_dict() for item in self.resolutions],
            "missing_tools": [tool.value for tool in self.missing_tools],
            "tool_failures": [item.to_dict() for item in self.tool_failures],
            "reason_codes": list(self.reason_codes),
        }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _validate_inputs(
    plan: object, results: object, assertions: object
) -> None:
    """Check shapes before anything is read, compared, or sorted.

    Messages name the offending key, index, or field. They never render the
    offending object, and never carry a ToolResult's error text.
    """
    if not isinstance(plan, Plan):
        raise ValueError("plan must be a Plan, got " + type(plan).__name__)
    if not isinstance(results, Mapping):
        raise ValueError("results must be a Mapping, got " + type(results).__name__)
    if not isinstance(assertions, tuple):
        raise ValueError(
            "assertions must be a tuple, got " + type(assertions).__name__
        )

    for key in results:
        if not isinstance(key, ToolName):
            raise ValueError(
                "results keys must be ToolName members, got " + type(key).__name__
            )
    for key in results:
        value = results[key]
        if not isinstance(value, ToolResult):
            raise ValueError(
                "results[" + key.value + "] must be a ToolResult, got "
                + type(value).__name__
            )

    for index, assertion in enumerate(assertions):
        if not isinstance(assertion, FactAssertion):
            raise ValueError(
                "assertions[" + str(index) + "] must be a FactAssertion, got "
                + type(assertion).__name__
            )


def _validate_evidence_shape(evidence: object, path: str) -> None:
    """Mirror the *entire* Evidence contract before reading any of it.

    `Evidence` validates on construction but is not frozen, so a field can be
    replaced afterwards and every construction-time guarantee is void by the
    time the policy layer runs. This checks each field the module dereferences,
    sorts by, or serializes - ahead of any `.strip()`, `.get()`, `.value`, or
    arithmetic - so malformed input surfaces as `ValueError` and never as an
    `AttributeError` or `TypeError`.
    """
    if not isinstance(evidence, Evidence):
        raise ValueError(
            path + " must be an Evidence, got " + type(evidence).__name__
        )

    _require_text(path + ".content", evidence.content)
    _require_text(path + ".source", evidence.source)

    if not isinstance(evidence.source_type, SourceType):
        raise ValueError(
            path + ".source_type must be a SourceType member, got "
            + type(evidence.source_type).__name__
        )
    if not isinstance(evidence.metadata, Mapping):
        raise ValueError(
            path + ".metadata must be a mapping, got "
            + type(evidence.metadata).__name__
        )
    for name in ("observed_at", "version", "locator"):
        value = getattr(evidence, name)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                path + "." + name + " must be a string or None, got "
                + type(value).__name__
            )

    # Authority is sorted on, so a non-int would fail inside the comparison.
    # bool is an int subclass and must be rejected explicitly.
    authority = evidence.authority
    if isinstance(authority, bool) or not isinstance(authority, int):
        raise ValueError(
            path + ".authority must be an integer, got " + type(authority).__name__
        )
    if not AUTHORITY_MIN <= authority <= AUTHORITY_MAX:
        raise ValueError(
            path + ".authority must be between " + str(AUTHORITY_MIN)
            + " and " + str(AUTHORITY_MAX)
        )

    confidence = evidence.confidence
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError(
                path + ".confidence must be a number or None, got "
                + type(confidence).__name__
            )
        if not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
            raise ValueError(
                path + ".confidence must be between " + str(CONFIDENCE_MIN)
                + " and " + str(CONFIDENCE_MAX)
            )


def _validate_wiring(
    plan: Plan, results: Mapping[ToolName, ToolResult]
) -> None:
    planned = set(plan.steps)
    for tool in results:
        if tool not in planned:
            raise ValueError(
                "results[" + tool.value + "] was not planned; plan.steps has "
                + (", ".join(step.value for step in plan.steps) or "no steps")
            )
    for tool in results:
        result = results[tool]
        if result.tool_name != tool.value:
            raise ValueError(
                "results[" + tool.value + "].tool_name does not match its key"
            )
        # An unrecognised status must never fall through to "usable". The
        # ToolResult invariant routes any unknown value into its error branch,
        # so such an object is constructible and would otherwise look OK here.
        if not isinstance(result.status, ToolStatus):
            raise ValueError(
                "results[" + tool.value + "].status must be a ToolStatus member, got "
                + type(result.status).__name__
            )
        # ToolResult is not frozen, so mirror its *whole* status invariant here
        # rather than trusting construction. A result mutated after the fact can
        # otherwise carry evidence on a failed step - which would let that
        # evidence reach fact resolution - or carry an error payload on a step
        # reported as successful.
        prefix = "results[" + tool.value + "]"
        # Before any truthiness test or iteration. A generator is always truthy
        # and is exhausted by the first pass, which would leave the sufficiency
        # stage reading an empty sequence and reporting success with no evidence.
        if not isinstance(result.evidence, tuple):
            raise ValueError(
                prefix + ".evidence must be a tuple, got "
                + type(result.evidence).__name__
            )

        if result.status is ToolStatus.OK:
            if not result.evidence:
                raise ValueError(prefix + " is ok but carries no evidence")
            if result.error_code is not None or result.error_message is not None:
                raise ValueError(prefix + " is ok but carries error fields")
        elif result.status is ToolStatus.EMPTY:
            if result.evidence:
                raise ValueError(prefix + " is empty but carries evidence")
            if result.error_code is not None or result.error_message is not None:
                raise ValueError(prefix + " is empty but carries error fields")
        else:
            if result.evidence:
                raise ValueError(prefix + " is error but carries evidence")
            for name in ("error_code", "error_message"):
                value = getattr(result, name)
                if not isinstance(value, str) or not value.strip():
                    # Describe the violation only; never echo the field's value.
                    raise ValueError(
                        prefix + "." + name + " must be a non-empty string"
                    )

        expected = TOOL_SOURCE_TYPE[tool]
        for position, evidence in enumerate(result.evidence):
            path = "results[" + tool.value + "].evidence[" + str(position) + "]"
            _validate_evidence_shape(evidence, path)
            if evidence.source_type is not expected:
                raise ValueError(
                    path + " has source_type " + evidence.source_type.value
                    + "; expected " + expected.value
                )


def _validate_assertions(
    results: Mapping[ToolName, ToolResult], assertions: tuple[FactAssertion, ...]
) -> None:
    # Second layer of defence: only a successful step can supply evidence a
    # caller may assert facts about. Evidence hanging off a failed result must
    # never reach fact resolution.
    known = set()
    for tool in results:
        result = results[tool]
        if result.status is not ToolStatus.OK:
            continue
        for evidence in result.evidence:
            known.add(id(evidence))

    seen: set[tuple[int, FactScope, str, str]] = set()
    for index, assertion in enumerate(assertions):
        if id(assertion.evidence) not in known:
            raise ValueError(
                "assertions[" + str(index)
                + "].evidence is not one of the Evidence objects in results"
            )
        key = (
            id(assertion.evidence),
            assertion.scope,
            assertion.subject,
            assertion.fact_key,
        )
        if key in seen:
            raise ValueError(
                "assertions[" + str(index) + "] duplicates an earlier assertion "
                "for the same evidence, scope, subject, and fact_key"
            )
        seen.add(key)


# --------------------------------------------------------------------------
# Conflict resolution
# --------------------------------------------------------------------------


def _is_eligible(assertion: FactAssertion) -> bool:
    """System never decides what a policy means; it is filtered, not outranked."""
    if assertion.scope is FactScope.POLICY:
        return assertion.evidence.source_type is not SourceType.SYSTEM
    return True


def _resolve_group(
    scope: FactScope,
    subject: str,
    fact_key: str,
    ordered: list[FactAssertion],
) -> tuple[FactResolution, bool]:
    """Bucket one group. Returns the resolution and whether it settled a real conflict."""
    ignored = tuple(a for a in ordered if not _is_eligible(a))
    eligible = [a for a in ordered if _is_eligible(a)]
    observed_values = tuple(sorted({a.fact_value for a in ordered}))

    if not eligible:
        return (
            FactResolution(
                scope=scope,
                subject=subject,
                fact_key=fact_key,
                observed_values=observed_values,
                resolved=False,
                winning_value=None,
                ignored_assertions=ignored,
            ),
            False,
        )

    eligible_values = {a.fact_value for a in eligible}
    top_authority = max(a.evidence.authority for a in eligible)
    top = [a for a in eligible if a.evidence.authority == top_authority]
    top_values = {a.fact_value for a in top}

    if len(top_values) > 1:
        # Lower tiers take no part: letting a weaker source break a tie between
        # stronger ones would invert the authority model.
        return (
            FactResolution(
                scope=scope,
                subject=subject,
                fact_key=fact_key,
                observed_values=observed_values,
                resolved=False,
                winning_value=None,
                contending_assertions=tuple(top),
                # Identity, not equality: two assertions can be value-equal yet
                # come from different Evidence objects.
                superseded_assertions=tuple(
                    a for a in eligible if id(a) not in {id(item) for item in top}
                ),
                ignored_assertions=ignored,
            ),
            False,
        )

    winning_value = next(iter(top_values))
    resolution = FactResolution(
        scope=scope,
        subject=subject,
        fact_key=fact_key,
        observed_values=observed_values,
        resolved=True,
        winning_value=winning_value,
        # Agreement from a lower tier corroborates; it did not lose anything.
        winning_assertions=tuple(a for a in eligible if a.fact_value == winning_value),
        superseded_assertions=tuple(
            a for a in eligible if a.fact_value != winning_value
        ),
        ignored_assertions=ignored,
    )
    # Only a disagreement among *eligible* assertions counts as a conflict that
    # authority settled; an excluded System value disagreeing does not.
    return resolution, len(eligible_values) > 1


def _resolve_assertions(
    assertions: tuple[FactAssertion, ...],
) -> tuple[tuple[FactResolution, ...], bool, bool, bool]:
    groups: dict[tuple[FactScope, str, str], list[tuple[int, FactAssertion]]] = {}
    for index, assertion in enumerate(assertions):
        key = (assertion.scope, assertion.subject, assertion.fact_key)
        groups.setdefault(key, []).append((index, assertion))

    resolutions: list[FactResolution] = []
    any_resolved_conflict = False
    any_unresolved = False
    any_ignored = False

    for key in sorted(groups, key=lambda item: (item[0].value, item[1], item[2])):
        scope, subject, fact_key = key
        ordered = [
            assertion
            for _, assertion in sorted(
                groups[key], key=lambda pair: (-pair[1].evidence.authority, pair[0])
            )
        ]
        resolution, settled_conflict = _resolve_group(scope, subject, fact_key, ordered)
        resolutions.append(resolution)
        any_resolved_conflict = any_resolved_conflict or settled_conflict
        any_unresolved = any_unresolved or not resolution.resolved
        any_ignored = any_ignored or bool(resolution.ignored_assertions)

    return tuple(resolutions), any_resolved_conflict, any_unresolved, any_ignored


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def evaluate_evidence(
    plan: Plan,
    results: Mapping[ToolName, ToolResult],
    *,
    assertions: tuple[FactAssertion, ...] = (),
) -> PolicyDecision:
    """Judge whether `results` can answer `plan`, and authorize their use.

    Every input is treated as read-only. Integration mistakes raise `ValueError`;
    a request that simply cannot be answered returns `REFUSE`. All checks run, so
    the decision lists every reason rather than only the first.
    """
    _validate_inputs(plan, results, assertions)
    _validate_wiring(plan, results)
    _validate_assertions(results, assertions)

    reasons: set[str] = set()

    if not plan.steps:
        # A direct plan needs no evidence, and accepts none (any result would
        # have been rejected as unplanned above).
        return PolicyDecision(
            outcome=PolicyOutcome.DIRECT,
            reason_codes=(REASON_PLAN_DIRECT,),
        )

    # --- route sufficiency -------------------------------------------------
    usable: list[Evidence] = []
    missing_tools: list[ToolName] = []
    tool_failures: list[ToolFailure] = []

    for tool in plan.steps:
        result = results.get(tool)
        if result is None:
            kind = FAILURE_MISSING
            reasons.add(REASON_MISSING_TOOL_RESULT)
        elif result.status is ToolStatus.EMPTY:
            kind = FAILURE_EMPTY
            reasons.add(REASON_EMPTY_TOOL_RESULT)
        elif result.status is ToolStatus.ERROR:
            kind = FAILURE_ERROR
            reasons.add(REASON_TOOL_ERROR)
        elif result.status is ToolStatus.OK:
            # Explicit, never a fall-through `else`: only a recognised OK may
            # contribute evidence. Wiring validation has already confirmed the
            # status is a real member and that OK carries matching evidence.
            usable.extend(result.evidence)
            continue
        else:  # pragma: no cover - _validate_wiring rejects other statuses
            raise ValueError(
                "results[" + tool.value + "].status is not a supported ToolStatus"
            )
        missing_tools.append(tool)
        tool_failures.append(ToolFailure(tool=tool, kind=kind))

    document_evidence = [
        item for item in usable if item.source_type is SourceType.DOCUMENT
    ]
    system_evidence = [item for item in usable if item.source_type is SourceType.SYSTEM]

    # --- freshness ---------------------------------------------------------
    for item in system_evidence:
        if not item.observed_at or not item.observed_at.strip():
            reasons.add(REASON_SYSTEM_MISSING_OBSERVED_AT)
        if item.metadata.get("authority_scope") != AUTHORITY_SCOPE_CURRENT_STATE:
            reasons.add(REASON_SYSTEM_MISSING_AUTHORITY_SCOPE)

    if plan.signals.requires_freshness:
        if ToolName.DOCUMENT_SEARCH in plan.steps:
            traceable = any(
                (item.version and item.version.strip())
                or (item.observed_at and item.observed_at.strip())
                for item in document_evidence
            )
            if not traceable:
                reasons.add(REASON_FRESHNESS_UNSUPPORTED)
        elif ToolName.SYSTEM_QUERY not in plan.steps:
            # Wiki alone cannot show the underlying document is current: a page
            # version describes the compiled page, not its source.
            reasons.add(REASON_FRESHNESS_UNSUPPORTED)

    # --- exact source ------------------------------------------------------
    exact_citation: tuple[Evidence, ...] = ()
    if plan.signals.requires_exact_citation:
        if ToolName.DOCUMENT_SEARCH not in plan.steps or not document_evidence:
            reasons.add(REASON_EXACT_CITATION_MISSING_DOCUMENT)
        else:
            qualifying = tuple(
                item
                for item in document_evidence
                if item.source
                and item.source.strip()
                and item.locator
                and item.locator.strip()
            )
            if not qualifying:
                reasons.add(REASON_EXACT_CITATION_MISSING_LOCATOR)
            exact_citation = qualifying

    # --- facts -------------------------------------------------------------
    resolutions, resolved_conflict, unresolved, ignored_any = _resolve_assertions(
        assertions
    )
    if ignored_any:
        reasons.add(REASON_SYSTEM_EXCLUDED_FROM_POLICY)
    if resolved_conflict:
        reasons.add(REASON_FACT_CONFLICT_RESOLVED)
    if unresolved:
        reasons.add(REASON_FACT_CONFLICT_UNRESOLVED)

    blocked = bool(reasons & BLOCKING_REASON_CODES)
    if not blocked:
        reasons.add(REASON_EVIDENCE_SUFFICIENT)

    return PolicyDecision(
        outcome=PolicyOutcome.REFUSE if blocked else PolicyOutcome.READY,
        usable_evidence=tuple(usable),
        exact_citation_evidence=exact_citation,
        resolutions=resolutions,
        missing_tools=tuple(missing_tools),
        tool_failures=tuple(tool_failures),
        reason_codes=tuple(
            code for code in REASON_CODE_ORDER if code in reasons
        ),
    )
