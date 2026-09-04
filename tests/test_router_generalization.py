"""M8A router-generalisation regression tests.

These cover the *rule categories* introduced in M8A, written as fresh sentences
rather than copies of the ones that exposed them.

What this suite is NOT: evidence of independence from Blind Audit V1. These
tests were written in response to that audit, so they inherit its findings by
construction. The verbatim-reuse guard below only rules out literal copying.

Table-driven on purpose: each table is one rule, and a new phrasing is added by
appending a row rather than by writing another test.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from chat_orchestration import (
    MESSAGE_MULTIPLE_SKU,
    MESSAGE_NO_SKU,
    MESSAGE_SYSTEM_LIMITED,
    Prepared,
    extract_sku,
    extract_skus,
    prepare,
)
from evaluate_answerability import (
    BEHAVIOR_BOUNDARY,
    BEHAVIOR_POLICY_REFUSE,
    BOUNDARY_MESSAGES,
    classify_behavior,
)
from orchestration.planner import Route, plan_request
from rag import Chunk

REPO_ROOT = Path(__file__).resolve().parent.parent


def route_of(question: str) -> Route:
    return plan_request(question).route


# --------------------------------------------------------------------------
# 1. Natural social utterances are direct
# --------------------------------------------------------------------------

PURE_SOCIAL = (
    "早上好啊，今天天气不错",
    "哈喽，好久不见",
    "多谢啦，太给力了",
    "麻烦你了，辛苦",
    "好的好的，明白",
    "那就这样，回头见",
    "晚安，早点休息",
    "在不在？",
)

# A social opener must never swallow the request that follows it.
SOCIAL_PLUS_REQUEST = (
    ("你好，报销上限是多少", Route.DOCUMENT_ONLY),
    ("谢谢，另外查下 SKU-B200", Route.SYSTEM_ONLY),
    ("哈喽，讲讲信息安全", Route.WIKI_ONLY),
    ("辛苦了，把年假原文发我", Route.DOCUMENT_ONLY),
    ("在吗，我的审批到哪一步了", Route.SYSTEM_ONLY),
    ("晚上好，加班调休怎么规定的", Route.DOCUMENT_ONLY),
)

# Requests this product cannot serve are still requests. Routing them to
# `direct` would answer a meeting-room booking with a greeting; the honest
# outcome is the normal path, which then refuses for lack of evidence.
SOCIAL_PLUS_UNKNOWN_REQUEST = (
    ("你好，帮我订一间会议室", Route.DOCUMENT_ONLY),
    ("你好，我想投诉服务问题", Route.DOCUMENT_ONLY),
    ("你好，帮我看看公司班车", Route.DOCUMENT_ONLY),
    ("谢谢，帮我联系一下人事", Route.DOCUMENT_ONLY),
)


# --------------------------------------------------------------------------
# 2. Overview intent is a rule, not a word list of one
# --------------------------------------------------------------------------

WIKI_OVERVIEW = (
    "讲讲设备领用这块",
    "说一下差旅安排",
    "科普下数据备份",
    "帮我捋捋绩效这块",
    "想了解一下保密义务",
    "培训这块整体是怎么安排的",
    "办公区域管理的大方向是什么",
    "离职交接的主要思路",
    "软件安装的要点有哪些",
    "大致说说工作时间",
)


# --------------------------------------------------------------------------
# 3. Negation
# --------------------------------------------------------------------------

# The caller names the source text only to decline it.
NEGATED_CITATION = (
    "讲讲考勤，不用原文",
    "说说报销，不需要引用",
    "科普下年假，无需条款",
    "捋一遍差旅，别给依据",
    "大致说说设备领用，不必附出处",
    "了解一下培训，不用给条款编号",
)

AFFIRMATIVE_CITATION = (
    "把考勤原文发我",
    "请引用报销条款",
    "给出年假依据",
    "差旅规定的原话是什么",
)


# --------------------------------------------------------------------------
# 4. A SKU is a System object on its own
# --------------------------------------------------------------------------

BARE_SKU = (
    "SKU-A100 到货没",
    "sku-b200 什么情况",
    "Sku_C300 现在几箱",
    "SKU_A100 有没有货",
    "看下 sku c300",
    "SKU-B200 还剩不剩",
    "帮我确认 SKU-A100",
    "sku-c300 补货了吗",
)

# A SKU must be a SKU, not any alphanumeric token that happens to look like one.
NOT_A_SKU = (
    "xsku-a100 是什么",
    "编号 a100 怎么查",
    "SKU-A1000 这个编号对吗",
    "ABC-123 是什么意思",
)


# --------------------------------------------------------------------------
# 5. Compound intent merges per clause
# --------------------------------------------------------------------------

WIKI_SYSTEM = (
    "讲讲库存管理，顺便看下 SKU-A100",
    "说说考勤，另外我的考勤状态是什么",
    "科普下积分规则，顺便查我的积分",
    "了解一下工单流程，同时看我的工单进度",
    "大致说说排班，另外我当前的排班呢",
)

DOCUMENT_SYSTEM = (
    "报销原文怎么写？顺便查 SKU-B200",
    "给我年假条款，另外 SKU-C300 还剩几箱",
    "考勤依据发我，同时看下 SKU-A100",
    "请引用差旅规定，另外我的审批到哪一步了",
    "培训预算上限是多少？顺便查下 SKU-A100",
)

WIKI_DOCUMENT_SYSTEM = (
    "讲讲报销，把原文给我，再查 SKU-A100",
    "说说年假，引用条款，另外 SKU-C300 几箱",
    "科普下信息安全，给出依据，顺便看 SKU-B200",
    "了解一下考勤，附上原文，同时查我的考勤状态",
    "大致说说差旅，请引用规定原文，另外 SKU-A100 什么情况",
)


# --------------------------------------------------------------------------
# 6. Policy questions must not acquire a System step
# --------------------------------------------------------------------------

# A hedge is not a request for a topic page. `大概`/`说下` soften a live lookup;
# they must not add a Wiki step to a clause that is plainly asking for a value.
SOFT_MARKER_LIVE_LOOKUP = (
    "SKU-A100 大概还有多少",
    "SKU-B200 多少件也说下",
    "我的账户余额大概多少",
)

# The same hedges still mark an overview when the clause is not a live lookup,
# and a genuine overview clause beside a lookup keeps its Wiki step.
SOFT_MARKER_STILL_OVERVIEW = (
    ("大概说说远程办公政策", Route.WIKI_ONLY),
    ("介绍库存管理，顺便查 SKU-A100", Route.WIKI_SYSTEM),
    ("概述信息安全，再查 SKU-C300", Route.WIKI_SYSTEM),
)


POLICY_NOT_SYSTEM = (
    "库存盘点制度怎么规定",
    "订单退换货办法是什么",
    "审批权限规则怎么写",
    "余额管理制度有哪些要求",
    "积分发放政策是什么",
    "考勤异常处理流程的规定",
)


MULTI_SKU = (
    "查一下 SKU-A100 和 SKU-B200 的库存",
    "SKU-B200 和 sku-c300 现在各有多少",
    "帮我对比 SKU-A100、SKU-C300 的库存",
    "sku_a100 跟 SKU-B200 哪个多",
)


class SocialUtteranceTests(unittest.TestCase):
    def test_natural_social_variants_are_direct(self):
        for question in PURE_SOCIAL:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertEqual(plan.route, Route.DIRECT)
                self.assertEqual(plan.steps, ())
                self.assertTrue(plan.signals.is_direct)

    def test_social_prefix_never_swallows_the_request(self):
        for question, expected in SOCIAL_PLUS_REQUEST:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.is_direct)
                self.assertEqual(plan.route, expected)

    def test_out_of_scope_requests_are_not_answered_with_a_greeting(self):
        for question, expected in SOCIAL_PLUS_UNKNOWN_REQUEST:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.is_direct)
                self.assertNotEqual(plan.route, Route.DIRECT)
                # Falling back to the document path is fine: the refusal chain
                # downstream can say there is no evidence for it.
                self.assertEqual(plan.route, expected)


class WikiOverviewTests(unittest.TestCase):
    def test_natural_overview_phrasings_select_wiki_only(self):
        for question in WIKI_OVERVIEW:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_wiki)
                self.assertEqual(plan.route, Route.WIKI_ONLY)

    def test_overview_does_not_request_exact_citation(self):
        for question in WIKI_OVERVIEW:
            with self.subTest(question=question):
                self.assertFalse(plan_request(question).signals.requires_exact_citation)

    def test_soft_hedge_does_not_add_wiki_to_a_live_lookup(self):
        for question in SOFT_MARKER_LIVE_LOOKUP:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_wiki)
                self.assertTrue(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_soft_hedge_still_marks_a_real_overview(self):
        for question, expected in SOFT_MARKER_STILL_OVERVIEW:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_wiki)
                self.assertEqual(plan.route, expected)


class NegatedCitationTests(unittest.TestCase):
    def test_declined_source_text_does_not_select_document(self):
        for question in NEGATED_CITATION:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.requires_exact_citation)
                self.assertFalse(plan.signals.needs_document)
                self.assertEqual(plan.route, Route.WIKI_ONLY)

    def test_requested_source_text_still_selects_document(self):
        for question in AFFIRMATIVE_CITATION:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.requires_exact_citation)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_negation_does_not_reach_across_a_clause_boundary(self):
        # The refusal applies to the first clause only.
        plan = plan_request("不用讲太宽泛的东西。请直接引用原文")
        self.assertTrue(plan.signals.requires_exact_citation)


class BareSkuTests(unittest.TestCase):
    def test_a_sku_alone_selects_system(self):
        for question in BARE_SKU:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_lookalike_tokens_are_not_treated_as_skus(self):
        for question in NOT_A_SKU:
            with self.subTest(question=question):
                self.assertFalse(plan_request(question).signals.needs_system)

    def test_sku_is_not_suppressed_by_policy_wording_in_another_clause(self):
        plan = plan_request("库存管理办法怎么写的？SKU-A100 现在还有多少库存？")
        self.assertTrue(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)
        self.assertEqual(plan.route, Route.DOCUMENT_SYSTEM)


class CompoundIntentTests(unittest.TestCase):
    def test_overview_plus_live_state(self):
        for question in WIKI_SYSTEM:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_SYSTEM)

    def test_source_text_plus_live_state(self):
        for question in DOCUMENT_SYSTEM:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.DOCUMENT_SYSTEM)

    def test_all_three_channels(self):
        for question in WIKI_DOCUMENT_SYSTEM:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_DOCUMENT_SYSTEM)


class PolicyStaysDocumentTests(unittest.TestCase):
    def test_policy_questions_do_not_acquire_a_system_step(self):
        for question in POLICY_NOT_SYSTEM:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)


class MultiSkuBoundaryTests(unittest.TestCase):
    """Several valid SKUs is a stated limit, not a missing parameter."""

    CHUNKS = [Chunk(text="## 请假制度\n年假 5 天。", source="rules.md", index=1)]

    def test_extract_skus_collects_each_distinct_sku(self):
        self.assertEqual(
            extract_skus("查一下 SKU-A100 和 sku_b200"), ["sku-a100", "sku-b200"]
        )
        self.assertEqual(extract_skus("SKU-A100 还有 SKU-A100 吗"), ["sku-a100"])
        self.assertEqual(extract_skus("没有编号"), [])

    def test_repeated_single_sku_is_still_one_request(self):
        self.assertEqual(extract_sku("SKU-A100 还有 SKU-A100 吗"), "sku-a100")

    def test_multiple_skus_report_the_one_per_request_limit(self):
        for question in MULTI_SKU:
            with self.subTest(question=question):
                prepared = prepare(question, self.CHUNKS)
                self.assertEqual(prepared.fixed_answer, MESSAGE_MULTIPLE_SKU)
                # Not "you gave me no SKU" - they gave several.
                self.assertNotEqual(prepared.fixed_answer, MESSAGE_NO_SKU)
                self.assertNotEqual(prepared.fixed_answer, MESSAGE_SYSTEM_LIMITED)

    def test_multiple_skus_never_reach_the_answer_model(self):
        for question in MULTI_SKU:
            with self.subTest(question=question):
                prepared = prepare(question, self.CHUNKS)
                self.assertFalse(prepared.needs_generation)
                self.assertEqual(prepared.sources, [])
                self.assertEqual(prepared.results_for_answer, [])

    def test_planner_still_routes_multi_sku_through_system(self):
        # The limit is a product decision at the chat layer; routing must still
        # recognise the intent, otherwise the message would be a plain refusal.
        for question in MULTI_SKU:
            with self.subTest(question=question):
                self.assertTrue(plan_request(question).signals.needs_system)


class EvaluatorBoundaryClassificationTests(unittest.TestCase):
    """The answerability evaluator must read the new message as a boundary.

    Left out of `BOUNDARY_MESSAGES`, a one-per-request limit would be scored as
    a refusal, which would report a capability statement as missing evidence.
    """

    CHUNKS = [Chunk(text="## 请假制度\n年假 5 天。", source="rules.md", index=1)]

    def _prepared(self, message: str) -> Prepared:
        return Prepared(
            route="system_only",
            steps=["system_query"],
            outcome="refuse",
            reason_codes=[],
            sources=[],
            results_for_answer=[],
            executor_seconds=0.0,
            fixed_answer=message,
        )

    def test_multiple_sku_message_classifies_as_boundary(self):
        behavior, flags = classify_behavior(
            self._prepared(MESSAGE_MULTIPLE_SKU), MESSAGE_MULTIPLE_SKU, "两个 sku"
        )
        self.assertEqual(behavior, BEHAVIOR_BOUNDARY)
        self.assertNotEqual(behavior, BEHAVIOR_POLICY_REFUSE)
        self.assertFalse(flags["model_called"])

    def test_existing_boundary_messages_are_unchanged(self):
        for message in (MESSAGE_SYSTEM_LIMITED, MESSAGE_NO_SKU):
            with self.subTest(message=message):
                behavior, flags = classify_behavior(
                    self._prepared(message), message, "问题"
                )
                self.assertEqual(behavior, BEHAVIOR_BOUNDARY)
                self.assertFalse(flags["model_called"])

    def test_an_evidence_refusal_is_still_a_policy_refusal(self):
        message = "根据现有资料无法确定。"
        behavior, _ = classify_behavior(self._prepared(message), message, "问题")
        self.assertEqual(behavior, BEHAVIOR_POLICY_REFUSE)

    def test_multiple_sku_boundary_end_to_end(self):
        prepared = prepare("查一下 SKU-A100 和 SKU-C300 的库存", self.CHUNKS)
        behavior, flags = classify_behavior(
            prepared, prepared.fixed_answer, "查一下 SKU-A100 和 SKU-C300 的库存"
        )
        self.assertEqual(prepared.fixed_answer, MESSAGE_MULTIPLE_SKU)
        self.assertEqual(behavior, BEHAVIOR_BOUNDARY)
        self.assertFalse(flags["model_called"])

    def test_the_message_is_registered_in_the_evaluator_set(self):
        self.assertIn(MESSAGE_MULTIPLE_SKU, BOUNDARY_MESSAGES)
        self.assertIn(MESSAGE_SYSTEM_LIMITED, BOUNDARY_MESSAGES)
        self.assertIn(MESSAGE_NO_SKU, BOUNDARY_MESSAGES)


class HoldoutVerbatimReuseTests(unittest.TestCase):
    """Guards against literal copying of Blind Audit V1 questions - nothing more.

    This check cannot show the suite was designed independently, and it cannot
    show the audit's feedback went unused: it demonstrably did, since these
    rules were written to fix what the audit found. Passing here means only
    that no question was reused word for word.
    """

    HOLDOUTS = (
        "eval_orchestrated_routes_holdout.json",
        "eval_answerability_holdout.json",
    )

    def test_no_regression_question_is_reused_verbatim(self):
        holdout_questions: set[str] = set()
        for name in self.HOLDOUTS:
            path = REPO_ROOT / name
            if not path.exists():  # pragma: no cover - dataset is optional here
                continue
            for case in json.loads(path.read_text(encoding="utf-8")):
                holdout_questions.add(case["question"].strip())

        local = (
            list(PURE_SOCIAL)
            + [q for q, _ in SOCIAL_PLUS_REQUEST]
            + [q for q, _ in SOCIAL_PLUS_UNKNOWN_REQUEST]
            + list(SOFT_MARKER_LIVE_LOOKUP)
            + [q for q, _ in SOFT_MARKER_STILL_OVERVIEW]
            + list(WIKI_OVERVIEW)
            + list(NEGATED_CITATION)
            + list(AFFIRMATIVE_CITATION)
            + list(BARE_SKU)
            + list(NOT_A_SKU)
            + list(WIKI_SYSTEM)
            + list(DOCUMENT_SYSTEM)
            + list(WIKI_DOCUMENT_SYSTEM)
            + list(POLICY_NOT_SYSTEM)
            + list(MULTI_SKU)
        )
        for question in local:
            with self.subTest(question=question):
                self.assertNotIn(question.strip(), holdout_questions)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
