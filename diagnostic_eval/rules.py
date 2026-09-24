"""Turn one case run's persisted trace into a stage-by-stage diagnosis.

Stages are checked in execution order - routing, planning, tool, retrieval,
evidence, generation - each with deterministic rules against the case's
labels. A failed case run gets a `primary_error` only when the earliest failing
stage is backed by deterministic evidence *and* every stage before it passed
or did not apply. Anything short of that is recorded as `unattributed` with the
reason (missing trace, environment, inconclusive rule, missing label) instead
of being forced into a category. Later failures are `secondary_effects`;
failures in a run that still passed are `latent_issues`. Neither is ever
counted as a primary root cause.

Stage responsibilities do not overlap:
    routing     the high-level route (channel mix) the planner chose
    planning    everything else the plan fixed: steps/tools, signals,
                arguments, and the availability short-circuit
    tool        whether each planned tool ran without error
    retrieval   whether the tools returned the evidence the case needs
    evidence    whether the evidence policy judged that evidence correctly
    generation  whether the model turned usable evidence into a valid reply

The trace layer is only read, never changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import evaluate_answerability as scorer

from .labels import (
    MECHANISM_GENERATION,
    MECHANISM_POLICY,
    OUTCOME_ANSWER,
    OUTCOME_BOUNDARY,
    OUTCOME_REFUSE,
    TOOL_TO_SOURCE,
    CaseLabels,
)

STAGES = ("routing", "planning", "tool", "retrieval", "evidence", "generation")
ERROR_CATEGORY = {stage: f"{stage}_error" for stage in STAGES}
ERROR_CATEGORIES = tuple(ERROR_CATEGORY.values())

# Diagnostic gaps. Not error categories.
MISSING_TRACE = "missing_trace"
ENVIRONMENT_FAILURE = "environment_failure"
RULE_INCONCLUSIVE = "rule_inconclusive"
LABEL_GAP = "label_gap"
UNATTRIBUTED_KINDS = (MISSING_TRACE, ENVIRONMENT_FAILURE, RULE_INCONCLUSIVE, LABEL_GAP)

PASS, FAIL, NOT_APPLICABLE, BLOCKED, INCONCLUSIVE = "pass", "fail", "not_applicable", "blocked", "inconclusive"
#: A check whose label does not apply to this run's environment. Never counted.
SKIPPED = "skipped"


@dataclass(frozen=True)
class DiagnosisContext:
    """Facts about the evaluated environment that decide which labels apply."""

    wiki_corpus: str | None = None  # what the run read: committed_sample | published_build:<id>

    def wiki_labels_apply(self, labels) -> bool:
        return labels.wiki_corpus is None or self.wiki_corpus is None or labels.wiki_corpus == self.wiki_corpus

# Which trace stage an aborted run's failed_stage belongs to.
ABORT_STAGE = {"router": "routing", "planner": "planning", "tool_call": "tool",
               "evidence": "evidence", "generation": "generation"}
# Policy refusals that are a direct consequence of a plan signal.
SIGNAL_FOR_REASON = {
    "freshness_unsupported": "requires_freshness",
    "exact_citation_missing_document": "requires_exact_citation",
    "exact_citation_missing_locator": "requires_exact_citation",
}
# Policy refusals caused by what the tools returned (judged in tool/retrieval).
TOOL_REASONS = {"missing_tool_result", "empty_tool_result", "tool_error"}


# --------------------------------------------------------------------------
# Semantic judge (interface only in v1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeVerdict:
    verdict: str  # "supported" | "not_supported" | "rule_inconclusive"
    detail: str = ""


class SemanticJudge(Protocol):
    def judge(self, *, question: str, answer: str, requirement: str, evidence: list[str]) -> JudgeVerdict: ...


class DisabledJudge:
    """Stage 2 v1: never calls a model; every semantic question stays inconclusive."""

    calls = 0

    def judge(self, *, question, answer, requirement, evidence) -> JudgeVerdict:
        return JudgeVerdict(RULE_INCONCLUSIVE, "semantic judge disabled in v1")


# --------------------------------------------------------------------------
# Trace view
# --------------------------------------------------------------------------


@dataclass
class TraceView:
    """The spans a diagnosis needs, pulled out of one decoded trace."""

    run: dict
    plan: dict | None = None
    availability: dict | None = None
    execute: dict | None = None
    tools: dict = field(default_factory=dict)
    evidence: dict | None = None
    generation: dict | None = None

    @classmethod
    def from_trace(cls, trace: dict) -> "TraceView":
        view = cls(run=trace["run"])
        spans = trace["spans"]
        for span in spans:
            stage, name = span["stage"], span["name"]
            if stage == "planner" and name == "plan_request":
                view.plan = span
            elif stage == "planner" and name == "availability_check":
                view.availability = span
            elif stage == "tool_call" and name == "execute_plan":
                view.execute = span
            elif stage == "evidence":
                view.evidence = span
            elif stage == "generation":
                view.generation = span
        if view.execute is not None:
            view.tools = {s["name"]: s for s in spans
                          if s["parent_span_id"] == view.execute["span_id"] and s["stage"] == "tool_call"}
        return view

    @property
    def aborted_stage(self) -> str | None:
        return ABORT_STAGE.get(self.run.get("failed_stage")) if self.run.get("status") == "failed" else None

    def evidence_items(self, tools=None) -> list[dict]:
        items = []
        for name, span in self.tools.items():
            if tools is None or name in tools:
                items.extend((span.get("output") or {}).get("evidence") or [])
        return items


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    status: str  # pass | fail | inconclusive | skipped
    detail: str = ""
    expected: object = None
    actual: object = None
    span_id: str | None = None
    gap: str | None = None  # for inconclusive: rule_inconclusive | label_gap | environment_failure
    method: str = "rule"  # "rule" | "judge" - a judge-decided check is never deterministic evidence

    def to_dict(self) -> dict:
        data = {"check": self.name, "status": self.status, "detail": self.detail, "method": self.method}
        for key in ("expected", "actual", "span_id", "gap"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass
class StageResult:
    stage: str
    status: str
    checks: list[Check] = field(default_factory=list)
    note: str = ""

    @classmethod
    def of(cls, stage: str, checks: list[Check], note: str = "") -> "StageResult":
        statuses = {check.status for check in checks if check.status != SKIPPED}
        status = (FAIL if FAIL in statuses else INCONCLUSIVE if INCONCLUSIVE in statuses
                  else PASS if PASS in statuses else NOT_APPLICABLE)
        return cls(stage, status, checks, note)

    def first(self, status: str) -> Check | None:
        return next((check for check in self.checks if check.status == status), None)

    def to_dict(self) -> dict:
        data = {"status": self.status, "checks": [check.to_dict() for check in self.checks]}
        if self.note:
            data["note"] = self.note
        return data


def _facts_hit(labels: CaseLabels, text: str) -> tuple[bool, list[dict]]:
    _mode, groups = scorer.evaluate_facts(labels.facts, scorer.body_for_fact_matching(text))
    return all(group["hit"] for group in groups), groups


def _evidence_text(items: list[dict]) -> str:
    return "\n".join(item.get("content", "") for item in items)


def _matches(expected: dict, item: dict) -> bool:
    source = expected["source_type"]
    if item.get("source_type") != source:
        return False
    if source == "document":
        return f"## {expected['heading']}" in (item.get("content") or "").splitlines()
    if source == "wiki":
        return (item.get("metadata") or {}).get("page_title") == expected["page_title"]
    return (item.get("locator") or "").lower().endswith(":" + expected["sku"].lower())


def check_routing(labels: CaseLabels, view: TraceView) -> StageResult:
    plan = view.plan
    if plan is None or plan["status"] == "error" or plan.get("output") is None:
        return StageResult.of("routing", [], note="planner produced no plan")
    route = plan["output"]["route"]
    ok = route in labels.acceptable_routes
    return StageResult.of("routing", [Check(
        "route_acceptable", PASS if ok else FAIL,
        "" if ok else f"route {route} is not one of {list(labels.acceptable_routes)}",
        list(labels.acceptable_routes), route, plan["span_id"])])


def check_planning(labels: CaseLabels, view: TraceView) -> StageResult:
    checks: list[Check] = []
    plan = view.plan
    if plan is None or plan["status"] == "error" or plan.get("output") is None:
        if view.aborted_stage == "planning" or (plan and plan["status"] == "error"):
            checks.append(Check("planner_completed", FAIL, f"planner aborted: {view.run.get('error_type')}",
                                span_id=(plan or {}).get("span_id")))
        return StageResult.of("planning", checks)
    output = plan["output"]
    steps = output.get("steps") or []
    missing = [tool for tool in labels.required_tools if tool not in steps]
    checks.append(Check("required_tools_planned", FAIL if missing else PASS,
                        f"missing {missing}" if missing else "", list(labels.required_tools), steps, plan["span_id"]))
    if labels.forbidden_tools:
        present = [tool for tool in labels.forbidden_tools if tool in steps]
        checks.append(Check("forbidden_tools_absent", FAIL if present else PASS,
                            f"planned {present}" if present else "", list(labels.forbidden_tools), steps, plan["span_id"]))
    signals = output.get("signals") or {}
    for signal, expected in labels.plan_constraints.items():
        actual = signals.get(signal)
        checks.append(Check(f"signal:{signal}", PASS if actual == expected else FAIL,
                            "" if actual == expected else f"{signal}={actual}, labelled {expected}",
                            expected, actual, plan["span_id"]))

    availability = view.availability
    if availability is not None and availability.get("output") is not None:
        fixed = availability["output"].get("fixed_answer")
        if labels.expected_outcome == OUTCOME_BOUNDARY:
            ok = fixed == labels.expected_message
            checks.append(Check("availability_boundary_message", PASS if ok else FAIL,
                                "" if ok else "wrong or missing boundary message",
                                labels.expected_message, fixed, availability["span_id"]))
        elif fixed in scorer.UNAVAILABLE_MESSAGES:
            checks.append(Check("availability_environment", INCONCLUSIVE, "a corpus was unavailable",
                                None, fixed, availability["span_id"], gap=ENVIRONMENT_FAILURE))
        else:
            checks.append(Check("availability_not_short_circuited", PASS if fixed is None else FAIL,
                                "" if fixed is None else "request was answered before executing its tools",
                                None, fixed, availability["span_id"]))
    elif labels.expected_outcome == OUTCOME_BOUNDARY and steps:
        checks.append(Check("availability_boundary_message", FAIL, "no availability decision recorded",
                            labels.expected_message, None, plan["span_id"]))

    for tool, expected_args in labels.expected_arguments.items():
        span = view.tools.get(tool)
        if span is None:
            continue  # not executed: whichever check explains that owns it
        actual = ((span.get("input") or {}).get("arguments") or {}).get("parameters") or {}
        for key, value in expected_args.items():
            ok = str(actual.get(key, "")).lower() == str(value).lower()
            checks.append(Check(f"argument:{tool}.{key}", PASS if ok else FAIL,
                                "" if ok else f"{tool} called with {key}={actual.get(key)!r}",
                                value, actual.get(key), span["span_id"]))
    return StageResult.of("planning", checks)


def _execution_expected(labels: CaseLabels) -> bool:
    return labels.expected_outcome != OUTCOME_BOUNDARY and bool(labels.required_tools)


def check_tool(labels: CaseLabels, view: TraceView) -> StageResult:
    execute = view.execute
    if execute is None:
        if view.aborted_stage == "tool":
            return StageResult.of("tool", [Check("executor_completed", FAIL, "executor aborted")])
        status = BLOCKED if _execution_expected(labels) else NOT_APPLICABLE
        return StageResult("tool", status, note="the plan was not executed")
    checks: list[Check] = []
    if execute["status"] == "error":
        checks.append(Check("executor_completed", FAIL,
                            f"executor raised {execute.get('error_type')}", span_id=execute["span_id"]))
    planned = (execute.get("input") or {}).get("steps") or []
    for tool in planned:
        span = view.tools.get(tool)
        if span is None:
            if execute["status"] != "error":
                checks.append(Check(f"executed:{tool}", FAIL, "planned but not executed", span_id=execute["span_id"]))
            continue
        ok = span["status"] != "error"
        checks.append(Check(f"executed:{tool}", PASS if ok else FAIL,
                            "" if ok else f"{span.get('error_code')} ({span.get('error_type')})",
                            None, span["status"], span["span_id"]))
    return StageResult.of("tool", checks)


def check_retrieval(labels: CaseLabels, view: TraceView, context: DiagnosisContext | None = None) -> StageResult:
    context = context or DiagnosisContext()
    labelled = labels.expected_tool_status or labels.expected_evidence
    if labels.expected_outcome == OUTCOME_BOUNDARY or (labels.expected_outcome == OUTCOME_REFUSE and not labelled):
        return StageResult("retrieval", NOT_APPLICABLE)
    if not view.tools:
        return StageResult("retrieval", BLOCKED, note="no tool results to inspect")
    checks: list[Check] = []
    for tool, expected in labels.expected_tool_status.items():
        span = view.tools.get(tool)
        if span is None:
            continue
        ok = span["status"] == expected
        checks.append(Check(f"status:{tool}", PASS if ok else FAIL,
                            "" if ok else f"{tool} returned {span['status']}, expected {expected}",
                            expected, span["status"], span["span_id"]))
    for expected in labels.expected_evidence:
        tool = next(t for t, s in TOOL_TO_SOURCE.items() if s == expected["source_type"])
        span = view.tools.get(tool)
        if span is None:
            continue
        items = (span.get("output") or {}).get("evidence") or []
        if expected["source_type"] == "wiki" and not context.wiki_labels_apply(labels):
            checks.append(Check("expected_evidence:wiki", SKIPPED,
                                f"label written for wiki {labels.wiki_corpus}; this run read {context.wiki_corpus}",
                                expected, [item.get("locator") for item in items], span["span_id"]))
            continue
        ok = any(_matches(expected, item) for item in items)
        checks.append(Check(f"expected_evidence:{expected['source_type']}", PASS if ok else FAIL,
                            "" if ok else f"{tool} did not return the expected evidence",
                            expected, [item.get("locator") for item in items], span["span_id"]))
    if labels.expected_outcome == OUTCOME_ANSWER and labels.has_fact_spec:
        ok, groups = _facts_hit(labels, _evidence_text(view.evidence_items()))
        checks.append(Check("answer_facts_retrieved", PASS if ok else FAIL,
                            "" if ok else "no retrieved evidence contains the answer facts",
                            labels.facts, [g["matched"] for g in groups]))
    return StageResult.of("retrieval", checks)


def _expected_policy_outcome(labels: CaseLabels) -> str | None:
    if labels.expected_outcome == OUTCOME_ANSWER:
        return "ready"
    if labels.refusal_mechanism == MECHANISM_GENERATION:
        return "ready"  # the model, not the policy, is meant to refuse
    if labels.refusal_mechanism == MECHANISM_POLICY:
        return "refuse"
    return None


def check_evidence(labels: CaseLabels, view: TraceView) -> StageResult:
    expected_outcome = _expected_policy_outcome(labels)
    span = view.evidence
    if expected_outcome is None:
        return StageResult("evidence", NOT_APPLICABLE)
    if span is None:
        if view.aborted_stage == "evidence":
            return StageResult.of("evidence", [Check("policy_completed", FAIL, "evidence policy aborted")])
        return StageResult("evidence", BLOCKED, note="no evidence decision was made")
    decision = span.get("output") or {}
    actual = decision.get("outcome")
    reasons = decision.get("reason_codes") or []
    signals = ((view.plan or {}).get("output") or {}).get("signals") or {}
    checks: list[Check] = []
    if actual != expected_outcome:
        check = Check("policy_outcome", FAIL, f"policy {actual} with {reasons}", expected_outcome, actual, span["span_id"])
        if actual == "refuse":
            linked = [SIGNAL_FOR_REASON[r] for r in reasons if r in SIGNAL_FOR_REASON]
            if linked:
                unlabelled = [s for s in linked if s not in labels.plan_constraints]
                if unlabelled:
                    # Was the signal wrong (planning) or the policy (evidence)? No label says.
                    check = Check("policy_outcome", INCONCLUSIVE,
                                  f"refusal follows from plan signal {unlabelled}, which has no label",
                                  expected_outcome, actual, span["span_id"], gap=LABEL_GAP)
                # A labelled, wrong signal already failed planning; this is its effect.
            elif set(reasons) & TOOL_REASONS:
                if any(s["status"] in ("error", "empty") for s in view.tools.values()) or \
                        len(view.tools) < len((view.execute or {}).get("input", {}).get("steps") or []):
                    # The policy reacted correctly to what the tools returned; the
                    # tool/retrieval stage owns this failure.
                    return StageResult("evidence", BLOCKED,
                                       note=f"policy refused because of upstream tool results {reasons}")
                check = Check("policy_outcome", INCONCLUSIVE,
                              f"refusal follows from tool results {sorted(set(reasons) & TOOL_REASONS)} "
                              "that the tool/retrieval rules did not flag",
                              expected_outcome, actual, span["span_id"], gap=RULE_INCONCLUSIVE)
        checks.append(check)
    else:
        checks.append(Check("policy_outcome", PASS, "", expected_outcome, actual, span["span_id"]))
        if expected_outcome == "ready" and labels.expected_outcome == OUTCOME_ANSWER:
            usable = decision.get("usable_evidence") or []
            covered = {item.get("source_type") for item in usable}
            missing = [s for s in labels.required_source_types if s not in covered]
            checks.append(Check("required_sources_usable", FAIL if missing else PASS,
                                f"no usable {missing} evidence" if missing else "",
                                list(labels.required_source_types), sorted(covered), span["span_id"]))
            if labels.has_fact_spec and _facts_hit(labels, _evidence_text(view.evidence_items()))[0]:
                ok = _facts_hit(labels, _evidence_text(usable))[0]
                checks.append(Check("answer_evidence_kept", PASS if ok else FAIL,
                                    "" if ok else "evidence with the answer facts was retrieved but not kept usable",
                                    span_id=span["span_id"]))
    return StageResult.of("evidence", checks)


def _semantic_check(name: str, judge: SemanticJudge, why: str, *, question: str, answer: str,
                    requirement: str, usable: list[dict], expected=None, span_id=None) -> Check:
    """A question the rules cannot settle. v1's disabled judge leaves it inconclusive."""
    verdict = judge.judge(question=question, answer=answer, requirement=requirement,
                          evidence=[item.get("content", "") for item in usable])
    if verdict.verdict == "supported":
        return Check(name, PASS, f"judge: {verdict.detail}", expected, answer, span_id, method="judge")
    if verdict.verdict == "not_supported":
        return Check(name, FAIL, f"judge: {verdict.detail}", expected, answer, span_id, method="judge")
    return Check(name, INCONCLUSIVE, why, expected, answer, span_id, gap=RULE_INCONCLUSIVE)


def _answer_text(view: TraceView, record: dict) -> str:
    output = (view.generation or {}).get("output") or {}
    return output.get("answer") if output.get("answer") is not None else (record.get("answer") or "")


def check_generation(labels: CaseLabels, view: TraceView, record: dict, judge: SemanticJudge) -> StageResult:
    if _expected_policy_outcome(labels) != "ready" or labels.expected_outcome == OUTCOME_BOUNDARY:
        return StageResult("generation", NOT_APPLICABLE)
    span = view.generation
    if span is None:
        if view.aborted_stage == "generation":
            return StageResult.of("generation", [Check("generation_completed", FAIL, "generation aborted")])
        return StageResult("generation", BLOCKED, note="the model was not called")
    if span["status"] == "error":
        return StageResult.of("generation", [Check(
            "generation_completed", FAIL, f"generation raised {span.get('error_type')}", span_id=span["span_id"])])
    answer = _answer_text(view, record)
    question = labels.question
    checks: list[Check] = []
    restated = scorer.restates_question(answer, question)
    refused = scorer.looks_like_refusal(answer, scorer.EVALUATOR_REFUSAL_MARKERS) and not restated
    if restated:
        checks.append(Check("not_a_restatement", FAIL, "the reply only restates the question", span_id=span["span_id"]))
        return StageResult.of("generation", checks)
    usable = ((view.evidence or {}).get("output") or {}).get("usable_evidence") or []

    if labels.expected_outcome == OUTCOME_REFUSE:
        if refused:
            checks.append(Check("refused", PASS, span_id=span["span_id"]))
        else:
            checks.append(_semantic_check(
                "refused", judge, "no refusal marker; whether this is an unsupported answer needs semantic judgement",
                question=question, answer=answer, requirement="the reply should decline for lack of evidence",
                usable=usable, expected="refusal", span_id=span["span_id"]))
        return StageResult.of("generation", checks)

    facts_available = labels.has_fact_spec and _facts_hit(labels, _evidence_text(usable))[0]
    if refused:
        if facts_available:
            checks.append(Check("no_false_refusal", FAIL, "refused although usable evidence contains the answer",
                                span_id=span["span_id"]))
        else:
            checks.append(Check("no_false_refusal", INCONCLUSIVE, "refused; usable evidence not shown to hold the answer",
                                span_id=span["span_id"], gap=RULE_INCONCLUSIVE))
        return StageResult.of("generation", checks)
    citations = scorer.extract_citation_indices(answer)
    if labels.citation_required:
        checks.append(Check("citation_present", PASS if citations else FAIL,
                            "" if citations else "no [来源 N] citation", span_id=span["span_id"]))
        if citations:
            valid = all(1 <= index <= len(usable) for index in citations)
            checks.append(Check("citation_indices_valid", PASS if valid else FAIL,
                                "" if valid else f"citations {citations} exceed {len(usable)} sources",
                                span_id=span["span_id"]))
    if labels.has_fact_spec:
        ok, groups = _facts_hit(labels, answer)
        if ok:
            checks.append(Check("answer_facts", PASS, span_id=span["span_id"]))
        else:
            checks.append(_semantic_check(
                "answer_facts", judge, "fact rules missed; a paraphrase cannot be ruled out without semantic judgement",
                question=question, answer=answer, requirement=f"the reply states the facts {labels.facts}",
                usable=usable, expected=labels.facts, span_id=span["span_id"]))
    return StageResult.of("generation", checks)


# --------------------------------------------------------------------------
# Root cause
# --------------------------------------------------------------------------


@dataclass
class Diagnosis:
    case_id: str
    run: int
    trace_run_id: str | None
    official_passed: bool
    expected_outcome: str
    stages: dict = field(default_factory=dict)
    primary_error: str | None = None
    primary_stage: str | None = None
    primary_reason: str = ""
    primary_method: str | None = None
    unattributed: dict | None = None
    secondary_effects: list = field(default_factory=list)
    latent_issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "run": self.run, "trace_run_id": self.trace_run_id,
            "official_passed": self.official_passed, "expected_outcome": self.expected_outcome,
            "primary_error": self.primary_error, "primary_stage": self.primary_stage,
            "primary_reason": self.primary_reason, "primary_method": self.primary_method,
            "unattributed": self.unattributed,
            "secondary_effects": self.secondary_effects, "latent_issues": self.latent_issues,
            "stages": {name: result.to_dict() for name, result in self.stages.items()},
        }


def _failure_summary(result: StageResult) -> dict:
    check = result.first(FAIL)
    return {"stage": result.stage, "category": ERROR_CATEGORY[result.stage],
            "check": check.name if check else None, "detail": check.detail if check else ""}


def diagnose(labels: CaseLabels, record: dict, trace: dict | None, judge: SemanticJudge | None = None,
             context: DiagnosisContext | None = None) -> Diagnosis:
    """Diagnose one case run. `record` is the case run from eval/<label>.json."""
    judge = judge or DisabledJudge()
    diagnosis = Diagnosis(labels.case_id, record.get("run", 0), record.get("trace_run_id"),
                          bool(record.get("passed")), labels.expected_outcome)
    if trace is None:
        if not diagnosis.official_passed:
            diagnosis.unattributed = {"kind": MISSING_TRACE, "detail": "no persisted trace for this case run"}
        return diagnosis

    view = TraceView.from_trace(trace)
    results = [
        check_routing(labels, view), check_planning(labels, view), check_tool(labels, view),
        check_retrieval(labels, view, context), check_evidence(labels, view),
        check_generation(labels, view, record, judge),
    ]
    diagnosis.stages = {result.stage: result for result in results}

    if diagnosis.official_passed:
        diagnosis.latent_issues = [_failure_summary(r) for r in results if r.status == FAIL]
        return diagnosis

    if view.run.get("status") == "failed" and view.aborted_stage is None:
        diagnosis.unattributed = {"kind": ENVIRONMENT_FAILURE,
                                  "detail": f"run aborted outside the agent stages ({view.run.get('failed_stage')}): "
                                            f"{view.run.get('error_type')}"}
        return diagnosis

    for index, result in enumerate(results):
        if result.status == FAIL:
            check = result.first(FAIL)
            diagnosis.primary_stage = result.stage
            diagnosis.primary_error = ERROR_CATEGORY[result.stage]
            diagnosis.primary_reason = f"{check.name}: {check.detail}".rstrip(": ")
            diagnosis.primary_method = check.method
            diagnosis.secondary_effects = [_failure_summary(r) for r in results[index + 1:] if r.status == FAIL]
            return diagnosis
        if result.status == INCONCLUSIVE:
            check = result.first(INCONCLUSIVE)
            diagnosis.unattributed = {"kind": check.gap or RULE_INCONCLUSIVE, "stage": result.stage,
                                      "check": check.name, "detail": check.detail}
            diagnosis.secondary_effects = [_failure_summary(r) for r in results[index + 1:] if r.status == FAIL]
            return diagnosis
    diagnosis.unattributed = {"kind": RULE_INCONCLUSIVE,
                              "detail": "the official score failed but no stage rule explains why"}
    return diagnosis
