import unittest
from pathlib import Path
from unittest.mock import Mock

from orchestration.planner import (
    CANONICAL_STEP_ORDER,
    MARKER_TABLES,
    REASON_DEFAULT_DOCUMENT,
    REASON_DIRECT_UTTERANCE,
    REASON_DOCUMENT_EXACT,
    REASON_DOCUMENT_POLICY_CONTEXT,
    REASON_DOCUMENT_VERSION_CHANGE,
    REASON_EXACT_CITATION_REQUESTED,
    REASON_FALLBACK_EMPTY,
    REASON_FALLBACK_EXCEPTION,
    REASON_FALLBACK_INVALID,
    REASON_FALLBACK_SELECTED,
    REASON_FRESHNESS_REQUESTED,
    REASON_SYSTEM_CURRENT_STATE,
    REASON_WIKI_OVERVIEW,
    ROUTE_BY_STEPS,
    Plan,
    RequestSignals,
    Route,
    ToolName,
    plan_request,
)

WIKI = ToolName.WIKI_QUERY
DOCUMENT = ToolName.DOCUMENT_SEARCH
SYSTEM = ToolName.SYSTEM_QUERY


class RouteTableTests(unittest.TestCase):
    CASES = (
        ("你好", Route.DIRECT),
        ("谢谢！", Route.DIRECT),
        ("你好，请问年假有几天？", Route.DOCUMENT_ONLY),
        ("这个制度大概讲什么", Route.WIKI_ONLY),
        ("介绍一下退款政策", Route.WIKI_ONLY),
        ("原文第三条具体怎么写", Route.DOCUMENT_ONLY),
        ("年假最多可以休多少天", Route.DOCUMENT_ONLY),
        ("最新公告是什么", Route.DOCUMENT_ONLY),
        ("我的订单现在什么状态", Route.SYSTEM_ONLY),
        ("当前库存还有多少", Route.SYSTEM_ONLY),
        ("订单管理制度是什么", Route.DOCUMENT_ONLY),
        ("现在的请假制度怎么规定", Route.DOCUMENT_ONLY),
        ("概述制度并引用关键条款", Route.WIKI_DOCUMENT),
        ("总结制度变化并分析影响", Route.WIKI_DOCUMENT),
        ("制度怎么规定，我当前是否符合", Route.DOCUMENT_SYSTEM),
        ("介绍审批流程，再看我的审批进度", Route.WIKI_SYSTEM),
        ("总结制度、引用条款并查询我当前审批状态", Route.WIKI_DOCUMENT_SYSTEM),
    )

    # Boundary probes around the System conjunction.
    SYSTEM_BOUNDARY_CASES = (
        ("帮我查一下账户余额", Route.SYSTEM_ONLY),
        ("账户余额是多少", Route.SYSTEM_ONLY),
        ("查询订单管理制度", Route.DOCUMENT_ONLY),
        ("现在的订单管理制度怎么规定", Route.DOCUMENT_ONLY),
        ("退款制度怎么规定，我的订单状态是什么", Route.DOCUMENT_SYSTEM),
        # `流程` is policy context; `进度`/`剩余` name a value, not its owner.
        ("查看审批流程", Route.DOCUMENT_ONLY),
        ("介绍审批流程", Route.WIKI_ONLY),
        ("介绍审批流程，再看我的审批进度", Route.WIKI_SYSTEM),
        ("审批进度规定怎么写", Route.DOCUMENT_ONLY),
        ("查看审批进度", Route.SYSTEM_ONLY),
        ("剩余库存管理办法", Route.DOCUMENT_ONLY),
    )

    def test_expected_routes(self):
        for question, expected in self.CASES:
            with self.subTest(question=question):
                self.assertEqual(plan_request(question).route, expected)

    def test_system_boundary_routes(self):
        for question, expected in self.SYSTEM_BOUNDARY_CASES:
            with self.subTest(question=question):
                self.assertEqual(plan_request(question).route, expected)

    def test_steps_always_match_the_route_matrix(self):
        for question, _ in self.CASES + self.SYSTEM_BOUNDARY_CASES:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertEqual(ROUTE_BY_STEPS[plan.steps], plan.route)

    def test_steps_follow_canonical_order(self):
        for question, _ in self.CASES + self.SYSTEM_BOUNDARY_CASES:
            with self.subTest(question=question):
                steps = plan_request(question).steps
                expected = [tool for tool in CANONICAL_STEP_ORDER if tool in steps]
                self.assertEqual(list(steps), expected)


class RouteMatrixTests(unittest.TestCase):
    def test_every_matrix_entry_is_reachable_and_canonical(self):
        expected = {
            (): Route.DIRECT,
            (WIKI,): Route.WIKI_ONLY,
            (DOCUMENT,): Route.DOCUMENT_ONLY,
            (SYSTEM,): Route.SYSTEM_ONLY,
            (WIKI, DOCUMENT): Route.WIKI_DOCUMENT,
            (WIKI, SYSTEM): Route.WIKI_SYSTEM,
            (DOCUMENT, SYSTEM): Route.DOCUMENT_SYSTEM,
            (WIKI, DOCUMENT, SYSTEM): Route.WIKI_DOCUMENT_SYSTEM,
        }
        self.assertEqual(ROUTE_BY_STEPS, expected)

    def test_matrix_covers_every_route_exactly_once(self):
        routes = list(ROUTE_BY_STEPS.values())
        self.assertEqual(sorted(routes, key=lambda r: r.value), sorted(Route, key=lambda r: r.value))
        self.assertEqual(len(routes), len(set(routes)))

    def test_canonical_order_is_wiki_document_system(self):
        self.assertEqual(CANONICAL_STEP_ORDER, (WIKI, DOCUMENT, SYSTEM))

    def test_enum_members_are_exactly_as_specified(self):
        self.assertEqual(
            {tool.value for tool in ToolName},
            {"wiki_query", "document_search", "system_query"},
        )
        self.assertEqual(
            {route.value for route in Route},
            {
                "direct",
                "wiki_only",
                "document_only",
                "system_only",
                "wiki_document",
                "wiki_system",
                "document_system",
                "wiki_document_system",
            },
        )


class PlanInvariantTests(unittest.TestCase):
    def test_rejects_more_than_three_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.WIKI_DOCUMENT_SYSTEM, steps=(WIKI, DOCUMENT, SYSTEM, WIKI))

    def test_rejects_duplicate_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.WIKI_DOCUMENT, steps=(WIKI, WIKI))

    def test_rejects_non_canonical_order(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.WIKI_DOCUMENT, steps=(DOCUMENT, WIKI))

    def test_rejects_route_that_does_not_match_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.WIKI_ONLY, steps=(DOCUMENT,))

    def test_rejects_direct_with_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.DIRECT, steps=(DOCUMENT,))

    def test_rejects_non_direct_without_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.DOCUMENT_ONLY, steps=())

    def test_rejects_non_tool_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.DOCUMENT_ONLY, steps=("document_search",))

    def test_rejects_list_steps(self):
        with self.assertRaises(ValueError):
            Plan(route=Route.DOCUMENT_ONLY, steps=[DOCUMENT])

    def test_rejects_empty_reason_codes(self):
        for codes in (("",), ("   ",), (None,)):
            with self.subTest(codes=codes):
                with self.assertRaises(ValueError):
                    Plan(route=Route.DOCUMENT_ONLY, steps=(DOCUMENT,), reason_codes=codes)

    def test_rejects_duplicate_reason_codes(self):
        with self.assertRaises(ValueError):
            Plan(
                route=Route.DOCUMENT_ONLY,
                steps=(DOCUMENT,),
                reason_codes=(REASON_DEFAULT_DOCUMENT, REASON_DEFAULT_DOCUMENT),
            )

    def test_rejects_non_boolean_fallback_used(self):
        for value in (1, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Plan(route=Route.DOCUMENT_ONLY, steps=(DOCUMENT,), fallback_used=value)

    def test_plan_is_frozen(self):
        plan = plan_request("年假最多可以休多少天")
        with self.assertRaises(Exception):
            plan.route = Route.DIRECT

    def test_signals_are_frozen(self):
        signals = RequestSignals()
        with self.assertRaises(Exception):
            signals.needs_wiki = True


class SerializationTests(unittest.TestCase):
    def test_request_signals_to_dict(self):
        self.assertEqual(
            RequestSignals(needs_document=True, requires_exact_citation=True).to_dict(),
            {
                "is_direct": False,
                "needs_wiki": False,
                "needs_document": True,
                "needs_system": False,
                "requires_freshness": False,
                "requires_exact_citation": True,
            },
        )

    def test_plan_to_dict_serializes_enums_as_strings_and_tuples_as_lists(self):
        plan = Plan(
            route=Route.WIKI_DOCUMENT,
            steps=(WIKI, DOCUMENT),
            signals=RequestSignals(needs_wiki=True, needs_document=True),
            reason_codes=(REASON_WIKI_OVERVIEW, REASON_DOCUMENT_EXACT),
            fallback_used=False,
        )

        payload = plan.to_dict()

        self.assertEqual(payload["route"], "wiki_document")
        self.assertNotIsInstance(payload["route"], Route)
        self.assertEqual(payload["steps"], ["wiki_query", "document_search"])
        self.assertIsInstance(payload["steps"], list)
        self.assertEqual(payload["reason_codes"], [REASON_WIKI_OVERVIEW, REASON_DOCUMENT_EXACT])
        self.assertIsInstance(payload["reason_codes"], list)
        self.assertEqual(payload["signals"], plan.signals.to_dict())
        self.assertIs(payload["fallback_used"], False)


class BlankInputTests(unittest.TestCase):
    def test_blank_question_raises_value_error(self):
        for blank in ("", "   ", "\n\t"):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    plan_request(blank)

    def test_blank_question_raises_before_fallback_is_called(self):
        fallback = Mock(return_value=(WIKI,))
        with self.assertRaises(ValueError):
            plan_request("   ", fallback=fallback)
        fallback.assert_not_called()


class SignalFlagTests(unittest.TestCase):
    def test_latest_document_is_not_a_freshness_request(self):
        # Stage 3: `最新` here names the newest published document, which the
        # knowledge base already is. Flagging freshness made the Evidence Policy
        # refuse, because document evidence carries no observation time.
        plan = plan_request("最新公告是什么")
        self.assertFalse(plan.signals.requires_freshness)
        self.assertFalse(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)
        self.assertNotIn(REASON_FRESHNESS_REQUESTED, plan.reason_codes)

    def test_exact_citation_for_quantity_question(self):
        plan = plan_request("年假最多可以休多少天")
        self.assertTrue(plan.signals.requires_exact_citation)
        self.assertIn(REASON_EXACT_CITATION_REQUESTED, plan.reason_codes)

    def test_current_system_value_does_not_request_exact_citation(self):
        plan = plan_request("当前库存还有多少")
        self.assertTrue(plan.signals.needs_system)
        self.assertFalse(plan.signals.needs_document)
        self.assertFalse(plan.signals.requires_exact_citation)
        self.assertTrue(plan.signals.requires_freshness)

    def test_policy_noun_alone_does_not_set_exact_citation(self):
        plan = plan_request("订单管理制度是什么")
        self.assertTrue(plan.signals.needs_document)
        self.assertFalse(plan.signals.requires_exact_citation)
        self.assertIn(REASON_DOCUMENT_POLICY_CONTEXT, plan.reason_codes)

    def test_overview_intent_keeps_policy_noun_as_context_only(self):
        plan = plan_request("介绍一下退款政策")
        self.assertTrue(plan.signals.needs_wiki)
        self.assertFalse(plan.signals.needs_document)
        self.assertNotIn(REASON_DOCUMENT_POLICY_CONTEXT, plan.reason_codes)

    def test_strong_exact_marker_still_adds_document_to_overview(self):
        plan = plan_request("概述制度并引用关键条款")
        self.assertTrue(plan.signals.needs_wiki)
        self.assertTrue(plan.signals.needs_document)
        self.assertIn(REASON_WIKI_OVERVIEW, plan.reason_codes)
        self.assertIn(REASON_DOCUMENT_EXACT, plan.reason_codes)

    def test_version_change_requires_wiki_and_document(self):
        plan = plan_request("总结制度变化并分析影响")
        self.assertEqual(plan.steps, (WIKI, DOCUMENT))
        self.assertIn(REASON_DOCUMENT_VERSION_CHANGE, plan.reason_codes)

    def test_system_needs_state_intent_and_object_together(self):
        self.assertFalse(plan_request("订单管理制度是什么").signals.needs_system)
        self.assertFalse(plan_request("现在的请假制度怎么规定").signals.needs_system)
        self.assertTrue(plan_request("我的订单现在什么状态").signals.needs_system)

    def test_direct_utterance_reports_direct_signal_only(self):
        plan = plan_request("你好")
        self.assertTrue(plan.signals.is_direct)
        self.assertEqual(plan.steps, ())
        self.assertEqual(plan.reason_codes, (REASON_DIRECT_UTTERANCE,))

    def test_greeting_prefix_is_not_a_direct_utterance(self):
        plan = plan_request("你好，请问年假有几天？")
        self.assertFalse(plan.signals.is_direct)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_reason_codes_are_unique_and_stably_ordered(self):
        for question, _ in RouteTableTests.CASES + RouteTableTests.SYSTEM_BOUNDARY_CASES:
            with self.subTest(question=question):
                codes = plan_request(question).reason_codes
                self.assertEqual(len(codes), len(set(codes)))
                self.assertEqual(codes, plan_request(question).reason_codes)


class SystemIntentTests(unittest.TestCase):
    """The three System intents are kept separate on purpose."""

    def test_query_verbs_cover_the_required_vocabulary(self):
        verbs = MARKER_TABLES["SYSTEM_QUERY_VERBS"]
        for required in ("查询", "查一下", "查看", "帮我查"):
            with self.subTest(verb=required):
                self.assertIn(required, verbs)

    def test_query_verb_plus_system_object_selects_system(self):
        for question in ("帮我查一下账户余额", "查看我的物流", "查询积分"):
            with self.subTest(question=question):
                self.assertTrue(plan_request(question).signals.needs_system)

    def test_query_verb_plus_policy_semantics_does_not_select_system(self):
        plan = plan_request("查询订单管理制度")
        self.assertFalse(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_time_marker_plus_object_plus_policy_does_not_select_system(self):
        plan = plan_request("现在的订单管理制度怎么规定")
        self.assertFalse(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)
        # Stage 3: the time word describes the policy, not a live value.
        self.assertFalse(plan.signals.requires_freshness)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_time_marker_plus_object_without_policy_selects_system(self):
        plan = plan_request("当前库存还有多少")
        self.assertTrue(plan.signals.needs_system)
        self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_personal_state_keeps_system_even_with_policy_semantics(self):
        plan = plan_request("退款制度怎么规定，我的订单状态是什么")
        self.assertTrue(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)
        self.assertEqual(plan.route, Route.DOCUMENT_SYSTEM)
        self.assertIn(REASON_SYSTEM_CURRENT_STATE, plan.reason_codes)

    def test_state_phrase_keeps_system_without_a_system_object(self):
        plan = plan_request("制度怎么规定，我当前是否符合")
        self.assertTrue(plan.signals.needs_system)
        self.assertEqual(plan.route, Route.DOCUMENT_SYSTEM)

    def test_value_question_on_a_system_object_selects_system(self):
        plan = plan_request("账户余额是多少")
        self.assertTrue(plan.signals.needs_system)
        self.assertFalse(plan.signals.needs_document)
        self.assertFalse(plan.signals.requires_exact_citation)
        self.assertEqual(plan.route, Route.SYSTEM_ONLY)

    def test_system_object_alone_is_not_enough(self):
        plan = plan_request("订单管理制度是什么")
        self.assertFalse(plan.signals.needs_system)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_state_intent_without_a_system_object_is_not_enough(self):
        plan = plan_request("现在的请假制度怎么规定")
        self.assertFalse(plan.signals.needs_system)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)

    def test_personal_markers_are_first_person_only(self):
        personal = MARKER_TABLES["PERSONAL_STATE_MARKERS"]
        self.assertEqual(set(personal), {"我的", "本人", "我当前"})
        for value_word in ("剩余", "进度", "到哪一步"):
            with self.subTest(value_word=value_word):
                self.assertNotIn(value_word, personal)
                self.assertIn(value_word, MARKER_TABLES["SYSTEM_VALUE_MARKERS"])

    def test_strong_state_phrase_is_retained(self):
        self.assertIn("到哪一步了", MARKER_TABLES["SYSTEM_STATE_PHRASES"])

    def test_value_word_plus_policy_semantics_stays_document(self):
        for question in ("审批进度规定怎么写", "剩余库存管理办法"):
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.needs_system)
                self.assertTrue(plan.signals.needs_document)

    def test_process_noun_is_policy_context(self):
        self.assertIn("流程", MARKER_TABLES["DOCUMENT_POLICY_MARKERS"])
        plan = plan_request("查看审批流程")
        self.assertFalse(plan.signals.needs_system)
        self.assertTrue(plan.signals.needs_document)

    def test_overview_still_suppresses_process_noun(self):
        plan = plan_request("介绍审批流程")
        self.assertTrue(plan.signals.needs_wiki)
        self.assertFalse(plan.signals.needs_document)
        self.assertFalse(plan.signals.needs_system)


class FallbackTests(unittest.TestCase):
    AMBIGUOUS = "帮我看看这个"

    def test_ambiguous_question_reaches_the_fallback(self):
        self.assertEqual(plan_request(self.AMBIGUOUS).steps, (DOCUMENT,))

    def test_deterministic_request_never_calls_fallback(self):
        fallback = Mock(return_value=(SYSTEM,))
        plan = plan_request("原文第三条具体怎么写", fallback=fallback)
        fallback.assert_not_called()
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertFalse(plan.fallback_used)

    def test_direct_request_never_calls_fallback(self):
        fallback = Mock(return_value=(SYSTEM,))
        plan = plan_request("你好", fallback=fallback)
        fallback.assert_not_called()
        self.assertEqual(plan.route, Route.DIRECT)
        self.assertFalse(plan.fallback_used)

    def test_no_fallback_defaults_to_document(self):
        plan = plan_request(self.AMBIGUOUS)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertFalse(plan.fallback_used)
        self.assertEqual(plan.reason_codes, (REASON_DEFAULT_DOCUMENT,))

    def test_fallback_called_once_with_the_original_question(self):
        question = "  帮我看看这个  "
        fallback = Mock(return_value=(SYSTEM,))
        plan = plan_request(question, fallback=fallback)
        fallback.assert_called_once_with(question)
        self.assertEqual(plan.route, Route.SYSTEM_ONLY)
        self.assertTrue(plan.fallback_used)
        self.assertIn(REASON_FALLBACK_SELECTED, plan.reason_codes)

    def test_fallback_order_is_canonicalized(self):
        plan = plan_request(self.AMBIGUOUS, fallback=lambda _: (SYSTEM, DOCUMENT, WIKI))
        self.assertEqual(plan.steps, (WIKI, DOCUMENT, SYSTEM))
        self.assertEqual(plan.route, Route.WIKI_DOCUMENT_SYSTEM)

    def test_fallback_signals_reflect_selected_tools(self):
        plan = plan_request(self.AMBIGUOUS, fallback=lambda _: (SYSTEM,))
        self.assertTrue(plan.signals.needs_system)
        self.assertFalse(plan.signals.needs_document)
        self.assertFalse(plan.signals.needs_wiki)

    def test_none_result_defaults_to_document(self):
        plan = plan_request(self.AMBIGUOUS, fallback=lambda _: None)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertTrue(plan.fallback_used)
        self.assertEqual(plan.reason_codes, (REASON_FALLBACK_EMPTY, REASON_DEFAULT_DOCUMENT))

    def test_empty_tuple_defaults_to_document(self):
        plan = plan_request(self.AMBIGUOUS, fallback=lambda _: ())
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertEqual(plan.reason_codes, (REASON_FALLBACK_EMPTY, REASON_DEFAULT_DOCUMENT))

    def test_invalid_results_default_to_document(self):
        invalid_results = (
            "document_search",
            ("document_search",),
            [DOCUMENT],
            (DOCUMENT, "wiki_query"),
            (object(),),
            iter((DOCUMENT,)),
            42,
            {DOCUMENT},
        )
        for raw in invalid_results:
            with self.subTest(raw=raw):
                plan = plan_request(self.AMBIGUOUS, fallback=lambda _, r=raw: r)
                self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
                self.assertTrue(plan.fallback_used)
                self.assertEqual(
                    plan.reason_codes, (REASON_FALLBACK_INVALID, REASON_DEFAULT_DOCUMENT)
                )

    def test_duplicate_fallback_items_default_to_document(self):
        plan = plan_request(self.AMBIGUOUS, fallback=lambda _: (DOCUMENT, DOCUMENT))
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertEqual(plan.reason_codes, (REASON_FALLBACK_INVALID, REASON_DEFAULT_DOCUMENT))

    def test_oversized_fallback_defaults_to_document(self):
        plan = plan_request(
            self.AMBIGUOUS, fallback=lambda _: (WIKI, DOCUMENT, SYSTEM, WIKI)
        )
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertEqual(plan.reason_codes, (REASON_FALLBACK_INVALID, REASON_DEFAULT_DOCUMENT))

    def test_fallback_exception_defaults_to_document(self):
        def boom(_question):
            raise RuntimeError("classifier exploded with secret detail")

        plan = plan_request(self.AMBIGUOUS, fallback=boom)
        self.assertEqual(plan.route, Route.DOCUMENT_ONLY)
        self.assertTrue(plan.fallback_used)
        self.assertEqual(
            plan.reason_codes, (REASON_FALLBACK_EXCEPTION, REASON_DEFAULT_DOCUMENT)
        )

    def test_exception_text_never_leaks_into_reason_codes(self):
        def boom(_question):
            raise RuntimeError("classifier exploded with secret detail")

        plan = plan_request(self.AMBIGUOUS, fallback=boom)
        joined = " ".join(plan.reason_codes)
        self.assertNotIn("secret", joined)
        self.assertNotIn("exploded", joined)
        self.assertNotIn("RuntimeError", joined)

    def test_fallback_can_never_select_direct(self):
        for raw in (None, (), "direct", Route.DIRECT):
            with self.subTest(raw=raw):
                plan = plan_request(self.AMBIGUOUS, fallback=lambda _, r=raw: r)
                self.assertNotEqual(plan.route, Route.DIRECT)
                self.assertEqual(plan.steps, (DOCUMENT,))


class MarkerHygieneTests(unittest.TestCase):
    STUDENT_DOMAIN_TERMS = (
        "学生", "学籍", "学分", "处分", "宿舍", "辅导员", "教务处", "奖学金",
        "班主任", "校区", "选课", "毕业设计", "军训",
    )

    def test_marker_tables_contain_no_student_domain_terms(self):
        for table_name, markers in MARKER_TABLES.items():
            for marker in markers:
                for term in self.STUDENT_DOMAIN_TERMS:
                    with self.subTest(table=table_name, marker=marker, term=term):
                        self.assertNotIn(term, marker)

    def test_marker_tables_are_non_empty_tuples_of_text(self):
        for table_name, markers in MARKER_TABLES.items():
            with self.subTest(table=table_name):
                self.assertIsInstance(markers, tuple)
                self.assertTrue(markers)
                for marker in markers:
                    self.assertIsInstance(marker, str)
                    self.assertTrue(marker.strip())

    def test_marker_tables_have_no_duplicates_within_a_table(self):
        for table_name, markers in MARKER_TABLES.items():
            with self.subTest(table=table_name):
                self.assertEqual(len(markers), len(set(markers)))


class PurityTests(unittest.TestCase):
    def test_planner_imports_only_the_standard_library(self):
        import orchestration.planner as planner_module

        source = Path(planner_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("import agent", "import api", "import rag", "import storage",
                          "import fastapi", "import requests"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()


class FreshnessScopeTests(unittest.TestCase):
    """Stage 3: a time word requests freshness only in a live-state clause."""

    def test_time_word_on_a_policy_does_not_request_freshness(self):
        for question in ("现行的远程办公规定每周能申请几天", "目前的报销流程需要谁审批",
                         "当前版本的保密条款怎么写的", "眼下这版制度对补卡的时限是多久"):
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertFalse(plan.signals.requires_freshness)
                self.assertFalse(plan.signals.needs_system)

    def test_time_word_that_is_not_about_currency(self):
        for question in ("试用期截至什么时候结束", "我现在就要报销，需要准备哪些材料",
                         "公司允许安装实时协作软件吗"):
            with self.subTest(question=question):
                self.assertFalse(plan_request(question).signals.requires_freshness)

    def test_live_value_with_a_time_word_requests_freshness(self):
        for question in ("SKU-A100 目前还剩多少", "今天 SKU-B200 的库存是多少",
                         "我最近一次的审批到哪一步了", "我的积分现在是多少"):
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertTrue(plan.signals.needs_system)
                self.assertTrue(plan.signals.requires_freshness)

    def test_live_only_time_words_never_select_system(self):
        plan = plan_request("我今天迟到了，按规定会扣款吗")
        self.assertFalse(plan.signals.needs_system)
        self.assertFalse(plan.signals.requires_freshness)

    def test_personal_remaining_balance_keeps_freshness_without_system(self):
        # `年假` is a policy noun, so routing stays on documents; the question is
        # still a live personal value, and keeping freshness makes it refuse
        # instead of answering a balance question from the policy text.
        plan = plan_request("截至目前我的年假还剩几天")
        self.assertFalse(plan.signals.needs_system)
        self.assertTrue(plan.signals.requires_freshness)

    def test_mixed_request_takes_freshness_from_the_live_clause(self):
        plan = plan_request("现行的库存管理办法怎么规定的？另外 SKU-A100 当前库存多少")
        self.assertTrue(plan.signals.needs_document)
        self.assertTrue(plan.signals.needs_system)
        self.assertTrue(plan.signals.requires_freshness)

    def test_policy_question_with_a_time_word_passes_the_freshness_check(self):
        from orchestration.contracts import Evidence, SourceType, ToolResult, ToolStatus
        from orchestration.evidence_policy import REASON_FRESHNESS_UNSUPPORTED, evaluate_evidence

        plan = plan_request("目前的制度里，每周最多远程办公几天？")
        evidence = Evidence(content="员工每周最多申请 2 天远程办公。", source_type=SourceType.DOCUMENT,
                            source="sample_company_rules.md", locator="chunk:5", authority=80)
        decision = evaluate_evidence(plan, {ToolName.DOCUMENT_SEARCH: ToolResult(
            tool_name=ToolName.DOCUMENT_SEARCH.value, status=ToolStatus.OK, evidence=(evidence,))})
        self.assertNotIn(REASON_FRESHNESS_UNSUPPORTED, decision.reason_codes)
