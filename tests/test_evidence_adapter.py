import unittest
from unittest.mock import patch

from rag import Chunk

from orchestration.contracts import Evidence, SourceType, ToolResult, ToolStatus
from orchestration.document_adapter import DOCUMENT_AUTHORITY, TOOL_NAME, document_search


def make_evidence(**overrides) -> Evidence:
    defaults = {
        "content": "年假为十天。",
        "source_type": SourceType.DOCUMENT,
        "source": "handbook.md",
        "authority": 80,
    }
    defaults.update(overrides)
    return Evidence(**defaults)


class EvidenceValidationTests(unittest.TestCase):
    def test_rejects_empty_content(self):
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    make_evidence(content=blank)

    def test_rejects_empty_source(self):
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    make_evidence(source=blank)

    def test_requires_authority(self):
        with self.assertRaises(TypeError):
            Evidence(
                content="年假为十天。",
                source_type=SourceType.DOCUMENT,
                source="handbook.md",
            )

    def test_rejects_authority_outside_bounds(self):
        for authority in (-1, 101):
            with self.subTest(authority=authority):
                with self.assertRaises(ValueError):
                    make_evidence(authority=authority)

    def test_accepts_authority_bounds(self):
        for authority in (0, 100):
            with self.subTest(authority=authority):
                self.assertEqual(make_evidence(authority=authority).authority, authority)

    def test_rejects_confidence_outside_bounds(self):
        for confidence in (-0.1, 1.1):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    make_evidence(confidence=confidence)

    def test_accepts_confidence_bounds_and_none(self):
        for confidence in (0.0, 1.0, None):
            with self.subTest(confidence=confidence):
                self.assertEqual(make_evidence(confidence=confidence).confidence, confidence)

    def test_metadata_defaults_to_a_new_dict_per_instance(self):
        first, second = make_evidence(), make_evidence()
        first.metadata["rank"] = 1
        self.assertEqual(second.metadata, {})


class EvidenceSerializationTests(unittest.TestCase):
    def test_to_dict_uses_plain_strings_for_enums(self):
        payload = make_evidence(
            locator="chunk:7",
            version="2022",
            observed_at="2026-08-26T12:00:00+08:00",
            confidence=0.5,
            metadata={"rank": 1, "retrieval_score": 3.25},
        ).to_dict()

        self.assertEqual(payload["source_type"], "document")
        self.assertNotIsInstance(payload["source_type"], SourceType)
        self.assertEqual(
            payload,
            {
                "content": "年假为十天。",
                "source_type": "document",
                "source": "handbook.md",
                "locator": "chunk:7",
                "version": "2022",
                "observed_at": "2026-08-26T12:00:00+08:00",
                "authority": 80,
                "confidence": 0.5,
                "metadata": {"rank": 1, "retrieval_score": 3.25},
            },
        )

    def test_to_dict_metadata_is_detached_from_the_instance(self):
        evidence = make_evidence(metadata={"rank": 1})
        payload = evidence.to_dict()
        payload["metadata"]["rank"] = 99
        self.assertEqual(evidence.metadata["rank"], 1)


class ToolResultValidationTests(unittest.TestCase):
    def test_rejects_empty_tool_name(self):
        with self.assertRaises(ValueError):
            ToolResult(tool_name="  ", status=ToolStatus.EMPTY)

    def test_ok_requires_evidence_and_no_error_fields(self):
        with self.assertRaises(ValueError):
            ToolResult(tool_name=TOOL_NAME, status=ToolStatus.OK)
        with self.assertRaises(ValueError):
            ToolResult(
                tool_name=TOOL_NAME,
                status=ToolStatus.OK,
                evidence=(make_evidence(),),
                error_code="boom",
                error_message="boom",
            )
        result = ToolResult(
            tool_name=TOOL_NAME, status=ToolStatus.OK, evidence=(make_evidence(),)
        )
        self.assertEqual(len(result.evidence), 1)

    def test_empty_rejects_evidence_and_error_fields(self):
        with self.assertRaises(ValueError):
            ToolResult(
                tool_name=TOOL_NAME, status=ToolStatus.EMPTY, evidence=(make_evidence(),)
            )
        with self.assertRaises(ValueError):
            ToolResult(
                tool_name=TOOL_NAME,
                status=ToolStatus.EMPTY,
                error_code="boom",
                error_message="boom",
            )
        self.assertEqual(
            ToolResult(tool_name=TOOL_NAME, status=ToolStatus.EMPTY).evidence, ()
        )

    def test_error_requires_both_error_fields_and_no_evidence(self):
        with self.assertRaises(ValueError):
            ToolResult(tool_name=TOOL_NAME, status=ToolStatus.ERROR)
        with self.assertRaises(ValueError):
            ToolResult(tool_name=TOOL_NAME, status=ToolStatus.ERROR, error_code="timeout")
        with self.assertRaises(ValueError):
            ToolResult(
                tool_name=TOOL_NAME, status=ToolStatus.ERROR, error_message="timed out"
            )
        with self.assertRaises(ValueError):
            ToolResult(
                tool_name=TOOL_NAME,
                status=ToolStatus.ERROR,
                evidence=(make_evidence(),),
                error_code="timeout",
                error_message="timed out",
            )
        result = ToolResult(
            tool_name=TOOL_NAME,
            status=ToolStatus.ERROR,
            error_code="timeout",
            error_message="timed out",
        )
        self.assertEqual(result.error_code, "timeout")

    def test_trace_defaults_to_a_new_dict_per_instance(self):
        first = ToolResult(tool_name=TOOL_NAME, status=ToolStatus.EMPTY)
        second = ToolResult(tool_name=TOOL_NAME, status=ToolStatus.EMPTY)
        first.trace["retrieval_path"] = "bm25"
        self.assertEqual(second.trace, {})


class ToolResultSerializationTests(unittest.TestCase):
    def test_to_dict_nests_serialized_evidence(self):
        result = ToolResult(
            tool_name=TOOL_NAME,
            status=ToolStatus.OK,
            evidence=(make_evidence(locator="chunk:1"),),
            trace={"retrieval_path": "bm25_fast"},
        )

        payload = result.to_dict()

        self.assertEqual(payload["tool_name"], TOOL_NAME)
        self.assertEqual(payload["status"], "ok")
        self.assertNotIsInstance(payload["status"], ToolStatus)
        self.assertEqual(payload["error_code"], None)
        self.assertEqual(payload["error_message"], None)
        self.assertEqual(payload["trace"], {"retrieval_path": "bm25_fast"})
        self.assertIsInstance(payload["evidence"], list)
        self.assertEqual(payload["evidence"], [result.evidence[0].to_dict()])
        self.assertEqual(payload["evidence"][0]["locator"], "chunk:1")


class DocumentSearchTests(unittest.TestCase):
    def setUp(self):
        self.first = Chunk(text="年假为十天。", source="handbook.md", index=3)
        self.second = Chunk(text="病假需要证明。", source="handbook.md", index=9)

    def test_maps_results_in_order_with_ranks_starting_at_one(self):
        native = [(self.first, 4.5), (self.second, 1.25)]
        with patch("orchestration.document_adapter.retrieve_fast", return_value=native):
            result = document_search("年假有多少天？", [self.first, self.second])

        self.assertEqual(result.tool_name, "document_search")
        self.assertEqual(result.status, ToolStatus.OK)
        self.assertEqual(len(result.evidence), 2)

        top, runner_up = result.evidence
        self.assertEqual(top.content, "年假为十天。")
        self.assertEqual(top.source, "handbook.md")
        self.assertEqual(top.source_type, SourceType.DOCUMENT)
        self.assertEqual(top.locator, "chunk:3")
        self.assertEqual(top.authority, DOCUMENT_AUTHORITY)
        self.assertIsNone(top.version)
        self.assertIsNone(top.observed_at)
        self.assertEqual(top.metadata["rank"], 1)
        self.assertEqual(top.metadata["chunk_index"], 3)

        self.assertEqual(runner_up.content, "病假需要证明。")
        self.assertEqual(runner_up.locator, "chunk:9")
        self.assertEqual(runner_up.metadata["rank"], 2)
        self.assertEqual(runner_up.metadata["chunk_index"], 9)

    def test_retrieval_score_stays_metadata_and_confidence_stays_none(self):
        native = [(self.first, 4.5)]
        with patch("orchestration.document_adapter.retrieve_fast", return_value=native):
            result = document_search("年假有多少天？", [self.first])

        evidence = result.evidence[0]
        self.assertEqual(evidence.metadata["retrieval_score"], 4.5)
        self.assertIsNone(evidence.confidence)

    def test_empty_retrieval_returns_empty_status(self):
        with patch("orchestration.document_adapter.retrieve_fast", return_value=[]):
            result = document_search("公司有几架飞机？", [self.first])

        self.assertEqual(result.status, ToolStatus.EMPTY)
        self.assertEqual(result.evidence, ())
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error_message)

    def test_blank_question_raises_before_retrieval(self):
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                with patch("orchestration.document_adapter.retrieve_fast") as retrieval:
                    with self.assertRaises(ValueError):
                        document_search(blank, [self.first])
                retrieval.assert_not_called()

    def test_invalid_top_k_raises_before_retrieval(self):
        for top_k in (0, -1):
            with self.subTest(top_k=top_k):
                with patch("orchestration.document_adapter.retrieve_fast") as retrieval:
                    with self.assertRaises(ValueError):
                        document_search("年假有多少天？", [self.first], top_k=top_k)
                retrieval.assert_not_called()

    def test_arguments_reach_retrieve_fast_exactly_once(self):
        chunks = [self.first, self.second]
        trace: dict = {}
        native = [(self.first, 4.5)]

        with patch(
            "orchestration.document_adapter.retrieve_fast", return_value=native
        ) as retrieval:
            document_search("年假有多少天？", chunks, top_k=2, trace=trace)

        retrieval.assert_called_once_with("年假有多少天？", chunks, top_k=2, trace=trace)
        passed_args, passed_kwargs = retrieval.call_args
        self.assertIs(passed_args[1], chunks)
        self.assertIs(passed_kwargs["trace"], trace)

    def test_trace_is_copied_after_retrieval(self):
        trace: dict = {"caller": "test"}

        def fake_retrieve(question, chunks, top_k=4, trace=None):
            # rag writes its own timings into the caller's trace.
            trace["retrieval_path"] = "bm25_fast"
            return [(self.first, 4.5)]

        with patch("orchestration.document_adapter.retrieve_fast", side_effect=fake_retrieve):
            result = document_search("年假有多少天？", [self.first], trace=trace)

        # The snapshot is taken after retrieval, so it includes what rag wrote.
        self.assertEqual(result.trace, {"caller": "test", "retrieval_path": "bm25_fast"})
        self.assertIsNot(result.trace, trace)

        trace["added_later"] = True
        self.assertNotIn("added_later", result.trace)

    def test_missing_trace_yields_an_empty_trace(self):
        with patch(
            "orchestration.document_adapter.retrieve_fast", return_value=[(self.first, 1.0)]
        ):
            result = document_search("年假有多少天？", [self.first])

        self.assertEqual(result.trace, {})

    def test_retrieval_exceptions_propagate_unchanged(self):
        failure = TimeoutError("read timed out")
        with patch("orchestration.document_adapter.retrieve_fast", side_effect=failure):
            with self.assertRaises(TimeoutError) as caught:
                document_search("年假有多少天？", [self.first])

        self.assertIs(caught.exception, failure)


if __name__ == "__main__":
    unittest.main()
