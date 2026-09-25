"""Score route-eval outputs with a label-revision overlay applied.

Frozen datasets are never edited. An overlay (eval/label_revisions/*.json)
lists corrections with reasons; this script re-scores existing
`evaluate_orchestrated_routes.py` outputs against the corrected labels and
reports both numbers side by side, so a corrected label can never pass for a
planner improvement.

Usage:
    .venv\\Scripts\\python.exe eval\\apply_label_revisions.py --revisions eval\\label_revisions\\<file>.json ^
        --route-eval eval\\artifacts\\<dev>.json --route-eval eval\\artifacts\\<validation>.json ^
        --output eval\\stage3\\<name>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = {"expected_requires_freshness": ("actual_requires_freshness", "freshness_signal_accuracy")}


def sha256_lf(path: Path) -> str:
    """Hash with line endings normalised, so a CRLF checkout matches the LF blob."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_revisions(path: Path) -> dict:
    overlay = json.loads(path.read_text(encoding="utf-8"))
    for item in overlay["revisions"]:
        if "blind" in item["dataset"] or "holdout" in item["dataset"]:
            raise ValueError(f"{item['dataset']}: revisions never target blind or holdout datasets")
        if item["field"] not in FIELDS:
            raise ValueError(f"{item['field']}: unsupported field")
        dataset = ROOT / item["dataset"]
        if sha256_lf(dataset) != item["dataset_sha256_lf"]:
            raise ValueError(f"{item['dataset']}: dataset changed since the overlay was written")
        cases = {c["id"]: c for c in json.loads(dataset.read_text(encoding="utf-8"))}
        case = cases.get(item["case_id"])
        if case is None or case["question"] != item["question"] or case[item["field"]] != item["old"]:
            raise ValueError(f"{item['case_id']}: the overlay's old value no longer matches the dataset")
    return overlay


def rescore(route_eval: dict, overlay: dict) -> dict:
    mine = [r for r in overlay["revisions"] if r["dataset"] == route_eval["dataset"]]
    cases = route_eval["cases"]
    out = {"dataset": route_eval["dataset"], "git_sha": route_eval.get("git_sha"), "revised_cases": [], "metrics": {}}
    for field, (actual_key, metric) in FIELDS.items():
        revised = {r["case_id"]: r["new"] for r in mine if r["field"] == field}
        original = sum(c[field] == c[actual_key] for c in cases)
        corrected = sum(revised.get(c["id"], c[field]) == c[actual_key] for c in cases)
        out["metrics"][metric] = {"original_labels": f"{original}/{len(cases)}",
                                  "with_revisions": f"{corrected}/{len(cases)}"}
        out["revised_cases"] += [{"id": c["id"], "field": field, "old": c[field], "new": revised[c["id"]],
                                  "actual": c[actual_key]} for c in cases if c["id"] in revised]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revisions", required=True)
    parser.add_argument("--route-eval", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        print(f"REFUSED: {output} exists; results are never overwritten")
        return 2
    overlay = load_revisions(Path(args.revisions))
    results = [rescore(json.loads(Path(p).read_text(encoding="utf-8")), overlay) for p in args.route_eval]
    report = {"revision_id": overlay["revision_id"], "revisions": args.revisions, "results": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print(r["dataset"], r["metrics"], [c["id"] for c in r["revised_cases"]])
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
