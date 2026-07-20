import json
import unittest
from unittest.mock import patch

import rag
from agent import decide_action
from rag import Chunk, decompose_question, is_valid_evidence_retry


class RoutingTests(unittest.TestCase):
    def test_leave_terms_use_local_search_route(self):
        decision = decide_action("实习生可以休年假吗？", [])
        self.assertEqual(decision["type"], "tool")
        self.assertEqual(decision["tool"], "search_knowledge_base")

    def test_follow_up_question_keeps_previous_context(self):
        parts = decompose_question("实习生可以休年假吗？需要谁审批？")
        self.assertEqual(parts, ["实习生可以休年假吗", "实习生可以休年假吗；需要谁审批"])


class FakeAnswerResponse:
    def __init__(self, answer: str):
        self.answer = answer

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": json.dumps({"answer": self.answer}, ensure_ascii=False)}}


class EvidenceRetryTests(unittest.TestCase):
    def test_rejects_question_echo_and_uncited_claim(self):
        question = "公司年终奖发几个月工资？"
        self.assertFalse(is_valid_evidence_retry(question, question))
        self.assertFalse(is_valid_evidence_retry("年终奖为三个月工资。", question))

    def test_accepts_refusal_or_cited_answer(self):
        question = "年假有多少天？"
        self.assertTrue(is_valid_evidence_retry("根据现有资料无法确定。", question))
        self.assertTrue(is_valid_evidence_retry("员工享有5天年假。[来源1]", question))

    def test_answer_structured_keeps_original_refusal_when_retry_echoes_question(self):
        question = "公司年终奖发几个月工资？"
        responses = [FakeAnswerResponse("根据现有资料无法确定。"), FakeAnswerResponse(question)]
        with patch.object(rag.requests, "post", side_effect=responses):
            answer = rag.answer_structured(
                question,
                [(Chunk(text="## 薪酬\n每月10日发放工资", source="rules.md", index=1), 1.0)],
                [],
            )
        self.assertEqual(answer, "根据现有资料无法确定。")


if __name__ == "__main__":
    unittest.main()
