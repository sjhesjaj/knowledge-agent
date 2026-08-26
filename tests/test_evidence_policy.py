import copy
import json
import unittest
from pathlib import Path

from orchestration.contracts import Evidence, SourceType, ToolResult, ToolStatus
from orchestration.evidence_policy import (
    BLOCKING_REASON_CODES,
    FAILURE_EMPTY,
    FAILURE_ERROR,
    FAILURE_MISSING,
    REASON_CODE_ORDER,
    REASON_EMPTY_TOOL_RESULT,
    REASON_EVIDENCE_SUFFICIENT,
    REASON_EXACT_CITATION_MISSING_DOCUMENT,
    REASON_EXACT_CITATION_MISSING_LOCATOR,
    REASON_FACT_CONFLICT_RESOLVED,
    REASON_FACT_CONFLICT_UNRESOLVED,
    REASON_FRESHNESS_UNSUPPORTED,
    REASON_MISSING_TOOL_RESULT,
    REASON_PLAN_DIRECT,
    REASON_SYSTEM_EXCLUDED_FROM_POLICY,
    REASON_SYSTEM_MISSING_AUTHORITY_SCOPE,
    REASON_SYSTEM_MISSING_OBSERVED_AT,
    REASON_TOOL_ERROR,
    FactAssertion,
    FactResolution,
    FactScope,
    PolicyDecision,
    PolicyOutcome,
    ToolFailure,
    evaluate_evidence,
)
from orchestration.planner import Plan, RequestSignals, Route, ToolName

WIKI = ToolName.WIKI_QUERY
DOCUMENT = ToolName.DOCUMENT_SEARCH
SYSTEM = ToolName.SYSTEM_QUERY

MODULE_SOURCE = (
    Path(__file__).resolve().parent.parent / "orchestration" / "evidence_policy.py"
).read_text(encoding="utf-8")

ROUTE_FOR_STEPS = {
    (WIKI,): Route.WIKI_ONLY,
    (DOCUMENT,): Route.DOCUMENT_ONLY,
    (SYSTEM,): Route.SYSTEM_ONLY,
    (WIKI, DOCUMENT): Route.WIKI_DOCUMENT,
    (WIKI, SYSTEM): Route.WIKI_SYSTEM,
    (DOCUMENT, SYSTEM): Route.DOCUMENT_SYSTEM,
    (WIKI, DOCUMENT, SYSTEM): Route.WIKI_DOCUMENT_SYSTEM,
}


def wiki_evidence(**overrides) -> Evidence:
    defaults = {
        "content": "年假概述",
        "source_type": SourceType.WIKI,
        "source": "sample_company_rules.md",
        "locator": "section:请假制度",
        "version": "1.0",
        "authority": 60,
    }
    defaults.update(overrides)
    return Evidence(**defaults)


def document_evidence(**overrides) -> Evidence:
    defaults = {
        "content": "正式员工每年享有 5 天带薪年假。",
        "source_type": SourceType.DOCUMENT,
        "source": "sample_company_rules.md",
        "locator": "chunk:12",
        "authority": 80,
    }
    defaults.update(overrides)
    return Evidence(**defaults)


def system_evidence(**overrides) -> Evidence:
    defaults = {
        "content": "订单 ord-1001 的当前状态为 已发货。",
        "source_type": SourceType.SYSTEM,
        "source": "demo-business-system",
        "locator": "orders:ord-1001",
        "observed_at": "2026-08-20T09:15:00+08:00",
        "authority": 100,
        "metadata": {"authority_scope": "current_operational_state"},
    }
    defaults.update(overrides)
    return Evidence(**defaults)


EVIDENCE_FACTORY = {
    WIKI: wiki_evidence,
    DOCUMENT: document_evidence,
    SYSTEM: system_evidence,
}


def ok_result(tool: ToolName, *evidence: Evidence) -> ToolResult:
    items = evidence or (EVIDENCE_FACTORY[tool](),)
    return ToolResult(tool_name=tool.value, status=ToolStatus.OK, evidence=items)


def empty_result(tool: ToolName) -> ToolResult:
    return ToolResult(tool_name=tool.value, status=ToolStatus.EMPTY)


def error_result(tool: ToolName, code="E_UNIQ_4711", message="unique-failure-text-4711"):
    return ToolResult(
        tool_name=tool.value,
        status=ToolStatus.ERROR,
        error_code=code,
        error_message=message,
    )


def make_plan(*steps: ToolName, **signal_overrides) -> Plan:
    route = Route.DIRECT if not steps else ROUTE_FOR_STEPS[steps]
    return Plan(
        route=route,
        steps=steps,
        signals=RequestSignals(
            needs_wiki=WIKI in steps,
            needs_document=DOCUMENT in steps,
            needs_system=SYSTEM in steps,
            **signal_overrides,
        ),
        reason_codes=("test",),
    )


def satisfied(*steps: ToolName, **signal_overrides):
    plan = make_plan(*steps, **signal_overrides)
    return plan, {tool: ok_result(tool) for tool in steps}


def assertion(evidence, scope, subject="年假", fact_key="天数", fact_value="5"):
    return FactAssertion(
        evidence=evidence,
        scope=scope,
        subject=subject,
        fact_key=fact_key,
        fact_value=fact_value,
    )


class RouteSufficiencyTests(unittest.TestCase):
    def test_every_non_direct_route_can_be_ready(self):
        for steps in ROUTE_FOR_STEPS:
            with self.subTest(steps=[s.value for s in steps]):
                plan, results = satisfied(*steps)
                decision = evaluate_evidence(plan, results)
                self.assertEqual(decision.outcome, PolicyOutcome.READY)
                self.assertEqual(
                    [e.source_type for e in decision.usable_evidence],
                    [results[tool].evidence[0].source_type for tool in steps],
                )
                self.assertIn(REASON_EVIDENCE_SUFFICIENT, decision.reason_codes)
                self.assertEqual(decision.missing_tools, ())

    def test_usable_evidence_follows_plan_and_result_order(self):
        first, second = document_evidence(content="A"), document_evidence(content="B")
        plan = make_plan(WIKI, DOCUMENT)
        results = {
            WIKI: ok_result(WIKI),
            DOCUMENT: ok_result(DOCUMENT, first, second),
        }
        decision = evaluate_evidence(plan, results)
        self.assertEqual(
            list(decision.usable_evidence),
            [results[WIKI].evidence[0], first, second],
        )

    def test_direct_plan(self):
        decision = evaluate_evidence(make_plan(), {})
        self.assertEqual(decision.outcome, PolicyOutcome.DIRECT)
        self.assertEqual(decision.usable_evidence, ())
        self.assertEqual(decision.reason_codes, (REASON_PLAN_DIRECT,))

    def test_direct_plan_rejects_any_result(self):
        with self.assertRaises(ValueError):
            evaluate_evidence(make_plan(), {DOCUMENT: ok_result(DOCUMENT)})

    def test_missing_step(self):
        plan = make_plan(WIKI, DOCUMENT)
        decision = evaluate_evidence(plan, {WIKI: ok_result(WIKI)})
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertEqual(decision.missing_tools, (DOCUMENT,))
        self.assertEqual(decision.tool_failures[0].kind, FAILURE_MISSING)
        self.assertIn(REASON_MISSING_TOOL_RESULT, decision.reason_codes)

    def test_empty_step(self):
        plan = make_plan(DOCUMENT)
        decision = evaluate_evidence(plan, {DOCUMENT: empty_result(DOCUMENT)})
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertEqual(decision.tool_failures[0].kind, FAILURE_EMPTY)
        self.assertIn(REASON_EMPTY_TOOL_RESULT, decision.reason_codes)

    def test_error_step_never_leaks_payload(self):
        plan = make_plan(DOCUMENT)
        decision = evaluate_evidence(plan, {DOCUMENT: error_result(DOCUMENT)})
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertEqual(decision.tool_failures[0].kind, FAILURE_ERROR)
        self.assertIn(REASON_TOOL_ERROR, decision.reason_codes)

        rendered = repr(decision) + json.dumps(decision.to_dict(), ensure_ascii=False)
        for secret in ("E_UNIQ_4711", "unique-failure-text-4711"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, rendered)
                self.assertNotIn(secret, " ".join(decision.reason_codes))
        self.assertEqual(
            set(decision.tool_failures[0].to_dict()), {"tool", "kind"}
        )

    def test_wrong_source_type_is_an_integration_error(self):
        plan = make_plan(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT, wiki_evidence())}
        with self.assertRaises(ValueError):
            evaluate_evidence(plan, results)

    def test_unplanned_result_raises(self):
        plan = make_plan(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT), SYSTEM: ok_result(SYSTEM)}
        with self.assertRaises(ValueError):
            evaluate_evidence(plan, results)

    def test_tool_name_mismatch_raises(self):
        plan = make_plan(DOCUMENT)
        mismatched = ToolResult(
            tool_name="wiki_query",
            status=ToolStatus.OK,
            evidence=(document_evidence(),),
        )
        with self.assertRaises(ValueError):
            evaluate_evidence(plan, {DOCUMENT: mismatched})

    def test_multiple_failures_follow_plan_step_order(self):
        plan = make_plan(WIKI, DOCUMENT, SYSTEM)
        results = {
            DOCUMENT: error_result(DOCUMENT),
            SYSTEM: empty_result(SYSTEM),
        }
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.missing_tools, (WIKI, DOCUMENT, SYSTEM))
        self.assertEqual(
            [(f.tool, f.kind) for f in decision.tool_failures],
            [
                (WIKI, FAILURE_MISSING),
                (DOCUMENT, FAILURE_ERROR),
                (SYSTEM, FAILURE_EMPTY),
            ],
        )

    def test_no_path_substitutes_for_another(self):
        plan = make_plan(WIKI, DOCUMENT)
        results = {
            WIKI: ok_result(WIKI, wiki_evidence(), wiki_evidence(content="更多")),
            DOCUMENT: empty_result(DOCUMENT),
        }
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertEqual(decision.missing_tools, (DOCUMENT,))


class MarkerRepr:
    def __repr__(self):
        return "REPR-MARKER-9182"


class InputTypeTests(unittest.TestCase):
    def assert_value_error(self, plan, results, **kwargs):
        try:
            evaluate_evidence(plan, results, **kwargs)
        except ValueError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - the point of the test
            self.fail("expected ValueError, got " + type(exc).__name__ + ": " + str(exc))
        self.fail("expected ValueError")

    def test_bad_result_keys(self):
        plan = make_plan(WIKI)
        for key in ("wiki_query", 1, None, (1, 2)):
            with self.subTest(key=key):
                self.assert_value_error(plan, {key: ok_result(WIKI)})

    def test_bad_result_values(self):
        plan = make_plan(WIKI)
        for value in (object(), None, {}, "result", 7):
            with self.subTest(value=type(value).__name__):
                self.assert_value_error(plan, {WIKI: value})

    def test_bad_top_level_types(self):
        plan = make_plan(WIKI)
        self.assert_value_error("not-a-plan", {})
        self.assert_value_error(plan, [(WIKI, ok_result(WIKI))])
        self.assert_value_error(plan, {WIKI: ok_result(WIKI)}, assertions=[])
        self.assert_value_error(
            plan, {WIKI: ok_result(WIKI)}, assertions=("not-an-assertion",)
        )

    def test_message_does_not_render_the_offending_value(self):
        plan = make_plan(WIKI)
        message = self.assert_value_error(plan, {WIKI: MarkerRepr()})
        self.assertNotIn("REPR-MARKER-9182", message)

    def test_unrecognised_status_is_never_treated_as_ok(self):
        # ToolResult routes an unknown status into its error branch, so this
        # object is constructible; it must not slip through as usable evidence.
        bogus = ToolResult(
            tool_name="document_search",
            status="bogus",
            error_code="E",
            error_message="M",
        )
        self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: bogus})

    def test_ok_emptied_after_construction_is_rejected(self):
        result = ok_result(DOCUMENT)
        result.evidence = ()  # ToolResult is not frozen
        self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})

    def test_failed_status_keeping_evidence_is_rejected(self):
        # A mutated result must not smuggle evidence off a failed step into
        # fact resolution.
        for status in (ToolStatus.EMPTY, ToolStatus.ERROR):
            with self.subTest(status=status.value):
                result = ok_result(DOCUMENT)
                result.status = status
                self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})

    def test_ok_carrying_error_fields_is_rejected_without_leaking_them(self):
        result = ok_result(DOCUMENT)
        result.error_code = "E_SECRET_5150"
        result.error_message = "secret-message-5150"
        message = self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})
        self.assertNotIn("E_SECRET_5150", message)
        self.assertNotIn("secret-message-5150", message)

    def test_empty_carrying_error_fields_is_rejected(self):
        result = empty_result(DOCUMENT)
        result.error_code = "E_SECRET_5151"
        result.error_message = "secret-message-5151"
        message = self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})
        self.assertNotIn("E_SECRET_5151", message)
        self.assertNotIn("secret-message-5151", message)

    def test_error_with_missing_or_non_string_fields_is_rejected(self):
        for code, text in (
            (None, "M"),
            ("E", None),
            ("", "M"),
            ("E", "   "),
            (1, "M"),
            ("E", 2),
        ):
            with self.subTest(code=type(code).__name__, text=type(text).__name__):
                result = error_result(DOCUMENT)
                result.error_code = code
                result.error_message = text
                self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})

    def test_assertions_may_only_reference_evidence_from_ok_results(self):
        doc = document_evidence()
        result = ok_result(DOCUMENT, doc)
        plan = make_plan(WIKI, DOCUMENT)
        results = {WIKI: ok_result(WIKI), DOCUMENT: result}
        # Sanity: the assertion is accepted while the step is genuinely OK.
        evaluate_evidence(
            plan, results, assertions=(assertion(doc, FactScope.POLICY),)
        )
        result.status = ToolStatus.EMPTY
        self.assert_value_error(
            plan, results, assertions=(assertion(doc, FactScope.POLICY),)
        )

    def test_evidence_container_must_be_a_tuple(self):
        item = document_evidence()
        # A set of Evidence is not constructible: Evidence is a non-frozen
        # dataclass with eq=True, so it is unhashable.
        containers = {
            "list": [item],
            "generator": (e for e in (item,)),
            "iterator": iter((item,)),
            "string": "evidence",
            "none": None,
        }
        for name, container in containers.items():
            with self.subTest(container=name):
                result = ok_result(DOCUMENT)
                result.evidence = container
                self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})

    def test_generator_evidence_never_yields_an_empty_ready(self):
        result = ok_result(DOCUMENT)
        result.evidence = (e for e in (document_evidence(),))
        with self.assertRaises(ValueError):
            decision = evaluate_evidence(make_plan(DOCUMENT), {DOCUMENT: result})
            self.fail(
                "generator evidence produced " + decision.outcome.value
                + " with " + str(len(decision.usable_evidence)) + " evidence items"
            )

    def test_mutated_evidence_fields_raise_value_error(self):
        cases = {
            "content_blank": ("content", "   "),
            "content_empty": ("content", ""),
            "content_int": ("content", 1),
            "source_int": ("source", 1),
            "source_blank": ("source", " "),
            "source_none": ("source", None),
            "authority_str": ("authority", "80"),
            "authority_bool": ("authority", True),
            "authority_high": ("authority", 999),
            "authority_negative": ("authority", -1),
            "authority_none": ("authority", None),
            "confidence_str": ("confidence", "high"),
            "confidence_high": ("confidence", 1.5),
            "confidence_negative": ("confidence", -0.1),
            "confidence_bool": ("confidence", True),
        }
        for name, (attribute, value) in cases.items():
            with self.subTest(case=name):
                item = document_evidence()
                setattr(item, attribute, value)  # Evidence is not frozen
                result = ok_result(DOCUMENT)
                result.evidence = (item,)
                self.assert_value_error(make_plan(DOCUMENT), {DOCUMENT: result})

    def test_malformed_nested_evidence_raises_value_error(self):
        plan = make_plan(DOCUMENT)
        probes = {
            "non_evidence_element": ok_result(DOCUMENT, document_evidence()),
            "source_type_str": ok_result(DOCUMENT, document_evidence()),
            "metadata_none": ok_result(SYSTEM, system_evidence()),
            "observed_at_int": ok_result(SYSTEM, system_evidence()),
            "version_int": ok_result(DOCUMENT, document_evidence()),
            "locator_int": ok_result(DOCUMENT, document_evidence()),
        }
        probes["non_evidence_element"].evidence = (object(),)
        probes["source_type_str"].evidence = (
            document_evidence(source_type="document"),
        )
        probes["metadata_none"].evidence = (system_evidence(metadata=None),)
        probes["observed_at_int"].evidence = (system_evidence(observed_at=1),)
        probes["version_int"].evidence = (document_evidence(version=1),)
        probes["locator_int"].evidence = (document_evidence(locator=1),)

        for name, result in probes.items():
            with self.subTest(probe=name):
                tool = DOCUMENT if result.tool_name == DOCUMENT.value else SYSTEM
                self.assert_value_error(
                    make_plan(tool) if tool is DOCUMENT else make_plan(SYSTEM),
                    {tool: result},
                )
        self.assertEqual(plan.steps, (DOCUMENT,))


class FreshnessTests(unittest.TestCase):
    def test_system_evidence_must_carry_observed_at(self):
        plan = make_plan(SYSTEM)
        results = {SYSTEM: ok_result(SYSTEM, system_evidence(observed_at=None))}
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertIn(REASON_SYSTEM_MISSING_OBSERVED_AT, decision.reason_codes)

    def test_system_evidence_must_carry_authority_scope(self):
        plan = make_plan(SYSTEM)
        for metadata in ({}, {"authority_scope": "policy"}):
            with self.subTest(metadata=metadata):
                results = {
                    SYSTEM: ok_result(SYSTEM, system_evidence(metadata=metadata))
                }
                decision = evaluate_evidence(plan, results)
                self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
                self.assertIn(
                    REASON_SYSTEM_MISSING_AUTHORITY_SCOPE, decision.reason_codes
                )

    def test_freshness_with_document_version_or_observed_at(self):
        for kwargs in ({"version": "2026版"}, {"observed_at": "2026-08-20T00:00:00Z"}):
            with self.subTest(kwargs=sorted(kwargs)):
                plan = make_plan(DOCUMENT, requires_freshness=True)
                results = {DOCUMENT: ok_result(DOCUMENT, document_evidence(**kwargs))}
                self.assertEqual(
                    evaluate_evidence(plan, results).outcome, PolicyOutcome.READY
                )

    def test_freshness_without_traceable_document_refuses(self):
        plan = make_plan(DOCUMENT, requires_freshness=True)
        results = {DOCUMENT: ok_result(DOCUMENT, document_evidence())}
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertIn(REASON_FRESHNESS_UNSUPPORTED, decision.reason_codes)

    def test_freshness_with_wiki_only_refuses(self):
        plan = make_plan(WIKI, requires_freshness=True)
        decision = evaluate_evidence(plan, {WIKI: ok_result(WIKI)})
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertIn(REASON_FRESHNESS_UNSUPPORTED, decision.reason_codes)

    def test_freshness_with_wiki_and_system_passes(self):
        plan, results = satisfied(WIKI, SYSTEM, requires_freshness=True)
        self.assertEqual(
            evaluate_evidence(plan, results).outcome, PolicyOutcome.READY
        )

    def test_freshness_with_wiki_and_document_passes(self):
        plan = make_plan(WIKI, DOCUMENT, requires_freshness=True)
        results = {
            WIKI: ok_result(WIKI),
            DOCUMENT: ok_result(DOCUMENT, document_evidence(version="2026版")),
        }
        self.assertEqual(
            evaluate_evidence(plan, results).outcome, PolicyOutcome.READY
        )


class ExactCitationTests(unittest.TestCase):
    def test_document_with_source_and_locator_passes(self):
        plan, results = satisfied(DOCUMENT, requires_exact_citation=True)
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.outcome, PolicyOutcome.READY)
        self.assertEqual(
            decision.exact_citation_evidence, results[DOCUMENT].evidence
        )

    def test_plan_without_document_refuses(self):
        for steps in ((WIKI,), (SYSTEM,), (WIKI, SYSTEM)):
            with self.subTest(steps=[s.value for s in steps]):
                plan, results = satisfied(*steps, requires_exact_citation=True)
                decision = evaluate_evidence(plan, results)
                self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
                self.assertIn(
                    REASON_EXACT_CITATION_MISSING_DOCUMENT, decision.reason_codes
                )
                self.assertEqual(decision.exact_citation_evidence, ())

    def test_document_without_locator_refuses(self):
        plan = make_plan(DOCUMENT, requires_exact_citation=True)
        results = {DOCUMENT: ok_result(DOCUMENT, document_evidence(locator=None))}
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertIn(REASON_EXACT_CITATION_MISSING_LOCATOR, decision.reason_codes)

    def test_exact_citation_evidence_is_conditional(self):
        plan, results = satisfied(DOCUMENT)
        decision = evaluate_evidence(plan, results)
        self.assertFalse(plan.signals.requires_exact_citation)
        self.assertEqual(decision.exact_citation_evidence, ())

    def test_only_qualifying_document_evidence_is_authorized(self):
        good = document_evidence(content="有 locator")
        bad = document_evidence(content="无 locator", locator=None)
        plan = make_plan(DOCUMENT, requires_exact_citation=True)
        results = {DOCUMENT: ok_result(DOCUMENT, bad, good)}
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.exact_citation_evidence, (good,))


class ConflictTests(unittest.TestCase):
    def state_plan(self, *evidence):
        plan = make_plan(DOCUMENT, SYSTEM)
        results = {
            DOCUMENT: ok_result(DOCUMENT, *[e for e in evidence if e.source_type is SourceType.DOCUMENT]),
            SYSTEM: ok_result(SYSTEM, *[e for e in evidence if e.source_type is SourceType.SYSTEM]),
        }
        return plan, results

    def assert_partition(self, resolution, expected):
        buckets = (
            resolution.winning_assertions
            + resolution.contending_assertions
            + resolution.superseded_assertions
            + resolution.ignored_assertions
        )
        self.assertEqual(len(buckets), len(expected))
        self.assertEqual({id(a) for a in buckets}, {id(a) for a in expected})

    def test_no_assertions(self):
        plan, results = satisfied(DOCUMENT)
        decision = evaluate_evidence(plan, results)
        self.assertEqual(decision.resolutions, ())
        self.assertEqual(decision.outcome, PolicyOutcome.READY)

    def test_single_value_group_is_corroboration(self):
        doc = document_evidence()
        plan, results = satisfied(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT, doc)}
        a1 = assertion(doc, FactScope.POLICY)
        decision = evaluate_evidence(plan, results, assertions=(a1,))
        resolution = decision.resolutions[0]
        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.winning_value, "5")
        self.assertEqual(resolution.winning_assertions, (a1,))
        self.assertNotIn(REASON_FACT_CONFLICT_RESOLVED, decision.reason_codes)
        self.assert_partition(resolution, (a1,))

    def test_system_resolves_a_current_state_conflict(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.CURRENT_OPERATIONAL_STATE, fact_value="旧")
        a_sys = assertion(sysev, FactScope.CURRENT_OPERATIONAL_STATE, fact_value="新")
        decision = evaluate_evidence(plan, results, assertions=(a_doc, a_sys))
        resolution = decision.resolutions[0]
        self.assertEqual(decision.outcome, PolicyOutcome.READY)
        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.winning_value, "新")
        self.assertEqual(resolution.winning_assertions, (a_sys,))
        self.assertEqual(resolution.superseded_assertions, (a_doc,))
        self.assertIn(REASON_FACT_CONFLICT_RESOLVED, decision.reason_codes)
        self.assert_partition(resolution, (a_doc, a_sys))

    def test_system_cannot_resolve_a_policy_conflict(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.POLICY, fact_value="5")
        a_sys = assertion(sysev, FactScope.POLICY, fact_value="8")
        decision = evaluate_evidence(plan, results, assertions=(a_doc, a_sys))
        resolution = decision.resolutions[0]
        self.assertEqual(decision.outcome, PolicyOutcome.READY)
        self.assertEqual(resolution.winning_assertions, (a_doc,))
        self.assertEqual(resolution.ignored_assertions, (a_sys,))
        self.assertIn(REASON_SYSTEM_EXCLUDED_FROM_POLICY, decision.reason_codes)
        self.assert_partition(resolution, (a_doc, a_sys))

    def test_policy_group_of_only_system_refuses(self):
        sysev = system_evidence()
        plan = make_plan(SYSTEM)
        results = {SYSTEM: ok_result(SYSTEM, sysev)}
        a_sys = assertion(sysev, FactScope.POLICY)
        decision = evaluate_evidence(plan, results, assertions=(a_sys,))
        resolution = decision.resolutions[0]
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertFalse(resolution.resolved)
        self.assertIsNone(resolution.winning_value)
        self.assertEqual(resolution.ignored_assertions, (a_sys,))
        self.assertEqual(resolution.contending_assertions, ())
        self.assertIn(REASON_FACT_CONFLICT_UNRESOLVED, decision.reason_codes)

    def test_resolution_reads_only_authority(self):
        strong_wiki = wiki_evidence(authority=80)
        weak_doc = document_evidence(authority=60)
        plan = make_plan(WIKI, DOCUMENT)
        results = {
            WIKI: ok_result(WIKI, strong_wiki),
            DOCUMENT: ok_result(DOCUMENT, weak_doc),
        }
        a_wiki = assertion(strong_wiki, FactScope.POLICY, fact_value="wiki-wins")
        a_doc = assertion(weak_doc, FactScope.POLICY, fact_value="doc-loses")
        resolution = evaluate_evidence(
            plan, results, assertions=(a_doc, a_wiki)
        ).resolutions[0]
        self.assertEqual(resolution.winning_value, "wiki-wins")
        self.assertEqual(resolution.superseded_assertions, (a_doc,))

    def test_corroborating_lower_authority_wins(self):
        doc = document_evidence()
        wiki_a = wiki_evidence(content="同值")
        wiki_b = wiki_evidence(content="异值")
        plan = make_plan(WIKI, DOCUMENT)
        results = {
            WIKI: ok_result(WIKI, wiki_a, wiki_b),
            DOCUMENT: ok_result(DOCUMENT, doc),
        }
        a_doc = assertion(doc, FactScope.POLICY, fact_value="A")
        a_w1 = assertion(wiki_a, FactScope.POLICY, fact_value="A")
        a_w2 = assertion(wiki_b, FactScope.POLICY, fact_value="B")
        resolution = evaluate_evidence(
            plan, results, assertions=(a_doc, a_w1, a_w2)
        ).resolutions[0]
        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.winning_value, "A")
        self.assertEqual({id(a) for a in resolution.winning_assertions}, {id(a_doc), id(a_w1)})
        self.assertEqual(resolution.superseded_assertions, (a_w2,))
        self.assertEqual(resolution.contending_assertions, ())
        self.assert_partition(resolution, (a_doc, a_w1, a_w2))

    def test_lower_authority_takes_no_part_in_an_unresolved_top_tier(self):
        doc_a, doc_b = document_evidence(content="A"), document_evidence(content="B")
        wiki_a, wiki_c = wiki_evidence(content="A"), wiki_evidence(content="C")
        plan = make_plan(WIKI, DOCUMENT)
        results = {
            WIKI: ok_result(WIKI, wiki_a, wiki_c),
            DOCUMENT: ok_result(DOCUMENT, doc_a, doc_b),
        }
        a_d1 = assertion(doc_a, FactScope.POLICY, fact_value="A")
        a_d2 = assertion(doc_b, FactScope.POLICY, fact_value="B")
        a_w1 = assertion(wiki_a, FactScope.POLICY, fact_value="A")
        a_w2 = assertion(wiki_c, FactScope.POLICY, fact_value="C")
        decision = evaluate_evidence(
            plan, results, assertions=(a_d1, a_d2, a_w1, a_w2)
        )
        resolution = decision.resolutions[0]
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertFalse(resolution.resolved)
        self.assertIsNone(resolution.winning_value)
        self.assertEqual(resolution.winning_assertions, ())
        self.assertEqual(
            {id(a) for a in resolution.contending_assertions}, {id(a_d1), id(a_d2)}
        )
        # Including the Wiki assertion whose value matches a contested value.
        self.assertEqual(
            {id(a) for a in resolution.superseded_assertions}, {id(a_w1), id(a_w2)}
        )
        self.assertEqual(resolution.observed_values, ("A", "B", "C"))
        self.assert_partition(resolution, (a_d1, a_d2, a_w1, a_w2))

    def test_observed_values_include_ignored_system(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.POLICY, fact_value="5")
        a_sys = assertion(sysev, FactScope.POLICY, fact_value="8")
        resolution = evaluate_evidence(
            plan, results, assertions=(a_doc, a_sys)
        ).resolutions[0]
        self.assertEqual(resolution.observed_values, ("5", "8"))

    def test_excluded_system_disagreement_is_not_a_resolved_conflict(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.POLICY, fact_value="5")
        a_sys = assertion(sysev, FactScope.POLICY, fact_value="8")
        decision = evaluate_evidence(plan, results, assertions=(a_doc, a_sys))
        self.assertNotIn(REASON_FACT_CONFLICT_RESOLVED, decision.reason_codes)

    def test_eligible_disagreement_settled_emits_resolved(self):
        doc, wiki = document_evidence(), wiki_evidence()
        plan = make_plan(WIKI, DOCUMENT)
        results = {WIKI: ok_result(WIKI, wiki), DOCUMENT: ok_result(DOCUMENT, doc)}
        decision = evaluate_evidence(
            plan,
            results,
            assertions=(
                assertion(doc, FactScope.POLICY, fact_value="5"),
                assertion(wiki, FactScope.POLICY, fact_value="8"),
            ),
        )
        self.assertIn(REASON_FACT_CONFLICT_RESOLVED, decision.reason_codes)

    def test_document_supersedes_wiki_in_policy(self):
        doc, wiki = document_evidence(), wiki_evidence()
        plan = make_plan(WIKI, DOCUMENT)
        results = {WIKI: ok_result(WIKI, wiki), DOCUMENT: ok_result(DOCUMENT, doc)}
        a_doc = assertion(doc, FactScope.POLICY, fact_value="5")
        a_wiki = assertion(wiki, FactScope.POLICY, fact_value="8")
        resolution = evaluate_evidence(
            plan, results, assertions=(a_wiki, a_doc)
        ).resolutions[0]
        self.assertEqual(resolution.winning_assertions, (a_doc,))
        self.assertEqual(resolution.superseded_assertions, (a_wiki,))

    def test_agreement_does_not_excuse_ineligibility(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.POLICY, fact_value="5")
        a_sys = assertion(sysev, FactScope.POLICY, fact_value="5")
        decision = evaluate_evidence(plan, results, assertions=(a_doc, a_sys))
        resolution = decision.resolutions[0]
        self.assertEqual(resolution.winning_assertions, (a_doc,))
        self.assertEqual(resolution.ignored_assertions, (a_sys,))
        self.assertIn(REASON_SYSTEM_EXCLUDED_FROM_POLICY, decision.reason_codes)

    def test_same_authority_different_values_refuses_in_both_scopes(self):
        for scope in FactScope:
            with self.subTest(scope=scope.value):
                first, second = document_evidence(content="A"), document_evidence(content="B")
                plan = make_plan(DOCUMENT)
                results = {DOCUMENT: ok_result(DOCUMENT, first, second)}
                a1 = assertion(first, scope, fact_value="A")
                a2 = assertion(second, scope, fact_value="B")
                decision = evaluate_evidence(plan, results, assertions=(a1, a2))
                resolution = decision.resolutions[0]
                self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
                self.assertEqual(resolution.winning_assertions, ())
                self.assertIsNone(resolution.winning_value)
                self.assertEqual(
                    {id(a) for a in resolution.contending_assertions},
                    {id(a1), id(a2)},
                )
                self.assertIn(REASON_FACT_CONFLICT_UNRESOLVED, decision.reason_codes)

    def test_resolved_groups_have_no_contenders(self):
        doc = document_evidence()
        plan = make_plan(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT, doc)}
        decision = evaluate_evidence(
            plan, results, assertions=(assertion(doc, FactScope.POLICY),)
        )
        for resolution in decision.resolutions:
            self.assertEqual(resolution.contending_assertions, ())
            self.assertIs(resolution.winning_value is None, not resolution.resolved)

    def test_losing_a_conflict_keeps_the_evidence_usable(self):
        doc, sysev = document_evidence(), system_evidence()
        plan, results = self.state_plan(doc, sysev)
        a_doc = assertion(doc, FactScope.CURRENT_OPERATIONAL_STATE, fact_value="旧")
        a_sys = assertion(sysev, FactScope.CURRENT_OPERATIONAL_STATE, fact_value="新")
        decision = evaluate_evidence(plan, results, assertions=(a_doc, a_sys))
        self.assertIn(doc, decision.usable_evidence)
        self.assertEqual(decision.resolutions[0].superseded_assertions, (a_doc,))


class AssertionValidationTests(unittest.TestCase):
    def test_assertion_must_reference_evidence_in_results(self):
        doc = document_evidence()
        plan = make_plan(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT, doc)}
        outsider = document_evidence()
        self.assertEqual(outsider, doc)  # equal by value, different object
        with self.assertRaises(ValueError):
            evaluate_evidence(
                plan, results, assertions=(assertion(outsider, FactScope.POLICY),)
            )

    def test_duplicate_assertions_are_rejected(self):
        doc = document_evidence()
        plan = make_plan(DOCUMENT)
        results = {DOCUMENT: ok_result(DOCUMENT, doc)}
        for second_value in ("5", "8"):
            with self.subTest(second_value=second_value):
                with self.assertRaises(ValueError):
                    evaluate_evidence(
                        plan,
                        results,
                        assertions=(
                            assertion(doc, FactScope.POLICY, fact_value="5"),
                            assertion(doc, FactScope.POLICY, fact_value=second_value),
                        ),
                    )

    def test_field_validation(self):
        doc = document_evidence()
        for field_name in ("subject", "fact_key", "fact_value"):
            for bad in ("", "   ", 1, True, None, [], {}):
                with self.subTest(field=field_name, bad=bad):
                    kwargs = {
                        "evidence": doc,
                        "scope": FactScope.POLICY,
                        "subject": "年假",
                        "fact_key": "天数",
                        "fact_value": "5",
                    }
                    kwargs[field_name] = bad
                    with self.assertRaises(ValueError):
                        FactAssertion(**kwargs)

    def test_scope_and_evidence_types(self):
        with self.assertRaises(ValueError):
            FactAssertion(
                evidence=document_evidence(),
                scope="policy",
                subject="年假",
                fact_key="天数",
                fact_value="5",
            )
        with self.assertRaises(ValueError):
            FactAssertion(
                evidence="not-evidence",
                scope=FactScope.POLICY,
                subject="年假",
                fact_key="天数",
                fact_value="5",
            )


class DeterminismAndImmutabilityTests(unittest.TestCase):
    def build(self):
        doc, wiki = document_evidence(), wiki_evidence()
        plan = make_plan(WIKI, DOCUMENT)
        results = {WIKI: ok_result(WIKI, wiki), DOCUMENT: ok_result(DOCUMENT, doc)}
        assertions = (
            assertion(doc, FactScope.POLICY, subject="b", fact_value="5"),
            assertion(wiki, FactScope.POLICY, subject="a", fact_value="8"),
        )
        return plan, results, assertions

    def test_group_order_is_independent_of_assertion_order(self):
        plan, results, assertions = self.build()
        forward = evaluate_evidence(plan, results, assertions=assertions)
        reversed_ = evaluate_evidence(plan, results, assertions=tuple(reversed(assertions)))
        self.assertEqual(
            [(r.scope.value, r.subject, r.fact_key) for r in forward.resolutions],
            [(r.scope.value, r.subject, r.fact_key) for r in reversed_.resolutions],
        )
        self.assertEqual(
            [r.subject for r in forward.resolutions], ["a", "b"]
        )

    def test_repeated_calls_are_equal(self):
        plan, results, assertions = self.build()
        first = evaluate_evidence(plan, results, assertions=assertions)
        second = evaluate_evidence(plan, results, assertions=assertions)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_inputs_are_not_modified(self):
        plan, results, assertions = self.build()
        plan_before = copy.deepcopy(plan)
        results_before = copy.deepcopy(results)
        assertions_before = copy.deepcopy(assertions)

        evaluate_evidence(plan, results, assertions=assertions)

        self.assertEqual(plan, plan_before)
        self.assertEqual(results, results_before)
        self.assertEqual(assertions, assertions_before)
        for tool in results:
            for item in results[tool].evidence:
                self.assertIsInstance(item.metadata, dict)
            self.assertEqual(results[tool].trace, results_before[tool].trace)

    def test_dataclasses_are_frozen(self):
        plan, results, assertions = self.build()
        decision = evaluate_evidence(plan, results, assertions=assertions)
        with self.assertRaises(Exception):
            decision.outcome = PolicyOutcome.REFUSE
        with self.assertRaises(Exception):
            decision.resolutions[0].resolved = False
        with self.assertRaises(Exception):
            assertions[0].fact_value = "changed"
        with self.assertRaises(Exception):
            ToolFailure(tool=WIKI, kind=FAILURE_EMPTY).kind = FAILURE_ERROR


class SerializationAndReasonTests(unittest.TestCase):
    def test_to_dict_is_json_serializable(self):
        doc = document_evidence()
        plan = make_plan(DOCUMENT, requires_exact_citation=True)
        results = {DOCUMENT: ok_result(DOCUMENT, doc)}
        decision = evaluate_evidence(
            plan, results, assertions=(assertion(doc, FactScope.POLICY),)
        )
        payload = decision.to_dict()
        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["outcome"], "ready")
        self.assertIsInstance(payload["reason_codes"], list)
        self.assertIsInstance(payload["usable_evidence"], list)
        self.assertEqual(payload["resolutions"][0]["scope"], "policy")
        self.assertNotIsInstance(payload["outcome"], PolicyOutcome)

    def test_reason_codes_are_unique_and_ordered(self):
        doc, sysev = document_evidence(), system_evidence(metadata={})
        plan = make_plan(DOCUMENT, SYSTEM, requires_exact_citation=True)
        results = {
            DOCUMENT: ok_result(DOCUMENT, document_evidence(locator=None)),
            SYSTEM: ok_result(SYSTEM, sysev),
        }
        decision = evaluate_evidence(plan, results)
        codes = decision.reason_codes
        self.assertEqual(len(codes), len(set(codes)))
        positions = [REASON_CODE_ORDER.index(code) for code in codes]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(decision.outcome, PolicyOutcome.REFUSE)
        self.assertNotIn(REASON_EVIDENCE_SUFFICIENT, codes)

    def test_evidence_sufficient_only_on_ready(self):
        plan, results = satisfied(DOCUMENT)
        self.assertIn(
            REASON_EVIDENCE_SUFFICIENT, evaluate_evidence(plan, results).reason_codes
        )
        refusing = evaluate_evidence(make_plan(DOCUMENT), {})
        self.assertNotIn(REASON_EVIDENCE_SUFFICIENT, refusing.reason_codes)

    def test_blocking_codes_are_exactly_the_refusing_ones(self):
        self.assertNotIn(REASON_PLAN_DIRECT, BLOCKING_REASON_CODES)
        self.assertNotIn(REASON_EVIDENCE_SUFFICIENT, BLOCKING_REASON_CODES)
        self.assertNotIn(REASON_SYSTEM_EXCLUDED_FROM_POLICY, BLOCKING_REASON_CODES)
        self.assertNotIn(REASON_FACT_CONFLICT_RESOLVED, BLOCKING_REASON_CODES)


class PurityTests(unittest.TestCase):
    def test_module_imports_no_clock_or_infrastructure(self):
        for forbidden in (
            "import datetime",
            "from datetime",
            "import time",
            "sqlite3",
            "import rag",
            "import api",
            "import storage",
            "requests",
            "wiki_adapter",
            "document_adapter",
            "system_provider",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, MODULE_SOURCE)


if __name__ == "__main__":
    unittest.main()
