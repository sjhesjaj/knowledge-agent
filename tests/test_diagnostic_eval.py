"""Stage 2 diagnostic eval tests.

Traces are written with the real agent_trace recorder into a temp SQLite file
and read back with load_trace, so every rule is exercised against the stored
trace shape. Nothing reaches a model or the network.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import agent_trace
import chat_orchestration
import llm_provider
import rag
import wiki_runtime
from diagnostic_eval import rules as dx
from diagnostic_eval.labels import LabelError, derive_labels, load_labels
from diagnostic_eval.report import aggregate, diagnose_eval, markdown
from rag import Chunk

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "eval_answerability_validation_v1.json"
OVERLAY = ROOT / "eval" / "diagnostic_labels" / "validation_v1.labels.json"

WORK_HOURS = "# 手册\n## 工作时间\n核心协作时间为 10:00-12:00、14:00-17:00。"
UNRELATED = "# 手册\n## 保密义务\n员工应保护公司信息。"


def doc(content, index=1):
    return {"content": content, "source_type": "document", "source": "rules.md", "locator": f"chunk:{index}",
            "metadata": {"chunk_index": index}}


def system(sku, content):
    return {"content": content, "source_type": "system", "source": "business_system",
            "locator": f"inventory:{sku}", "observed_at": "2026-08-22T08:00:00+08:00", "metadata": {}}


def wiki(title, content):
    return {"content": content, "source_type": "wiki", "source": "rules.md", "locator": "section:x",
            "metadata": {"page_title": title}}


def case(**overrides):
    base = {"id": "c1", "question": "核心协作时间是几点到几点？", "expected_behavior": "answer",
            "expected_route": "document_only", "required_source_types": ["document"],
            "expected_fact_groups": [["10"], ["12", "14", "17"]], "category": "document",
            "difficulty": "normal", "notes": "依据「工作时间」"}
    base.update(overrides)
    return base


def labels_for(raw_case, **extra):
    labels = derive_labels(raw_case)
    for name, value in extra.items():
        setattr(labels, name, value)
    return labels


SIGNALS = {"is_direct": False, "needs_wiki": False, "needs_document": True, "needs_system": False,
           "requires_freshness": False, "requires_exact_citation": False}


class TraceBuilder:
    """Writes traces with the same span layout the product records."""

    def __init__(self, db):
        self.db = db

    def build(self, *, question="q", route="document_only", steps=("document_search",), signals=None,
              fixed_answer=None, tools=None, decision=None, answer=None, generation_raises=None,
              executor_raises=None):
        run = agent_trace.start_run(self.db, kind="eval", entrypoint="test", question=question, truncate=False)
        tools = tools or {}
        try:
            with run:
                with agent_trace.span("planner", "plan_request") as span:
                    span.output = {"route": route, "steps": list(steps), "signals": {**SIGNALS, **(signals or {})},
                                   "reason_codes": [], "fallback_used": False}
                if not steps:
                    return run.run_id
                with agent_trace.span("planner", "availability_check") as span:
                    span.output = {"fixed_answer": fixed_answer}
                if fixed_answer is not None:
                    return run.run_id
                with agent_trace.span("tool_call", "execute_plan", input={"steps": list(steps)}) as execute:
                    if executor_raises:
                        raise executor_raises
                    for name in steps:
                        spec = tools.get(name, {})
                        status = spec.get("status", "ok")
                        run.record("tool_call", name, parent=execute, status=status,
                                   input={"arguments": spec.get("arguments", {})},
                                   output={"status": status, "evidence": spec.get("evidence", []),
                                           "error_code": spec.get("error_code")},
                                   error_type=spec.get("error_type"), error_code=spec.get("error_code"),
                                   latency_ms=1.0)
                run.record("evidence", "evaluate_evidence", output=decision)
                if decision and decision.get("outcome") == "ready":
                    with agent_trace.span("generation", "answer_structured") as span:
                        if generation_raises:
                            raise generation_raises
                        span.output = {"answer": answer}
        except Exception:
            pass
        return run.run_id

    def load(self, run_id):
        return agent_trace.load_trace(self.db, run_id)


def ready(*evidence):
    return {"outcome": "ready", "usable_evidence": list(evidence), "reason_codes": ["evidence_sufficient"],
            "missing_tools": [], "tool_failures": []}


def refuse(*reasons, usable=()):
    return {"outcome": "refuse", "usable_evidence": list(usable), "reason_codes": list(reasons),
            "missing_tools": [], "tool_failures": []}


class DiagnosisTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patch.dict(os.environ, {"TRACE_ENABLED": "1"}).start()
        self.addCleanup(patch.stopall)
        self.traces = TraceBuilder(Path(directory.name) / "traces.sqlite")

    def run_diagnosis(self, labels, *, passed=False, answer_record=None, judge=None, context=None, **trace):
        run_id = self.traces.build(question=labels.question, **trace)
        record = {"run": 1, "passed": passed, "trace_run_id": run_id, "answer": answer_record}
        return dx.diagnose(labels, record, self.traces.load(run_id), judge, context)

    def assertPrimary(self, diagnosis, category, check=None):
        self.assertIsNone(diagnosis.unattributed, diagnosis.unattributed)
        self.assertEqual(diagnosis.primary_error, category, diagnosis.to_dict()["stages"])
        if check:
            self.assertTrue(diagnosis.primary_reason.startswith(check), diagnosis.primary_reason)

    def assertUnattributed(self, diagnosis, kind):
        self.assertIsNone(diagnosis.primary_error, diagnosis.primary_reason)
        self.assertEqual(diagnosis.unattributed["kind"], kind, diagnosis.unattributed)


# --------------------------------------------------------------------------
# One or more tests per error category
# --------------------------------------------------------------------------


class RoutingErrorTests(DiagnosisTestCase):
    def test_wrong_high_level_route(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(labels, route="wiki_only", steps=("wiki_query",),
                                       tools={"wiki_query": {"evidence": [wiki("远程办公", "每周 2 天")]}},
                                       decision=ready(wiki("远程办公", "每周 2 天")), answer="无法确定")
        self.assertPrimary(diagnosis, "routing_error", "route_acceptable")
        # Its consequences are secondary, never a second primary.
        self.assertIn("planning_error", [e["category"] for e in diagnosis.secondary_effects])

    def test_boundary_question_routed_to_documents(self):
        labels = labels_for(case(expected_behavior="boundary", expected_route="system_only",
                                 required_source_types=[], expected_fact_groups=[],
                                 expected_message=chat_orchestration.MESSAGE_SYSTEM_LIMITED))
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(UNRELATED)]}},
                                       decision=ready(doc(UNRELATED)), answer="无法确定。")
        self.assertPrimary(diagnosis, "routing_error")

    def test_acceptable_routes_overlay_allows_an_alternative(self):
        labels = labels_for(case(), acceptable_routes=("document_only", "wiki_document"))
        diagnosis = self.run_diagnosis(labels, passed=True, route="wiki_document",
                                       steps=("wiki_query", "document_search"),
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="10:00-12:00、14:00-17:00。[来源 1]")
        self.assertEqual(diagnosis.stages["routing"].status, dx.PASS)
        self.assertEqual(diagnosis.latent_issues, [])


class PlanningErrorTests(DiagnosisTestCase):
    def test_wrong_signal_is_primary_and_its_policy_refusal_is_secondary(self):
        labels = labels_for(case(), plan_constraints={"requires_freshness": False})
        diagnosis = self.run_diagnosis(labels, signals={"requires_freshness": True},
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=refuse("freshness_unsupported", usable=[doc(WORK_HOURS)]))
        self.assertPrimary(diagnosis, "planning_error", "signal:requires_freshness")
        self.assertEqual([e["category"] for e in diagnosis.secondary_effects], ["evidence_error"])
        self.assertEqual(diagnosis.stages["generation"].status, dx.BLOCKED)

    def test_wrong_tool_argument(self):
        labels = labels_for(case(question="SKU-A100 还有多少库存？", expected_route="system_only",
                                 required_source_types=["system"], expected_fact_groups=[["42"]]),
                            expected_arguments={"system_query": {"sku": "sku-a100"}})
        diagnosis = self.run_diagnosis(labels, route="system_only", steps=("system_query",),
                                       tools={"system_query": {"arguments": {"parameters": {"sku": "sku-b200"}},
                                                               "evidence": [system("sku-b200", "库存 0 件")]}},
                                       decision=ready(system("sku-b200", "库存 0 件")), answer="0 件。[来源 1]")
        self.assertPrimary(diagnosis, "planning_error", "argument:system_query.sku")

    def test_wrong_boundary_message(self):
        labels = labels_for(case(expected_behavior="boundary", expected_route="system_only",
                                 required_source_types=[], expected_fact_groups=[],
                                 expected_message=chat_orchestration.MESSAGE_SYSTEM_LIMITED))
        diagnosis = self.run_diagnosis(labels, route="system_only", steps=("system_query",),
                                       fixed_answer=chat_orchestration.MESSAGE_NO_SKU)
        self.assertPrimary(diagnosis, "planning_error", "availability_boundary_message")

    def test_required_tool_missing_from_an_acceptable_route_is_planning(self):
        labels = labels_for(case(required_source_types=["document", "wiki"]),
                            acceptable_routes=("document_only", "wiki_document"))
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="无法确定")
        self.assertPrimary(diagnosis, "planning_error", "required_tools_planned")


class ToolErrorTests(DiagnosisTestCase):
    def test_required_tool_failed(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(
            labels, tools={"document_search": {"status": "error", "error_code": "tool_execution_failed",
                                               "error_type": "RuntimeError"}},
            decision=refuse("tool_error"))
        self.assertPrimary(diagnosis, "tool_error", "executed:document_search")
        # Nothing retrieved is a downstream effect; the policy's refusal is the
        # correct reaction to a failed tool, so evidence is blocked, not an error.
        self.assertEqual([e["category"] for e in diagnosis.secondary_effects], ["retrieval_error"])
        self.assertEqual(diagnosis.stages["evidence"].status, dx.BLOCKED)

    def test_executor_aborted_the_run(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(labels, executor_raises=ValueError("bad adapter call"))
        self.assertPrimary(diagnosis, "tool_error", "executor_completed")


class RetrievalErrorTests(DiagnosisTestCase):
    def test_answer_evidence_not_retrieved(self):
        labels = labels_for(case(), expected_evidence=({"source_type": "document", "heading": "工作时间"},))
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(UNRELATED)]}},
                                       decision=ready(doc(UNRELATED)), answer="根据现有资料无法确定。")
        self.assertPrimary(diagnosis, "retrieval_error", "expected_evidence:document")
        self.assertEqual(diagnosis.stages["retrieval"].first(dx.FAIL).name, "expected_evidence:document")

    def test_unknown_sku_returned_a_row(self):
        labels = labels_for(case(question="SKU-Y321 库存？", expected_behavior="policy_refuse",
                                 expected_route="system_only", required_source_types=[], expected_fact_groups=[]),
                            expected_tool_status={"system_query": "empty"})
        diagnosis = self.run_diagnosis(labels, route="system_only", steps=("system_query",),
                                       tools={"system_query": {"evidence": [system("sku-y321", "库存 3 件")]}},
                                       decision=ready(system("sku-y321", "库存 3 件")), answer="3 件。[来源 1]")
        self.assertPrimary(diagnosis, "retrieval_error", "status:system_query")

    def test_facts_alone_decide_when_no_evidence_label_exists(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(UNRELATED)]}},
                                       decision=ready(doc(UNRELATED)), answer="根据现有资料无法确定。")
        self.assertPrimary(diagnosis, "retrieval_error", "answer_facts_retrieved")


class EvidenceErrorTests(DiagnosisTestCase):
    def test_policy_refused_good_evidence(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=refuse("fact_conflict_unresolved", usable=[doc(WORK_HOURS)]))
        self.assertPrimary(diagnosis, "evidence_error", "policy_outcome")

    def test_refusal_mechanism_mismatch_with_clean_upstream(self):
        labels = labels_for(case(expected_behavior="generation_refuse", required_source_types=[],
                                 expected_fact_groups=[]))
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(UNRELATED)]}},
                                       decision=refuse("system_excluded_from_policy"))
        self.assertPrimary(diagnosis, "evidence_error", "policy_outcome")

    def test_answer_evidence_dropped_from_usable(self):
        labels = labels_for(case())
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(WORK_HOURS), doc(UNRELATED, 2)]}},
                                       decision=ready(doc(UNRELATED, 2)), answer="根据现有资料无法确定。")
        self.assertPrimary(diagnosis, "evidence_error", "answer_evidence_kept")

    def test_labelled_correct_signal_makes_the_policy_the_root_cause(self):
        labels = labels_for(case(), plan_constraints={"requires_exact_citation": True})
        diagnosis = self.run_diagnosis(labels, signals={"requires_exact_citation": True},
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=refuse("exact_citation_missing_locator", usable=[doc(WORK_HOURS)]))
        self.assertPrimary(diagnosis, "evidence_error")


class GenerationErrorTests(DiagnosisTestCase):
    def good_upstream(self, labels, answer, **kwargs):
        return self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                  decision=ready(doc(WORK_HOURS)), answer=answer, **kwargs)

    def test_false_refusal_with_the_answer_in_usable_evidence(self):
        diagnosis = self.good_upstream(labels_for(case()), "根据现有资料无法确定。")
        self.assertPrimary(diagnosis, "generation_error", "no_false_refusal")

    def test_missing_citation(self):
        diagnosis = self.good_upstream(labels_for(case()), "核心协作时间为 10:00-12:00、14:00-17:00。")
        self.assertPrimary(diagnosis, "generation_error", "citation_present")

    def test_citation_index_out_of_range(self):
        diagnosis = self.good_upstream(labels_for(case()), "10:00-12:00、14:00-17:00。[来源 3]")
        self.assertPrimary(diagnosis, "generation_error", "citation_indices_valid")

    def test_generation_raised(self):
        diagnosis = self.good_upstream(labels_for(case()), None, generation_raises=ConnectionError("down"))
        self.assertPrimary(diagnosis, "generation_error", "generation_completed")

    def test_restating_the_question(self):
        labels = labels_for(case())
        diagnosis = self.good_upstream(labels, labels.question)
        self.assertPrimary(diagnosis, "generation_error", "not_a_restatement")


# --------------------------------------------------------------------------
# Unattributed: diagnostic gaps are never forced into a category
# --------------------------------------------------------------------------


class UnattributedTests(DiagnosisTestCase):
    def test_missing_trace(self):
        diagnosis = dx.diagnose(labels_for(case()), {"run": 1, "passed": False, "trace_run_id": None}, None)
        self.assertUnattributed(diagnosis, dx.MISSING_TRACE)

    def test_unlabelled_signal_behind_a_policy_refusal_is_a_label_gap(self):
        diagnosis = self.run_diagnosis(labels_for(case()), signals={"requires_freshness": True},
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=refuse("freshness_unsupported", usable=[doc(WORK_HOURS)]))
        self.assertUnattributed(diagnosis, dx.LABEL_GAP)
        self.assertEqual(diagnosis.unattributed["stage"], "evidence")

    def test_unproven_generation_failure_is_not_a_generation_error(self):
        # Facts not matched and no refusal: a paraphrase cannot be ruled out by rules.
        diagnosis = self.run_diagnosis(labels_for(case()), tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="上午十点到中午、下午两点到五点。[来源 1]")
        self.assertUnattributed(diagnosis, dx.RULE_INCONCLUSIVE)
        self.assertEqual(diagnosis.unattributed["stage"], "generation")

    def test_answering_a_should_refuse_case_without_markers_is_inconclusive(self):
        labels = labels_for(case(expected_behavior="generation_refuse", required_source_types=[],
                                 expected_fact_groups=[]))
        diagnosis = self.run_diagnosis(labels, tools={"document_search": {"evidence": [doc(UNRELATED)]}},
                                       decision=ready(doc(UNRELATED)), answer="年会在 12 月举办。[来源 1]")
        self.assertUnattributed(diagnosis, dx.RULE_INCONCLUSIVE)

    def test_tool_reason_refusal_with_clean_tools_is_inconclusive(self):
        diagnosis = self.run_diagnosis(labels_for(case()), tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=refuse("empty_tool_result", usable=[doc(WORK_HOURS)]))
        self.assertUnattributed(diagnosis, dx.RULE_INCONCLUSIVE)
        self.assertEqual(diagnosis.unattributed["stage"], "evidence")

    def test_unavailable_corpus_is_environment(self):
        diagnosis = self.run_diagnosis(labels_for(case()), fixed_answer=chat_orchestration.MESSAGE_NO_DOCUMENTS)
        self.assertUnattributed(diagnosis, dx.ENVIRONMENT_FAILURE)

    def test_failure_outside_the_agent_stages_is_environment(self):
        run = agent_trace.start_run(self.traces.db, kind="eval", entrypoint="test", question="q")
        with self.assertRaises(OSError):
            with run:
                raise OSError("disk full")
        diagnosis = dx.diagnose(labels_for(case()), {"run": 1, "passed": False, "trace_run_id": run.run_id},
                                self.traces.load(run.run_id))
        self.assertUnattributed(diagnosis, dx.ENVIRONMENT_FAILURE)

    def test_official_failure_no_rule_explains(self):
        diagnosis = self.run_diagnosis(labels_for(case()), tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="10:00-12:00、14:00-17:00。[来源 1]")
        self.assertUnattributed(diagnosis, dx.RULE_INCONCLUSIVE)


# --------------------------------------------------------------------------
# Counting, latent issues, judge interface
# --------------------------------------------------------------------------


class CountingTests(DiagnosisTestCase):
    def test_primary_secondary_latent_and_gaps_are_counted_separately(self):
        planning = self.run_diagnosis(labels_for(case(), plan_constraints={"requires_freshness": False}),
                                      signals={"requires_freshness": True},
                                      tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                      decision=refuse("freshness_unsupported", usable=[doc(WORK_HOURS)]))
        gap = self.run_diagnosis(labels_for(case()), signals={"requires_freshness": True},
                                 tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                 decision=refuse("freshness_unsupported", usable=[doc(WORK_HOURS)]))
        latent = self.run_diagnosis(labels_for(case(), required_tools=("document_search", "wiki_query")),
                                    passed=True, tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                    decision=ready(doc(WORK_HOURS)), answer="10:00-12:00、14:00-17:00。[来源 1]")
        result = aggregate([planning, gap, latent])
        self.assertEqual((result["official_failed"], result["primary_attributed"]), (2, 1))
        self.assertEqual(result["primary_errors"]["planning_error"], 1)
        self.assertEqual(result["primary_errors"]["evidence_error"], 0)  # secondary never counts as primary
        self.assertEqual(result["secondary_effects"]["evidence_error"], 1)
        self.assertEqual(result["unattributed"]["label_gap"], 1)
        self.assertEqual(result["latent_issues"]["planning_error"], 1)
        self.assertIsNone(latent.primary_error)

    def test_disabled_judge_keeps_semantic_questions_inconclusive(self):
        calls = []

        class Recording(dx.DisabledJudge):
            def judge(self, **kwargs):
                calls.append(kwargs)
                return super().judge(**kwargs)

        diagnosis = self.run_diagnosis(labels_for(case()), judge=Recording(),
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="上午十点到中午。[来源 1]")
        self.assertEqual(len(calls), 1)
        self.assertUnattributed(diagnosis, dx.RULE_INCONCLUSIVE)

    def test_a_plugged_in_judge_is_marked_as_judge_decided(self):
        class Strict:
            def judge(self, **_kwargs):
                return dx.JudgeVerdict("not_supported", "misses 14:00-17:00")

        diagnosis = self.run_diagnosis(labels_for(case()), judge=Strict(),
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="上午十点到中午。[来源 1]")
        self.assertEqual((diagnosis.primary_error, diagnosis.primary_method), ("generation_error", "judge"))
        self.assertEqual(aggregate([diagnosis])["primary_by_method"], {"judge": 1})

    def test_rules_never_call_the_judge_when_they_can_decide(self):
        class Exploding:
            def judge(self, **_kwargs):
                raise AssertionError("judge must not be consulted")

        diagnosis = self.run_diagnosis(labels_for(case()), judge=Exploding(),
                                       tools={"document_search": {"evidence": [doc(WORK_HOURS)]}},
                                       decision=ready(doc(WORK_HOURS)), answer="根据现有资料无法确定。")
        self.assertPrimary(diagnosis, "generation_error")


# --------------------------------------------------------------------------
# Labels and compatibility with the existing 40 cases
# --------------------------------------------------------------------------


class LabelTests(unittest.TestCase):
    def test_every_validation_case_gets_labels_and_the_overlay_applies(self):
        derived = load_labels(DATASET, None)
        self.assertEqual(len(derived), 40)
        labels = load_labels(DATASET, OVERLAY)
        self.assertEqual(labels["answer_document_h008"].plan_constraints, {"requires_freshness": False})
        self.assertEqual(labels["refuse_policy_h001"].expected_tool_status, {"system_query": "empty"})
        self.assertEqual(labels["refuse_missing_h001"].required_tools, ("document_search",))
        self.assertEqual(labels["boundary_order_h001"].required_tools, ())
        self.assertEqual(labels["answer_multi_h004"].required_tools, ("wiki_query", "document_search"))

    def test_notes_are_never_parsed_into_labels(self):
        labels = derive_labels(case(notes="依据「工作时间」；考察时效词"))
        self.assertEqual((labels.expected_evidence, labels.plan_constraints), ((), {}))

    def test_frozen_dataset_is_unchanged(self):
        overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
        import hashlib
        self.assertEqual(hashlib.sha256(DATASET.read_bytes()).hexdigest(), overlay["dataset_sha256"])

    def write_overlay(self, **changes):
        overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
        overlay.update(changes)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "overlay.json"
        path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
        return path

    def test_overlay_rejects_a_changed_dataset(self):
        with self.assertRaisesRegex(LabelError, "dataset_sha256"):
            load_labels(DATASET, self.write_overlay(dataset_sha256="0" * 64))

    def test_overlay_rejects_unknown_cases_and_contradictions(self):
        path = self.write_overlay(cases={"nope": {}, "answer_document_h001": {"forbidden_tools": ["document_search"]},
                                         "refuse_missing_h001": {"expected_evidence": [
                                             {"source_type": "document", "heading": "x"}]}})
        with self.assertRaises(LabelError) as caught:
            load_labels(DATASET, path)
        message = str(caught.exception)
        for fragment in ("unknown case 'nope'", "contradicts required tool", "only applies to answer cases"):
            self.assertIn(fragment, message)

    def test_blind_datasets_are_refused(self):
        with self.assertRaisesRegex(LabelError, "blind"):
            load_labels(ROOT / "eval_answerability_blind_v2.json", None)

    def test_wiki_page_labels_are_skipped_when_the_run_read_another_wiki(self):
        labels = load_labels(DATASET, OVERLAY)["answer_wiki_h001"]
        other = dx.DiagnosisContext(wiki_corpus="published_build:build-0001")
        same = dx.DiagnosisContext(wiki_corpus="committed_sample")
        self.assertFalse(other.wiki_labels_apply(labels))
        self.assertTrue(same.wiki_labels_apply(labels))


class CorpusMismatchTests(DiagnosisTestCase):
    def test_skipped_label_is_not_a_failure(self):
        labels = load_labels(DATASET, OVERLAY)["answer_wiki_h001"]
        diagnosis = self.run_diagnosis(
            labels, passed=True, route="wiki_only", steps=("wiki_query",),
            tools={"wiki_query": {"evidence": [wiki("远程办公（编译版）", "每周最多 2 天远程办公")]}},
            decision=ready(wiki("远程办公（编译版）", "每周最多 2 天远程办公")), answer="每周 2 天。[来源 1]",
            context=dx.DiagnosisContext(wiki_corpus="published_build:build-0001"))
        retrieval = diagnosis.stages["retrieval"]
        self.assertEqual(retrieval.status, dx.PASS)  # decided by the fact check alone
        self.assertIn(dx.SKIPPED, [c.status for c in retrieval.checks])
        self.assertEqual(diagnosis.latent_issues, [])


# --------------------------------------------------------------------------
# Integration: real orchestration traces, and the end-to-end report
# --------------------------------------------------------------------------


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "traces.sqlite"
        patch.dict(os.environ, {"TRACE_ENABLED": "1"}).start()
        patch.object(wiki_runtime, "RUNTIME", wiki_runtime.WikiRuntime(root=self.root / "wiki")).start()
        patch("orchestration.document_adapter.retrieve_fast",
              side_effect=lambda question, chunks, top_k=4, trace=None: [(chunks[0], 3.5)]).start()
        llm_provider.reset_provider()
        self.addCleanup(llm_provider.reset_provider)
        self.addCleanup(patch.stopall)

    def run_real(self, question, answer):
        body = {"message": {"content": json.dumps({"answer": answer}, ensure_ascii=False)},
                "prompt_eval_count": 10, "eval_count": 5}

        class Fake:
            def raise_for_status(self):
                return None

            def json(self):
                return body

        chunk = Chunk(text=WORK_HOURS, source="rules.md", index=1)
        run = agent_trace.start_run(self.db, kind="eval", entrypoint="test", question=question, truncate=False)
        with run, patch.object(requests, "post", return_value=Fake()):
            prepared = chat_orchestration.prepare(question, [chunk])
            if prepared.needs_generation:
                with agent_trace.span("generation", "answer_structured") as span:
                    span.output = {"answer": rag.answer_structured(question, prepared.results_for_answer, [])}
        return run.run_id

    def test_real_success_trace_passes_every_stage(self):
        question = "核心协作时间是几点到几点？"
        run_id = self.run_real(question, "10:00-12:00、14:00-17:00。[来源 1]")
        diagnosis = dx.diagnose(labels_for(case(question=question)), {"run": 1, "passed": True, "trace_run_id": run_id},
                                agent_trace.load_trace(self.db, run_id))
        self.assertEqual({name: r.status for name, r in diagnosis.stages.items()},
                         {s: dx.PASS for s in dx.STAGES})

    def test_real_planner_freshness_signal_is_diagnosed_as_planning(self):
        question = "目前的制度里，核心协作时间是几点到几点？"
        run_id = self.run_real(question, "unused")
        labels = labels_for(case(question=question), plan_constraints={"requires_freshness": False})
        diagnosis = dx.diagnose(labels, {"run": 1, "passed": False, "trace_run_id": run_id},
                                agent_trace.load_trace(self.db, run_id))
        self.assertEqual(diagnosis.primary_error, "planning_error")
        self.assertEqual([e["category"] for e in diagnosis.secondary_effects], ["evidence_error"])

    def test_end_to_end_report(self):
        question = "目前的制度里，核心协作时间是几点到几点？"
        run_id = self.run_real(question, "unused")
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        eval_json = {
            "dataset": DATASET.name, "dataset_sha256": None, "trace_db": str(self.db),
            "environment": {"wiki": {"published_build_id": None}},
            "cases": [{"id": c["id"], "runs": [{"run": 1, "passed": c["id"] != "answer_document_h008",
                                                "trace_run_id": run_id if c["id"] == "answer_document_h008" else None}]}
                      for c in dataset],
        }
        path = self.root / "stage_x.json"
        path.write_text(json.dumps(eval_json), encoding="utf-8")
        report = diagnose_eval(path, OVERLAY, trace_db=self.db)
        self.assertEqual(report["aggregate"]["primary_errors"]["planning_error"], 1)
        self.assertEqual(report["aggregate"]["official_failed"], 1)
        self.assertEqual(report["judge"], {"name": "DisabledJudge", "calls": 0})
        text = markdown(report)
        self.assertIn("answer_document_h008", text)
        self.assertIn("planning_error", text)


if __name__ == "__main__":
    unittest.main()
