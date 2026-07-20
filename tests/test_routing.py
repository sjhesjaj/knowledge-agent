import unittest

from agent import decide_action
from rag import decompose_question


class RoutingTests(unittest.TestCase):
    def test_leave_terms_use_local_search_route(self):
        decision = decide_action("实习生可以休年假吗？", [])
        self.assertEqual(decision["type"], "tool")
        self.assertEqual(decision["tool"], "search_knowledge_base")

    def test_follow_up_question_keeps_previous_context(self):
        parts = decompose_question("实习生可以休年假吗？需要谁审批？")
        self.assertEqual(parts, ["实习生可以休年假吗", "实习生可以休年假吗；需要谁审批"])


if __name__ == "__main__":
    unittest.main()
