"""Undo each rework-9 fix in a scratch copy and confirm its test goes red.

Covers the ranking merge (F1) and the two route mechanisms (F3). Reverts only
count when they produce *assertion* failures, so failures and errors are
counted separately - an import blowing up also exits non-zero and proves
nothing.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_9.py
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

# F1: score every field separately again, so one term can stack 4 + 2 + 1.
MERGED_RANK = """            rank_total = _merged_rank(
                query_terms, page_fields + ((claim_tokens, CLAIM_WEIGHT),), specificity
            )"""
STACKED_RANK = """            rank_total = (  # reverted: fields stack, one term counts up to 7x
                _weighted_match(query_terms, title_tokens, specificity) * TITLE_WEIGHT
                + _weighted_match(query_terms, alias_tokens, specificity) * ALIAS_WEIGHT
                + _weighted_match(query_terms, summary_tokens, specificity) * SUMMARY_WEIGHT
                + _weighted_match(query_terms, claim_tokens, specificity) * CLAIM_WEIGHT
            )"""

# F3a: drop the deferral and self-service dismissals.
DISMISSALS = """    r"|(?:先|就|都|也|暂)(?:不用|不必|不需要)"
    r"|自己(?:翻|看|查|找|读|搜|来)|我来(?:翻|看|查|找|读)\""""
DISMISSALS_REVERTED = '    r""  # reverted: deferral and self-service go unnoticed"'

# F3b: let `别` match as a bare substring again, so `分别` reads as a refusal.
GUARDED_BIE = r'_BARE_NEGATION_BIE = r"(?:(?<![一-鿿])别|(?<=[先就可请你您我也都还万千])别)"'
BARE_BIE = r'_BARE_NEGATION_BIE = r"别"  # reverted: matches inside 分别 / 级别'

# R1: fold the claim back into the page maximum, so claims on one page tie.
TIEBREAK = """                    (rank_total, claim_rank, total, page.page_id, claim.claim_id, page, claim)"""
TIEBREAK_REVERTED = """                    (rank_total, 0.0, total, page.page_id, claim.claim_id, page, claim)"""

REVERTS = (
    (
        "R1 claim tie-break removed, claims on one page tie again",
        "orchestration/wiki_adapter.py", TIEBREAK, TIEBREAK_REVERTED,
        "tests.test_final_rework.RankingTests",
    ),
    (
        "F1 ranking stacks the fields again (one term worth up to 7x)",
        "orchestration/wiki_adapter.py", MERGED_RANK, STACKED_RANK,
        "tests.test_final_rework.RankingTests",
    ),
    (
        "F3a deferral and self-service dismissals removed",
        "orchestration/planner.py", DISMISSALS, DISMISSALS_REVERTED,
        "tests.test_declined_channels.SelfServiceDeclineTests",
    ),
    (
        "F3a, the deferral half",
        "orchestration/planner.py", DISMISSALS, DISMISSALS_REVERTED,
        "tests.test_declined_channels.DeferredDeclineTests",
    ),
    (
        "F3b `别` matches inside another word again",
        "orchestration/planner.py", GUARDED_BIE, BARE_BIE,
        "tests.test_declined_channels.ListedThreeChannelTests",
    ),
    (
        "F3b, checked on the word-internal controls",
        "orchestration/planner.py", GUARDED_BIE, BARE_BIE,
        "tests.test_declined_channels.BareNegationBieTests",
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
    print("Reverting each rework-9 fix in a scratch copy; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
