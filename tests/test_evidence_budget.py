"""R2: the sub-query cap and the evidence budget are two limits, enforced separately.

Before this round a compound question split into as many sub-queries as it had
clauses, and two retrieval paths ignored the caller's `top_k`:

    年假多少天？谁审批？多久失效？怎么申请？还能顺延吗？
        -> five sub-queries; `document_search(top_k=3)` returned five pieces
           of evidence, and `top_k=4` returned five as well.

- the full chain cut its selection with `[:max(top_k, len(sub_questions))]`,
  which *raises* the ceiling to the sub-question count;
- the confident BM25 multi path returned one chunk per clause and never cut.

The two limits do different jobs, so they are tested apart:

1. **at most `MAX_SUB_QUESTIONS` sub-queries** - bounds the number of retrieval
   rounds. Extra clauses are *merged* into a neighbour, never dropped;
2. **at most `top_k` pieces of evidence** - bounds what reaches the prompt.
   Both over-long lists arrive in *clause order*, not relevance order, so a
   plain `[:top_k]` would drop the last clauses first. The cut removes the
   least relevant clause's evidence instead.

Everything here is offline. The full chain's model boundaries
(`embed_many`, `hybrid_retrieve`, `select_for_subquestions`) are patched, and the
BM25 fast-path tests patch the full chain to raise, so a question that stopped
being "confident" fails loudly instead of quietly calling Ollama.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import rag
from orchestration.document_adapter import document_search
from rag import Chunk

REFERENCE = "年假多少天？谁审批？多久失效？怎么申请？还能顺延吗？"


def _no_network(*_args, **_kwargs):
    raise AssertionError("a test in this module tried to reach the model server")


_GUARD = patch.object(rag.requests, "post", _no_network)


def setUpModule():
    # Every model boundary is patched per test; this catches the one that was not.
    _GUARD.start()


def tearDownModule():
    _GUARD.stop()


CORPUS = Path(__file__).resolve().parent.parent / "sample_company_rules.md"


def chunk(index: int, text: str) -> Chunk:
    return Chunk(text=text, source="rules.md", index=index)


def handbook() -> list[Chunk]:
    return rag.split_text(CORPUS.read_text(encoding="utf-8"), source=CORPUS.name)


def segments(sub_questions: list[str]) -> list[str]:
    return [segment for sub in sub_questions for segment in sub.split("；")]


class SubQueryCapTests(unittest.TestCase):
    """Limit 1: retrieval rounds, bounded by merging - never by dropping."""

    def test_reference_question_is_capped_at_three(self):
        self.assertEqual(len(rag.split_clauses(REFERENCE)), 5)
        self.assertLessEqual(len(rag.decompose_question(REFERENCE)), rag.MAX_SUB_QUESTIONS)
        self.assertEqual(rag.MAX_SUB_QUESTIONS, 3)

    def test_no_clause_is_lost_when_merging(self):
        for question in (
            REFERENCE,
            "报销要多久？谁签字？发票怎么交？超额怎么办？能预支吗？",
            "工资几号发？工资条在哪看？绩效多久考一次？结果能申请复核吗？出差谁审批？",
            "甲？乙？丙？丁？戊？己？庚？",
        ):
            with self.subTest(question=question):
                merged = rag.decompose_question(question)
                self.assertLessEqual(len(merged), 3)
                kept = set(segments(merged))
                for clause in rag.split_clauses(question):
                    for segment in clause.split("；"):
                        self.assertIn(segment, kept)

    def test_merging_keeps_the_order_of_the_question(self):
        merged = segments(rag.decompose_question("甲？乙？丙？丁？戊？"))
        self.assertEqual(merged, ["甲", "乙", "丙", "丁", "戊"])

    def test_the_most_related_neighbours_merge_first(self):
        # `年假多少天` and the follow-up that inherited it share the most words.
        merged = rag.merge_sub_questions(
            ["年假多少天", "年假多少天；谁审批", "工资几号发", "远程办公几天"], limit=3
        )
        self.assertEqual(merged[0], "年假多少天；谁审批")
        self.assertEqual(merged[1:], ["工资几号发", "远程办公几天"])

    def test_an_inherited_prefix_is_not_repeated(self):
        merged = rag.merge_sub_questions(["年假多少天", "年假多少天；谁审批"], limit=1)
        self.assertEqual(merged, ["年假多少天；谁审批"])

    def test_a_short_question_is_untouched(self):
        question = "年假多少天？谁审批？"
        self.assertEqual(rag.decompose_question(question), rag.split_clauses(question))


class FitToBudgetTests(unittest.TestCase):
    """Limit 2: evidence count, cut by relevance - never by position."""

    A, B, C = chunk(1, "甲"), chunk(2, "乙"), chunk(3, "丙")

    def test_within_budget_is_untouched(self):
        selected = [(self.A, 1.0), (self.B, 0.5)]
        self.assertEqual(rag.fit_to_budget(selected, 2, {}, top_k=3), selected)

    def test_the_least_relevant_clause_goes_not_the_last(self):
        # Clause order A, B, C; C is the most relevant and A the least.
        selected = [(self.A, 1.0), (self.B, 0.5), (self.C, 0.33)]
        relevance = {1: 0.1, 2: 0.6, 3: 0.9}
        kept = rag.fit_to_budget(selected, 3, relevance, top_k=2)
        self.assertEqual([item.index for item, _ in kept], [2, 3])
        # The shape the review warned against keeps A and drops C.
        self.assertNotEqual(kept, selected[:2])

    def test_survivors_keep_their_original_order(self):
        selected = [(self.A, 1.0), (self.B, 0.5), (self.C, 0.33)]
        relevance = {1: 0.9, 2: 0.1, 3: 0.8}
        kept = rag.fit_to_budget(selected, 3, relevance, top_k=2)
        self.assertEqual([item.index for item, _ in kept], [1, 3])

    def test_surplus_beyond_one_per_clause_goes_first(self):
        # Two clauses; the selector volunteered a third chunk.
        selected = [(self.A, 1.0), (self.B, 0.5), (self.C, 0.33)]
        relevance = {1: 0.1, 2: 0.2, 3: 0.99}
        kept = rag.fit_to_budget(selected, 2, relevance, top_k=2)
        self.assertEqual([item.index for item, _ in kept], [1, 2])


class CoverMergedClausesTests(unittest.TestCase):
    """A free slot goes to a clause that merging left without evidence."""

    def setUp(self):
        self.chunks = handbook()
        self.by_index = {item.index: item for item in self.chunks}
        self.pool = [(item, 1.0 - item.index / 100) for item in self.chunks]

    def test_an_uncovered_clause_gets_its_section(self):
        leave = self.by_index[3]
        clauses = ["带薪年假多少天", "访客进入办公区域陪同"]
        filled = rag.cover_merged_clauses(
            [(leave, 1.0)], clauses, self.pool, self.chunks, top_k=2
        )
        self.assertEqual([item.index for item, _ in filled], [3, 19])

    def test_it_never_exceeds_the_budget(self):
        leave = self.by_index[3]
        clauses = ["带薪年假多少天", "访客进入办公区域陪同", "工资每月几号发放"]
        filled = rag.cover_merged_clauses(
            [(leave, 1.0)], clauses, self.pool, self.chunks, top_k=2
        )
        self.assertEqual(len(filled), 2)

    def test_a_vague_clause_is_not_padded_with_a_guess(self):
        # `还能顺延吗` scores 1.86 on its own - it cannot say which section it means.
        leave = self.by_index[3]
        filled = rag.cover_merged_clauses(
            [(leave, 1.0)], ["年假多少天", "还能顺延吗"], self.pool, self.chunks, top_k=4
        )
        self.assertEqual([item.index for item, _ in filled], [3])

    def test_a_wrong_topic_best_match_is_not_padded_in(self):
        # For a leave question, `多久失效` matches the overtime section best
        # (调休 expires after 3 months) - at 2.55, under the line. Padding it in
        # would hand the model an expiry rule for the wrong thing.
        leave = self.by_index[3]
        filled = rag.cover_merged_clauses(
            [(leave, 1.0)], ["年假多少天", "多久失效"], self.pool, self.chunks, top_k=4
        )
        self.assertNotIn(4, [item.index for item, _ in filled])

    def test_only_recalled_chunks_are_used(self):
        # The best match for `访客…` is section 19. Without it in the pool, the
        # runner-up (3.08) is a different topic, so nothing is filled.
        leave = self.by_index[3]
        pool_without_visitors = [pair for pair in self.pool if pair[0].index != 19]
        filled = rag.cover_merged_clauses(
            [(leave, 1.0)], ["带薪年假多少天", "访客进入办公区域陪同"],
            pool_without_visitors, self.chunks, top_k=2,
        )
        self.assertEqual([item.index for item, _ in filled], [3])


def _no_full_chain(*_args, **_kwargs):
    raise AssertionError("fell through to the full chain; this test must stay on BM25")


class ConfidentMultiPathTests(unittest.TestCase):
    """`bm25_multi_fast`: every clause confident and on a distinct section."""

    # BM25 on the handbook: 工资 8.25, 年假 12.69, 远程办公 15.49. The weakest
    # clause is asked *first*, so a positional cut would keep it.
    QUESTION = "工资每月几号发放？带薪年假多少天？远程办公每周最多几天？"

    def setUp(self):
        self.chunks = handbook()

    def search(self, top_k: int) -> tuple[list[int], dict]:
        trace: dict = {}
        with patch.object(rag, "retrieve_with_rerank", _no_full_chain):
            results = rag.retrieve_fast(self.QUESTION, self.chunks, top_k=top_k, trace=trace)
        return [item.index for item, _ in results], trace

    def test_the_path_is_the_fast_one(self):
        _indices, trace = self.search(top_k=3)
        self.assertEqual(trace["retrieval_path"], "bm25_multi_fast")

    def test_top_k_is_honoured(self):
        for top_k in (1, 2, 3, 4):
            with self.subTest(top_k=top_k):
                indices, _trace = self.search(top_k)
                self.assertLessEqual(len(indices), top_k)

    def test_the_weakest_clause_is_dropped_not_the_last(self):
        indices, _trace = self.search(top_k=2)
        self.assertEqual(indices, [3, 5])


def _vector(*_args, **_kwargs):
    return [[0.0]] * 8


class FullChainBudgetTests(unittest.TestCase):
    """`hybrid_rerank`: the selector returns one chunk per sub-query, in order."""

    def setUp(self):
        self.chunks = handbook()
        self.by_index = {item.index: item for item in self.chunks}

    def run_chain(self, question: str, top_k: int, picks: list[int], recalled: list[int]):
        recall = [(self.by_index[i], 1.0 - n / 10) for n, i in enumerate(recalled)]
        chosen = [(self.by_index[i], 1.0 / r) for r, i in enumerate(picks, start=1)]
        seen: dict = {}

        def select(sub_questions, candidates):
            seen["sub_questions"] = list(sub_questions)
            return chosen[: len(sub_questions)]

        trace: dict = {}
        with patch.object(rag, "embed_many", lambda texts: [[0.0]] * len(texts)), \
             patch.object(rag, "hybrid_retrieve", lambda *a, **k: recall), \
             patch.object(rag, "select_for_subquestions", select):
            results = rag.retrieve_with_rerank(question, self.chunks, top_k=top_k, trace=trace)
        return [item.index for item, _ in results], trace, seen

    def test_the_ceiling_is_no_longer_raised_to_the_sub_query_count(self):
        # Seven clauses on seven sections, `top_k=2`: before this round the
        # ceiling became max(2, 7).
        question = "甲？乙？丙？丁？戊？己？庚？"
        indices, trace, seen = self.run_chain(
            question, top_k=2, picks=[1, 2, 3], recalled=[1, 2, 3, 4, 5]
        )
        self.assertLessEqual(len(indices), 2)
        self.assertEqual(trace["sub_questions"], 3)
        self.assertEqual(trace["clauses"], 7)
        self.assertEqual(len(seen["sub_questions"]), 3)

    def test_the_cut_follows_relevance_not_clause_order(self):
        # Recall ranks chunk 5 highest and chunk 3 lowest; the selector returns
        # them in clause order 3, 4, 5.
        indices, _trace, _seen = self.run_chain(
            "甲？乙？丙？丁？戊？", top_k=2, picks=[3, 4, 5], recalled=[5, 4, 3]
        )
        self.assertEqual(indices, [4, 5])

    def test_a_free_slot_covers_a_merged_away_clause(self):
        # Merging puts 带薪年假 and 远程办公 in one sub-query; the selector gives
        # that sub-query only the leave chunk. The spare slot goes to 远程办公.
        question = (
            "带薪年假多少天？远程办公每周最多几天？工资每月几号发放？"
            "费用报销多少天内提交？访客进入办公区域陪同？"
        )
        indices, trace, _seen = self.run_chain(
            question, top_k=4, picks=[3, 6, 19], recalled=[3, 5, 6, 10, 19]
        )
        self.assertEqual(trace["sub_questions"], 3)
        self.assertLessEqual(len(indices), 4)
        # Two clauses are uncovered (远程办公 -> 5 at 15.49, 报销 -> 10 at 11.05);
        # the one free slot goes to the stronger match.
        self.assertEqual(indices, [3, 6, 19, 5])

    def test_an_unmerged_question_is_not_padded(self):
        # Nothing was merged, so each clause already has the selector's pick -
        # even where BM25 would have preferred another section (远程办公 -> 5).
        # No extra evidence is added: this is the behaviour every existing
        # answer set was measured on, and none of their questions has more
        # than three clauses.
        indices, trace, _seen = self.run_chain(
            "带薪年假多少天？远程办公每周最多几天？", top_k=4, picks=[3, 6], recalled=[3, 5, 6]
        )
        self.assertEqual(trace["clauses"], trace["sub_questions"])
        self.assertEqual(indices, [3, 6])


class _Reply:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}}


class RerankDeduplicationTests(unittest.TestCase):
    """Requirement 2 says de-duplicate: one chunk must not take two slots."""

    def test_a_repeated_id_does_not_take_two_slots(self):
        a, b, c = chunk(3, "甲"), chunk(5, "乙"), chunk(7, "丙")
        candidates = [(a, 0.9), (b, 0.8), (c, 0.7)]
        reply = _Reply('{"ranking": [3, 3, 5, 7]}')
        with patch.object(rag.requests, "post", lambda *args, **kwargs: reply):
            ranked = rag.rerank("问题", candidates, top_k=2)
        self.assertEqual([item.index for item, _ in ranked], [3, 5])


class DocumentSearchCeilingTests(unittest.TestCase):
    """The reviewer's reference input, through the document tool, on both limits."""

    def test_reference_input_honours_top_k(self):
        chunks = handbook()
        by_index = {item.index: item for item in chunks}
        recall = [(by_index[i], 1.0 - n / 10) for n, i in enumerate([3, 4, 8, 16, 2])]

        def select(sub_questions, candidates):
            return [(by_index[i], 1.0 / r) for r, i in enumerate([3, 4, 8, 16, 2], 1)][
                : len(sub_questions)
            ]

        for top_k in (2, 3, 4):
            with self.subTest(top_k=top_k):
                trace: dict = {}
                with patch.object(rag, "embed_many", lambda texts: [[0.0]] * len(texts)), \
                     patch.object(rag, "hybrid_retrieve", lambda *a, **k: recall), \
                     patch.object(rag, "select_for_subquestions", select), \
                     patch.object(rag, "bm25_rank", _weak_bm25):
                    result = document_search(REFERENCE, chunks, top_k=top_k, trace=trace)
                self.assertLessEqual(len(result.evidence), top_k)
                self.assertLessEqual(trace["sub_questions"], 3)


def _weak_bm25(question, chunks, *args, **kwargs):
    """No clause is confident, so the fast path must hand over to the full chain."""
    return [(item, 0.5) for item in chunks]


if __name__ == "__main__":
    unittest.main()
