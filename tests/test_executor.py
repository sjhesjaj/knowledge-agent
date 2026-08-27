import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from rag import Chunk

from orchestration.contracts import Evidence, SourceType, ToolResult, ToolStatus
from orchestration.evidence_policy import FactAssertion, FactScope, PolicyOutcome
from orchestration.executor import (
    ERROR_CODE_EXECUTION_FAILED,
    ERROR_CODE_TOOL_REPORTED,
    ExecutionBundle,
    ExecutionContext,
    SystemRequest,
    ToolExecutionError,
    execute_plan,
)
from orchestration.planner import Plan, RequestSignals, Route, ToolName
from orchestration.system_provider import OPERATION_PARAMETERS, SystemOperation
from orchestration.wiki_schema import WikiClaim, WikiPage

WIKI = ToolName.WIKI_QUERY
DOCUMENT = ToolName.DOCUMENT_SEARCH
SYSTEM = ToolName.SYSTEM_QUERY

QUESTION = "年假有多少天"
SUBJECT_SENTINEL = "subject-sentinel-7742"
ORDER_SENTINEL = "ord-sentinel-8853"

MODULE_SOURCE = (
    Path(__file__).resolve().parent.parent / "orchestration" / "executor.py"
).read_text(encoding="utf-8")

ROUTE_FOR_STEPS = {
    (): Route.DIRECT,
    (WIKI,): Route.WIKI_ONLY,
    (DOCUMENT,): Route.DOCUMENT_ONLY,
    (SYSTEM,): Route.SYSTEM_ONLY,
    (WIKI, DOCUMENT): Route.WIKI_DOCUMENT,
    (WIKI, SYSTEM): Route.WIKI_SYSTEM,
    (DOCUMENT, SYSTEM): Route.DOCUMENT_SYSTEM,
    (WIKI, DOCUMENT, SYSTEM): Route.WIKI_DOCUMENT_SYSTEM,
}


def make_plan(*steps: ToolName) -> Plan:
    return Plan(
        route=ROUTE_FOR_STEPS[steps],
        steps=steps,
        signals=RequestSignals(
            needs_wiki=WIKI in steps,
            needs_document=DOCUMENT in steps,
            needs_system=SYSTEM in steps,
        ),
        reason_codes=("test",),
    )


def a_chunk() -> Chunk:
    return Chunk(text="## 请假制度\n正式员工每年 5 天年假。", source="rules.md", index=1)


def a_wiki_page() -> WikiPage:
    return WikiPage(
        page_id="p1",
        title="请假与年假",
        summary="年假天数随司龄增加。",
        version="1.0",
        aliases=("年假",),
        claims=(
            WikiClaim(
                claim_id="c1",
                text="正式员工每年享有 5 天带薪年假。",
                source="rules.md",
                locator="section:请假制度",
            ),
        ),
    )


def wiki_evidence() -> Evidence:
    return Evidence(
        content="正式员工每年享有 5 天带薪年假。",
        source_type=SourceType.WIKI,
        source="rules.md",
        locator="section:请假制度",
        version="1.0",
        authority=60,
    )


def document_evidence() -> Evidence:
    return Evidence(
        content="正式员工每年享有 5 天带薪年假。",
        source_type=SourceType.DOCUMENT,
        source="rules.md",
        locator="chunk:1",
        authority=80,
    )


def system_evidence() -> Evidence:
    return Evidence(
        content="订单 " + ORDER_SENTINEL + " 的当前状态为 已发货。",
        source_type=SourceType.SYSTEM,
        source="demo-business-system",
        locator="orders:" + ORDER_SENTINEL,
        observed_at="2026-08-20T09:15:00+08:00",
        authority=100,
        metadata={
            "authority_scope": "current_operational_state",
            "record_id": ORDER_SENTINEL,
        },
    )


EVIDENCE_FACTORY = {
    WIKI: wiki_evidence,
    DOCUMENT: document_evidence,
    SYSTEM: system_evidence,
}


# The system trace is validated against M4's full published schema, so the
# default mock must look like a real one.
DEFAULT_TRACE = {
    WIKI: {"wiki_scanned_pages": 1, "wiki_returned_evidence": 1},
    DOCUMENT: {"retrieval_path": "bm25_fast", "candidates": 1},
    SYSTEM: {},  # derived per call by system_side_effect
}


def ok_result(tool: ToolName, *, trace=None) -> ToolResult:
    return ToolResult(
        tool_name=tool.value,
        status=ToolStatus.OK,
        evidence=(EVIDENCE_FACTORY[tool](),),
        trace=dict(trace if trace is not None else DEFAULT_TRACE[tool]),
    )


def system_side_effect(connection, operation, parameters, *, trace=None):
    """Mirror M4: the trace describes the call it actually received."""
    return ok_result(
        SYSTEM,
        trace={
            "system_operation": operation.value,
            "system_parameter_names": sorted(parameters),
            "system_rows_matched": 1,
            "system_returned_evidence": 1,
        },
    )


def error_result(tool: ToolName, *, code="adapter-code", message="adapter-message",
                 trace=None, evidence=(), tool_name=None) -> ToolResult:
    return ToolResult(
        tool_name=tool_name or tool.value,
        status=ToolStatus.ERROR,
        evidence=evidence,
        error_code=code,
        error_message=message,
        trace=dict(trace or {}),
    )


def a_connection() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def order_request() -> SystemRequest:
    return SystemRequest(
        operation=SystemOperation.GET_ORDER_STATUS,
        parameters={"order_id": ORDER_SENTINEL},
    )


def approval_request() -> SystemRequest:
    return SystemRequest(
        operation=SystemOperation.GET_APPROVAL_STATUS,
        parameters={"approval_id": "apr-3001"},
    )


def inventory_request() -> SystemRequest:
    return SystemRequest(
        operation=SystemOperation.GET_INVENTORY_LEVEL,
        parameters={"sku": "sku-a100"},
    )


# Both subject-scoped operations, so neither can regress unnoticed.
SUBJECT_SCOPED_REQUESTS = {
    "get_order_status": order_request,
    "get_approval_status": approval_request,
}


class ExecutorTestCase(unittest.TestCase):
    """Patches the adapters as imported into orchestration.executor."""

    def setUp(self):
        self.connection = a_connection()
        self.addCleanup(self.connection.close)

        self.wiki = patch(
            "orchestration.executor.wiki_query", side_effect=lambda *a, **k: ok_result(WIKI)
        ).start()
        self.document = patch(
            "orchestration.executor.document_search",
            side_effect=lambda *a, **k: ok_result(DOCUMENT),
        ).start()
        self.system = patch(
            "orchestration.executor.system_query", side_effect=system_side_effect
        ).start()
        self.addCleanup(patch.stopall)

    def context(self, **overrides) -> ExecutionContext:
        defaults = {
            "chunks": (a_chunk(),),
            "wiki_pages": (a_wiki_page(),),
            "system_connection": self.connection,
            "subject_id": SUBJECT_SENTINEL,
            "system_request": order_request(),
            "knowledge_version": 3,
        }
        defaults.update(overrides)
        return ExecutionContext(**defaults)

    def adapters(self):
        return (self.wiki, self.document, self.system)

    def assert_no_adapter_called(self):
        for mock in self.adapters():
            self.assertEqual(mock.call_count, 0, mock)

    def assert_value_error(self, *args, **kwargs):
        try:
            execute_plan(*args, **kwargs)
        except ValueError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - the point of the test
            self.fail("expected ValueError, got " + type(exc).__name__ + ": " + str(exc))
        self.fail("expected ValueError")


class RouteExecutionTests(ExecutorTestCase):
    def test_every_route_invokes_planned_steps_once_in_order(self):
        for steps in ROUTE_FOR_STEPS:
            if not steps:
                continue
            with self.subTest(steps=[s.value for s in steps]):
                self.setUp()
                bundle = execute_plan(QUESTION, make_plan(*steps), self.context())
                self.assertEqual(tuple(bundle.results), steps)
                for tool, mock in zip((WIKI, DOCUMENT, SYSTEM), self.adapters()):
                    self.assertEqual(mock.call_count, 1 if tool in steps else 0)
                self.assertEqual(
                    bundle.trace["executor_executed_steps"],
                    [s.value for s in steps],
                )
                self.assertEqual(bundle.decision.outcome, PolicyOutcome.READY)

    def test_direct_plan_calls_nothing(self):
        bundle = execute_plan(QUESTION, make_plan(), self.context())
        self.assert_no_adapter_called()
        self.assertEqual(bundle.results, {})
        self.assertEqual(bundle.tool_errors, ())
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.DIRECT)

    def test_call_arguments_are_injected_verbatim(self):
        context = self.context(document_top_k=7, wiki_top_k=2)
        execute_plan(QUESTION, make_plan(WIKI, DOCUMENT, SYSTEM), context)

        wiki_args, wiki_kwargs = self.wiki.call_args
        self.assertEqual(wiki_args[0], QUESTION)
        self.assertIs(wiki_args[1], context.wiki_pages)
        self.assertEqual(wiki_kwargs["top_k"], 2)

        doc_args, doc_kwargs = self.document.call_args
        self.assertEqual(doc_args[0], QUESTION)
        self.assertEqual(doc_args[1], list(context.chunks))
        self.assertIsNot(doc_args[1], context.chunks)
        self.assertEqual(doc_kwargs["top_k"], 7)

        sys_args, sys_kwargs = self.system.call_args
        self.assertIs(sys_args[0], self.connection)
        self.assertEqual(sys_args[1], SystemOperation.GET_ORDER_STATUS)

        traces = [
            wiki_kwargs["trace"],
            doc_kwargs["trace"],
            sys_kwargs["trace"],
        ]
        self.assertEqual(len({id(t) for t in traces}), 3)

    def test_question_is_not_rewritten(self):
        execute_plan(QUESTION, make_plan(WIKI, DOCUMENT), self.context())
        self.assertEqual(self.wiki.call_args[0][0], QUESTION)
        self.assertEqual(self.document.call_args[0][0], QUESTION)


class FailureBehaviourTests(ExecutorTestCase):
    def test_failure_does_not_stop_the_pass_and_does_not_retry(self):
        self.wiki.side_effect = RuntimeError("boom")
        bundle = execute_plan(QUESTION, make_plan(WIKI, DOCUMENT, SYSTEM), self.context())
        self.assertEqual(self.wiki.call_count, 1)
        self.assertEqual(self.document.call_count, 1)
        self.assertEqual(self.system.call_count, 1)
        self.assertEqual(
            bundle.trace["executor_executed_steps"],
            [WIKI.value, DOCUMENT.value, SYSTEM.value],
        )
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.REFUSE)

    def test_value_and_type_errors_propagate(self):
        for exception in (ValueError("bad call"), TypeError("bad call")):
            with self.subTest(exception=type(exception).__name__):
                self.setUp()
                self.document.side_effect = exception
                with self.assertRaises(type(exception)):
                    execute_plan(QUESTION, make_plan(DOCUMENT), self.context())

    def test_other_exceptions_become_sanitized_error_results(self):
        self.document.side_effect = sqlite3.OperationalError("secret-9931")
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())
        result = bundle.results[DOCUMENT]
        self.assertEqual(result.status, ToolStatus.ERROR)
        self.assertEqual(result.error_code, ERROR_CODE_EXECUTION_FAILED)
        self.assertEqual(
            result.error_message, "document_search failed with OperationalError"
        )
        self.assertEqual(
            bundle.tool_errors,
            (ToolExecutionError(tool=DOCUMENT, exception_type="OperationalError"),),
        )

    def test_exception_text_is_never_captured(self):
        self.document.side_effect = sqlite3.OperationalError("secret-9931")
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())
        blob = repr(bundle) + json.dumps(bundle.to_dict(), ensure_ascii=False)
        self.assertNotIn("secret-9931", blob)

    def test_base_exceptions_are_not_caught(self):
        self.document.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_plan(QUESTION, make_plan(DOCUMENT), self.context())

    def test_adapter_reported_error_is_sanitized(self):
        returned = error_result(
            DOCUMENT,
            code="secret-code-111",
            message="secret-message-222",
            trace={"leak": "secret-trace-333"},
        )
        self.document.side_effect = lambda *a, **k: returned
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())

        result = bundle.results[DOCUMENT]
        self.assertEqual(result.error_code, ERROR_CODE_TOOL_REPORTED)
        self.assertEqual(result.error_message, "document_search returned an error")
        self.assertEqual(dict(result.trace), {})
        self.assertEqual(bundle.tool_errors, ())
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.REFUSE)

        blob = (
            repr(bundle)
            + json.dumps(bundle.to_dict(), ensure_ascii=False)
            + repr(dict(bundle.trace))
        )
        for secret in ("secret-code-111", "secret-message-222", "secret-trace-333"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob)

        # The adapter's own object is not mutated.
        self.assertEqual(returned.error_code, "secret-code-111")

    def test_malformed_adapter_error_is_rejected_by_the_executor(self):
        # ToolResult rejects these at construction, so build a valid ERROR and
        # mutate it - ToolResult is not frozen, which is exactly why the
        # executor re-checks the shape before sanitizing.
        mutations = {
            "wrong_tool_name": ("tool_name", "wiki_query"),
            "blank_code": ("error_code", "   "),
            "non_string_code": ("error_code", 7),
            "blank_message": ("error_message", "  "),
            "non_string_message": ("error_message", None),
            "carries_evidence": ("evidence", (document_evidence(),)),
        }
        for name, (attribute, value) in mutations.items():
            with self.subTest(case=name):
                self.setUp()
                returned = error_result(DOCUMENT)
                setattr(returned, attribute, value)
                self.document.side_effect = lambda *a, **k: returned
                message = self.assert_value_error(
                    QUESTION, make_plan(DOCUMENT), self.context()
                )
                self.assertNotIn("adapter-code", message)
                self.assertNotIn("adapter-message", message)

    def test_adapter_returning_a_non_tool_result_is_rejected(self):
        self.document.side_effect = lambda *a, **k: object()
        self.assert_value_error(QUESTION, make_plan(DOCUMENT), self.context())

    def test_malformed_ok_and_empty_never_reach_the_assertion_provider(self):
        calls = []

        def provider(results):
            calls.append(results)
            return ()

        def ok_with_wrong_tool_name():
            result = ok_result(DOCUMENT)
            result.tool_name = "wiki_query"
            return result

        def ok_without_evidence():
            result = ok_result(DOCUMENT)
            result.evidence = ()
            return result

        def ok_with_error_fields():
            result = ok_result(DOCUMENT)
            result.error_code = "leaked-code"
            result.error_message = "leaked-message"
            return result

        def empty_with_error_fields():
            result = ToolResult(tool_name=DOCUMENT.value, status=ToolStatus.EMPTY)
            result.error_code = "leaked-code"
            result.error_message = "leaked-message"
            return result

        def empty_with_evidence():
            result = ToolResult(tool_name=DOCUMENT.value, status=ToolStatus.EMPTY)
            result.evidence = (document_evidence(),)
            return result

        def evidence_not_a_tuple():
            result = ok_result(DOCUMENT)
            result.evidence = [document_evidence()]
            return result

        builders = {
            "ok_wrong_tool_name": ok_with_wrong_tool_name,
            "ok_without_evidence": ok_without_evidence,
            "ok_with_error_fields": ok_with_error_fields,
            "empty_with_error_fields": empty_with_error_fields,
            "empty_with_evidence": empty_with_evidence,
            "evidence_not_a_tuple": evidence_not_a_tuple,
        }
        for name, build in builders.items():
            with self.subTest(case=name):
                self.setUp()
                calls.clear()
                returned = build()
                self.document.side_effect = lambda *a, **k: returned
                message = self.assert_value_error(
                    QUESTION,
                    make_plan(DOCUMENT),
                    self.context(),
                    assertion_provider=provider,
                )
                self.assertEqual(calls, [], "provider must not see a malformed result")
                self.assertNotIn("leaked-code", message)
                self.assertNotIn("leaked-message", message)


class PreflightTests(ExecutorTestCase):
    def test_blank_question(self):
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=blank):
                self.setUp()
                self.assert_value_error(blank, make_plan(DOCUMENT), self.context())
                self.assert_no_adapter_called()

    def test_bad_top_level_types(self):
        self.assert_value_error(QUESTION, "not-a-plan", self.context())
        self.assert_value_error(QUESTION, make_plan(DOCUMENT), "not-a-context")
        self.assert_value_error(
            QUESTION,
            make_plan(DOCUMENT),
            self.context(),
            assertion_provider="not-callable",
        )
        self.assert_no_adapter_called()

    def test_missing_dependencies(self):
        cases = {
            "no_chunks": (make_plan(DOCUMENT), {"chunks": ()}),
            "no_pages": (make_plan(WIKI), {"wiki_pages": ()}),
            "no_connection": (make_plan(SYSTEM), {"system_connection": None}),
            "no_request": (make_plan(SYSTEM), {"system_request": None}),
        }
        for name, (plan, overrides) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.assert_value_error(QUESTION, plan, self.context(**overrides))
                self.assert_no_adapter_called()

    def test_preflight_is_total_on_a_composite_route(self):
        plan = make_plan(WIKI, DOCUMENT, SYSTEM)
        cases = {
            "blank_parameter_value": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters={"order_id": "   "},
                )
            },
            "non_string_parameter_value": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters={"order_id": 1001},
                )
            },
            "none_parameter_value": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters={"order_id": None},
                )
            },
            "non_string_parameter_key": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters={1: "x"},
                )
            },
            "parameters_not_a_mapping": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters=[("order_id", "x")],
                )
            },
            "subject_id_smuggled": {
                "system_request": SystemRequest(
                    operation=SystemOperation.GET_ORDER_STATUS,
                    parameters={"order_id": "x", "subject_id": "attacker"},
                )
            },
            "blank_subject": {"subject_id": "  "},
            "operation_not_enum": {
                "system_request": SystemRequest(
                    operation="get_order_status", parameters={"order_id": "x"}
                )
            },
            "chunks_as_list": {"chunks": [a_chunk()]},
            "chunk_element_wrong": {"chunks": ("not-a-chunk",)},
            "pages_as_list": {"wiki_pages": [a_wiki_page()]},
            "page_element_wrong": {"wiki_pages": ("not-a-page",)},
            "knowledge_version_str": {"knowledge_version": "3"},
            "knowledge_version_bool": {"knowledge_version": True},
            "knowledge_version_negative": {"knowledge_version": -1},
            "bad_document_top_k": {"document_top_k": 0},
            "bad_wiki_top_k": {"wiki_top_k": True},
        }
        for name, overrides in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.assert_value_error(QUESTION, plan, self.context(**overrides))
                self.assert_no_adapter_called()

    def test_dependencies_are_reported_before_top_k(self):
        # Frozen preflight order: all planned dependencies (step 4), then all
        # top_k (step 5). Both are wrong here; the dependency must win.
        message = self.assert_value_error(
            QUESTION,
            make_plan(WIKI, DOCUMENT),
            self.context(wiki_pages=(), document_top_k=0),
        )
        self.assertIn("wiki_pages", message)
        self.assertNotIn("document_top_k", message)
        self.assert_no_adapter_called()

    def test_top_k_is_only_checked_for_planned_tools(self):
        bundle = execute_plan(
            QUESTION, make_plan(DOCUMENT), self.context(wiki_top_k=0)
        )
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.READY)

    def test_knowledge_version_is_validated_even_when_unused(self):
        self.assert_value_error(
            QUESTION, make_plan(DOCUMENT), self.context(knowledge_version="3")
        )
        self.assert_no_adapter_called()

    def test_too_many_steps(self):
        plan = make_plan(WIKI, DOCUMENT, SYSTEM)
        object.__setattr__(plan, "steps", (WIKI, DOCUMENT, SYSTEM, WIKI))
        self.assert_value_error(QUESTION, plan, self.context())
        self.assert_no_adapter_called()


class SystemBoundaryTests(ExecutorTestCase):
    def test_subject_id_is_injected_for_subject_scoped_operations(self):
        for name, build in SUBJECT_SCOPED_REQUESTS.items():
            with self.subTest(operation=name):
                self.setUp()
                request = build()
                execute_plan(
                    QUESTION, make_plan(SYSTEM), self.context(system_request=request)
                )
                parameters = self.system.call_args[0][2]
                self.assertEqual(parameters["subject_id"], SUBJECT_SENTINEL)
                self.assertEqual(
                    set(parameters),
                    set(OPERATION_PARAMETERS[request.operation]),
                )

    def test_subject_id_is_not_injected_for_inventory(self):
        execute_plan(
            QUESTION,
            make_plan(SYSTEM),
            self.context(system_request=inventory_request()),
        )
        parameters = self.system.call_args[0][2]
        self.assertEqual(parameters, {"sku": "sku-a100"})
        self.assertNotIn("subject_id", parameters)

    def test_subject_scoped_operations_require_a_trusted_subject(self):
        for name, build in SUBJECT_SCOPED_REQUESTS.items():
            for subject in (None, "", "   ", 1):
                with self.subTest(operation=name, subject=subject):
                    self.setUp()
                    message = self.assert_value_error(
                        QUESTION,
                        make_plan(SYSTEM),
                        self.context(subject_id=subject, system_request=build()),
                    )
                    self.assertIn("subject_id", message)
                    self.assertIn(name, message)
                    self.assert_no_adapter_called()

    def test_inventory_works_without_a_subject(self):
        bundle = execute_plan(
            QUESTION,
            make_plan(SYSTEM),
            self.context(subject_id=None, system_request=inventory_request()),
        )
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.READY)

    def test_smuggled_subject_id_is_rejected(self):
        request = SystemRequest(
            operation=SystemOperation.GET_ORDER_STATUS,
            parameters={"order_id": "x", "subject_id": "attacker-value"},
        )
        message = self.assert_value_error(
            QUESTION, make_plan(SYSTEM), self.context(system_request=request)
        )
        self.assertNotIn("attacker-value", message)

    def test_whitelist_comes_from_m4(self):
        # Identity, not a source-text grep: a restated local copy would satisfy
        # a substring check while being free to drift from M4's table.
        import orchestration.executor as executor_module
        import orchestration.system_provider as provider_module

        self.assertIs(
            executor_module.OPERATION_PARAMETERS,
            provider_module.OPERATION_PARAMETERS,
        )
        request = SystemRequest(
            operation=SystemOperation.GET_INVENTORY_LEVEL,
            parameters={"sku": "x", "extra": "y"},
        )
        self.assert_value_error(
            QUESTION, make_plan(SYSTEM), self.context(system_request=request)
        )

    def test_adapter_trace_carrying_a_parameter_value_is_rejected(self):
        leaky = {
            "parameter_value": {"debug_order_id": ORDER_SENTINEL},
            "subject_value": {"debug_subject": SUBJECT_SENTINEL},
            "embedded_in_text": {"note": "looked up " + ORDER_SENTINEL + " ok"},
            "nested": {"outer": {"inner": [ORDER_SENTINEL]}},
            "as_a_key": {ORDER_SENTINEL: "seen"},
        }
        for name, trace in leaky.items():
            with self.subTest(case=name):
                self.setUp()
                self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=trace)
                message = self.assert_value_error(
                    QUESTION, make_plan(SYSTEM), self.context()
                )
                self.assertNotIn(ORDER_SENTINEL, message)
                self.assertNotIn(SUBJECT_SENTINEL, message)

    def test_sql_in_a_trace_is_rejected(self):
        sql = "SELECT order_id, status FROM orders WHERE subject_id = ?"
        leaky = {
            "top_level": {"debug_sql": sql},
            "nested": {"system_operation": "get_order_status",
                       "detail": {"query": sql}},
            "in_a_list": {"system_operation": "get_order_status",
                          "queries": [sql]},
        }
        for name, trace in leaky.items():
            with self.subTest(case=name):
                self.setUp()
                self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=trace)
                message = self.assert_value_error(
                    QUESTION, make_plan(SYSTEM), self.context()
                )
                self.assertNotIn("SELECT", message.upper())
                self.assertNotIn("orders", message)

    def system_trace(self, **overrides):
        trace = {
            "system_operation": "get_order_status",
            "system_parameter_names": ["order_id", "subject_id"],
            "system_rows_matched": 1,
            "system_returned_evidence": 1,
        }
        trace.update(overrides)
        return trace

    def test_system_trace_schema_is_enforced(self):
        cases = {
            # A payload riding inside a legitimate field name.
            "payload_in_a_count": self.system_trace(
                system_rows_matched={"raw_row": "customer-secret-991"}
            ),
            "payload_in_a_list": self.system_trace(
                system_returned_evidence=["customer-secret-991"]
            ),
            "count_is_a_string": self.system_trace(system_rows_matched="1"),
            "count_is_a_bool": self.system_trace(system_rows_matched=True),
            "count_is_negative": self.system_trace(system_rows_matched=-1),
            "operation_disagrees": self.system_trace(
                system_operation="get_inventory_level"
            ),
            "operation_not_a_string": self.system_trace(system_operation=7),
            "names_disagree": self.system_trace(
                system_parameter_names=["order_id"]
            ),
            "names_not_strings": self.system_trace(system_parameter_names=[1, 2]),
            "names_not_a_list": self.system_trace(
                system_parameter_names="order_id,subject_id"
            ),
            "returned_disagrees_with_evidence": self.system_trace(
                system_returned_evidence=5, system_rows_matched=5
            ),
            "matched_below_returned": self.system_trace(system_rows_matched=0),
        }
        for name, trace in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=trace)
                message = self.assert_value_error(
                    QUESTION, make_plan(SYSTEM), self.context()
                )
                self.assertNotIn("customer-secret-991", message)

    def test_missing_system_trace_fields_are_rejected(self):
        full = self.system_trace()
        for name in sorted(full):
            with self.subTest(missing=name):
                self.setUp()
                partial = {k: v for k, v in full.items() if k != name}
                self.system.side_effect = lambda *a, **k: ok_result(
                    SYSTEM, trace=partial
                )
                message = self.assert_value_error(
                    QUESTION, make_plan(SYSTEM), self.context()
                )
                self.assertIn(name, message)

        self.setUp()
        self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace={})
        self.assert_value_error(QUESTION, make_plan(SYSTEM), self.context())

    def test_whitespace_separated_sql_is_rejected(self):
        variants = (
            "SELECT\norder_id\nFROM\norders",
            "SELECT\torder_id\tFROM\torders",
            "select\n\n  order_id  \r\n from   orders",
        )
        for sql in variants:
            with self.subTest(sql=repr(sql[:20])):
                self.setUp()
                trace = self.system_trace(system_operation=sql)
                self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=trace)
                message = self.assert_value_error(
                    QUESTION, make_plan(SYSTEM), self.context()
                )
                self.assertNotIn("orders", message)

    def test_unpublished_system_trace_fields_are_rejected(self):
        trace = {
            "system_operation": "get_order_status",
            "system_parameter_names": ["order_id", "subject_id"],
            "system_rows_matched": 1,
            "system_returned_evidence": 1,
            "debug_note": "harmless looking",
        }
        self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=trace)
        message = self.assert_value_error(QUESTION, make_plan(SYSTEM), self.context())
        self.assertIn("debug_note", message)

    def test_a_real_m4_trace_passes_the_whole_schema(self):
        """Pin fields, types, and semantics against a real call, offline."""
        from orchestration.executor import (
            SYSTEM_TRACE_FIELDS,
            _assert_system_trace_schema,
        )
        from orchestration.system_provider import (
            SAMPLE_FIXTURE_PATH,
            system_query as real_system_query,
        )

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(SAMPLE_FIXTURE_PATH.read_text(encoding="utf-8"))
        parameters = {"subject_id": "subject-001", "order_id": "ord-1001"}
        result = real_system_query(
            connection, SystemOperation.GET_ORDER_STATUS, parameters
        )

        self.assertEqual(set(result.trace), set(SYSTEM_TRACE_FIELDS))
        # Would raise if any type or value disagreed.
        _assert_system_trace_schema(
            result.trace,
            operation_value=SystemOperation.GET_ORDER_STATUS.value,
            parameter_names=sorted(parameters),
            evidence_count=len(result.evidence),
        )

    def test_a_real_m4_trace_flows_through_the_executor(self):
        """End to end with the genuine adapter: real trace, real evidence."""
        from orchestration.system_provider import (
            SAMPLE_FIXTURE_PATH,
            system_query as real_system_query,
        )

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(SAMPLE_FIXTURE_PATH.read_text(encoding="utf-8"))
        self.system.side_effect = real_system_query

        context = self.context(
            system_connection=connection,
            subject_id="subject-001",
            system_request=SystemRequest(
                operation=SystemOperation.GET_ORDER_STATUS,
                parameters={"order_id": "ord-1001"},
            ),
        )
        bundle = execute_plan(QUESTION, make_plan(SYSTEM), context)
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.READY)
        self.assertEqual(
            bundle.trace["executor_tool_traces"][SYSTEM.value]["system_operation"],
            "get_order_status",
        )
        # The subject value never surfaces, even from the genuine adapter.
        self.assertNotIn(
            "subject-001", json.dumps(bundle.to_dict(), ensure_ascii=False)
        )

    def test_parameter_names_in_a_trace_are_allowed(self):
        # M4's real trace records names only; that must keep working.
        realistic = {
            "system_operation": "get_order_status",
            "system_parameter_names": ["order_id", "subject_id"],
            "system_rows_matched": 1,
            "system_returned_evidence": 1,
        }
        self.system.side_effect = lambda *a, **k: ok_result(SYSTEM, trace=realistic)
        bundle = execute_plan(QUESTION, make_plan(SYSTEM), self.context())
        self.assertEqual(bundle.decision.outcome, PolicyOutcome.READY)
        self.assertEqual(
            bundle.trace["executor_tool_traces"][SYSTEM.value], realistic
        )

    def test_caller_parameters_are_copied_not_reused(self):
        caller_parameters = {"order_id": ORDER_SENTINEL}
        request = SystemRequest(
            operation=SystemOperation.GET_ORDER_STATUS, parameters=caller_parameters
        )
        execute_plan(QUESTION, make_plan(SYSTEM), self.context(system_request=request))
        passed = self.system.call_args[0][2]
        self.assertIsNot(passed, caller_parameters)
        self.assertEqual(caller_parameters, {"order_id": ORDER_SENTINEL})


class AssertionProviderTests(ExecutorTestCase):
    def test_default_provider_yields_no_resolutions(self):
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())
        self.assertEqual(bundle.decision.resolutions, ())

    def test_provider_is_called_once_with_the_results(self):
        seen = []

        def provider(results):
            seen.append(results)
            return ()

        bundle = execute_plan(
            QUESTION, make_plan(DOCUMENT), self.context(), assertion_provider=provider
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(tuple(seen[0]), (DOCUMENT,))
        self.assertEqual(seen[0], bundle.results)

    def test_provider_output_reaches_the_policy_layer(self):
        def provider(results):
            evidence = results[DOCUMENT].evidence[0]
            return (
                FactAssertion(
                    evidence=evidence,
                    scope=FactScope.POLICY,
                    subject="年假",
                    fact_key="天数",
                    fact_value="5",
                ),
            )

        bundle = execute_plan(
            QUESTION, make_plan(DOCUMENT), self.context(), assertion_provider=provider
        )
        self.assertEqual(len(bundle.decision.resolutions), 1)
        self.assertEqual(bundle.decision.resolutions[0].winning_value, "5")

    def test_falsy_but_callable_provider_is_still_used(self):
        # Selecting the provider with `or` would silently swap this for the
        # default and report "no facts asserted".
        class FalsyProvider:
            def __init__(self):
                self.calls = 0

            def __len__(self):
                return 0

            def __call__(self, results):
                self.calls += 1
                evidence = results[DOCUMENT].evidence[0]
                return (
                    FactAssertion(
                        evidence=evidence,
                        scope=FactScope.POLICY,
                        subject="年假",
                        fact_key="天数",
                        fact_value="5",
                    ),
                )

        provider = FalsyProvider()
        self.assertFalse(bool(provider))
        self.assertTrue(callable(provider))

        bundle = execute_plan(
            QUESTION, make_plan(DOCUMENT), self.context(), assertion_provider=provider
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(bundle.decision.resolutions), 1)

    def test_provider_exception_propagates(self):
        def provider(results):
            raise RuntimeError("provider broke")

        with self.assertRaisesRegex(RuntimeError, "provider broke"):
            execute_plan(
                QUESTION,
                make_plan(DOCUMENT),
                self.context(),
                assertion_provider=provider,
            )

    def test_executor_never_reads_evidence_content(self):
        self.assertNotIn(".content", MODULE_SOURCE)


class BundleAndTraceTests(ExecutorTestCase):
    def test_trace_fields(self):
        bundle = execute_plan(QUESTION, make_plan(WIKI, DOCUMENT), self.context())
        trace = bundle.trace
        self.assertEqual(trace["executor_route"], Route.WIKI_DOCUMENT.value)
        self.assertEqual(
            trace["executor_planned_steps"], [WIKI.value, DOCUMENT.value]
        )
        self.assertEqual(
            trace["executor_executed_steps"], [WIKI.value, DOCUMENT.value]
        )
        self.assertEqual(
            trace["executor_step_status"],
            {WIKI.value: "ok", DOCUMENT.value: "ok"},
        )
        self.assertEqual(set(trace["executor_step_seconds"]), {WIKI.value, DOCUMENT.value})
        self.assertEqual(set(trace["executor_tool_traces"]), {WIKI.value, DOCUMENT.value})
        self.assertEqual(trace["executor_outcome"], "ready")
        self.assertEqual(trace["executor_knowledge_version"], 3)
        self.assertIsInstance(trace["executor_total_seconds"], float)

    def test_tool_traces_are_shallow_copies(self):
        nested = {"inner": 1}
        returned = ToolResult(
            tool_name=DOCUMENT.value,
            status=ToolStatus.OK,
            evidence=(document_evidence(),),
            trace={"nested": nested, "top": "a"},
        )
        self.document.side_effect = lambda *a, **k: returned
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())

        returned.trace["added_later"] = True
        self.assertNotIn(
            "added_later", bundle.trace["executor_tool_traces"][DOCUMENT.value]
        )
        nested["inner"] = 2
        self.assertEqual(
            bundle.trace["executor_tool_traces"][DOCUMENT.value]["nested"]["inner"], 2
        )

    def test_top_level_mappings_are_read_only(self):
        bundle = execute_plan(QUESTION, make_plan(DOCUMENT), self.context())
        with self.assertRaises(TypeError):
            bundle.results[WIKI] = ok_result(WIKI)
        with self.assertRaises(TypeError):
            bundle.trace["executor_route"] = "changed"
        with self.assertRaises(Exception):
            bundle.plan = make_plan()

    def test_to_dict_is_json_serializable(self):
        bundle = execute_plan(QUESTION, make_plan(WIKI, DOCUMENT, SYSTEM), self.context())
        payload = bundle.to_dict()
        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(set(payload["results"]), {WIKI.value, DOCUMENT.value, SYSTEM.value})
        self.assertEqual(payload["decision"]["outcome"], "ready")
        self.assertEqual(payload["tool_errors"], [])

    def test_privacy_matrix(self):
        bundle = execute_plan(QUESTION, make_plan(SYSTEM), self.context())
        serialized = json.dumps(bundle.to_dict(), ensure_ascii=False)
        whole_bundle = serialized + repr(bundle)
        trace_only = json.dumps(dict(bundle.trace), ensure_ascii=False)

        # subject_id VALUE: forbidden everywhere.
        self.assertNotIn(SUBJECT_SENTINEL, whole_bundle)
        # subject_id NAME: legitimately present via M4's system_parameter_names,
        # so it must not be asserted against - only recorded here as expected.
        self.assertIn(ORDER_SENTINEL, serialized)  # business key lives in Evidence
        # Business record key: forbidden in traces only.
        self.assertNotIn(ORDER_SENTINEL, trace_only)

    def test_inputs_are_not_modified(self):
        context = self.context()
        plan = make_plan(WIKI, DOCUMENT, SYSTEM)
        chunks_before = tuple(context.chunks)
        pages_before = tuple(context.wiki_pages)
        parameters_before = dict(context.system_request.parameters)

        execute_plan(QUESTION, plan, context)

        self.assertEqual(context.chunks, chunks_before)
        self.assertEqual(context.wiki_pages, pages_before)
        self.assertEqual(dict(context.system_request.parameters), parameters_before)
        self.assertEqual(plan.steps, (WIKI, DOCUMENT, SYSTEM))


class IsolationTests(ExecutorTestCase):
    def test_executor_opens_nothing(self):
        with patch(
            "sqlite3.connect", side_effect=AssertionError("must not open a connection")
        ):
            with patch(
                "orchestration.wiki_adapter.load_wiki_pages",
                side_effect=AssertionError("must not load the wiki"),
            ):
                execute_plan(
                    QUESTION, make_plan(WIKI, DOCUMENT, SYSTEM), self.context()
                )

    def test_module_imports_no_product_infrastructure(self):
        for forbidden in (
            "import agent",
            "import api",
            "import storage",
            "import requests",
            "fastapi",
            "load_wiki_pages",
            "SQLiteStorage",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, MODULE_SOURCE)


if __name__ == "__main__":
    unittest.main()
