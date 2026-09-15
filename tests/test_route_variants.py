"""M10 routing-behaviour tests: does a rule survive rephrasing?

Each table below states one claim about *meaning*, then varies the surface the
meaning arrives on - clause order, punctuation, an inserted aside, a different
topic or record, where the negation sits. A rule that only fires on the word
order it was written against is not a rule, and these tables are the way that
shows up.

Written against behaviour, not internals: nothing here imports a marker table
or asserts on a reason code, so re-implementing the same semantics differently
leaves the suite passing. `MarkerTableIndependenceTests` makes that explicit.

Sentences are fresh. `VerbatimReuseTests` checks they were not lifted from the
frozen regression sets; it says nothing about independence, since these rules
were written while looking at those sets' failures.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from orchestration.planner import Route, plan_request

REPO_ROOT = Path(__file__).resolve().parent.parent


def route_of(question: str) -> Route:
    return plan_request(question).route


# --------------------------------------------------------------------------
# 1. Clause order does not decide the route
# --------------------------------------------------------------------------
# Each group asks for the same things in a different order. A planner that
# unions per-clause signals returns one route for the whole group; one that
# lets the first clause win does not.

ORDER_INVARIANT_GROUPS = (
    (
        Route.WIKI_SYSTEM,
        (
            "先介绍一下差旅报销这块，再看下 SKU-A100 那边还剩多少",
            "先看下 SKU-A100 那边还剩多少，再介绍一下差旅报销这块",
            "SKU-A100 还剩多少？另外差旅报销这块也介绍一下",
        ),
    ),
    (
        Route.DOCUMENT_SYSTEM,
        (
            "设备保管责任由谁承担要准确的说法，顺便报一下 sku b200 的存量",
            "顺便报一下 sku b200 的存量，设备保管责任由谁承担要准确的说法",
            "sku b200 的存量报我；设备保管责任由谁承担，要准确的说法",
        ),
    ),
    (
        Route.WIKI_DOCUMENT_SYSTEM,
        (
            "培训管理先给个总览，考核合格线那句原话也发我，再看 SKU-C300 的存量",
            "先看 SKU-C300 的存量，培训管理给个总览，考核合格线那句原话也发我",
            "考核合格线那句原话发我；SKU-C300 存量报一下；培训管理给个总览",
        ),
    ),
)


# --------------------------------------------------------------------------
# 2. Punctuation is a separator, not a signal
# --------------------------------------------------------------------------

PUNCTUATION_VARIANT_GROUPS = (
    (
        Route.WIKI_DOCUMENT,
        (
            "保密义务整体讲讲，对外披露那条要准确措辞",
            "保密义务整体讲讲；对外披露那条要准确措辞",
            "保密义务整体讲讲。对外披露那条要准确措辞",
            "保密义务整体讲讲、对外披露那条要准确措辞",
            "保密义务整体讲讲？对外披露那条要准确措辞",
        ),
    ),
    (
        Route.DOCUMENT_SYSTEM,
        (
            "值班补贴标准要精确的，SKU-A100 现在的数量也报一下",
            "值班补贴标准要精确的；SKU-A100 现在的数量也报一下",
            "值班补贴标准要精确的。SKU-A100 现在的数量也报一下",
        ),
    ),
)


# --------------------------------------------------------------------------
# 3. An aside does not change what was asked
# --------------------------------------------------------------------------
# Greetings, narrative背景, urgency, and who-needs-it framing all arrive in
# front of real questions. None of them is a request.

BACKGROUND_INSERTION_GROUPS = (
    (
        Route.WIKI_ONLY,
        (
            "外派补助这块整体介绍一下",
            "你好，外派补助这块整体介绍一下",
            "我刚接手这条线，还不太熟，外派补助这块整体介绍一下",
            "下午要跟新同事过一遍，外派补助这块整体介绍一下，辛苦了",
        ),
    ),
    (
        Route.SYSTEM_ONLY,
        (
            "SKU-B200 目前还剩多少",
            "早上好，SKU-B200 目前还剩多少",
            "客户那边一直在催，我这边压力挺大的，SKU-B200 目前还剩多少",
            "不好意思打扰一下，SKU-B200 目前还剩多少，谢谢",
        ),
    ),
    (
        Route.DOCUMENT_ONLY,
        (
            "试用期考核标准的准确表述发我",
            "刚开完会，试用期考核标准的准确表述发我",
            "我要写进入职材料里，试用期考核标准的准确表述发我",
        ),
    ),
)


# --------------------------------------------------------------------------
# 4. The route follows the shape, not the topic or the record
# --------------------------------------------------------------------------

ENTITY_SUBSTITUTION_GROUPS = (
    (
        Route.WIKI_ONLY,
        (
            "食堂补贴这块的大方向是什么",
            "工装发放这块的大方向是什么",
            "内部转岗这块的大方向是什么",
        ),
    ),
    (
        Route.DOCUMENT_ONLY,
        (
            "夜班津贴的计算比例要准确的",
            "岗位津贴的计算比例要准确的",
            "外派津贴的计算比例要准确的",
        ),
    ),
    (
        Route.SYSTEM_ONLY,
        (
            "SKU-A100 那边还有货吗",
            "sku_b200 那边还有货吗",
            "Sku-C300 那边还有货吗",
            "sku c300 那边还有货吗",
        ),
    ),
)


# --------------------------------------------------------------------------
# 5. Negation scope: where the refusal sits changes what it refuses
# --------------------------------------------------------------------------

# The caller declines the source text. Chinese puts the refusal either in front
# of the verb that would fetch it, or after the object it fronted.
DECLINED_SOURCE_TEXT = (
    "值班安排整体讲讲，不用给条款",
    "值班安排整体讲讲，条款就不用给了",
    "值班安排整体讲讲，具体条款先别发我",
    "值班安排整体讲讲，原文不必附上",
    "值班安排整体讲讲，原文先别列了",
)

# The same words with the refusal aimed somewhere else. `别省略` asks for *more*
# of the source, and a refusal in a previous clause does not reach this one.
REQUESTED_SOURCE_TEXT = (
    "值班安排的条款发我，别省略",
    "值班安排别讲太宽泛。原文发我",
    "值班安排不用铺垫。条款原话发我",
)


# --------------------------------------------------------------------------
# 6. Sign-offs are social, and social messages need no evidence
# --------------------------------------------------------------------------

CLOSING_UTTERANCES = (
    "行，那我先去忙了，回头聊",
    "好的，今天就聊到这儿吧，明天再说",
    "收到，我这边先这样，改天说",
    "明白了，不打扰了，周末愉快",
    "谢谢，先这样，我准备下班了",
)

# A sign-off wrapped around a real request is not a sign-off.
CLOSING_PLUS_REQUEST = (
    ("行，我先去忙了，走之前把年假天数的准确说法发我", Route.DOCUMENT_ONLY),
    ("回头聊，不过 SKU-A100 现在还剩多少先告诉我", Route.SYSTEM_ONLY),
    ("今天就到这儿吧，明天前把考勤这块整体介绍一下", Route.WIKI_ONLY),
)


# --------------------------------------------------------------------------
# 7. Category rules, stated one per table
# --------------------------------------------------------------------------

# A demand for precision is a request for what the source actually says.
PRECISION_SELECTS_DOCUMENT = (
    "培训考勤的扣减比例，要准确的",
    "值班津贴那句我要一字不差的",
    "外派期限的确切天数是多少",
    "宿舍申请条件按原样给我",
)

# An interrogative about who approves is a question about the rule, not about
# any particular record.
AUTHORITY_SELECTS_DOCUMENT = (
    "单笔超过五千的采购由谁批准",
    "跨部门借调找谁签字",
    "延长试用期需要谁审核",
    "特批加班的审批人是哪一级",
)

# A duration or measure interrogative asks for a threshold the document sets.
DURATION_SELECTS_DOCUMENT = (
    "离职交接要留几个工作日",
    "工伤申报最迟几天内提交",
    "体检报告多久内交上来",
    "培训欠费要在多长时间内补齐",
)

# A record identifier plus a state cue is a live lookup, even with no object
# noun in the clause.
RECORD_ID_SELECTS_SYSTEM = (
    "ord-4102 现在什么状态",
    "apr-7788 的审批走完了没",
    "wo-20451 目前进展到哪一步了",
)

# An identifier with nothing asked about its state is not a lookup.
RECORD_ID_ALONE_IS_NOT_SYSTEM = (
    "ABC-123 这个编号是什么意思",
    "xsku-a100 属于哪一类",
    "SKU-A1000 这样写规范吗",
)

# Availability is the same question a count asks, phrased without a number.
AVAILABILITY_SELECTS_SYSTEM = (
    "库存那边还有货吗",
    "看一眼库存够不够",
    "系统里现在显示多少件",
)

# The same vocabulary inside a policy question stays on the document path.
POLICY_OUTRANKS_STATE_VOCABULARY = (
    "缺货补货的规定怎么写",
    "库存盘点周期制度是什么",
    "退货办法里对到货确认怎么要求",
    "余量预警的管理办法有哪些内容",
)

# Over-selection is a failure too: a plain overview must not collect three
# channels just because a policy noun appears in it.
OVERVIEW_STAYS_SINGLE_CHANNEL = (
    "接待管理的规定整体介绍一下",
    "值班制度大致讲讲",
    "档案借阅办法梳理一遍",
)


class ClauseOrderTests(unittest.TestCase):
    def test_reordering_the_same_requests_keeps_the_route(self):
        for expected, variants in ORDER_INVARIANT_GROUPS:
            for question in variants:
                with self.subTest(question=question):
                    self.assertEqual(route_of(question), expected)


class PunctuationTests(unittest.TestCase):
    def test_delimiter_choice_does_not_change_the_route(self):
        for expected, variants in PUNCTUATION_VARIANT_GROUPS:
            for question in variants:
                with self.subTest(question=question):
                    self.assertEqual(route_of(question), expected)


class BackgroundInsertionTests(unittest.TestCase):
    def test_narrative_context_does_not_change_the_route(self):
        for expected, variants in BACKGROUND_INSERTION_GROUPS:
            for question in variants:
                with self.subTest(question=question):
                    self.assertEqual(route_of(question), expected)


class EntitySubstitutionTests(unittest.TestCase):
    def test_swapping_the_subject_keeps_the_route(self):
        for expected, variants in ENTITY_SUBSTITUTION_GROUPS:
            for question in variants:
                with self.subTest(question=question):
                    self.assertEqual(route_of(question), expected)


class NegationScopeTests(unittest.TestCase):
    def test_a_declined_source_request_does_not_select_document(self):
        for question in DECLINED_SOURCE_TEXT:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_document)
                self.assertFalse(plan.signals.requires_exact_citation)
                self.assertEqual(plan.route, Route.WIKI_ONLY)

    def test_a_negation_aimed_elsewhere_leaves_the_request_standing(self):
        for question in REQUESTED_SOURCE_TEXT:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_document)
                self.assertTrue(plan.signals.requires_exact_citation)


class ClosingUtteranceTests(unittest.TestCase):
    def test_sign_offs_need_no_evidence(self):
        for question in CLOSING_UTTERANCES:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.is_direct)
                self.assertEqual(plan.route, Route.DIRECT)
                self.assertEqual(plan.steps, ())

    def test_a_sign_off_never_swallows_the_request_it_carries(self):
        for question, expected in CLOSING_PLUS_REQUEST:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.is_direct)
                self.assertEqual(plan.route, expected)


class DocumentCategoryTests(unittest.TestCase):
    def test_precision_demands_select_document(self):
        for question in PRECISION_SELECTS_DOCUMENT:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_document)
                self.assertFalse(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_approval_authority_questions_select_document(self):
        for question in AUTHORITY_SELECTS_DOCUMENT:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_document)
                self.assertFalse(plan.signals.needs_system)

    def test_duration_thresholds_select_document(self):
        for question in DURATION_SELECTS_DOCUMENT:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_document)
                self.assertFalse(plan.signals.needs_system)


class SystemCategoryTests(unittest.TestCase):
    def test_a_record_id_with_a_state_cue_selects_system(self):
        for question in RECORD_ID_SELECTS_SYSTEM:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_a_record_id_alone_does_not_select_system(self):
        for question in RECORD_ID_ALONE_IS_NOT_SYSTEM:
            with self.subTest(question=question):
                self.assertFalse(plan_request(question).signals.needs_system)

    def test_availability_questions_select_system(self):
        for question in AVAILABILITY_SELECTS_SYSTEM:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_policy_semantics_still_outrank_state_vocabulary(self):
        for question in POLICY_OUTRANKS_STATE_VOCABULARY:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_system)
                self.assertTrue(plan.signals.needs_document)


class OverSelectionTests(unittest.TestCase):
    """Selecting too much is as wrong as selecting too little."""

    def test_an_overview_request_stays_on_one_channel(self):
        for question in OVERVIEW_STAYS_SINGLE_CHANNEL:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertEqual(plan.route, Route.WIKI_ONLY)
                self.assertEqual(len(plan.steps), 1)

    def test_no_variant_in_this_suite_exceeds_the_step_limit(self):
        for question in _all_questions():
            with self.subTest(question=question):
                self.assertLessEqual(len(plan_request(question).steps), 3)

    def test_planning_is_pure_and_repeatable(self):
        for question in _all_questions():
            with self.subTest(question=question):
                first, second = plan_request(question), plan_request(question)
                self.assertEqual(first.route, second.route)
                self.assertEqual(first.steps, second.steps)
                self.assertFalse(first.fallback_used)


# --------------------------------------------------------------------------
# 8. Negation applies to every channel signal, not just the document ones
# --------------------------------------------------------------------------
# The first round only checked negation around source-text markers, so
# "I don't need an overview" still selected Wiki and "no need to explain the
# policy" still selected Document. These are fresh phrasings of that shape.

DECLINED_OVERVIEW = (
    "别给我综述了，把值班津贴那条的原文贴出来",
    "不用概述，直接给设备报修时限的准确条款",
    "无需梳理背景，只要考勤异常处理的原文措辞",
    "不必介绍整体情况，我要的是加班审批的确切表述",
)

DECLINED_POLICY_EXPLANATION = (
    "只报 ord-5501 现在的状态，不用讲订单管理办法",
    "我要 apr-6602 走到哪一步了，不必解释审批规则",
    "先看 wo-7703 的当前状态，别介绍工单制度",
)

# `总结` is a verb in a request and a noun in a document name. Only the verb
# asks for a compiled page.
SUMMARY_AS_VERB = (
    "总结一下差旅报销的要求",
    "帮我总结加班调休这块",
    "请总结信息安全的主要内容",
)

SUMMARY_AS_NOUN = (
    "实习总结要在几个工作日内交，按原文说",
    "年度总结的提交期限，准确条款发我",
    "项目总结归档要求的原样条文",
)

# Sign-offs that pair an acknowledgement, a "nothing further" and a farewell.
ACKNOWLEDGED_SIGN_OFFS = (
    "知道了，暂时没其他问题，回见",
    "清楚了，就这些，先到这吧",
    "了解了，没别的要问的了，改天聊",
    "明白，问完了，辛苦",
)

# Thanks plus a good wish - the commonest way a conversation actually ends.
# The table used to hold only the full `谢谢`/`多谢`/`感谢`, so a bare `谢啦`
# carried no social signal at all, and a wish (`祝你接下来一切顺利`) carried
# none either. A message made of nothing but those two went to document search.
THANKS_AND_WISHES = (
    "谢啦，祝你接下来一切顺利",
    "谢了，祝顺利",
    "多谢啦，周末愉快",
    "太感谢了，祝你一切顺利",
    "辛苦啦，早点休息",
    "费心了，保重",
    "谢谢啦，往后一切顺利",
    "有劳，祝你万事如意",
)

# The same pleasantries with a real request attached. Any one requesting clause
# has to survive: a wish must not swallow the question that follows it.
WISH_PLUS_REQUEST = (
    ("谢啦，请告诉我年假的具体天数", Route.DOCUMENT_ONLY),
    ("祝你一切顺利，顺便把 SKU-A100 的库存查一下", Route.SYSTEM_ONLY),
    ("辛苦啦，最后把差旅报销这块整体讲讲", Route.WIKI_ONLY),
    ("保重，走之前把值班津贴那条的原文发我", Route.DOCUMENT_ONLY),
)

# A refusal that arrives *after* the thing refused. The backward window cannot
# see it, and there is no verb of supply to negate - `免了` supplies nothing.
TRAILING_DISMISSAL = (
    "给我薪资疑问反馈时限的原始条款，制度概览就免了",
    "把加班审批的准确条款发我，整体介绍免了",
    "只要设备报修时限的原文，背景梳理跳过",
    "报销上限的原样条文就行，综述不用了",
)


class NegationCoversEveryChannelTests(unittest.TestCase):
    def test_a_declined_overview_does_not_select_wiki(self):
        for question in DECLINED_OVERVIEW:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_wiki)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_a_declined_policy_explanation_does_not_select_document(self):
        for question in DECLINED_POLICY_EXPLANATION:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_document)
                self.assertTrue(plan.signals.needs_system)
                self.assertEqual(plan.route, Route.SYSTEM_ONLY)


class SummaryWordSenseTests(unittest.TestCase):
    def test_the_verb_asks_for_a_compiled_page(self):
        for question in SUMMARY_AS_VERB:
            with self.subTest(question=question):
                self.assertTrue(plan_request(question).signals.needs_wiki)

    def test_the_noun_names_a_document_and_does_not(self):
        for question in SUMMARY_AS_NOUN:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_wiki)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)


class AcknowledgedSignOffTests(unittest.TestCase):
    def test_an_acknowledged_sign_off_needs_no_evidence(self):
        for question in ACKNOWLEDGED_SIGN_OFFS:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.is_direct)
                self.assertEqual(plan.route, Route.DIRECT)

    def test_the_same_words_around_a_real_question_still_ask(self):
        for question in (
            "知道了，那年假天数的原文再发我一次",
            "明白，不过 SKU-A100 现在还剩多少",
            "清楚了，最后确认下报销上限的准确数字",
        ):
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.is_direct)
                self.assertNotEqual(plan.route, Route.DIRECT)


class ThanksAndWishTests(unittest.TestCase):
    """Pleasantries ask for nothing, however they are spelled."""

    def test_thanks_and_wishes_need_no_evidence(self):
        for question in THANKS_AND_WISHES:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.is_direct)
                self.assertEqual(plan.route, Route.DIRECT)

    def test_a_wish_never_swallows_the_request_beside_it(self):
        for question, expected in WISH_PLUS_REQUEST:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.is_direct)
                self.assertEqual(plan.route, expected)

    def test_the_wish_may_move_without_changing_the_route(self):
        """Same two clauses, both orders: the request still decides."""
        for question in (
            "谢啦，请告诉我年假的具体天数",
            "请告诉我年假的具体天数，谢啦",
        ):
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.DOCUMENT_ONLY)

    def test_a_topic_word_inside_a_wish_is_still_a_wish(self):
        """`顺利` alone must not be read as pleasantry.

        A wish is recognised by whole phrases precisely so that a genuine
        question containing the same character keeps its route.
        """
        self.assertNotEqual(route_of("报销流程顺利吗？走到哪一步了"), Route.DIRECT)


class TrailingDismissalTests(unittest.TestCase):
    """A refusal placed after the thing refused still refuses it."""

    def test_a_declined_overview_does_not_add_the_wiki_step(self):
        for question in TRAILING_DISMISSAL:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.DOCUMENT_ONLY)

    def test_the_same_sentence_without_the_dismissal_keeps_the_overview(self):
        """The control: remove the four trailing characters and Wiki returns.

        Without this pair the test above would also pass if the overview signal
        had simply been deleted.
        """
        self.assertEqual(
            route_of("给我薪资疑问反馈时限的原始条款，也要制度概览"),
            Route.WIKI_DOCUMENT,
        )

    def test_a_dismissal_does_not_reach_across_a_clause_boundary(self):
        """`免了` cancels the overview in its own clause, not a later request."""
        self.assertEqual(
            route_of("制度概览就免了，不过请把考勤这块整体介绍一下"),
            Route.WIKI_ONLY,
        )

    def test_words_that_merely_contain_a_dismissal_are_not_dismissals(self):
        """`避免` contains `免了` and means the opposite."""
        self.assertEqual(
            route_of("为了避免了解偏差，请把考勤制度整体介绍一下"),
            Route.WIKI_ONLY,
        )


class MarkerTableIndependenceTests(unittest.TestCase):
    """This module must not restate the implementation it is checking.

    Read off the parsed import statements rather than the file text, so the
    check cannot be satisfied - or broken - by what the prose happens to name.
    """

    def test_only_the_public_planner_surface_is_imported(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "orchestration"
            ):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("orchestration")
                )
        self.assertEqual(imported, {"Route", "plan_request"})


class VerbatimReuseTests(unittest.TestCase):
    """No question here was copied out of a regression set.

    This rules out literal reuse and nothing else: the rules under test were
    written after reading those sets' failures, so the suite is not evidence of
    independence from them.
    """

    DATASETS = (
        "eval_orchestrated_routes_blind_v2.json",
        "eval_orchestrated_routes_dev.json",
        "eval_orchestrated_routes_holdout.json",
        "eval_orchestrated_routes_validation_v1.json",
        "eval_answerability_blind_v2.json",
        "eval_answerability_dev.json",
        "eval_answerability_holdout.json",
        "eval_answerability_validation_v1.json",
    )

    def test_no_dataset_question_is_reused_verbatim(self):
        known: set[str] = set()
        for name in self.DATASETS:
            path = REPO_ROOT / name
            if not path.exists():  # pragma: no cover - dataset is optional here
                continue
            for case in json.loads(path.read_text(encoding="utf-8")):
                known.add(case["question"].strip())

        for question in _all_questions():
            with self.subTest(question=question):
                self.assertNotIn(question.strip(), known)


def _all_questions() -> list[str]:
    questions: list[str] = []
    for groups in (
        ORDER_INVARIANT_GROUPS,
        PUNCTUATION_VARIANT_GROUPS,
        BACKGROUND_INSERTION_GROUPS,
        ENTITY_SUBSTITUTION_GROUPS,
    ):
        for _expected, variants in groups:
            questions.extend(variants)
    for table in (DECLINED_OVERVIEW, DECLINED_POLICY_EXPLANATION,
                  SUMMARY_AS_VERB, SUMMARY_AS_NOUN, ACKNOWLEDGED_SIGN_OFFS,
                  THANKS_AND_WISHES, TRAILING_DISMISSAL):
        questions.extend(table)
    questions.extend(question for question, _ in WISH_PLUS_REQUEST)
    questions.extend(DECLINED_SOURCE_TEXT)
    questions.extend(REQUESTED_SOURCE_TEXT)
    questions.extend(CLOSING_UTTERANCES)
    questions.extend(question for question, _ in CLOSING_PLUS_REQUEST)
    for table in (
        PRECISION_SELECTS_DOCUMENT,
        AUTHORITY_SELECTS_DOCUMENT,
        DURATION_SELECTS_DOCUMENT,
        RECORD_ID_SELECTS_SYSTEM,
        RECORD_ID_ALONE_IS_NOT_SYSTEM,
        AVAILABILITY_SELECTS_SYSTEM,
        POLICY_OUTRANKS_STATE_VOCABULARY,
        OVERVIEW_STAYS_SINGLE_CHANNEL,
    ):
        questions.extend(table)
    return questions


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
