"""Write the Blind Holdout V2 freeze manifest.

Run once, after schema validation and the independence gate have both passed
and before any product scoring. The manifest is the record of exactly what was
frozen, so that a later score can be tied to a specific pair of files.

Usage:
    python make_blind_v2_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent
ROUTE = ROOT / "eval_orchestrated_routes_blind_v2.json"
ANSWER = ROOT / "eval_answerability_blind_v2.json"
OVERLAP_JSON = ROOT / "evaluation_runs" / "blind-v2-overlap-report.json"
OVERLAP_TXT = ROOT / "evaluation_runs" / "blind-v2-overlap-report.txt"
AUTHOR_REPORT = ROOT / "evaluation_runs" / "blind-v2-author-report.md"
MANIFEST = ROOT / "evaluation_runs" / "blind-v2-freeze-manifest.json"

PRODUCT_COMMIT = "df3b4c9f3143c803a991ac0afc6f734b74a25139"


def digest(path: Path) -> dict:
    raw = path.read_bytes()
    return {
        "file": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def git_head() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=15, cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def main() -> int:
    route_cases = json.loads(ROUTE.read_text(encoding="utf-8"))
    answer_cases = json.loads(ANSWER.read_text(encoding="utf-8"))
    overlap = json.loads(OVERLAP_JSON.read_text(encoding="utf-8"))

    gates = overlap["gates"]
    if not all(gate["passed"] for gate in gates):
        print("REFUSING to write manifest: overlap gates have not all passed")
        return 1

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product_commit": PRODUCT_COMMIT,
        "git_head_at_freeze": git_head(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "datasets": {
            "route": {
                **digest(ROUTE),
                "cases": len(route_cases),
                "per_route": dict(
                    sorted(Counter(c["expected_route"] for c in route_cases).items())
                ),
                "difficulty": dict(
                    sorted(Counter(c["difficulty"] for c in route_cases).items())
                ),
                "requires_freshness_true": sum(
                    1 for c in route_cases if c["expected_requires_freshness"]
                ),
                "requires_exact_citation_true": sum(
                    1 for c in route_cases if c["expected_requires_exact_citation"]
                ),
            },
            "answerability": {
                **digest(ANSWER),
                "cases": len(answer_cases),
                "expected_behavior": dict(
                    sorted(Counter(c["expected_behavior"] for c in answer_cases).items())
                ),
                "answer_categories": dict(
                    sorted(
                        Counter(
                            c["category"]
                            for c in answer_cases
                            if c["expected_behavior"] == "answer"
                        ).items()
                    )
                ),
                "difficulty": dict(
                    sorted(Counter(c["difficulty"] for c in answer_cases).items())
                ),
            },
        },
        "author_report": str(AUTHOR_REPORT.relative_to(ROOT)).replace("\\", "/"),
        "overlap": {
            "report_json": str(OVERLAP_JSON.relative_to(ROOT)).replace("\\", "/"),
            "report_txt": str(OVERLAP_TXT.relative_to(ROOT)).replace("\\", "/"),
            "reference_sets": overlap["comparisons"][0]["reference_sets"],
            "reference_questions": overlap["comparisons"][0].get("reference_questions"),
            "thresholds": overlap["thresholds"],
            "max_similarity": {
                comparison["name"]: comparison["max_similarity"]
                for comparison in overlap["comparisons"]
            },
            "gates": gates,
            "all_gates_passed": True,
        },
        "product_scoring_started": False,
        "note": (
            "Frozen before any product scoring. From this point the questions, "
            "labels, regexes, and metric definitions of Blind Holdout V2 are "
            "immutable; any later score must cite these SHA-256 values."
        ),
    }

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {MANIFEST}")
    print(f"  route        : {manifest['datasets']['route']['sha256']}")
    print(f"  answerability: {manifest['datasets']['answerability']['sha256']}")
    print(f"  product_scoring_started: {manifest['product_scoring_started']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
