"""前提复述的绑定：答案里那个数，跟提问者给的到底是不是同一件事。

第十一轮独立验收给出两个反例，它们都只靠**一个与属性无关的共有词**就拿到了豁免：

    我在公司工作了99天，请说明年假政策。 -> 公司年假为99天。
        问句给的是"我干了多久"，答案说的是"年假是多少"，两边只共有 `公司`；
    我的报销金额是2500元，请说明审批规则。 -> 这笔2500元是报销审批分界线。
        `这笔` 只说明讲的是谁的，句子本身把 2500 定义成了一条制度界线。

判据因此改成看**这一次出现挂在谁身上**，而不是两段文字有没有交集：

    `X为N` / `X是N` —— 数值被等同于属性 X，要算复述，问句必须也把同一个数
        等同于同一个属性（`我的报销金额是2500元` -> `你的报销金额是2500元`）；
    `N是X` —— 数值被定义成 X，那是在陈述制度，一律不豁免；
    其余位置 —— 紧挨数值的那**一个**中心词必须出现在问句那一次的相邻实词里，
        `公司` 这种状语／领属词当不了中心词。

同时补上一个绑定漏洞：核对词在下一个分句时，原来一律当成"问的是别的事"。
`我的年假是99天，是真的吗？` 的 `是真的吗` 自己没有内容，指回的正是前一句。
"""
from __future__ import annotations

import unittest

import rag
from rag import Chunk


LEAVE = "正式员工入职满一年后，每年享有5天带薪年假。"
EXPENSE = "单笔金额不超过2000元由直属主管审批，超过2000元还需部门负责人审批。"


def evidence(text: str) -> list[tuple[Chunk, float]]:
    return [(Chunk(text=text, source="policy.md", index=1), 1.0)]


class PremiseAttributeBindingTests(unittest.TestCase):
    """同一个数字换了对象，就是在替资料发言。"""

    def assert_blocked(self, question: str, answer: str, source: str) -> None:
        ok, reason = rag.validate_answer(answer, evidence(source), question)
        self.assertFalse(ok, f"{answer!r} 应被拦下，实际交付了")
        self.assertEqual(reason, "ungrounded_quantity")

    def assert_delivered(self, question: str, answer: str, source: str) -> None:
        ok, reason = rag.validate_answer(answer, evidence(source), question)
        self.assertTrue(ok, f"{answer!r} 应可交付，实际被 {reason} 拦下")

    def test_tenure_number_reused_as_leave_value(self):
        # 只共有 `公司`——一个状语，证明不了两边在说同一件事。
        self.assert_blocked(
            "我在公司工作了99天，请说明年假政策。", "公司年假为99天。[来源1]", LEAVE
        )

    def test_tenure_number_reused_without_copula(self):
        self.assert_blocked(
            "我在公司工作了99天，请说明年假政策。", "公司规定年假99天。[来源1]", LEAVE
        )

    def test_tenure_number_reused_as_personal_leave(self):
        self.assert_blocked(
            "我在公司工作了99天，请说明年假政策。", "你的年假是99天。[来源1]", LEAVE
        )

    def test_expense_number_defined_as_a_threshold(self):
        # `这笔` 只说明讲的是谁的；句子把 2500 定义成了制度界线。
        self.assert_blocked(
            "我的报销金额是2500元，请说明审批规则。",
            "这笔2500元是报销审批分界线。[来源1]",
            EXPENSE,
        )

    def test_bare_trailing_verification_binds_back(self):
        # `是真的吗` 自己没有内容，它要核对的就是前一句里的 99。
        self.assert_blocked(
            "我的年假是99天，是真的吗？", "你的年假是99天。[来源1]", LEAVE
        )

    def test_same_predicate_restatement_is_delivered(self):
        # 中心词都是 `工作`，说的确实是同一件事。
        self.assert_delivered(
            "我在公司工作了99天，请说明年假政策。",
            f"你在公司工作了99天。{LEAVE}[来源1]",
            LEAVE,
        )

    def test_same_attribute_restatement_is_delivered(self):
        # 属性都是 `报销金额`，`我的`/`你的` 只是换了人称。
        self.assert_delivered(
            "我的报销金额是2500元，请说明审批规则。",
            "你的报销金额是2500元，超过2000元还需部门负责人审批。[来源1]",
            EXPENSE,
        )

    def test_instance_predicate_restatement_is_delivered(self):
        self.assert_delivered(
            "我的报销金额是2500元，请说明审批规则。",
            "这笔2500元报销需要部门负责人审批，门槛金额是2000元。[来源1]",
            EXPENSE,
        )

    def test_contentful_trailing_question_keeps_the_premise(self):
        # `是否需要部门负责人审批` 问的是审批，2500 仍是他给定的前提。
        self.assert_delivered(
            "这笔报销费用为2500元，是否需要部门负责人审批？",
            "这笔2500元报销需要部门负责人审批，门槛金额是2000元。[来源1]",
            EXPENSE,
        )

    def test_scenario_question_keeps_the_premise(self):
        self.assert_delivered(
            "单笔 2500 元的报销，除了直属主管还需要谁审批？门槛金额是多少",
            "单笔2500元的报销需要部门负责人审批，门槛金额是2000元。[来源1]",
            EXPENSE,
        )


def only_mention(text: str) -> rag.QuantityMention:
    """文本里唯一那个数量。下标由分词器给出，不手数。"""
    mentions = rag.quantity_mentions(text)
    assert len(mentions) == 1, mentions
    return mentions[0]


class CopularRoleTests(unittest.TestCase):
    """系动词决定这个数是"某个属性的取值"还是"被定义成一条规定"。"""

    def role_of(self, text: str) -> tuple[str | None, str]:
        mention = only_mention(text)
        return rag._copular_role(text, mention.start, mention.end, 0)

    def test_left_copula_makes_it_a_value(self):
        role, span = self.role_of("公司年假为99天。")
        self.assertEqual(role, "value")
        self.assertEqual(span, "公司年假")

    def test_right_copula_makes_it_a_definition(self):
        role, span = self.role_of("这笔2500元是报销审批分界线。")
        self.assertEqual(role, "defined")
        self.assertEqual(span, "报销审批分界线。")

    def test_verbal_predicate_is_neither(self):
        role, _span = self.role_of("你在公司工作了99天。")
        self.assertIsNone(role)


class AttributeCoreTests(unittest.TestCase):
    """指示词与人称词说明的是"谁的"，不是属性本身。"""

    def test_person_determiner_is_stripped(self):
        self.assertEqual(rag._attribute_core("我的报销金额"), "报销金额")
        self.assertEqual(rag._attribute_core("你的报销金额"), "报销金额")

    def test_instance_determiner_is_stripped(self):
        self.assertEqual(rag._attribute_core("这笔报销费用"), "报销费用")

    def test_attribute_itself_survives(self):
        self.assertEqual(rag._attribute_core("公司年假"), "公司年假")


class GoverningHeadTests(unittest.TestCase):
    """中心词只取紧挨数值的那一个，不取一片。"""

    def head_of(self, text: str) -> str | None:
        mention = only_mention(text)
        return rag._governing_head(text, mention.start, mention.end, 0)

    def test_verb_before_the_number(self):
        self.assertEqual(self.head_of("你在公司工作了99天。"), "工作")

    def test_determiner_only_left_looks_right(self):
        # 左边只有 `这笔`，中心词只能从右边取，而且取**紧挨着**的那一个。
        self.assertEqual(self.head_of("这笔2500元报销需要部门负责人审批。"), "报销")


class BareVerificationTests(unittest.TestCase):
    """核对句自己有没有内容，决定它问的是前一句还是别的事。"""

    def test_bare_back_reference(self):
        self.assertTrue(rag._is_bare_verification("是真的吗"))
        self.assertTrue(rag._is_bare_verification("对不对"))

    def test_clause_with_its_own_content(self):
        self.assertFalse(rag._is_bare_verification("是否需要部门负责人审批"))

    def test_given_premise_survives_a_contentful_question(self):
        question = "这笔报销费用为2500元，是否需要部门负责人审批？"
        mention = rag.quantity_mentions(question)[0]
        self.assertTrue(rag._question_occurrence_is_given(question, mention))

    def test_given_premise_lost_to_a_bare_back_reference(self):
        question = "我的年假是99天，是真的吗？"
        mention = rag.quantity_mentions(question)[0]
        self.assertFalse(rag._question_occurrence_is_given(question, mention))


if __name__ == "__main__":
    unittest.main()
