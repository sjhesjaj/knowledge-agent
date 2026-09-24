"""Undo each rework-7 fix in a scratch copy and confirm its test goes red.

Rounds 3-6 keep their own probes. This one covers the two changes here: a
refutation must end where a refutation ends (A), and pure refusal is granted by
accounting for the whole text rather than by failing to find an objection (B).

Reverts only mean something if they produce *assertion* failures, so failures
and errors are counted separately and the raw last line is printed on a miss -
an import blowing up also exits non-zero and would prove nothing.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_7.py
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

# A: remove the completeness check, leaving the rework-6 bare word list.
#
# Kept current with the code it reverts. Rework 7 expressed this check as a
# lookahead inside `_REFUTATION_PATTERN`; rework 8 moved it into
# `_refutation_ends_cleanly()` so the boundary could be judged on the full text
# rather than on the truncated window. Disabling that function is the same
# revert - the tail of a match stops being checked and prefixes match again.
BOUNDARY = """    while index < len(text) and text[index] in _REFUTATION_PARTICLES:"""
BOUNDARY_REVERTED = """    return True  # reverted: no tail check at all, prefixes match
    while index < len(text) and text[index] in _REFUTATION_PARTICLES:"""

# B: go back to the rework-6 reverse judgement.
POSITIVE = """    remaining = text.strip()
    if not remaining:
        return False
    position = 0
    while position < len(remaining):
        match = _REFUSAL_CLAUSE.match(remaining, position)
        if not match or match.end() == position:
            return False"""
POSITIVE_REVERTED = """    segments = [p for p in _ASSERTION_SPLIT.split(text) if p.strip()]
    if not segments:
        return False
    for segment in segments:  # reverted: reverse judgement, rework-6 shape
        hits = [segment.find(m) for m in REFUSAL_MARKERS]
        hits = [i for i in hits if i >= 0]
        if not hits:
            return False
        if any(q.start < min(hits) for q in quantity_mentions(segment)):
            return False
    return True
    remaining = text.strip()
    position = 0
    while position < len(remaining):
        match = _REFUSAL_CLAUSE.match(remaining, position)
        if not match or match.end() == position:
            return False"""

REVERTS = (
    (
        "A refutation matches a prefix again (no tail boundary)",
        "rag.py", BOUNDARY, BOUNDARY_REVERTED,
        "tests.test_predicate_completeness.RefutationCompletenessTests",
    ),
    (
        "A, checked through the real interfaces too",
        "rag.py", BOUNDARY, BOUNDARY_REVERTED,
        "tests.test_predicate_completeness.PredicateCompletenessApiTests",
    ),
    (
        "B pure refusal goes back to reverse judgement",
        "rag.py", POSITIVE, POSITIVE_REVERTED,
        "tests.test_predicate_completeness.PureRefusalCompletenessTests",
    ),
    (
        "B, checked through the real interfaces too",
        "rag.py", POSITIVE, POSITIVE_REVERTED,
        "tests.test_predicate_completeness.PredicateCompletenessApiTests",
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
            print(f"  [SKIP] needle not found in {relative}: {needle.splitlines()[0][:60]}")
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
        good = completed.returncode != 0 and failures > 0 and errors == 0
        print(f"  [{'OK  ' if good else 'FAIL'}] {label}")
        print(f"         {target} -> exit {completed.returncode}, "
              f"failures={failures}, errors={errors}")
        if not good:
            print("         " + (completed.stderr.strip().splitlines() or ["<no output>"])[-1])
        return good


if __name__ == "__main__":
    print("Reverting each rework-7 fix in a scratch copy; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
