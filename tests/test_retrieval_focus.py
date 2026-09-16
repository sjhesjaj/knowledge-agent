"""M10 retrieval tests: what the document path searches for, and what it keeps.

Two changes are covered.

`document_focus` decides *what to search for*. A compound request carries
clauses the source documents cannot answer; handed to retrieval whole, the
question decomposer turns each into a sub-question and allocates it an evidence
slot, which it fills with whatever chunk scores highest. The policy evidence is
not outranked - it is crowded out of the budget.

`drop_padding_results` decides *what to keep*. When BM25 is confident enough to
skip embedding and reranking, the results far below the best match were
returned to fill `top_k`, not because they matched.

Everything here is offline: no model, no network, no database.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import rag
from orchestration.document_adapter import document_search
from orchestration.planner import document_focus
from rag import Chunk, decompose_question, drop_padding_results

LEAVE = Chunk(
    text="## 请假制度\n正式员工入职满一年后，每年享有 5 天带薪年假。",
    source="rules.md",
    index=1,
)
EQUIPMENT = Chunk(
    text="## 设备领用\n设备遗失应在 2 小时内报告直属主管和信息技术部门。",
    source="rules.md",
    index=2,
)
HOURS = Chunk(
    text="## 工作时间\n公司实行弹性工作制，每日工作满 8 小时。",
    source="rules.md",
    index=3,
)


class DocumentFocusTests(unittest.TestCase):
    # (request, the clause a document cannot answer)
    DROPS_THE_SYSTEM_CLAUSE = (
        ("设备遗失几小时内要上报？Sku-C300 当前存量报我一下", "sku-c300"),
        ("单笔超过 2000 元由谁签字，要精确的；SKU_B200 目前的存量报一下", "sku_b200"),
        ("年假天数那句准确措辞发我，再看下 SKU-A100 最新库存", "sku-a100"),
        ("公共网盘那条的原样条文，另外 ord-1002 现在什么状态", "ord-1002"),
    )

    def test_a_clause_only_the_business_system_can_answer_is_dropped(self):
        for question, fragment in self.DROPS_THE_SYSTEM_CLAUSE:
            with self.subTest(question=question):
                focused = document_focus(question)
                self.assertNotIn(fragment, focused.lower())
                self.assertNotEqual(focused, question)

    def test_the_policy_half_of_the_request_survives_intact(self):
        focused = document_focus("设备遗失几小时内要上报？Sku-C300 当前存量报我一下")
        self.assertIn("设备遗失", focused)
        self.assertIn("上报", focused)

    def test_a_topic_clause_is_kept_even_though_only_the_wiki_serves_it(self):
        # `信息安全概览` names the topic; dropping it would leave the lexical
        # search with only the demonstrative half of the request.
        focused = document_focus("信息安全概览、公共网盘那条的原样条文、SKU-A100 实时库存")
        self.assertIn("信息安全", focused)
        self.assertIn("公共网盘", focused)
        self.assertNotIn("sku-a100", focused.lower())

    def test_a_request_with_nothing_to_drop_is_returned_unchanged(self):
        for question in (
            "年假有多少天？",
            "报销申请最晚要在费用发生后多少天内提交？需要附哪些材料",
            "请假超过 1 天谁审批？远程办公每周最多几天？",
        ):
            with self.subTest(question=question):
                self.assertIs(document_focus(question), question)

    def test_a_request_that_is_entirely_a_system_lookup_is_left_alone(self):
        # Nothing would remain, and an empty query is worse than a bad one.
        question = "SKU-A100 现在还剩多少"
        self.assertEqual(document_focus(question), question)

    def test_pleasantries_are_dropped_but_the_question_is_not(self):
        focused = document_focus("你好，年假天数的准确说法是什么")
        self.assertNotIn("你好", focused)
        self.assertIn("年假", focused)

    def test_it_is_pure_and_repeatable(self):
        question = "设备遗失几小时内要上报？Sku-C300 当前存量报我一下"
        self.assertEqual(document_focus(question), document_focus(question))

    def test_a_blank_request_is_rejected(self):
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    document_focus(blank)


class SubQuestionBudgetTests(unittest.TestCase):
    """The sub-question count is what allocates evidence slots."""

    def test_a_system_clause_no_longer_earns_a_sub_question(self):
        question = "设备遗失几小时内要上报？Sku-C300 当前存量报我一下"
        self.assertEqual(len(decompose_question(question)), 2)
        self.assertEqual(len(decompose_question(document_focus(question))), 1)

    def test_two_policy_questions_still_earn_one_sub_question_each(self):
        question = "请假超过 1 天谁审批？远程办公每周最多几天？"
        self.assertEqual(len(decompose_question(document_focus(question))), 2)

    def test_focusing_never_increases_the_sub_question_count(self):
        for question in (
            "设备遗失几小时内要上报？Sku-C300 当前存量报我一下",
            "年假总览；准确措辞发我；SKU-A100 库存",
            "报销要几天内提交？需要哪些材料？SKU_B200 还有多少",
        ):
            with self.subTest(question=question):
                self.assertLessEqual(
                    len(decompose_question(document_focus(question))),
                    len(decompose_question(question)),
                )

    def test_the_focused_query_stays_within_the_three_sub_query_bound(self):
        question = "年假总览；准确措辞发我；审批人是谁；SKU-A100 库存；SKU_B200 库存"
        self.assertLessEqual(len(decompose_question(document_focus(question))), 3)


class PaddingTests(unittest.TestCase):
    def test_results_far_below_the_best_match_are_dropped(self):
        ranked = [(LEAVE, 9.5), (HOURS, 4.75), (EQUIPMENT, 1.0)]
        self.assertEqual(drop_padding_results(ranked), ranked[:2])

    def test_a_zero_score_is_never_kept(self):
        ranked = [(LEAVE, 5.45), (HOURS, 0.0)]
        self.assertEqual(drop_padding_results(ranked), ranked[:1])

    def test_the_best_match_is_always_kept(self):
        ranked = [(LEAVE, 0.01)]
        self.assertEqual(drop_padding_results(ranked), ranked)

    def test_comparable_results_are_all_kept(self):
        ranked = [(LEAVE, 9.0), (HOURS, 8.0), (EQUIPMENT, 7.5)]
        self.assertEqual(drop_padding_results(ranked), ranked)

    def test_an_empty_ranking_stays_empty(self):
        self.assertEqual(drop_padding_results([]), [])

    def test_the_confident_bm25_path_returns_no_padding(self):
        """The branch that skips reranking must not still pad to `top_k`.

        The ranking is supplied directly so the test states one thing - what
        the fast path does with a long tail - rather than also depending on
        whether this corpus happens to trip the confidence gate.
        """
        ranking = [(LEAVE, 9.5), (HOURS, 4.75), (EQUIPMENT, 0.9)]
        trace: dict = {}
        with patch.object(rag, "bm25_rank", return_value=ranking):
            results = rag.retrieve_fast(
                "年假有多少天", [LEAVE, HOURS, EQUIPMENT], top_k=4, trace=trace
            )

        self.assertEqual(trace["retrieval_path"], "bm25_fast")
        self.assertEqual(trace["padding_dropped"], 1)
        self.assertEqual([chunk.index for chunk, _ in results], [LEAVE.index, HOURS.index])

    def test_the_fast_path_still_honours_top_k_as_a_ceiling(self):
        """Three comparable candidates, `top_k=2`, so two come back.

        The scores have to clear the fast path's own confidence gate
        (`first >= 3.0` and `first / second >= 1.5`). They did not before, so
        this fell through to the rerank branch and tried to reach a real
        embedding endpoint - the assertion below then depended on a service
        being up. The trace is asserted too, so a future change to the gate
        cannot silently move this back off the branch it is meant to cover.
        """
        ranking = [(LEAVE, 9.5), (HOURS, 6.0), (EQUIPMENT, 5.9)]
        trace: dict = {}
        with patch.object(rag, "bm25_rank", return_value=ranking):
            results = rag.retrieve_fast(
                "年假有多少天", [LEAVE, HOURS, EQUIPMENT], top_k=2, trace=trace
            )
        self.assertEqual(trace["retrieval_path"], "bm25_fast")
        self.assertEqual(len(results), 2)


class DocumentSearchIntegrationTests(unittest.TestCase):
    def test_retrieval_receives_the_focused_query(self):
        chunks = [LEAVE, EQUIPMENT]
        captured: dict = {}

        def fake_retrieve(question, corpus, top_k=4, trace=None):
            captured["question"] = question
            return [(corpus[0], 3.5)]

        with patch(
            "orchestration.document_adapter.retrieve_fast", side_effect=fake_retrieve
        ):
            document_search("设备遗失几小时内要上报？Sku-C300 存量报一下", chunks, top_k=4)

        self.assertNotIn("sku-c300", captured["question"].lower())
        self.assertIn("设备遗失", captured["question"])

    def test_an_unfocused_question_reaches_retrieval_byte_for_byte(self):
        chunks = [LEAVE]
        question = "年假有多少天？"
        with patch(
            "orchestration.document_adapter.retrieve_fast",
            return_value=[(LEAVE, 3.5)],
        ) as retrieval:
            document_search(question, chunks, top_k=4)
        retrieval.assert_called_once_with(question, chunks, top_k=4, trace=None)

    def test_the_trace_records_that_the_query_was_narrowed(self):
        trace: dict = {}
        with patch(
            "orchestration.document_adapter.retrieve_fast",
            return_value=[(LEAVE, 3.5)],
        ):
            document_search(
                "年假条款发我，SKU-A100 库存也报一下", [LEAVE], top_k=4, trace=trace
            )
        self.assertTrue(trace["document_focus_applied"])
        self.assertNotIn("sku-a100", trace["document_focus_query"].lower())

    def test_top_k_remains_a_ceiling_not_a_quota(self):
        with patch(
            "orchestration.document_adapter.retrieve_fast",
            return_value=[(LEAVE, 3.5)],
        ):
            result = document_search("年假有多少天？", [LEAVE, HOURS], top_k=4)
        self.assertLessEqual(len(result.evidence), 4)
        self.assertEqual(len(result.evidence), 1)

    def test_the_chunk_to_evidence_boundary_is_unchanged(self):
        with patch(
            "orchestration.document_adapter.retrieve_fast",
            return_value=[(LEAVE, 3.5)],
        ):
            result = document_search("年假有多少天？", [LEAVE], top_k=4)
        evidence = result.evidence[0]
        self.assertEqual(evidence.content, LEAVE.text)
        self.assertEqual(evidence.locator, f"chunk:{LEAVE.index}")
        self.assertEqual(evidence.metadata["retrieval_score"], 3.5)
        self.assertIsNone(evidence.confidence)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
