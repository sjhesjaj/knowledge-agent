"""Undo each rework-4 fix in a scratch copy and confirm its test goes red.

Round 3's probe (`revert_probe_3.py`) still covers that round. This one covers
the two R5 changes, which are independent paths and therefore get independent
reverts: tightening the exemption cannot fix the refusal short-circuit, and
fixing the short-circuit cannot fix the exemption scope.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_4.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# (label, file, needle, replacement, test to run)
REVERTS = (
    (
        "R5-a scope: any cue in the prefix governs again",
        "rag.py",
        '    residue = "".join(span.split())',
        '    return True  # reverted: no relation between cue and numeral\n'
        '    residue = "".join(span.split())',
        "tests.test_quantity_exemptions.ExemptionScopeTests",
    ),
    (
        "R5-a endorsement: ignore an endorsement following the quotation",
        "rag.py",
        "    if _ENDORSEMENT_PATTERN.search(text[end:following]):",
        "    if False:  # reverted: a quoted number stays excused after endorsement",
        "tests.test_quantity_exemptions.ExemptionScopeTests",
    ),
    (
        "R5-b short-circuit: a refusal phrase skips the quantity check again",
        "rag.py",
        "    refusal = is_refusal(answer_text)",
        '    if is_refusal(answer_text):  # reverted: early return\n'
        '        return True, "refusal"\n'
        "    refusal = is_refusal(answer_text)",
        "tests.test_quantity_exemptions.RefusalDoesNotSkipFactsTests",
    ),
    (
        "R5-b short-circuit, checked through the real interfaces too",
        "rag.py",
        "    refusal = is_refusal(answer_text)",
        '    if is_refusal(answer_text):  # reverted: early return\n'
        '        return True, "refusal"\n'
        "    refusal = is_refusal(answer_text)",
        "tests.test_quantity_exemptions.QuantityExemptionApiTests",
    ),
)


def run(label, relative, needle, replacement, target):
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "tree"
        shutil.copytree(
            REPO, tree,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "node_modules", "data", ".cache",
                "evaluation_runs", "__pycache__", "*.pyc",
            ),
        )
        path = tree / relative
        source = path.read_text(encoding="utf-8")
        if needle not in source:
            print(f"  [SKIP] needle not found in {relative}: {needle[:60]}")
            return False
        path.write_text(source.replace(needle, replacement, 1), encoding="utf-8")

        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", target],
            cwd=tree, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        went_red = completed.returncode != 0
        status = "OK  " if went_red else "FAIL"
        print(f"  [{status}] {label}")
        print(f"         {target} -> exit {completed.returncode}"
              f" ({'fails as expected' if went_red else 'STILL PASSES - test does not pin the fix'})")
        return went_red


if __name__ == "__main__":
    print("Reverting each rework-4 fix in a scratch copy; every test must go red.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced a failing test.")
    sys.exit(0 if all(outcomes) else 1)
