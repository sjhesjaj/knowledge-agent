"""Undo each rework-3 fix in a scratch copy and confirm its test goes red.

A passing test proves nothing on its own - it may be passing for a reason that
has nothing to do with the change. So for every fix this round, the fix is
reverted in a throwaway copy of the tree and the test that is supposed to cover
it is run again. If it still passes, the test was not actually pinning the fix
and this script fails.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_3.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# (label, file, needle -> replacement, test to run)
REVERTS = (
    (
        "A1 complete numeral: narrow the character class back to `千`",
        "rag.py",
        r'_NUMERAL_BODY = r"[\d.零〇一二两三四五六七八九十百千万亿兆点]+"',
        r'_NUMERAL_BODY = r"[\d.零〇一二两三四五六七八九十百千]+"',
        "tests.test_quantity_scope.CompleteNumeralTests",
    ),
    # A2 (modifier scope) used to be reverted here by rewriting
    # `before = segment[:index]`. Rework 4 replaced `_is_quoted_or_denied()`
    # outright - a prefix keyword was never proof that the keyword modified
    # *this* number - so that line no longer exists and the revert would only
    # report SKIP. The behaviour it protected is pinned by `revert_probe_4.py`,
    # which reverts `_governs()` and re-runs the same kind of test. Keeping a
    # dead entry here would report a false 5/6.
    (
        "B1 cleaning order: stop looking for an envelope after removing the shell",
        "rag.py",
        "    for candidate in (content, strip_think_tags(content)):",
        "    for candidate in (content,):  # reverted: single pass",
        "tests.test_delivery_forms.ExtractAnswerTextTests",
    ),
    (
        "B2 whitespace: stop normalising at the shared entry",
        "rag.py",
        "    result = first_answer.strip()",
        "    result = first_answer  # reverted: padding survives into the deltas",
        "tests.test_delivery_forms.DeliveryFormApiTests",
    ),
    (
        "C trailing dismissal: stop checking for a refusal after the marker",
        "orchestration/planner.py",
        "    return bool(_POST_DISMISSAL_PATTERN.search(trailing))",
        "    return False  # reverted: post-positioned refusals invisible again",
        "tests.test_route_variants.TrailingDismissalTests",
    ),
    (
        "C social vocabulary: drop thanks and wishes from the social table",
        "orchestration/planner.py",
        ") + SOCIAL_CLOSING_MARKERS + SOCIAL_THANKS_MARKERS + SOCIAL_WISH_MARKERS",
        ") + SOCIAL_CLOSING_MARKERS",
        "tests.test_route_variants.ThanksAndWishTests",
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
    print("Reverting each rework-3 fix in a scratch copy; every test must go red.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced a failing test.")
    sys.exit(0 if all(outcomes) else 1)
