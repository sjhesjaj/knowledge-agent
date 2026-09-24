"""python -m diagnostic_eval --eval eval/<label>.json [--labels overlay.json] [--output path]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import ROOT, diagnose_eval, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m diagnostic_eval")
    parser.add_argument("--eval", required=True, help="eval/<label>.json written by run_stage0_eval.py")
    parser.add_argument("--labels", help="hand-written label overlay (optional)")
    parser.add_argument("--traces", help="override the trace store recorded in the eval file")
    parser.add_argument("--output", help="default: eval/diagnostics/<label>.diagnostic.json")
    args = parser.parse_args(argv)

    report = diagnose_eval(args.eval, args.labels, trace_db=args.traces)
    output = Path(args.output) if args.output else ROOT / "eval" / "diagnostics" / (
        Path(args.eval).stem + ".diagnostic.json")
    json_path, md_path = write_report(report, output)
    a = report["aggregate"]
    print(f"case runs {a['case_runs']}: passed {a['official_passed']}, failed {a['official_failed']}")
    print("primary:", {k: v for k, v in a["primary_errors"].items() if v} or "none")
    print("unattributed:", {k: v for k, v in a["unattributed"].items() if v} or "none")
    print("secondary:", {k: v for k, v in a["secondary_effects"].items() if v} or "none")
    print("latent:", {k: v for k, v in a["latent_issues"].items() if v} or "none")
    print(f"Wrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    sys.exit(main())
