"""Compare a Stage 0 regression run against the frozen Qwen baseline.

The pass criteria below were fixed before any regression data existed.

1. Aggregate range: for every metric, each regression run lies inside the
   baseline runs' [min, max] widened by one case of that metric's denominator.
2. Per-case flips, using each case's pass count over the runs (0..N):
   - hard regression   baseline N/N pass, regression <= 1/N    -> FAIL
   - hard improvement  baseline 0/N pass, regression >= N-1/N  -> reported (unexpected)
   - behaviour flip    a case whose baseline behaviour was the same in every
                       run shows none of that behaviour in regression -> FAIL
   - soft flip         any other change in pass count          -> reported
3. Request identity: every system prompt and every request shape (model,
   think, format, options, tools) the regression sends was also sent by the
   baseline. Anything new -> FAIL. A baseline-only shape (e.g. a fallback path
   that happened not to fire) and per-shape call counts are reported only.

Usage:
    python eval/compare_stage0.py eval/baseline_qwen.json eval/regression_qwen.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Subset sizes of eval_answerability_validation_v1.json: 20 answer,
# 12 refusal (8 generation + 4 policy), 8 boundary, 40 total.
TOLERANCE_DENOMINATOR = {
    "pass_rate": 40,
    "answer_success_rate": 20,
    "false_refusal_rate": 20,
    "unanswerable_refusal_rate": 12,
    "refusal_mechanism_match": 12,
    "boundary_accuracy": 8,
    "citation_presence_rate": 20,
    "citation_index_validity": 20,
    "required_source_coverage": 20,
    "expected_fact_hit_rate": 20,
    "expected_fact_group_hit_rate": 20,
    "route_accuracy": 40,
}


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def aggregate_check(base: dict, reg: dict) -> tuple[list[dict], bool]:
    rows, ok = [], True
    for metric, denominator in TOLERANCE_DENOMINATOR.items():
        base_values = [r[metric] for r in base["per_run_summary"] if r[metric] is not None]
        reg_values = [r[metric] for r in reg["per_run_summary"] if r[metric] is not None]
        if not base_values or not reg_values:
            continue
        low, high = min(base_values) - 1 / denominator, max(base_values) + 1 / denominator
        inside = all(low - 1e-9 <= v <= high + 1e-9 for v in reg_values)
        ok &= inside
        rows.append({"metric": metric, "baseline": base_values, "regression": reg_values,
                     "allowed": [max(low, 0.0), min(high, 1.0)], "inside": inside})
    base_passed = [r["passed"] for r in base["per_run_summary"]]
    reg_passed = [r["passed"] for r in reg["per_run_summary"]]
    inside = all(min(base_passed) - 1 <= v <= max(base_passed) + 1 for v in reg_passed)
    ok &= inside
    rows.insert(0, {"metric": "passed_cases", "baseline": base_passed, "regression": reg_passed,
                    "allowed": [min(base_passed) - 1, max(base_passed) + 1], "inside": inside})
    return rows, ok


def flip_check(base: dict, reg: dict) -> tuple[dict, bool]:
    runs = base["runs"]
    base_cases = {c["id"]: c for c in base["cases"]}
    reg_cases = {c["id"]: c for c in reg["cases"]}
    result = {"hard_regression": [], "hard_improvement": [], "behaviour_flip": [],
              "soft_flip": [], "missing": sorted(set(base_cases) ^ set(reg_cases))}
    for case_id in sorted(set(base_cases) & set(reg_cases)):
        b, r = base_cases[case_id], reg_cases[case_id]
        entry = {
            "id": case_id, "expected": b["expected_behavior"],
            "baseline_pass": f"{b['pass_count']}/{runs}", "regression_pass": f"{r['pass_count']}/{reg['runs']}",
            "baseline_behaviours": [x["actual_behavior"] for x in b["runs"]],
            "regression_behaviours": [x["actual_behavior"] for x in r["runs"]],
        }
        base_behaviours = set(entry["baseline_behaviours"])
        if b["pass_count"] == runs and r["pass_count"] <= 1:
            result["hard_regression"].append(entry)
        elif b["pass_count"] == 0 and r["pass_count"] >= reg["runs"] - 1:
            result["hard_improvement"].append(entry)
        elif b["pass_count"] != r["pass_count"]:
            result["soft_flip"].append(entry)
        if len(base_behaviours) == 1 and not base_behaviours & set(entry["regression_behaviours"]):
            result["behaviour_flip"].append(entry)
    ok = not result["hard_regression"] and not result["behaviour_flip"] and not result["missing"]
    return result, ok


def request_check(base: dict, reg: dict) -> tuple[dict, bool]:
    def shapes(report: dict) -> dict:
        return {json.dumps({k: v for k, v in s.items() if k != "calls"}, sort_keys=True, ensure_ascii=False): s["calls"]
                for s in report["observed_llm_requests"]["request_shapes"]}

    base_prompts = {p["sha256"] for p in base["observed_llm_requests"]["system_prompts"]}
    reg_prompts = {p["sha256"] for p in reg["observed_llm_requests"]["system_prompts"]}
    base_shapes, reg_shapes = shapes(base), shapes(reg)
    result = {
        "system_prompts_identical": base_prompts == reg_prompts,
        "no_new_system_prompts": reg_prompts <= base_prompts,
        "system_prompts_only_in_baseline": sorted(base_prompts - reg_prompts),
        "system_prompts_only_in_regression": sorted(reg_prompts - base_prompts),
        "request_shapes_identical": set(base_shapes) == set(reg_shapes),
        "no_new_request_shapes": set(reg_shapes) <= set(base_shapes),
        "shape_call_counts": [
            {"shape": json.loads(key), "baseline_calls": base_shapes.get(key, 0), "regression_calls": reg_shapes.get(key, 0)}
            for key in sorted(set(base_shapes) | set(reg_shapes))
        ],
        "chat_calls": [base["observed_llm_requests"]["chat_calls"], reg["observed_llm_requests"]["chat_calls"]],
    }
    return result, result["no_new_system_prompts"] and result["no_new_request_shapes"]


def pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def main(argv: list[str]) -> int:
    base, reg = load(argv[1]), load(argv[2])
    aggregate_rows, aggregate_ok = aggregate_check(base, reg)
    flips, flips_ok = flip_check(base, reg)
    requests_report, requests_ok = request_check(base, reg)
    verdict = aggregate_ok and flips_ok and requests_ok

    print("## Aggregate (per run)")
    print("| metric | baseline runs | regression runs | allowed | ok |")
    print("|---|---|---|---|---|")
    for row in aggregate_rows:
        fmt = (lambda v: str(v)) if row["metric"] == "passed_cases" else pct
        print(f"| {row['metric']} | {' / '.join(fmt(v) for v in row['baseline'])} | "
              f"{' / '.join(fmt(v) for v in row['regression'])} | "
              f"{fmt(row['allowed'][0])} – {fmt(row['allowed'][1])} | {'✅' if row['inside'] else '❌'} |")
    print()
    print("## Per-case flips")
    for kind in ("hard_regression", "behaviour_flip", "hard_improvement", "soft_flip"):
        print(f"- {kind}: {len(flips[kind])}")
        for entry in flips[kind]:
            print(f"    - {entry['id']} ({entry['expected']}): {entry['baseline_pass']} -> {entry['regression_pass']}; "
                  f"behaviours {entry['baseline_behaviours']} -> {entry['regression_behaviours']}")
    if flips["missing"]:
        print(f"- missing cases: {flips['missing']}")
    print()
    print("## Request identity")
    print(f"- system prompts identical: {requests_report['system_prompts_identical']}"
          f" (no new: {requests_report['no_new_system_prompts']})")
    print(f"- request shapes identical: {requests_report['request_shapes_identical']}"
          f" (no new: {requests_report['no_new_request_shapes']})")
    print(f"- /api/chat calls baseline vs regression: {requests_report['chat_calls'][0]} vs {requests_report['chat_calls'][1]}")
    for item in requests_report["shape_call_counts"]:
        shape = item["shape"]
        print(f"    - format={shape['format']} options={shape['options']} tools={shape['tools']} "
              f"prompt={shape['system_prompt_sha256'][:12]}: {item['baseline_calls']} vs {item['regression_calls']}")
    print()
    print(f"VERDICT: {'NO REGRESSION' if verdict else 'REGRESSION OR MISMATCH'} "
          f"(aggregate={aggregate_ok}, flips={flips_ok}, requests={requests_ok})")

    output = Path(argv[2]).with_name("stage0_comparison.json")
    output.write_text(json.dumps({
        "baseline": argv[1], "regression": argv[2], "verdict_no_regression": verdict,
        "aggregate": aggregate_rows, "flips": flips, "requests": requests_report,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
