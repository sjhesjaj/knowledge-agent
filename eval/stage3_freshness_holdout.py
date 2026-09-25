"""Stage 3: open the sealed temporal/freshness holdout exactly once.

The holdout (`eval_temporal_freshness_holdout.json`) was written by a separate
agent that never saw the planner, the dev set or the candidate rule; its
contents stay unread until this script runs. The script:

1. refuses to run if a result already exists (the holdout is opened once);
2. refuses unless the committed file still matches the sealed sha256;
3. refuses on a dirty tree, so the scored planner is a committed one;
4. scores both the `main` planner (via `git archive`) and the working-tree
   planner on the same cases, and writes one result.

Usage:
    .venv\\Scripts\\python.exe eval\\stage3_freshness_holdout.py --open
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOLDOUT = "eval_temporal_freshness_holdout.json"
SEALED_SHA256 = "b24d326eb3040b9b9690d51c270ed2b5aeece41676871faaffe422df75940cfb"
SEAL_COMMIT = "7cc5abee5dd85ff63c23c3a91d29603f7d42a7f9"
OUTPUT = ROOT / "eval" / "stage3" / "holdout_result.json"


def git(*args: str, text: bool = True):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text)
    return out.stdout.strip() if text else out.stdout


def main_planner(ref: str = "main"):
    archive = git("archive", ref, "orchestration", text=False)
    tmp = Path(tempfile.mkdtemp())
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(tmp)
    (tmp / "orchestration").rename(tmp / "orchestration_main")
    sys.path.insert(0, str(tmp))
    return importlib.import_module("orchestration_main.planner")


def score(planner, cases: list[dict]) -> dict:
    rows, counts, by_intent = [], Counter(), defaultdict(Counter)
    for case in cases:
        plan = planner.plan_request(case["question"])
        pred, exp = plan.signals.requires_freshness, case["expected_requires_freshness"]
        outcome = "TP" if pred and exp else "FP" if pred else "FN" if exp else "TN"
        route_ok = plan.route.value == case["expected_route"]
        counts[outcome] += 1
        by_intent[case["temporal_intent"]][outcome] += 1
        by_intent[case["temporal_intent"]]["route_match"] += route_ok
        by_intent[case["temporal_intent"]]["n"] += 1
        rows.append({"id": case["id"], "question": case["question"], "temporal_intent": case["temporal_intent"],
                     "markers": case["markers"], "expected_requires_freshness": exp,
                     "predicted_requires_freshness": pred, "freshness_outcome": outcome,
                     "expected_route": case["expected_route"], "predicted_route": plan.route.value,
                     "route_match": route_ok, "steps": [s.value for s in plan.steps],
                     "reason_codes": list(plan.reason_codes)})
    tp, fp, fn, tn = (counts[k] for k in ("TP", "FP", "FN", "TN"))
    n = len(rows)
    rate = lambda a, b: None if b == 0 else round(a / b, 4)
    return {"n": n, "accuracy": rate(tp + tn, n), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": rate(tp, tp + fp), "recall": rate(tp, tp + fn),
            "false_positive_rate": rate(fp, fp + tn), "false_negative_rate": rate(fn, fn + tp),
            "route_accuracy": rate(sum(r["route_match"] for r in rows), n),
            "by_intent": {k: dict(v) for k, v in sorted(by_intent.items())}, "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="required: acknowledges this is the one opening")
    args = parser.parse_args()
    if not args.open:
        print("REFUSED: pass --open to open the sealed holdout (this can happen once)")
        return 2
    if OUTPUT.exists():
        print(f"REFUSED: {OUTPUT} exists; the holdout has already been opened")
        return 2
    if git("status", "--porcelain", "--untracked-files=no"):
        print("REFUSED: the working tree has changes; commit the planner you want scored")
        return 2
    blob = git("show", f"HEAD:{HOLDOUT}", text=False)
    if hashlib.sha256(blob).hexdigest() != SEALED_SHA256:
        print("REFUSED: the committed holdout no longer matches the sealed sha256")
        return 2
    if git("log", "--format=%H", f"{SEAL_COMMIT}..HEAD", "--", HOLDOUT):
        print("REFUSED: the holdout file was touched after it was sealed")
        return 2

    cases = json.loads(blob.decode("utf-8"))
    from orchestration import planner as working

    result = {
        "stage": "3", "kind": "sealed holdout, opened once",
        "dataset": HOLDOUT, "dataset_sha256": SEALED_SHA256, "seal_commit": SEAL_COMMIT,
        "scored_commit": git("rev-parse", "HEAD"), "main_commit": git("rev-parse", "main"),
        "main_planner": score(main_planner(), cases),
        "working_planner": score(working, cases),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("main_planner", "working_planner"):
        s = result[name]
        print(f"{name}: freshness accuracy {s['accuracy']} | TP {s['tp']} FP {s['fp']} FN {s['fn']} TN {s['tn']} | "
              f"precision {s['precision']} recall {s['recall']} | route accuracy {s['route_accuracy']}")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
