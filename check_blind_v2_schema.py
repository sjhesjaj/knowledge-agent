"""Static schema validation for the Blind Holdout V2 datasets.

Read-only. Runs before anything touches the planner, a model, or a database, so
a malformed dataset is caught while it can still be sent back to the author
rather than silently changing every score.

The two evaluators already validate their own schemas; this adds the V2-specific
constraints from the milestone that they do not know about - the ID sequence,
the exact per-route and per-behaviour counts, the boundary-difficulty floor, and
two named cases whose fact groups were specified individually.

Usage:
    python check_blind_v2_schema.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent
ROUTE_PATH = ROOT / "eval_orchestrated_routes_blind_v2.json"
ANSWER_PATH = ROOT / "eval_answerability_blind_v2.json"

ROUTES = (
    "direct",
    "wiki_only",
    "document_only",
    "system_only",
    "wiki_document",
    "wiki_system",
    "document_system",
    "wiki_document_system",
)
STEPS_FOR_ROUTE = {
    "direct": [],
    "wiki_only": ["wiki_query"],
    "document_only": ["document_search"],
    "system_only": ["system_query"],
    "wiki_document": ["wiki_query", "document_search"],
    "wiki_system": ["wiki_query", "system_query"],
    "document_system": ["document_search", "system_query"],
    "wiki_document_system": ["wiki_query", "document_search", "system_query"],
}
CANONICAL_ORDER = ("wiki_query", "document_search", "system_query")

VALID_DIFFICULTY = ("normal", "boundary")
VALID_SOURCE_TYPES = ("wiki", "document", "system")
BOUNDARY_MESSAGES = ("当前版本仅支持库存查询。", "请提供需要查询的 SKU。",
                     "当前一次支持查询一个 SKU，请拆分后分别查询。")

BEHAVIOR_COUNTS = {
    "answer": 20,
    "policy_refuse": 4,
    "generation_refuse": 8,
    "boundary": 8,
}
ANSWER_CATEGORY_COUNTS = {"document": 8, "wiki": 4, "system": 4, "multi_channel": 4}

MIN_ROUTE_BOUNDARY = 28
# Exact distribution of the frozen V2 route set. Asserted rather than merely
# reported, so a silently swapped dataset cannot reach scoring.
EXPECTED_ROUTE_BOUNDARY = 45
EXPECTED_ROUTE_FRESHNESS = 25
EXPECTED_ROUTE_EXACT = 36

# A pattern that is only a number matches any stray digit in an answer.
BARE_NUMBER = re.compile(r"^[0-9〇零一二三四五六七八九十百千两]{1,3}$")

# Fact groups the milestone specified case by case.
NAMED_FACT_GROUPS = {
    "answer_v2_008": (5, ("10", "分钟"), ("2", "次"), ("提醒",), ("不扣款",), ("异常说明",)),
    "answer_v2_018": (4, ("2", "小时"), ("直属主管",), ("信息技术部门",), ("7", "箱")),
}


def load(path: Path) -> list:
    if not path.exists():
        raise SystemExit(f"missing dataset: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path.name}: top level must be a JSON array")
    return data


def check_routes(cases: list) -> list[str]:
    problems: list[str] = []
    if len(cases) != 80:
        problems.append(f"route: expected 80 cases, got {len(cases)}")

    counts: Counter[str] = Counter()
    ids: list[str] = []
    questions: dict[str, int] = {}
    boundary = 0

    for index, case in enumerate(cases):
        where = f"route[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{where} must be an object")
            continue
        case_id = case.get("id")
        ids.append(case_id)
        question = case.get("question")
        route = case.get("expected_route")
        steps = case.get("expected_steps")

        if not isinstance(question, str) or not question.strip():
            problems.append(f"{where}.question must be a non-empty string")
        elif question in questions:
            problems.append(f"{where}.question duplicates route[{questions[question]}]")
        else:
            questions[question] = index

        if route not in ROUTES:
            problems.append(f"{where}.expected_route unknown: {route!r}")
        else:
            counts[route] += 1
            if steps != STEPS_FOR_ROUTE[route]:
                problems.append(
                    f"{where}.expected_steps {steps} != {STEPS_FOR_ROUTE[route]} "
                    f"for route {route!r}"
                )
            if isinstance(steps, list):
                ordered = [s for s in CANONICAL_ORDER if s in steps]
                if steps != ordered:
                    problems.append(f"{where}.expected_steps not in canonical order")

        difficulty = case.get("difficulty")
        if difficulty not in VALID_DIFFICULTY:
            problems.append(f"{where}.difficulty invalid: {difficulty!r}")
        elif difficulty == "boundary":
            boundary += 1

        for flag in ("expected_requires_freshness", "expected_requires_exact_citation"):
            if not isinstance(case.get(flag), bool):
                problems.append(f"{where}.{flag} must be a boolean")

    expected_ids = [f"route_v2_{n:03d}" for n in range(1, 81)]
    if ids != expected_ids:
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        problems.append(
            "route ids are not the unique consecutive sequence route_v2_001..080"
            + (f"; missing {missing[:5]}" if missing else "")
            + (f"; unexpected {extra[:5]}" if extra else "")
        )

    for route in ROUTES:
        if counts[route] != 10:
            problems.append(f"route {route!r} has {counts[route]} cases, expected 10")

    if boundary < MIN_ROUTE_BOUNDARY:
        problems.append(
            f"route boundary count {boundary} is below the required {MIN_ROUTE_BOUNDARY}"
        )
    if boundary != EXPECTED_ROUTE_BOUNDARY:
        problems.append(
            f"route boundary count {boundary} != frozen {EXPECTED_ROUTE_BOUNDARY}"
        )

    freshness = sum(1 for c in cases if c.get("expected_requires_freshness"))
    exact = sum(1 for c in cases if c.get("expected_requires_exact_citation"))
    if freshness != EXPECTED_ROUTE_FRESHNESS:
        problems.append(f"freshness=True is {freshness}, expected {EXPECTED_ROUTE_FRESHNESS}")
    if exact != EXPECTED_ROUTE_EXACT:
        problems.append(f"exact_citation=True is {exact}, expected {EXPECTED_ROUTE_EXACT}")

    print(f"  cases            : {len(cases)}")
    print(f"  per route        : {dict(sorted(counts.items()))}")
    print(f"  boundary         : {boundary} (expect {EXPECTED_ROUTE_BOUNDARY})")
    print(f"  freshness=True   : {freshness} (expect {EXPECTED_ROUTE_FRESHNESS})")
    print(f"  exact=True       : {exact} (expect {EXPECTED_ROUTE_EXACT})")
    return problems


def check_answers(cases: list) -> list[str]:
    problems: list[str] = []
    if len(cases) != 40:
        problems.append(f"answerability: expected 40 cases, got {len(cases)}")

    behaviors: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    ids: list[str] = []
    questions: dict[str, int] = {}

    for index, case in enumerate(cases):
        where = f"answer[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{where} must be an object")
            continue
        ids.append(case.get("id"))
        question = case.get("question")
        behavior = case.get("expected_behavior")
        patterns = case.get("expected_fact_patterns", [])
        sources = case.get("required_source_types", [])

        if not isinstance(question, str) or not question.strip():
            problems.append(f"{where}.question must be a non-empty string")
        elif question in questions:
            problems.append(f"{where}.question duplicates answer[{questions[question]}]")
        else:
            questions[question] = index

        if behavior not in BEHAVIOR_COUNTS:
            problems.append(f"{where}.expected_behavior unknown: {behavior!r}")
        else:
            behaviors[behavior] += 1
            if behavior == "answer":
                categories[case.get("category")] += 1

        if not isinstance(sources, list) or any(
            s not in VALID_SOURCE_TYPES for s in sources
        ):
            problems.append(f"{where}.required_source_types invalid: {sources!r}")

        if not isinstance(patterns, list):
            problems.append(f"{where}.expected_fact_patterns must be a list")
        elif behavior == "answer":
            if not patterns:
                problems.append(f"{where} expects an answer but states no fact patterns")
            for gi, group in enumerate(patterns):
                if not isinstance(group, list) or not group:
                    problems.append(f"{where}.expected_fact_patterns[{gi}] must be non-empty")
                    continue
                for item in group:
                    if not isinstance(item, str) or not item.strip():
                        problems.append(f"{where}.expected_fact_patterns[{gi}] bad entry")
                        continue
                    if BARE_NUMBER.match(item.strip()):
                        problems.append(
                            f"{where}.expected_fact_patterns[{gi}] context-free number "
                            f"{item!r}"
                        )
                    try:
                        re.compile(item, re.UNICODE | re.IGNORECASE)
                    except re.error as exc:
                        problems.append(
                            f"{where}.expected_fact_patterns[{gi}] {item!r} will not "
                            f"compile: {exc}"
                        )
        elif patterns:
            problems.append(f"{where}.expected_fact_patterns must be empty for {behavior!r}")

        message = case.get("expected_message")
        if behavior == "boundary":
            if not isinstance(message, str) or not message.strip():
                problems.append(f"{where}.expected_message required for boundary")
            elif message not in BOUNDARY_MESSAGES:
                problems.append(f"{where}.expected_message not a product message: {message!r}")
        elif message:
            problems.append(f"{where}.expected_message set on a non-boundary case")

        if case.get("difficulty") not in VALID_DIFFICULTY:
            problems.append(f"{where}.difficulty invalid: {case.get('difficulty')!r}")

    expected_ids = [f"answer_v2_{n:03d}" for n in range(1, 41)]
    if ids != expected_ids:
        problems.append("answer ids are not the unique consecutive sequence answer_v2_001..040")

    for behavior, expected in BEHAVIOR_COUNTS.items():
        if behaviors[behavior] != expected:
            problems.append(
                f"behaviour {behavior!r} has {behaviors[behavior]}, expected {expected}"
            )
    for category, expected in ANSWER_CATEGORY_COUNTS.items():
        if categories[category] != expected:
            problems.append(
                f"answer category {category!r} has {categories[category]}, expected {expected}"
            )

    by_id = {c.get("id"): c for c in cases if isinstance(c, dict)}
    for case_id, spec in NAMED_FACT_GROUPS.items():
        expected_count, *required = spec
        case = by_id.get(case_id)
        if case is None:
            problems.append(f"{case_id} is missing")
            continue
        groups = case.get("expected_fact_patterns", [])
        if len(groups) != expected_count:
            problems.append(
                f"{case_id} has {len(groups)} fact groups, expected {expected_count}"
            )
        blob = json.dumps(groups, ensure_ascii=False)
        for tokens in required:
            if not all(token in blob for token in tokens):
                problems.append(
                    f"{case_id} fact groups do not cover {'+'.join(tokens)}"
                )

    print(f"  cases            : {len(cases)}")
    print(f"  behaviours       : {dict(sorted(behaviors.items()))}")
    print(f"  answer categories: {dict(sorted(categories.items()))}")
    print(
        f"  difficulty       : "
        f"{dict(sorted(Counter(c.get('difficulty') for c in cases).items()))}"
    )
    return problems


def main() -> int:
    print("=" * 74)
    print("Blind Holdout V2 - static schema validation")
    print("=" * 74)
    print(f"Route dataset: {ROUTE_PATH.name}")
    route_problems = check_routes(load(ROUTE_PATH))
    print()
    print(f"Answerability dataset: {ANSWER_PATH.name}")
    answer_problems = check_answers(load(ANSWER_PATH))

    problems = route_problems + answer_problems
    print()
    if problems:
        print(f"FAILED: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("SCHEMA OK: both datasets satisfy every V2 constraint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
