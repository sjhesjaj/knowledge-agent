"""Undo each rework-11 fix in a scratch copy and confirm its test goes red.

R1 covers premise binding; R2 covers the sub-query cap and the evidence
budget, one revert per mechanism.

Every revert here restores a *specific* way of proving "both sides are talking
about the same thing" that turned out not to prove it. Reverts only count when
they produce *assertion* failures, so failures and errors are counted separately
- an import blowing up also exits non-zero and proves nothing.

Nothing here touches the real worktree. Run from the repository root:

    python docs/m10-selftest/revert_probe_11.py
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

# R1a: ignore the copular value position, so `公司年假为99天` is judged by the
# same head rule as a verbal predicate - and `公司` becomes that head.
VALUE_POSITION = """    answer_head: str | None = None
    if role is None:
        answer_head = _governing_head(
            answer_text, mention.start, mention.end, segment_start
        )
        if answer_head is None:
            return False"""
VALUE_POSITION_REVERTED = """    answer_head = _governing_head(  # reverted: the value position is ignored
        answer_text, mention.start, mention.end, segment_start
    )
    if answer_head is None:
        return False
    role = None"""

# R1b: go back to comparing whole word sets, so one shared adverbial (`公司`)
# grants the exemption again.
SINGLE_HEAD = """        if answer_head in _attribute_words(asked):
            return True"""
WORD_INTERSECTION = """        # reverted: any shared content word grants the exemption
        if _attribute_words(
            _role_context(answer_text, mention.start, mention.end, segment_start)
        ) & _attribute_words(asked):
            return True"""

# R1c: stop noticing the definition position, so `这笔2500元是报销审批分界线`
# picks up `报销` from its right-hand side and passes as a restatement.
DEFINITION_POSITION = """    after = text[end:].lstrip()
    for copula in _COPULAS:
        if after.startswith(copula):
            return "defined", after[len(copula):]"""
DEFINITION_POSITION_REVERTED = """    # reverted: the definition position goes unnoticed"""

# R1d: keep the person/instance determiner in the attribute name, so
# `你的报销金额` and `我的报销金额` no longer line up.
DETERMINER_STRIPPED = """    return _ROLE_DETERMINER_RE.sub("", span).replace("的", "").strip()"""
DETERMINER_KEPT = """    return span  # reverted: `我的` / `你的` stay part of the attribute name"""

# R1e: a verification cue in the next clause always means "asking about something
# else", so `我的年假是99天，是真的吗？` hands 99 over as a given premise.
BARE_BACK_REFERENCE = """        for _later_start, later in clauses[index + 1 :]:
            if not later.strip():
                continue
            if any(cue in later for cue in _VERIFICATION_CUES) and _is_bare_verification(
                later
            ):
                return False
            break
        return True"""
BARE_BACK_REFERENCE_REVERTED = """        return True  # reverted: a bare trailing check no longer binds back"""

SUITE = "tests.test_premise_binding"

REVERTS = (
    (
        "R1a copular value position ignored (`公司` becomes the head)",
        "rag.py", VALUE_POSITION, VALUE_POSITION_REVERTED,
        f"{SUITE}.PremiseAttributeBindingTests.test_tenure_number_reused_as_leave_value",
    ),
    (
        "R1b whole word sets again (one shared adverbial exempts)",
        "rag.py", SINGLE_HEAD, WORD_INTERSECTION,
        f"{SUITE}.PremiseAttributeBindingTests.test_tenure_number_reused_without_copula",
    ),
    (
        "R1c definition position unnoticed (`N是X` reads as a restatement)",
        "rag.py", DEFINITION_POSITION, DEFINITION_POSITION_REVERTED,
        f"{SUITE}.PremiseAttributeBindingTests.test_expense_number_defined_as_a_threshold",
    ),
    (
        "R1d determiner kept in the attribute name (a false refusal)",
        "rag.py", DETERMINER_STRIPPED, DETERMINER_KEPT,
        f"{SUITE}.PremiseAttributeBindingTests.test_same_attribute_restatement_is_delivered",
    ),
    (
        "R1e bare trailing check no longer binds back to the number",
        "rag.py", BARE_BACK_REFERENCE, BARE_BACK_REFERENCE_REVERTED,
        f"{SUITE}.PremiseAttributeBindingTests.test_bare_trailing_verification_binds_back",
    ),
    (
        "R1e, checked on the clause-level predicate as well",
        "rag.py", BARE_BACK_REFERENCE, BARE_BACK_REFERENCE_REVERTED,
        f"{SUITE}.BareVerificationTests.test_given_premise_lost_to_a_bare_back_reference",
    ),
)

# ---- R2: the sub-query cap and the evidence budget -------------------------

# R2a: raise the full chain's ceiling to the sub-query count again.
# Closeout changed the call's shape, not the ceiling being reverted.
FULL_CHAIN_BUDGET = """        results = fit_to_budget(
            selected, len(selected), relevance, top_k,
            coverage=getattr(selected, "coverage", None),
        )"""
FULL_CHAIN_BUDGET_REVERTED = """        results = selected[:max(top_k, len(sub_questions))]  # reverted"""

# R2b: let the confident BM25 multi path return one chunk per clause, uncut.
FAST_PATH_BUDGET = """            kept = fit_to_budget(selected, len(selected), relevance, top_k, coverage=coverage)"""
FAST_PATH_BUDGET_REVERTED = """            kept = selected  # reverted: never cut"""

# R2c: cut by position - the shape the review warned against.
RELEVANCE_CUT = """    order = sorted(
        range(len(covering)),
        key=lambda i: (-relevance.get(covering[i][0].index, 0.0), i),
    )
    return [covering[i] for i in sorted(order[:top_k])]"""
POSITION_CUT = """    return covering[:top_k]  # reverted: the last clauses go first"""

# R2d: no cap at all.
MERGE_LOOP = """    while len(merged) > limit:"""
MERGE_LOOP_REVERTED = """    while False:  # reverted: as many sub-queries as clauses"""

# R2e: cap by dropping the extra clauses instead of merging them.
MERGE_BODY = """    merged = list(parts)
    while len(merged) > limit:"""
DROP_BODY = """    return list(parts)[:limit]  # reverted: the fourth and fifth questions vanish
    merged = list(parts)
    while len(merged) > limit:"""

# R2f: never use a free slot for a clause the merge left uncovered.
COVER_CALL = """        if len(clauses) > len(sub_questions):
            results = cover_merged_clauses(
                results, clauses, candidates, chunks, top_k,
                coverage=getattr(selected, "coverage", None),
            )"""
COVER_CALL_REVERTED = """        pass  # reverted: merged-away clauses get nothing"""

# R2g: fill without the confidence gate - any recalled best match will do.
COVER_GATE = """        if not bm25_confident(ranked):
            continue"""
COVER_GATE_REVERTED = """        if not ranked:  # reverted: no confidence gate
            continue"""

# R2h: pad every multi-clause question, merged or not.
COVER_ONLY_AFTER_MERGE = """        if len(clauses) > len(sub_questions):"""
COVER_ALWAYS = """        if True:  # reverted: pad even when nothing was merged"""

# R2i: let a repeated ID from the reranker take two slots.
RERANK_DEDUP = """        ordered: list[Chunk] = []
        for index in ranking:
            if index in by_index and index not in {item.index for item in ordered}:
                ordered.append(by_index[index])"""
RERANK_DUPLICATES = """        ordered = [by_index[index] for index in ranking if index in by_index]  # reverted"""

BUDGET = "tests.test_evidence_budget"

REVERTS = REVERTS + (
    (
        "R2a full chain ceiling raised to the sub-query count again",
        "rag.py", FULL_CHAIN_BUDGET, FULL_CHAIN_BUDGET_REVERTED,
        "tests.test_full_budget_coverage.FullBudgetCoverageTests.test_unmerged_query_still_needs_the_first_budget_limit",
    ),
    (
        "R2b bm25_multi_fast returns one chunk per clause, uncut",
        "rag.py", FAST_PATH_BUDGET, FAST_PATH_BUDGET_REVERTED,
        f"{BUDGET}.ConfidentMultiPathTests.test_top_k_is_honoured",
    ),
    (
        "R2c cut by position (`[:top_k]`), unit level",
        "rag.py", RELEVANCE_CUT, POSITION_CUT,
        f"{BUDGET}.FitToBudgetTests.test_the_least_relevant_clause_goes_not_the_last",
    ),
    (
        "R2c, through bm25_multi_fast",
        # Fast path now uses the coverage branch; restore positional truncation
        # at that live call, instead of mutating an unreachable fallback branch.
        "rag.py", FAST_PATH_BUDGET, "            kept = selected[:top_k]  # reverted: positional cut",
        f"{BUDGET}.ConfidentMultiPathTests.test_the_weakest_clause_is_dropped_not_the_last",
    ),
    (
        "R2c, through the full chain",
        "rag.py", RELEVANCE_CUT, POSITION_CUT,
        f"{BUDGET}.FullChainBudgetTests.test_the_cut_follows_relevance_not_clause_order",
    ),
    (
        "R2d no sub-query cap",
        "rag.py", MERGE_LOOP, MERGE_LOOP_REVERTED,
        f"{BUDGET}.SubQueryCapTests.test_reference_question_is_capped_at_three",
    ),
    (
        "R2e cap by dropping clauses instead of merging",
        "rag.py", MERGE_BODY, DROP_BODY,
        f"{BUDGET}.SubQueryCapTests.test_no_clause_is_lost_when_merging",
    ),
    (
        "R2f merged-away clauses never get a free slot",
        "rag.py", COVER_CALL, COVER_CALL_REVERTED,
        f"{BUDGET}.FullChainBudgetTests.test_a_free_slot_covers_a_merged_away_clause",
    ),
    (
        "R2g fill without the confidence gate (vague clause)",
        "rag.py", COVER_GATE, COVER_GATE_REVERTED,
        f"{BUDGET}.CoverMergedClausesTests.test_a_vague_clause_is_not_padded_with_a_guess",
    ),
    (
        "R2g, wrong-topic best match",
        "rag.py", COVER_GATE, COVER_GATE_REVERTED,
        f"{BUDGET}.CoverMergedClausesTests.test_a_wrong_topic_best_match_is_not_padded_in",
    ),
    (
        "R2h pad even when nothing was merged",
        "rag.py", COVER_ONLY_AFTER_MERGE, COVER_ALWAYS,
        f"{BUDGET}.FullChainBudgetTests.test_an_unmerged_question_is_not_padded",
    ),
    (
        "R2i reranker duplicates take two slots",
        "rag.py", RERANK_DEDUP, RERANK_DUPLICATES,
        f"{BUDGET}.RerankDeduplicationTests.test_a_repeated_id_does_not_take_two_slots",
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
        print(f"         {target.rsplit('.', 1)[-1]} -> exit {completed.returncode}, "
              f"failures={failures}, errors={errors}")
        if not good:
            print("         " + (completed.stderr.strip().splitlines() or ["<no output>"])[-1])
        return good


if __name__ == "__main__":
    print("Reverting each rework-11 fix in a scratch copy; every test must fail on assertions.")
    print()
    outcomes = [run(*case) for case in REVERTS]
    print()
    print(f"{sum(outcomes)}/{len(outcomes)} reverts produced assertion failures.")
    sys.exit(0 if all(outcomes) else 1)
