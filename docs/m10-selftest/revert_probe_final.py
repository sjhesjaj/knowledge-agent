"""Undo each of the two binding mechanisms and confirm its tests go red.

These target the *mechanisms*, not the public wording:

A. the premise check has to receive the question and the matching occurrence in
   it - reverting it to "look only at words near the answer's occurrence" is the
   shape that let `我的年假是99天吗` come back as `你的年假是99天`;
B. identifier scanning has to keep checking explicit unknown families and
   compare the whole identifier - reverting it to "known families only" is the
   shape that let `SPU-C300` through.

Reverts only mean something if they produce *assertion* failures, so failures
and errors are counted separately.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_final.py
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

# A: drop the question side entirely, leaving the answer-window heuristic.
# Re-anchored in rework 11: the check kept the question side but was rewritten
# around the governing head, so the old needle no longer matched anything and
# this revert silently skipped.
BINDING_PRESENT = """    answer_head: str | None = None
    if role is None:
        answer_head = _governing_head(
            answer_text, mention.start, mention.end, segment_start
        )
        if answer_head is None:
            return False

    for other in question_mentions:
        if other.key != mention.key:
            continue
        if not _question_occurrence_is_given(question, other):
            continue

        if role == "value":
            # 答案把这个数**等同于**左边那个属性（`公司年假为99天`）。要算复述，
            # 问句必须也把同一个数等同于同一个属性。问句只是把它当成某个动作的量
            # （`工作了99天`）时，答案就是把它挪到了别的东西头上——那是在陈述制度。
            question_floor = max(0, other.start - _ROLE_WINDOW_BEFORE)
            question_role, question_span = _copular_role(
                question, other.start, other.end, question_floor
            )
            if question_role != "value":
                continue
            answer_core = _attribute_core(span)
            question_core = _attribute_core(question_span)
            if not answer_core or not question_core:
                continue
            if answer_core in question_core or question_core in answer_core:
                return True
            continue

        asked = _role_context(question, other.start, other.end, 0)
        # 答案的**中心词**必须出现在问句那一次的相邻实词里。共有一个状语或领属词
        # （`公司`）不算——它证明不了两边在说同一件事。
        if answer_head in _attribute_words(asked):
            return True
    return False"""
BINDING_REVERTED = """    # reverted: no question side, personal words alone decide
    return any(word in context for word in ("我的", "你的", "这笔", "单笔"))"""

# B: scan only the known families, as the previous shape did.
SCAN_PRESENT = """    found = set(_EXPLICIT_IDENTIFIER.findall(text))
    if family is not None:
        found |= set(family.findall(text))
    return found"""
SCAN_REVERTED = """    # reverted: known families only, explicit unknown ones ignored
    return set(family.findall(text)) if family is not None else set()"""

REVERTS = (
    (
        "A premise binding loses the question side",
        "rag.py", BINDING_PRESENT, BINDING_REVERTED,
        "tests.test_final_rework.PremiseRoleBindingTests",
    ),
    (
        "A, through both real interfaces",
        "rag.py", BINDING_PRESENT, BINDING_REVERTED,
        "tests.test_final_rework.BindingApiTests",
    ),
    (
        "B identifier scan drops unknown explicit families",
        "rag.py", SCAN_PRESENT, SCAN_REVERTED,
        "tests.test_final_rework.IdentifierSpellingTests",
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
    print("Reverting each binding mechanism; every test must fail on assertions.\n")
    outcomes = [run(*case) for case in REVERTS]
    print(f"\n{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
