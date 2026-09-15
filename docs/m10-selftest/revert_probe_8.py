"""Undo each rework-8 fix in a scratch copy and confirm its test goes red.

Rounds 3-7 keep their own probes. This one covers the two changes here:
the refutation's end is judged on the full text rather than on the truncated
window (A), and a refusal topic must be positively recognised as a noun phrase
rather than accepted as any short unobjectionable span (B).

Reverts only mean something if they produce *assertion* failures, so failures
and errors are counted separately and the raw last line is printed on a miss -
an import blowing up also exits non-zero and would prove nothing.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_8.py
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

# A1: judge the boundary on the truncated window again (the rework-7 shape).
BOUNDARY_FULLTEXT = """        if _governs(trailing[: match.start()]) and _refutation_ends_cleanly(
            text, end + match.end()
        ):"""
BOUNDARY_WINDOW = """        if _governs(trailing[: match.start()]) and _refutation_ends_cleanly(
            trailing, match.end()
        ):  # reverted: window end mistaken for a sentence end"""

# A2: accept bare whitespace as a predicate ending again.
#
# Kept current with the code it reverts: the final rework added bracket
# handling after this point, so the needle moved with it. The revert is
# unchanged - whitespace on its own becomes an ending again.
WHITESPACE_STRICT = """    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return True"""
WHITESPACE_LOOSE = """    if index < len(text) and text[index].isspace():
        return True  # reverted: bare whitespace ends a predicate
    if index >= len(text):
        return True"""

# B: go back to "any short span with no quantity and no listed breaker".
TOPIC_POSITIVE = """    r"(?P<topic>" + _TOPIC_PHRASE + r")"
    r"(?<![不未非无是])"
    r"(?:" + "|".join(REFUSAL_MARKERS) + r")"
    r"(?P<about>" + _TOPIC_PHRASE + r")\""""
TOPIC_WILDCARD = """    r"(?P<topic>[^，,。；;：:！？!?\\n]{0,12}?)"
    r"(?:" + "|".join(REFUSAL_MARKERS) + r")"
    r"(?P<about>[^，,。；;：:！？!?\\n]{0,16})\"  # reverted: any short span"""

REVERTS = (
    (
        "A1 boundary judged on the truncated window again",
        "rag.py", BOUNDARY_FULLTEXT, BOUNDARY_WINDOW,
        "tests.test_unconsumed_meaning.RefutationBoundaryTests",
    ),
    (
        "A1, checked through the real interfaces too",
        "rag.py", BOUNDARY_FULLTEXT, BOUNDARY_WINDOW,
        "tests.test_unconsumed_meaning.UnconsumedMeaningApiTests",
    ),
    (
        "A2 bare whitespace ends a predicate again",
        "rag.py", WHITESPACE_STRICT, WHITESPACE_LOOSE,
        "tests.test_unconsumed_meaning.RefutationBoundaryTests",
    ),
    (
        "B topic goes back to any short unobjectionable span",
        "rag.py", TOPIC_POSITIVE, TOPIC_WILDCARD,
        "tests.test_unconsumed_meaning.RefusalTopicRecognitionTests",
    ),
    (
        "B, checked through the real interfaces too",
        "rag.py", TOPIC_POSITIVE, TOPIC_WILDCARD,
        "tests.test_unconsumed_meaning.UnconsumedMeaningApiTests",
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
    print("Reverting each rework-8 fix in a scratch copy; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
