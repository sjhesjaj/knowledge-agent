"""Diagnostic labels: what a case needs from each stage.

A case's labels come from two places, merged in this order:

1. **Derived** from the frozen dataset's structured fields only (expected
   behaviour, route, required source types, fact groups, boundary message).
   Free-text `notes` are never parsed.
2. **Overlay** - a hand-written file (`eval/diagnostic_labels/*.labels.json`)
   that adds what the structured fields cannot say: the evidence a case needs,
   the plan signals it probes, the tool arguments and statuses it expects.

The dataset file itself is never modified; the overlay pins its sha256 so the
two cannot drift apart. An overlay may add labels but may not contradict the
dataset. Anything left unlabelled stays unlabelled - checks that need it
report a label gap rather than guessing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

OUTCOME_ANSWER, OUTCOME_REFUSE, OUTCOME_BOUNDARY = "answer", "refuse", "boundary"
MECHANISM_GENERATION, MECHANISM_POLICY = "generation", "policy"

SOURCE_TO_TOOL = {"document": "document_search", "wiki": "wiki_query", "system": "system_query"}
TOOL_TO_SOURCE = {tool: source for source, tool in SOURCE_TO_TOOL.items()}

# The product's route vocabulary and the channels each route covers, restated
# here so labels do not depend on importing planner internals.
ROUTE_STEPS = {
    "direct": (),
    "wiki_only": ("wiki_query",),
    "document_only": ("document_search",),
    "system_only": ("system_query",),
    "wiki_document": ("wiki_query", "document_search"),
    "wiki_system": ("wiki_query", "system_query"),
    "document_system": ("document_search", "system_query"),
    "wiki_document_system": ("wiki_query", "document_search", "system_query"),
}
PLAN_SIGNALS = ("is_direct", "needs_wiki", "needs_document", "needs_system",
                "requires_freshness", "requires_exact_citation")
TOOL_STATUSES = ("ok", "empty", "error")
EVIDENCE_KEYS = {"document": "heading", "wiki": "page_title", "system": "sku"}
#: Which Wiki an overlay's page labels were written against. The committed
#: sample and a compiled build have different pages, so a page label only
#: applies when the evaluated run read the same Wiki.
WIKI_CORPUS_SAMPLE = "committed_sample"
WIKI_CORPUS_BUILD_PREFIX = "published_build:"

OVERLAY_FIELDS = {"acceptable_routes", "forbidden_tools", "plan_constraints", "expected_arguments",
                  "expected_tool_status", "expected_evidence", "rationale"}


class LabelError(ValueError):
    pass


@dataclass
class CaseLabels:
    case_id: str
    question: str
    category: str
    expected_outcome: str
    refusal_mechanism: str | None
    acceptable_routes: tuple[str, ...]
    required_tools: tuple[str, ...]
    required_source_types: tuple[str, ...]
    expected_message: str | None
    facts: dict  # the dataset's own fact spec, fed to the evaluator's matcher
    citation_required: bool
    # Overlay-only (empty when not labelled).
    forbidden_tools: tuple[str, ...] = ()
    plan_constraints: dict = field(default_factory=dict)
    expected_arguments: dict = field(default_factory=dict)
    expected_tool_status: dict = field(default_factory=dict)
    expected_evidence: tuple[dict, ...] = ()
    provenance: dict = field(default_factory=dict)
    wiki_corpus: str | None = None  # overlay-level; None when no overlay

    @property
    def should_refuse(self) -> bool:
        return self.expected_outcome == OUTCOME_REFUSE

    @property
    def has_fact_spec(self) -> bool:
        return any(self.facts.values())


def derive_labels(case: dict) -> CaseLabels:
    """Labels that follow from the dataset's structured fields alone."""
    behavior = case["expected_behavior"]
    outcome, mechanism = {
        "answer": (OUTCOME_ANSWER, None),
        "generation_refuse": (OUTCOME_REFUSE, MECHANISM_GENERATION),
        "policy_refuse": (OUTCOME_REFUSE, MECHANISM_POLICY),
        "boundary": (OUTCOME_BOUNDARY, None),
    }[behavior]
    route = case["expected_route"]
    if route not in ROUTE_STEPS:
        raise LabelError(f"{case['id']}: unknown expected_route {route!r}")
    sources = tuple(case["required_source_types"])
    if outcome == OUTCOME_BOUNDARY:
        required = ()  # a boundary answer is fixed before any tool runs
    elif sources:
        required = tuple(SOURCE_TO_TOOL[s] for s in sources)
    else:
        required = ROUTE_STEPS[route]  # a refusal still has to consult its channel first
    derived = "derived"
    return CaseLabels(
        case_id=case["id"], question=case["question"], category=case["category"],
        expected_outcome=outcome, refusal_mechanism=mechanism,
        acceptable_routes=(route,), required_tools=required, required_source_types=sources,
        expected_message=case.get("expected_message"),
        facts={"expected_fact_groups": case.get("expected_fact_groups") or [],
               "expected_fact_patterns": case.get("expected_fact_patterns") or []},
        citation_required=outcome == OUTCOME_ANSWER,
        provenance={name: derived for name in (
            "expected_outcome", "refusal_mechanism", "acceptable_routes", "required_tools",
            "required_source_types", "expected_message", "facts", "citation_required")},
    )


def _overlay_problems(case_id: str, entry: dict, base: CaseLabels) -> list[str]:
    where = f"overlay.cases[{case_id}]"
    problems = [f"{where}: unknown field {name!r}" for name in sorted(set(entry) - OVERLAY_FIELDS)]
    routes = entry.get("acceptable_routes")
    if routes is not None:
        if not isinstance(routes, list) or not routes or any(r not in ROUTE_STEPS for r in routes):
            problems.append(f"{where}.acceptable_routes must be a non-empty list of known routes")
        elif base.acceptable_routes[0] not in routes:
            problems.append(f"{where}.acceptable_routes must still include the dataset's expected_route")
    for name in entry.get("forbidden_tools", []):
        if name not in TOOL_TO_SOURCE:
            problems.append(f"{where}.forbidden_tools: unknown tool {name!r}")
        elif name in base.required_tools:
            problems.append(f"{where}.forbidden_tools contradicts required tool {name!r}")
    for signal, value in entry.get("plan_constraints", {}).items():
        if signal not in PLAN_SIGNALS or not isinstance(value, bool):
            problems.append(f"{where}.plan_constraints.{signal} must be a known signal with a boolean")
    for tool, arguments in entry.get("expected_arguments", {}).items():
        if tool not in TOOL_TO_SOURCE or not isinstance(arguments, dict):
            problems.append(f"{where}.expected_arguments: {tool!r} must be a known tool with an object")
    for tool, status in entry.get("expected_tool_status", {}).items():
        if tool not in TOOL_TO_SOURCE or status not in TOOL_STATUSES:
            problems.append(f"{where}.expected_tool_status: {tool!r} -> {status!r} is not valid")
    for index, item in enumerate(entry.get("expected_evidence", [])):
        source = item.get("source_type")
        key = EVIDENCE_KEYS.get(source)
        if key is None or not isinstance(item.get(key), str) or not item[key].strip():
            problems.append(f"{where}.expected_evidence[{index}] needs source_type and its {key or 'key'}")
        elif base.expected_outcome != OUTCOME_ANSWER:
            problems.append(f"{where}.expected_evidence only applies to answer cases")
    return problems


def load_labels(dataset_path: str | Path, overlay_path: str | Path | None) -> dict[str, CaseLabels]:
    """Derived labels for every case, with the overlay (if any) applied."""
    dataset_path = Path(dataset_path)
    if "blind" in dataset_path.name.lower():
        # Stage 2 develops the diagnostic on seen data only; a blind set must stay unread.
        raise LabelError(f"refusing to read blind dataset {dataset_path.name}")
    raw = dataset_path.read_bytes()
    cases = json.loads(raw.decode("utf-8"))
    labels = {case["id"]: derive_labels(case) for case in cases}
    if overlay_path is None:
        return labels

    overlay = json.loads(Path(overlay_path).read_text(encoding="utf-8"))
    problems: list[str] = []
    if overlay.get("schema_version") != 1:
        problems.append("overlay.schema_version must be 1")
    if overlay.get("dataset") != dataset_path.name:
        problems.append(f"overlay is for {overlay.get('dataset')!r}, not {dataset_path.name!r}")
    corpus = overlay.get("wiki_corpus")
    if corpus != WIKI_CORPUS_SAMPLE and not (isinstance(corpus, str) and corpus.startswith(WIKI_CORPUS_BUILD_PREFIX)):
        problems.append(f"overlay.wiki_corpus must be {WIKI_CORPUS_SAMPLE!r} or '{WIKI_CORPUS_BUILD_PREFIX}<build_id>'")
    if overlay.get("dataset_sha256") != hashlib.sha256(raw).hexdigest():
        problems.append("overlay.dataset_sha256 does not match the dataset file (it changed, or the overlay is stale)")
    for case_id, entry in overlay.get("cases", {}).items():
        if case_id not in labels:
            problems.append(f"overlay names unknown case {case_id!r}")
            continue
        problems.extend(_overlay_problems(case_id, entry, labels[case_id]))
    if problems:
        raise LabelError("invalid diagnostic overlay:\n  - " + "\n  - ".join(problems))

    for target in labels.values():
        target.wiki_corpus = corpus
    for case_id, entry in overlay.get("cases", {}).items():
        target = labels[case_id]
        if "acceptable_routes" in entry:
            target.acceptable_routes = tuple(entry["acceptable_routes"])
        target.forbidden_tools = tuple(entry.get("forbidden_tools", ()))
        target.plan_constraints = dict(entry.get("plan_constraints", {}))
        target.expected_arguments = dict(entry.get("expected_arguments", {}))
        target.expected_tool_status = dict(entry.get("expected_tool_status", {}))
        target.expected_evidence = tuple(entry.get("expected_evidence", ()))
        for name in OVERLAY_FIELDS - {"rationale"}:
            if name in entry:
                target.provenance[name] = "overlay"
    return labels
