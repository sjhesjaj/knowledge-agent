"""Answerability, refusal, and boundary evaluation for the V1 orchestrated chain.

Runs the real chain - Planner -> Executor -> Evidence Policy -> answer model -
against the real sample corpus, the real Wiki pages, and the real System
fixture. Nothing is mocked: the same `chat_orchestration.prepare` and
`rag.answer_structured` the API uses are called here.

Isolation: the System channel uses `chat_orchestration.demo_system_connection`,
a fresh in-memory SQLite database per request, so no persistent product data is
read and no business state is written. No conversation is committed to storage.

Usage:
    python evaluate_answerability.py --dataset <file> --runs N --output-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter

import chat_orchestration
import rag
from chat_orchestration import (
    MESSAGE_NO_DOCUMENTS,
    MESSAGE_NO_SKU,
    MESSAGE_NO_WIKI,
    MESSAGE_SYSTEM_LIMITED,
    SKU_PATTERN,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - already UTF-8 or piped
        pass


DOCUMENT_PATH = Path(__file__).resolve().parent / "sample_company_rules.md"

BEHAVIOR_ANSWER = "answer"
BEHAVIOR_GENERATION_REFUSE = "generation_refuse"
BEHAVIOR_POLICY_REFUSE = "policy_refuse"
BEHAVIOR_BOUNDARY = "boundary"
BEHAVIOR_DIRECT = "direct"

REFUSAL_BEHAVIORS = (BEHAVIOR_GENERATION_REFUSE, BEHAVIOR_POLICY_REFUSE)
VALID_EXPECTED_BEHAVIORS = (
    BEHAVIOR_ANSWER,
    BEHAVIOR_GENERATION_REFUSE,
    BEHAVIOR_POLICY_REFUSE,
    BEHAVIOR_BOUNDARY,
)

# The two fixed messages that mean "this is outside what V1 offers", as opposed
# to "the evidence does not support an answer".
BOUNDARY_MESSAGES = frozenset({MESSAGE_SYSTEM_LIMITED, MESSAGE_NO_SKU})
# A missing corpus is an environment fault, not a product decision. If either
# appears, the run is misconfigured and the report must say so.
UNAVAILABLE_MESSAGES = frozenset({MESSAGE_NO_DOCUMENTS, MESSAGE_NO_WIKI})

# `rag.REFUSAL_MARKERS` is what production uses to detect a refusal. The model
# also refuses in wordings that set misses, so behaviour is classified with this
# wider set and both verdicts are recorded per case - the difference is itself a
# reportable gap in the production marker table.
EVALUATOR_REFUSAL_MARKERS = tuple(rag.REFUSAL_MARKERS) + (
    "没有提及",
    "未找到",
    "没有找到",
    "无法提供",
    "没有说明",
    "未说明",
    "没有明确",
    "没有具体",
    "不包含",
    "未包含",
)

CITATION_PATTERN = re.compile(r"\[\s*来源\s*([^\]]*)\]")
_DIGITS = re.compile(r"\d+")
_PUNCTUATION = re.compile(r"[\s，。！？；：、,.!?;:]")

REQUIRED_FIELDS = (
    "id",
    "question",
    "expected_behavior",
    "expected_route",
    "required_source_types",
    "category",
    "difficulty",
)

# Two ways to state the facts an answer must contain, both list[list[str]]:
#
# - `expected_fact_groups`  plain substrings. Kept only so the Dev and
#   Validation V1 datasets can still be replayed unchanged.
# - `expected_fact_patterns` regular expressions. Used by the blind holdout.
#   Regexes let a number be bound to its unit, which a substring cannot do:
#   `"42"` alone also matches "SKU-B4200" or a stray year, whereas
#   `"(?:42|四十二)\s*件"` only matches the quantity actually asserted.
#
# A case uses exactly one of them. Both are absent/empty for non-answer cases.
FACT_GROUPS_FIELD = "expected_fact_groups"
FACT_PATTERNS_FIELD = "expected_fact_patterns"
FACT_MODE_GROUPS = "substring_groups"
FACT_MODE_PATTERNS = "regex_patterns"

# A pattern that is nothing but a number carries no context, so it matches any
# stray digit in the answer. Rejected at load time rather than silently scored.
_BARE_NUMBER = re.compile(r"^[0-9〇零一二三四五六七八九十百千两]{1,3}$")

FACT_REGEX_FLAGS = re.UNICODE | re.IGNORECASE

VALID_DIFFICULTIES = ("normal", "boundary")
VALID_SOURCE_TYPES = ("wiki", "document", "system")

# Required dataset shape, from the milestone specification.
EXPECTED_BEHAVIOR_COUNTS = {"answer": 20, "refuse": 12, "boundary": 8}
EXPECTED_ANSWER_CATEGORY_COUNTS = {
    "document": 8,
    "wiki": 4,
    "system": 4,
    "multi_channel": 4,
}

GATES = (
    ("Answer Success Rate", "answer_success_rate", "min", 0.90),
    ("False Refusal Rate", "false_refusal_rate", "max", 0.10),
    ("Unanswerable Refusal Rate", "unanswerable_refusal_rate", "min", 0.90),
    ("Boundary Message Accuracy", "boundary_accuracy", "min", 0.90),
    ("Citation Index Validity", "citation_index_validity", "min", 0.95),
    ("Expected Fact Hit Rate", "expected_fact_hit_rate", "min", 0.90),
)
GATE_STABILITY = 0.90


def git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Dataset validation
# --------------------------------------------------------------------------


def _fact_spec_problems(case: dict, where: str, behavior: object) -> list[str]:
    """Validate whichever of the two fact-specification fields the case uses.

    A case declares its facts one way or the other, never both: mixing them
    would make "all groups must hit" ambiguous about which list is meant.
    """
    problems: list[str] = []
    present = {}
    for field in (FACT_GROUPS_FIELD, FACT_PATTERNS_FIELD):
        value = case.get(field, [])
        if not isinstance(value, list):
            problems.append(f"{where}.{field} must be a list")
            continue
        present[field] = value
    if problems:
        return problems

    non_empty = [field for field, value in present.items() if value]
    if behavior != BEHAVIOR_ANSWER:
        for field in non_empty:
            problems.append(
                f"{where}.{field} must be empty unless the case expects an answer"
            )
        return problems
    if len(non_empty) > 1:
        problems.append(
            f"{where} declares both {FACT_GROUPS_FIELD} and {FACT_PATTERNS_FIELD}; "
            "use exactly one"
        )
        return problems
    if not non_empty:
        problems.append(
            f"{where} expects an answer but states no facts: give a non-empty "
            f"{FACT_PATTERNS_FIELD} (preferred) or {FACT_GROUPS_FIELD}"
        )
        return problems

    field = non_empty[0]
    for index, group in enumerate(present[field]):
        at = f"{where}.{field}[{index}]"
        if not isinstance(group, list) or not group:
            problems.append(f"{at} must be a non-empty list")
            continue
        for item in group:
            if not isinstance(item, str) or not item.strip():
                problems.append(f"{at} must contain non-empty strings")
                continue
            if field != FACT_PATTERNS_FIELD:
                # Legacy substring groups are deliberately grandfathered: the Dev
                # and Validation V1 datasets are frozen historical records and
                # must stay replayable byte-for-byte. Their weaker matching is
                # reported rather than retrofitted.
                continue
            if _BARE_NUMBER.match(item.strip()):
                # The whole point of the tightening: a naked number matches any
                # digit that happens to appear, including one belonging to a
                # different fact in the same sentence.
                problems.append(
                    f"{at} contains the context-free number {item!r}; bind it to a "
                    "unit or surrounding wording"
                )
            try:
                re.compile(item, FACT_REGEX_FLAGS)
            except re.error as exc:
                problems.append(f"{at} pattern {item!r} does not compile: {exc}")
    return problems


def evaluate_facts(case: dict, body: str) -> tuple[str, list[dict]]:
    """Score one case's fact groups against the cleaned answer body.

    Returns the mode used and, per group, whether it hit and which candidate
    produced the hit - so a report can show *what* matched, not just that
    something did.
    """
    patterns = case.get(FACT_PATTERNS_FIELD) or []
    if patterns:
        results = []
        for group in patterns:
            matched = next(
                (p for p in group if re.search(p, body, FACT_REGEX_FLAGS)), None
            )
            results.append(
                {"candidates": group, "hit": matched is not None,
                 "matched": matched}
            )
        return FACT_MODE_PATTERNS, results

    results = []
    for group in case.get(FACT_GROUPS_FIELD) or []:
        matched = next((c for c in group if c in body), None)
        results.append(
            {"candidates": group, "hit": matched is not None, "matched": matched}
        )
    return FACT_MODE_GROUPS, results


def validate_dataset(cases: object, path: Path) -> list[dict]:
    if not isinstance(cases, list):
        raise ValueError(f"{path}: dataset must be a JSON array")
    if not cases:
        raise ValueError(f"{path}: dataset must not be empty")

    problems: list[str] = []
    seen_ids: dict[str, int] = {}
    seen_questions: dict[str, int] = {}
    behavior_counts: Counter[str] = Counter()
    answer_categories: Counter[str] = Counter()

    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{where} must be a JSON object")
            continue
        missing = [field for field in REQUIRED_FIELDS if field not in case]
        for field in missing:
            problems.append(f"{where}.{field} is required")
        if missing:
            continue

        case_id = case["id"]
        question = case["question"]
        behavior = case["expected_behavior"]

        if not isinstance(case_id, str) or not case_id.strip():
            problems.append(f"{where}.id must be a non-empty string")
        elif case_id in seen_ids:
            problems.append(
                f"{where}.id duplicates cases[{seen_ids[case_id]}].id: {case_id}"
            )
        else:
            seen_ids[case_id] = index

        if not isinstance(question, str) or not question.strip():
            problems.append(f"{where}.question must be a non-empty string")
        elif question in seen_questions:
            problems.append(
                f"{where}.question duplicates cases[{seen_questions[question]}]"
            )
        else:
            seen_questions[question] = index

        if behavior not in VALID_EXPECTED_BEHAVIORS:
            problems.append(
                f"{where}.expected_behavior must be one of "
                f"{VALID_EXPECTED_BEHAVIORS}, got {behavior!r}"
            )
        else:
            bucket = "refuse" if behavior in REFUSAL_BEHAVIORS else behavior
            behavior_counts[bucket] += 1
            if behavior == BEHAVIOR_ANSWER:
                answer_categories[case["category"]] += 1

        if not isinstance(case["expected_route"], str) or not case["expected_route"]:
            problems.append(f"{where}.expected_route must be a non-empty string")

        sources = case["required_source_types"]
        if not isinstance(sources, list) or any(
            item not in VALID_SOURCE_TYPES for item in sources
        ):
            problems.append(
                f"{where}.required_source_types must be a list drawn from "
                f"{VALID_SOURCE_TYPES}"
            )

        problems.extend(_fact_spec_problems(case, where, behavior))

        if case["difficulty"] not in VALID_DIFFICULTIES:
            problems.append(
                f"{where}.difficulty must be one of {VALID_DIFFICULTIES}, "
                f"got {case['difficulty']!r}"
            )

        if behavior == BEHAVIOR_BOUNDARY:
            message = case.get("expected_message")
            if not isinstance(message, str) or not message.strip():
                problems.append(
                    f"{where}.expected_message is required for a boundary case"
                )
            elif message not in BOUNDARY_MESSAGES:
                problems.append(
                    f"{where}.expected_message is not one of the product's boundary "
                    f"messages: {sorted(BOUNDARY_MESSAGES)}"
                )

    for bucket, expected in EXPECTED_BEHAVIOR_COUNTS.items():
        actual = behavior_counts[bucket]
        if actual != expected:
            problems.append(
                f"behaviour bucket {bucket!r} has {actual} cases, expected {expected}"
            )
    for category, expected in EXPECTED_ANSWER_CATEGORY_COUNTS.items():
        actual = answer_categories[category]
        if actual != expected:
            problems.append(
                f"answer category {category!r} has {actual} cases, expected {expected}"
            )
    unexpected = sorted(set(answer_categories) - set(EXPECTED_ANSWER_CATEGORY_COUNTS))
    for category in unexpected:
        problems.append(f"answer category {category!r} is not part of the required mix")

    if problems:
        raise ValueError(
            f"{path}: {len(problems)} dataset problem(s):\n  - "
            + "\n  - ".join(problems)
        )
    return cases


# --------------------------------------------------------------------------
# Answer inspection
# --------------------------------------------------------------------------


def extract_citation_indices(text: str) -> list[int]:
    """Every number inside a `[来源 ...]` marker, in order of appearance."""
    indices: list[int] = []
    for match in CITATION_PATTERN.finditer(text):
        indices.extend(int(number) for number in _DIGITS.findall(match.group(1)))
    return indices


def body_for_fact_matching(text: str) -> str:
    """Strip what would produce spurious fact hits.

    Citation markers and SKU tokens both carry digits (`[来源 2]`, `SKU-B200`),
    so a fact group like `["0"]` or `["2"]` would match them rather than the
    stated value.
    """
    without_citations = CITATION_PATTERN.sub(" ", text)
    return SKU_PATTERN.sub(" ", without_citations)


def restates_question(answer_text: str, question: str) -> bool:
    """A bare echo of the question is neither an answer nor a refusal."""
    normalize = lambda value: _PUNCTUATION.sub("", value).lower()
    stripped = answer_text.strip()
    if not stripped:
        return True
    return normalize(stripped) == normalize(question)


def looks_like_refusal(text: str, markers) -> bool:
    return any(marker in text for marker in markers)


def classify_behavior(prepared, reply: str, question: str) -> tuple[str, dict]:
    """Map one prepared+generated result onto an observed behaviour."""
    flags = {
        "model_called": prepared.needs_generation,
        "restates_question": False,
        "refusal_by_production_markers": False,
        "refusal_by_evaluator_markers": False,
        "corpus_unavailable": False,
    }
    if prepared.outcome == "direct":
        return BEHAVIOR_DIRECT, flags
    if not prepared.needs_generation:
        if prepared.fixed_answer in BOUNDARY_MESSAGES:
            return BEHAVIOR_BOUNDARY, flags
        if prepared.fixed_answer in UNAVAILABLE_MESSAGES:
            flags["corpus_unavailable"] = True
        return BEHAVIOR_POLICY_REFUSE, flags

    flags["restates_question"] = restates_question(reply, question)
    flags["refusal_by_production_markers"] = rag.is_refusal(reply)
    flags["refusal_by_evaluator_markers"] = looks_like_refusal(
        reply, EVALUATOR_REFUSAL_MARKERS
    )
    if flags["refusal_by_evaluator_markers"] and not flags["restates_question"]:
        return BEHAVIOR_GENERATION_REFUSE, flags
    return BEHAVIOR_ANSWER, flags


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def build_chunks() -> list:
    raw = DOCUMENT_PATH.read_bytes()
    text = rag.read_file(DOCUMENT_PATH.name, raw)
    return rag.build_index(rag.split_text(text, DOCUMENT_PATH.name))


def warm_up(chunks: list) -> float:
    """One full pass so model load time does not land on case 1.

    Uses a question that is in neither dataset, and its result is discarded.
    """
    started = perf_counter()
    prepared = chat_orchestration.prepare("年假有多少天？", chunks)
    if prepared.needs_generation:
        rag.answer_structured("年假有多少天？", prepared.results_for_answer, [])
    return perf_counter() - started


def run_case(case: dict, chunks: list, run_index: int, context: dict) -> dict:
    question = case["question"]
    started = perf_counter()
    prepared = chat_orchestration.prepare(question, chunks)
    if prepared.needs_generation:
        # History is deliberately empty: each case is judged on its own.
        reply = rag.answer_structured(question, prepared.results_for_answer, [])
    else:
        reply = prepared.fixed_answer
    duration = perf_counter() - started

    behavior, flags = classify_behavior(prepared, reply, question)
    citations = extract_citation_indices(reply)
    source_count = len(prepared.sources)
    source_types = sorted({item["type"] for item in prepared.sources})
    body = body_for_fact_matching(reply)

    fact_mode, fact_hits = evaluate_facts(case, body)
    all_facts_hit = all(entry["hit"] for entry in fact_hits)
    required = case["required_source_types"]
    required_covered = set(required) <= set(source_types)
    has_citation = bool(citations)
    citation_indices_valid = bool(citations) and all(
        1 <= index <= source_count for index in citations
    )
    route_match = prepared.route == case["expected_route"]
    expected = case["expected_behavior"]

    if expected == BEHAVIOR_ANSWER:
        passed = (
            behavior == BEHAVIOR_ANSWER
            and all_facts_hit
            and required_covered
            and has_citation
            and citation_indices_valid
        )
    elif expected == BEHAVIOR_GENERATION_REFUSE:
        passed = behavior == BEHAVIOR_GENERATION_REFUSE
    elif expected == BEHAVIOR_POLICY_REFUSE:
        passed = behavior == BEHAVIOR_POLICY_REFUSE and not flags["model_called"]
    else:  # boundary
        passed = (
            behavior == BEHAVIOR_BOUNDARY
            and route_match
            and reply == case.get("expected_message")
            and not flags["model_called"]
        )

    return {
        "run": run_index,
        "id": case["id"],
        "question": question,
        "category": case["category"],
        "difficulty": case["difficulty"],
        "notes": case.get("notes", ""),
        "git_sha": context["git_sha"],
        "chat_model": context["chat_model"],
        "embed_model": context["embed_model"],
        "dataset_sha256": context["dataset_sha256"],
        "expected_route": case["expected_route"],
        "actual_route": prepared.route,
        "route_match": route_match,
        "actual_steps": list(prepared.steps),
        "expected_behavior": expected,
        "actual_behavior": behavior,
        "outcome": prepared.outcome,
        "reason_codes": list(prepared.reason_codes),
        "source_types": source_types,
        "source_count": source_count,
        "answer": reply,
        "citation_indices": citations,
        "has_citation": has_citation,
        "citation_indices_valid": citation_indices_valid,
        "required_source_types": required,
        "required_source_coverage": required_covered,
        "fact_mode": fact_mode,
        "expected_fact_groups": fact_hits,
        "all_facts_hit": all_facts_hit,
        "duration_seconds": duration,
        "passed": passed,
        **flags,
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_run(records: list[dict]) -> dict:
    answers = [r for r in records if r["expected_behavior"] == BEHAVIOR_ANSWER]
    refusals = [r for r in records if r["expected_behavior"] in REFUSAL_BEHAVIORS]
    boundaries = [r for r in records if r["expected_behavior"] == BEHAVIOR_BOUNDARY]
    generated_answers = [
        r for r in records if r["model_called"] and r["actual_behavior"] == BEHAVIOR_ANSWER
    ]
    cited = [r for r in generated_answers if r["has_citation"]]
    fact_cases = [r for r in answers if r["expected_fact_groups"]]
    all_groups = [g for r in fact_cases for g in r["expected_fact_groups"]]
    durations = [r["duration_seconds"] for r in records]

    return {
        "total_cases": len(records),
        "passed": sum(r["passed"] for r in records),
        "pass_rate": _rate(sum(r["passed"] for r in records), len(records)),
        "answer_success_rate": _rate(sum(r["passed"] for r in answers), len(answers)),
        "false_refusal_rate": _rate(
            sum(
                r["actual_behavior"] in REFUSAL_BEHAVIORS + (BEHAVIOR_BOUNDARY,)
                for r in answers
            ),
            len(answers),
        ),
        "unanswerable_refusal_rate": _rate(
            sum(r["actual_behavior"] in REFUSAL_BEHAVIORS for r in refusals),
            len(refusals),
        ),
        "refusal_mechanism_match": _rate(
            sum(r["actual_behavior"] == r["expected_behavior"] for r in refusals),
            len(refusals),
        ),
        "boundary_accuracy": _rate(sum(r["passed"] for r in boundaries), len(boundaries)),
        "citation_presence_rate": _rate(len(cited), len(generated_answers)),
        "citation_index_validity": _rate(
            sum(r["citation_indices_valid"] for r in cited), len(cited)
        ),
        "required_source_coverage": _rate(
            sum(r["required_source_coverage"] for r in answers), len(answers)
        ),
        "expected_fact_hit_rate": _rate(
            sum(r["all_facts_hit"] for r in fact_cases), len(fact_cases)
        ),
        "expected_fact_group_hit_rate": _rate(
            sum(g["hit"] for g in all_groups), len(all_groups)
        ),
        "route_accuracy": _rate(sum(r["route_match"] for r in records), len(records)),
        "production_marker_refusal_gap": sum(
            r["refusal_by_evaluator_markers"] and not r["refusal_by_production_markers"]
            for r in records
        ),
        "question_restatements": sum(r["restates_question"] for r in records),
        "duration_p50": _percentile(durations, 0.50),
        "duration_p95": _percentile(durations, 0.95),
        "duration_max": max(durations) if durations else 0.0,
        "duration_total": sum(durations),
    }


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def aggregate(run_summaries: list[dict], runs: list[list[dict]]) -> dict:
    keys = [
        "pass_rate",
        "answer_success_rate",
        "false_refusal_rate",
        "unanswerable_refusal_rate",
        "refusal_mechanism_match",
        "boundary_accuracy",
        "citation_presence_rate",
        "citation_index_validity",
        "required_source_coverage",
        "expected_fact_hit_rate",
        "expected_fact_group_hit_rate",
        "route_accuracy",
    ]
    mean_metrics = {key: _mean([s[key] for s in run_summaries]) for key in keys}

    per_question: dict[str, list[bool]] = defaultdict(list)
    for records in runs:
        for record in records:
            per_question[record["id"]].append(record["passed"])

    stable_pass = [qid for qid, results in per_question.items() if all(results)]
    stable_fail = [qid for qid, results in per_question.items() if not any(results)]
    unstable = [
        qid
        for qid, results in per_question.items()
        if any(results) and not all(results)
    ]
    consistent = len(stable_pass) + len(stable_fail)

    durations = [r["duration_seconds"] for records in runs for r in records]
    mean_metrics.update(
        {
            "run_count": len(runs),
            "stable_pass": len(stable_pass),
            "stable_fail": len(stable_fail),
            "unstable": len(unstable),
            "stability_rate": _rate(consistent, len(per_question)),
            "stable_pass_rate": _rate(len(stable_pass), len(per_question)),
            "unstable_ids": sorted(unstable),
            "stable_fail_ids": sorted(stable_fail),
            "duration_p50": _percentile(durations, 0.50),
            "duration_p95": _percentile(durations, 0.95),
            "duration_max": max(durations) if durations else 0.0,
        }
    )
    return mean_metrics


def check_gates(aggregated: dict) -> list[dict]:
    gates = []
    for name, key, direction, threshold in GATES:
        value = aggregated.get(key)
        if value is None:
            passed = False
        elif direction == "min":
            passed = value >= threshold
        else:
            passed = value <= threshold
        gates.append(
            {
                "name": name,
                "value": value,
                "direction": direction,
                "threshold": threshold,
                "passed": passed,
            }
        )
    stability = aggregated.get("stability_rate")
    gates.append(
        {
            "name": "Cross-run Stability",
            "value": stability,
            "direction": "min",
            "threshold": GATE_STABILITY,
            "passed": stability is not None and stability >= GATE_STABILITY,
        }
    )
    return gates


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


# The six canonical failure kinds. A case can exhibit more than one; all that
# apply are reported rather than only the first, so a route gap that also caused
# a citation problem is not filed under one label alone.
FAILURE_REASONS = (
    ("route marker gap", lambda r: not r["route_match"]),
    (
        "unsupported boundary",
        lambda r: r["actual_behavior"] == BEHAVIOR_BOUNDARY
        and r["expected_behavior"] != BEHAVIOR_BOUNDARY,
    ),
    (
        "policy/refusal",
        lambda r: r["actual_behavior"] == BEHAVIOR_POLICY_REFUSE
        and r["expected_behavior"] != BEHAVIOR_POLICY_REFUSE,
    ),
    (
        "retrieval miss",
        lambda r: r["expected_behavior"] == BEHAVIOR_ANSWER
        and r["actual_behavior"] == BEHAVIOR_ANSWER
        and not r["required_source_coverage"],
    ),
    (
        "citation mismatch",
        lambda r: r["expected_behavior"] == BEHAVIOR_ANSWER
        and r["actual_behavior"] == BEHAVIOR_ANSWER
        and (not r["has_citation"] or not r["citation_indices_valid"]),
    ),
    (
        # Three shapes of the same layer failing: refusing an answerable
        # question, answering with the wrong facts, or answering one it should
        # have refused.
        "generation miss",
        lambda r: (
            r["expected_behavior"] == BEHAVIOR_ANSWER
            and r["actual_behavior"] == BEHAVIOR_GENERATION_REFUSE
        )
        or (
            r["expected_behavior"] == BEHAVIOR_ANSWER
            and r["actual_behavior"] == BEHAVIOR_ANSWER
            and not r["all_facts_hit"]
        )
        or (
            r["expected_behavior"] in REFUSAL_BEHAVIORS
            and r["actual_behavior"] == BEHAVIOR_ANSWER
        ),
    ),
)


def failure_reason(record: dict) -> str:
    reasons = [name for name, predicate in FAILURE_REASONS if predicate(record)]
    return "; ".join(reasons) if reasons else "unclassified"


def print_run(run_index: int, records: list[dict], summary: dict) -> None:
    print()
    print("=" * 78)
    print(f"Run {run_index}")
    print("=" * 78)
    for position, record in enumerate(records, start=1):
        status = "PASS" if record["passed"] else "FAIL"
        print(
            f"{position:03d}. {status} | {record['id']} | "
            f"{record['duration_seconds']:.2f}s"
        )
        print(f"     Q: {record['question']}")
        print(
            f"     route {record['expected_route']} -> {record['actual_route']}"
            f" | behavior {record['expected_behavior']} -> {record['actual_behavior']}"
            f" | outcome {record['outcome']}"
        )
        print(
            f"     sources={record['source_types'] or '-'}"
            f" citations={record['citation_indices'] or '-'}"
            f" facts={'ok' if record['all_facts_hit'] else 'MISS'}"
            f" reasons={', '.join(record['reason_codes']) or '-'}"
        )
        print(f"     A: {record['answer']}")
        if not record["passed"]:
            print(f"     -> {failure_reason(record)}")

    print()
    print(f"Run {run_index} summary")
    print(f"  Pass rate                 : {_pct(summary['pass_rate'])}"
          f" ({summary['passed']}/{summary['total_cases']})")
    print(f"  Answer Success Rate       : {_pct(summary['answer_success_rate'])}")
    print(f"  False Refusal Rate        : {_pct(summary['false_refusal_rate'])}")
    print(f"  Unanswerable Refusal Rate : {_pct(summary['unanswerable_refusal_rate'])}")
    print(f"  Boundary Accuracy         : {_pct(summary['boundary_accuracy'])}")
    print(f"  Citation Presence Rate    : {_pct(summary['citation_presence_rate'])}")
    print(f"  Citation Index Validity   : {_pct(summary['citation_index_validity'])}")
    print(f"  Required Source Coverage  : {_pct(summary['required_source_coverage'])}")
    print(f"  Expected Fact Hit Rate    : {_pct(summary['expected_fact_hit_rate'])}")
    print(f"  Route Accuracy            : {_pct(summary['route_accuracy'])}")
    print(
        f"  Duration p50/p95/max      : {summary['duration_p50']:.2f}s / "
        f"{summary['duration_p95']:.2f}s / {summary['duration_max']:.2f}s"
    )


def markdown_report(context: dict, run_summaries: list[dict], aggregated: dict,
                    gates: list[dict], runs: list[list[dict]]) -> str:
    lines = [
        "# Answerability evaluation",
        "",
        f"- Dataset: `{context['dataset']}`",
        f"- Dataset SHA-256: `{context['dataset_sha256']}`",
        f"- Git SHA: `{context['git_sha']}`",
        f"- Chat model: `{context['chat_model']}`",
        f"- Embedding model: `{context['embed_model']}`",
        f"- Python: {context['python_version']} on {context['platform']}",
        f"- Runs: {len(runs)}",
        f"- Warm-up seconds (excluded): {context['warmup_seconds']:.2f}",
        "",
        "## Per-run metrics",
        "",
        "| Metric | " + " | ".join(f"Run {i}" for i in range(1, len(runs) + 1))
        + " | Mean |",
        "|---|" + "---:|" * (len(runs) + 1),
    ]
    metric_rows = [
        ("Pass rate", "pass_rate"),
        ("Answer Success Rate", "answer_success_rate"),
        ("False Refusal Rate", "false_refusal_rate"),
        ("Unanswerable Refusal Rate", "unanswerable_refusal_rate"),
        ("Refusal Mechanism Match", "refusal_mechanism_match"),
        ("Boundary Message Accuracy", "boundary_accuracy"),
        ("Citation Presence Rate", "citation_presence_rate"),
        ("Citation Index Validity", "citation_index_validity"),
        ("Required Source Coverage", "required_source_coverage"),
        ("Expected Fact Hit Rate", "expected_fact_hit_rate"),
        ("Expected Fact Group Hit Rate", "expected_fact_group_hit_rate"),
        ("Route Accuracy", "route_accuracy"),
    ]
    for label, key in metric_rows:
        cells = " | ".join(_pct(summary[key]) for summary in run_summaries)
        lines.append(f"| {label} | {cells} | {_pct(aggregated[key])} |")

    lines += [
        "",
        "## Latency",
        "",
        "| Run | p50 | p95 | max |",
        "|---|---:|---:|---:|",
    ]
    for index, summary in enumerate(run_summaries, start=1):
        lines.append(
            f"| {index} | {summary['duration_p50']:.2f}s | "
            f"{summary['duration_p95']:.2f}s | {summary['duration_max']:.2f}s |"
        )
    lines.append(
        f"| all | {aggregated['duration_p50']:.2f}s | "
        f"{aggregated['duration_p95']:.2f}s | {aggregated['duration_max']:.2f}s |"
    )

    lines += [
        "",
        "## Stability",
        "",
        f"- Stable pass (all runs): {aggregated['stable_pass']}",
        f"- Unstable (some runs): {aggregated['unstable']}",
        f"- Stable fail (no run): {aggregated['stable_fail']}",
        f"- Consistency rate: {_pct(aggregated['stability_rate'])}",
        "",
    ]
    if aggregated["unstable_ids"]:
        lines.append("Unstable cases: " + ", ".join(f"`{i}`" for i in aggregated["unstable_ids"]))
        lines.append("")
    if aggregated["stable_fail_ids"]:
        lines.append("Stable failures: " + ", ".join(f"`{i}`" for i in aggregated["stable_fail_ids"]))
        lines.append("")

    lines += [
        "## Failures",
        "",
        "| Run | ID | Expected | Actual | Route | Reason | Answer |",
        "|---|---|---|---|---|---|---|",
    ]
    for run_index, records in enumerate(runs, start=1):
        for record in records:
            if record["passed"]:
                continue
            answer = record["answer"].replace("|", "/").replace("\n", " ")
            if len(answer) > 90:
                answer = answer[:90] + "…"
            lines.append(
                f"| {run_index} | `{record['id']}` | {record['expected_behavior']} | "
                f"{record['actual_behavior']} | "
                f"{record['expected_route']}→{record['actual_route']} | "
                f"{failure_reason(record)} | {answer} |"
            )

    lines += ["", "## Gates", "", "| Gate | Value | Threshold | Result |", "|---|---:|---:|---|"]
    for gate in gates:
        comparison = ">=" if gate["direction"] == "min" else "<="
        lines.append(
            f"| {gate['name']} | {_pct(gate['value'])} | "
            f"{comparison} {gate['threshold']:.0%} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Answerability dataset JSON")
    parser.add_argument("--runs", type=int, default=1, help="How many full passes")
    parser.add_argument("--output-dir", help="Directory for per-run JSON and summary")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check the dataset schema and exit without calling any model",
    )
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    cases = validate_dataset(
        json.loads(dataset_path.read_text(encoding="utf-8")), dataset_path
    )

    if args.validate_only:
        print(f"Schema OK: {dataset_path} ({len(cases)} cases)")
        print(f"Dataset SHA-256: {sha256_of(dataset_path)}")
        return 0

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not args.output_dir:
        parser.error("--output-dir is required unless --validate-only is given")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    context = {
        "dataset": str(dataset_path),
        "dataset_sha256": sha256_of(dataset_path),
        "git_sha": git_sha(),
        "chat_model": rag.CHAT_MODEL,
        "embed_model": rag.EMBED_MODEL,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "evaluator": Path(__file__).name,
    }

    print(f"Dataset      : {dataset_path} ({len(cases)} cases)")
    print(f"Dataset SHA  : {context['dataset_sha256']}")
    print(f"Git SHA      : {context['git_sha']}")
    print(f"Chat model   : {context['chat_model']}")
    print(f"Embed model  : {context['embed_model']}")
    print(f"Runs         : {args.runs}")

    print("Building document index …")
    chunks = build_chunks()
    print(f"Indexed {len(chunks)} chunks from {DOCUMENT_PATH.name}")

    print("Warming up the answer model …")
    context["warmup_seconds"] = warm_up(chunks)
    print(f"Warm-up took {context['warmup_seconds']:.2f}s (excluded from results)")

    runs: list[list[dict]] = []
    run_summaries: list[dict] = []
    for run_index in range(1, args.runs + 1):
        records = [run_case(case, chunks, run_index, context) for case in cases]
        summary = summarize_run(records)
        runs.append(records)
        run_summaries.append(summary)
        print_run(run_index, records, summary)
        (output_dir / f"run-{run_index}.json").write_text(
            json.dumps(
                {"context": context, "run": run_index, "summary": summary,
                 "cases": records},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    aggregated = aggregate(run_summaries, runs)
    gates = check_gates(aggregated)

    print()
    print("=" * 78)
    print("Aggregate")
    print("=" * 78)
    print(f"Runs                      : {aggregated['run_count']}")
    print(f"Answer Success Rate       : {_pct(aggregated['answer_success_rate'])}")
    print(f"False Refusal Rate        : {_pct(aggregated['false_refusal_rate'])}")
    print(f"Unanswerable Refusal Rate : {_pct(aggregated['unanswerable_refusal_rate'])}")
    print(f"Boundary Accuracy         : {_pct(aggregated['boundary_accuracy'])}")
    print(f"Citation Presence Rate    : {_pct(aggregated['citation_presence_rate'])}")
    print(f"Citation Index Validity   : {_pct(aggregated['citation_index_validity'])}")
    print(f"Required Source Coverage  : {_pct(aggregated['required_source_coverage'])}")
    print(f"Expected Fact Hit Rate    : {_pct(aggregated['expected_fact_hit_rate'])}")
    print(
        f"Stability                 : {_pct(aggregated['stability_rate'])}"
        f" (3/3 pass {aggregated['stable_pass']}, unstable {aggregated['unstable']},"
        f" 0/N fail {aggregated['stable_fail']})"
    )
    print(
        f"Duration p50/p95/max      : {aggregated['duration_p50']:.2f}s / "
        f"{aggregated['duration_p95']:.2f}s / {aggregated['duration_max']:.2f}s"
    )
    print()
    print("Gates")
    for gate in gates:
        comparison = ">=" if gate["direction"] == "min" else "<="
        print(
            f"  [{'PASS' if gate['passed'] else 'FAIL'}] {gate['name']:<28}"
            f"{_pct(gate['value'])} ({comparison} {gate['threshold']:.0%})"
        )

    (output_dir / "summary.json").write_text(
        json.dumps(
            {"context": context, "gates": gates, "runs": run_summaries,
             "aggregate": aggregated},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        markdown_report(context, run_summaries, aggregated, gates, runs),
        encoding="utf-8",
    )
    print()
    print(f"Wrote {output_dir / 'summary.json'} and {output_dir / 'summary.md'}")

    return 0 if all(gate["passed"] for gate in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
