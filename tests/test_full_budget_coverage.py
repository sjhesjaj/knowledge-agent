"""A full evidence budget must still consider strong omitted clause matches."""
from pathlib import Path
import unittest
from unittest.mock import patch

import rag


class FullBudgetCoverageTests(unittest.TestCase):
    def setUp(self):
        self.chunks = rag.split_text(
            (Path(__file__).resolve().parents[1] / "sample_company_rules.md").read_text("utf-8"),
            "sample_company_rules.md",
        )
        self.by_id = {c.index: c for c in self.chunks}
        self.pool = [(c, 1 - c.index / 100) for c in self.chunks]
        self.question = "申诉要在多久内提交？多久给反馈？建议可以匿名吗？绩效结果分几档？"
        # Observed wrong model response: section 7 only answers performance,
        # and sections 6 and 2 cannot answer the first three questions.
        self.selected = [(self.by_id[i], 1 / n) for n, i in enumerate((7, 6, 2), 1)]
        self.ownership = {7: {1, 4}, 6: {2}, 2: {3}}
        self.network = patch.object(rag.requests, "post", side_effect=AssertionError("unexpected network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_full_budget_recovers_grievance_without_losing_performance(self):
        for k in (2, 3, 4):
            with self.subTest(top_k=k):
                out = rag.cover_merged_clauses(
                    self.selected[:k], rag.split_clauses(self.question), self.pool, self.chunks, k,
                    coverage=self.ownership,
                )
                ids = [c.index for c, _ in out]
                self.assertTrue({7, 20}.issubset(ids), ids)
                self.assertLessEqual(len(ids), k)
                self.assertEqual(len(ids), len(set(ids)))

    def test_semantic_owner_survives_a_wrong_but_confident_lexical_winner(self):
        # For the fourth clause BM25 confidently ranks section 20 first, but
        # only the model-selected performance section actually lists A-D.
        ranked = rag.bm25_rank(rag.split_clauses(self.question)[3], self.chunks)
        self.assertTrue(rag.bm25_confident(ranked))
        self.assertEqual(ranked[0][0].index, 20)
        out = rag.cover_merged_clauses(
            self.selected, rag.split_clauses(self.question), self.pool, self.chunks, 3,
            coverage=self.ownership,
        )
        self.assertIn(7, [c.index for c, _ in out])

    def test_weak_clause_keeps_its_semantic_source(self):
        a, b, c, d = [rag.Chunk(text=str(i), source="fixture", index=i) for i in range(1, 5)]
        def rank(q, _chunks):
            return [(d, 9), (a, 1)] if q in ("alpha", "delta") else [(a, 1), (d, .9)]
        with patch.object(rag, "bm25_rank", side_effect=rank):
            out = rag.cover_merged_clauses(
                [(a, 1), (b, .9), (c, .8)], ["alpha", "beta", "gamma", "delta"],
                [(a, 1), (b, .9), (c, .8), (d, .7)], [a, b, c, d], 3,
                coverage={a.index: {1}, b.index: {2, 3}, c.index: {1}},
            )
        self.assertIn(b.index, [x.index for x, _ in out])
        self.assertIn(d.index, [x.index for x, _ in out])

    def test_unrecalled_best_match_cannot_displace_selected_evidence(self):
        pool = [(c, s) for c, s in self.pool if c.index != 20]
        out = rag.cover_merged_clauses(
            self.selected, rag.split_clauses(self.question), pool, self.chunks, 3,
            coverage=self.ownership,
        )
        self.assertEqual(out, self.selected)

    def test_retrieval_passes_semantic_ownership_into_full_budget_repair(self):
        selected = rag._EvidenceSelection(self.selected, self.ownership)
        with patch.object(rag, "embed_many", side_effect=lambda q: [[0]] * len(q)) as embed, patch.object(
            rag, "hybrid_retrieve", return_value=self.pool
        ) as recall, patch.object(rag, "select_for_subquestions", return_value=selected):
            out = rag.retrieve_with_rerank(self.question, self.chunks, top_k=3)
        self.assertTrue({7, 20}.issubset({c.index for c, _ in out}))
        self.assertEqual(len(embed.call_args.args[0]), 3)
        self.assertEqual(recall.call_count, 3)
        self.assertLessEqual(len(out), 3)

    def test_unmerged_query_still_needs_the_first_budget_limit(self):
        # The new post-merge repair also bounds its output. It does not execute
        # for three unmerged clauses, where the original budget guard is vital.
        with patch.object(rag, "embed_many", side_effect=lambda q: [[0]] * len(q)), patch.object(
            rag, "hybrid_retrieve", return_value=self.pool
        ), patch.object(rag, "select_for_subquestions", return_value=self.selected):
            out = rag.retrieve_with_rerank("甲？乙？丙？", self.chunks, top_k=2)
        self.assertLessEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
