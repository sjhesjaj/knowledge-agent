"""Undo each rework-5 fix in a scratch copy and confirm its test goes red.

Rounds 3 and 4 keep their own probes. This one covers the two R5 changes:
attribution is no longer an exemption (A), and only a pure refusal is excused
from citing (B). Each revert prints the failure counts so a non-zero exit
cannot be mistaken for a syntax error or an import failure.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_5.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# (label, file, needle, replacement, test to run)
REVERTS = (
    (
        "A1 attribution is an exemption cue again",
        "rag.py",
        "    for position in _cue_positions(prefix, _DENIAL_CUES):",
        "    for position in _cue_positions(prefix, _DENIAL_CUES + _ATTRIBUTION_MODIFIERS):",
        "tests.test_assertion_boundaries.AssertionBoundaryTests",
    ),
    (
        "A2 a negation that is itself negated still exempts",
        "rag.py",
        "        if _governs(prefix[position[1] :]) and not _denies(prefix, position[0]):",
        "        if _governs(prefix[position[1] :]):  # reverted: no double-negation check",
        "tests.test_assertion_boundaries.AssertionBoundaryTests",
    ),
    (
        "B any refusal phrase excuses the citation requirement again",
        "rag.py",
        "    if results and not is_pure_refusal(answer_text) and not citation_indices(answer_text):",
        "    if results and not refusal and not citation_indices(answer_text):",
        "tests.test_assertion_boundaries.PureRefusalTests",
    ),
    (
        "B, checked through the real interfaces too",
        "rag.py",
        "    if results and not is_pure_refusal(answer_text) and not citation_indices(answer_text):",
        "    if results and not refusal and not citation_indices(answer_text):",
        "tests.test_assertion_boundaries.AssertionBoundaryApiTests",
    ),
    (
        "A, checked through the real interfaces too",
        "rag.py",
        "    for position in _cue_positions(prefix, _DENIAL_CUES):",
        "    for position in _cue_positions(prefix, _DENIAL_CUES + _ATTRIBUTION_MODIFIERS):",
        "tests.test_assertion_boundaries.AssertionBoundaryApiTests",
    ),
)

COUNTS = re.compile(r"(failures|errors)=(\d+)")


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
        tally = dict((key, int(value)) for key, value in COUNTS.findall(completed.stderr))
        failures, errors = tally.get("failures", 0), tally.get("errors", 0)
        # A revert has to produce *assertion failures*. An import blowing up
        # would also exit non-zero and would prove nothing about the test.
        good = completed.returncode != 0 and failures > 0 and errors == 0
        print(f"  [{'OK  ' if good else 'FAIL'}] {label}")
        print(f"         {target} -> exit {completed.returncode}, "
              f"failures={failures}, errors={errors}")
        if not good:
            print("         " + (completed.stderr.strip().splitlines() or ["<no output>"])[-1])
        return good


if __name__ == "__main__":
    print("Reverting each rework-5 fix in a scratch copy; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
