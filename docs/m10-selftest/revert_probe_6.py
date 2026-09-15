"""Undo each rework-6 fix in a scratch copy and confirm its test goes red.

Rounds 3-5 keep their own probes. This one covers the two changes here: a
trailing denial must refute the numeral's claim (A), and pure refusal must
cover the whole meaning rather than each fragment containing a refusal word (B).

Reverts are only meaningful if they produce *assertion* failures, so failures
and errors are counted separately and the raw last line is printed on a miss -
an import blowing up also exits non-zero and would prove nothing.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_6.py
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

# The rework-5 shape of the trailing branch: adjacency accepted as aboutness.
# Kept current with the code it reverts: rework 8 added the full-text boundary
# check to this branch, so the needle moved with it. The revert itself is
# unchanged - it still swaps the refutation-based branch for rework 5's
# "any adjacent denial cue" branch.
ROUND_5_TRAILING = """    for match in _REFUTATION_PATTERN.finditer(trailing):
        if _governs(trailing[: match.start()]) and _refutation_ends_cleanly(
            text, end + match.end()
        ):
            return True"""
ROUND_5_TRAILING_REVERTED = """    for position in _cue_positions(trailing, _DENIAL_CUES):
        if _governs(trailing[: position[0]]) and not _denies(trailing, position[0]):
            return True"""

REVERTS = (
    (
        "A trailing denial accepts adjacency again (the rework-5 regression)",
        "rag.py", ROUND_5_TRAILING, ROUND_5_TRAILING_REVERTED,
        "tests.test_predicate_scopes.TrailingDenialObjectTests",
    ),
    (
        "A, checked through the real interfaces too",
        "rag.py", ROUND_5_TRAILING, ROUND_5_TRAILING_REVERTED,
        "tests.test_predicate_scopes.PredicateScopeApiTests",
    ),
    # B (pure refusal) used to be reverted here by swapping this round's
    # segment loop for the full-stop/substring version. Rework 7 replaced
    # `is_pure_refusal()` with a positive judgement - the text must be
    # accounted for by refusal clauses - so that needle no longer exists and
    # the entry would only report SKIP.
    #
    # The coverage did not disappear, it moved and got stronger:
    # `revert_probe_7.py` reverts the positive judgement back to *this round's*
    # reverse-judgement shape, which is exactly the code this entry protected,
    # and checks that both `PureRefusalScopeTests` (this round's tests, still
    # present and still passing) and the newer completeness tests go red.
    # Keeping a dead entry here would report a false 2/4.
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
    print("Reverting each rework-6 fix in a scratch copy; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
