"""M9D-B: pages are compiled in batches of `PAGE_BATCH_SIZE`.

One call per page made a 20-page document cost 22 model calls; on a local 4B
model the round trip, not the page, is the minute. Batching trades that for a
mapping problem, so most of these tests are about the batch coming back intact.

Scripted models throughout. No Ollama, no network, no writes outside temp dirs.
"""

import math
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_wiki_compiler import ScriptedModel, batch_sections, dumps
from wiki_maintenance import (
    PAGE_BATCH_SIZE,
    WikiCompilationError,
    WikiMaintainer,
    WikiRepository,
    build_document_snapshot_from_text,
    build_source_spans_from_text,
    compile_wiki,
)

DOCUMENT_ID = "handbook-md"
SOURCE = "handbook.md"
CJK_DIGITS = "〇一二三四五六七八九"


def topic_name(index: int) -> str:
    """No Arabic digits in the topic: they would end up in a title and summary
    that no source span can account for."""
    return "主题" + "".join(CJK_DIGITS[int(d)] for d in str(index))


def document_with(page_count: int) -> str:
    """A Markdown document with one section - and so one page - per topic."""
    sections = [
        f"## {topic_name(index)}\n\n第 {index} 条规定：每年可申请 {index} 次。"
        for index in range(1, page_count + 1)
    ]
    return "# 员工手册\n\n" + "\n\n".join(sections) + "\n"


def spans_for(page_count: int):
    return build_source_spans_from_text(DOCUMENT_ID, document_with(page_count), SOURCE)


def batching_model(spans, *, shuffle=False, mutate=None):
    """One page per heading, answered batch by batch.

    `mutate` rewrites the reply so a test can inject a dropped, duplicated or
    invented page without hand-writing a whole batch.
    """
    by_id = {span.span_id: span for span in spans}

    def visible(text):
        return [span for span_id, span in by_id.items() if span_id in text]

    def handler(request):
        if request.stage == "document_decision":
            return dumps(
                {"action": "update", "reason": "制度", "supersedes_document_ids": []}
            )
        if request.stage == "topic_plan":
            return dumps(
                {
                    "pages": [
                        {
                            "topic": span.heading,
                            "existing_page_id": None,
                            "source_span_ids": [span.span_id],
                        }
                        for span in visible(request.user)
                    ]
                }
            )
        if request.stage.startswith("page_compilation:"):
            pages = []
            for index, topic, section in batch_sections(request):
                span = visible(section)[0]
                pages.append(
                    {
                        "index": index,
                        "topic": topic,
                        "title": topic,
                        # Quoting the span keeps every numeral sourced.
                        "summary": f"本页说明：{span.text}",
                        "aliases": [],
                        "claims": [
                            {
                                "text": span.text,
                                "source_span_ids": [span.span_id],
                                "existing_claim_id": None,
                            }
                        ],
                    }
                )
            if shuffle:
                pages.reverse()
            if mutate is not None:
                pages = mutate(pages)
            return dumps({"pages": pages})
        raise AssertionError(f"unscripted stage {request.stage!r}")

    return ScriptedModel(handler)


def compile_document(page_count: int, **model_kwargs):
    """Compile straight through `compile_wiki`: one plan call plus the batches."""
    spans = spans_for(page_count)
    model = batching_model(spans, **model_kwargs)
    return compile_wiki(model, spans=spans), model


def page_calls(model) -> list[str]:
    return [stage for stage in model.stages if stage.startswith("page_compilation:")]


class BatchSizingTests(unittest.TestCase):
    def test_the_batch_size_is_four(self):
        self.assertEqual(PAGE_BATCH_SIZE, 4)

    def test_page_counts_map_to_the_expected_number_of_calls(self):
        for page_count, expected_calls in ((1, 1), (4, 1), (5, 2), (20, 5)):
            with self.subTest(pages=page_count):
                pages, model = compile_document(page_count)

                self.assertEqual(len(pages), page_count)
                self.assertEqual(len(page_calls(model)), expected_calls)
                self.assertEqual(
                    expected_calls, math.ceil(page_count / PAGE_BATCH_SIZE)
                )

    def test_twenty_pages_cost_seven_model_calls_end_to_end(self):
        """1 document decision + 1 topic plan + 5 page batches, down from 22."""
        with tempfile.TemporaryDirectory() as directory:
            repository = WikiRepository(Path(directory))
            text = document_with(20)
            snapshot = build_document_snapshot_from_text(
                document_id=DOCUMENT_ID, filename=SOURCE, text=text
            )
            repository.save_document_snapshot(snapshot)
            model = batching_model(snapshot.spans)

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            self.assertEqual(outcome.page_count, 20)
            self.assertEqual(len(model.stages), 7)
            self.assertEqual(model.stages[0], "document_decision")
            self.assertEqual(model.stages[1], "topic_plan")
            self.assertEqual(
                model.stages[2:],
                [f"page_compilation:批次 {n}/5" for n in range(1, 6)],
            )

    def test_the_stage_keeps_the_prefix_the_progress_ui_reads(self):
        _, model = compile_document(5)
        stages = page_calls(model)
        self.assertEqual(
            stages, ["page_compilation:批次 1/2", "page_compilation:批次 2/2"]
        )
        for stage in stages:
            self.assertTrue(stage.startswith("page_compilation:"))

    def test_the_last_batch_carries_the_remainder(self):
        _, model = compile_document(6)
        sizes = [
            len(batch_sections(request))
            for request in model.requests
            if request.stage.startswith("page_compilation:")
        ]
        self.assertEqual(sizes, [4, 2])


class BatchIntegrityTests(unittest.TestCase):
    def test_an_out_of_order_reply_is_restored_to_plan_order(self):
        ordered, _ = compile_document(6)
        shuffled, model = compile_document(6, shuffle=True)

        self.assertEqual(
            [page.title for page in shuffled],
            [topic_name(n) for n in range(1, 7)],
        )
        self.assertEqual(
            [page.page_id for page in shuffled], [page.page_id for page in ordered]
        )
        # Each page kept its own claim rather than its neighbour's.
        for position, page in enumerate(shuffled, start=1):
            self.assertIn(f"第 {position} 条规定", page.claims[0].text)
        self.assertEqual(len(page_calls(model)), 2)

    def _expect_rejection(self, mutate, fragment, page_count=5):
        with self.assertRaises(WikiCompilationError) as raised:
            compile_document(page_count, mutate=mutate)
        self.assertIn(fragment, str(raised.exception))

    def test_a_missing_page_is_rejected(self):
        self._expect_rejection(lambda pages: pages[:-1], "the batch asked for")

    def test_a_duplicated_page_is_rejected(self):
        self._expect_rejection(
            lambda pages: pages[:-1] + [dict(pages[0])], "appears twice"
        )

    def test_an_extra_page_is_rejected(self):
        self._expect_rejection(
            lambda pages: pages + [dict(pages[0], index=len(pages) + 1)],
            "the batch asked for",
        )

    def test_an_index_outside_the_batch_is_rejected(self):
        self._expect_rejection(
            lambda pages: [dict(pages[0], index=99)] + pages[1:], "outside 1.."
        )

    def test_a_swapped_topic_is_rejected(self):
        """The index says one page and the topic says another. Guessing which is
        right would file claims under the wrong page, silently."""
        self._expect_rejection(
            lambda pages: [dict(pages[0], topic="别的主题")] + pages[1:],
            "that position is",
        )

    def test_a_page_may_not_cite_another_page_in_the_same_batch(self):
        def steal(pages):
            borrowed = pages[1]["claims"][0]["source_span_ids"]
            first = dict(pages[0])
            first["claims"] = [dict(pages[0]["claims"][0], source_span_ids=borrowed)]
            return [first] + pages[1:]

        self._expect_rejection(steal, "not available here", page_count=4)

    def test_number_sourcing_still_applies_per_page(self):
        def invent(pages):
            first = dict(pages[0])
            first["claims"] = [dict(pages[0]["claims"][0], text="每年可申请 999 次。")]
            return [first] + pages[1:]

        self._expect_rejection(invent, "999", page_count=4)


class BatchRepairTests(unittest.TestCase):
    def test_one_repair_recovers_a_malformed_batch(self):
        spans = spans_for(3)
        good = batching_model(spans)
        state = {"failed": False}

        def handler(request):
            if request.stage.endswith("_repair"):
                # The repair prompt quotes the original request verbatim.
                original = re.search(
                    r"原始任务：\n(.*?)\n\n你上一次的输出：", request.user, re.S
                ).group(1)
                return good.generate(
                    type(request)(
                        stage="page_compilation:批次 1/1",
                        system=request.system,
                        user=original,
                    )
                )
            if request.stage.startswith("page_compilation:") and not state["failed"]:
                state["failed"] = True
                return "这不是 JSON"
            return good.generate(request)

        model = ScriptedModel(handler)
        pages = compile_wiki(model, spans=spans)

        self.assertEqual(len(pages), 3)
        self.assertIn("page_compilation:批次 1/1_repair", model.stages)
        self.assertEqual(len(page_calls(model)), 2, "one call plus one repair")

    def test_a_second_failure_stops_and_does_not_fall_back_to_one_call_per_page(self):
        spans = spans_for(8)
        good = batching_model(spans)

        def handler(request):
            if request.stage.startswith("page_compilation:"):
                return "still not JSON"
            return good.generate(request)

        model = ScriptedModel(handler)
        with self.assertRaises(WikiCompilationError) as raised:
            compile_wiki(model, spans=spans)

        self.assertIn("one repair attempt", str(raised.exception))
        # One batch attempt and one repair, then stop. A per-page retry loop
        # would hide the very regression this milestone exists to prevent - and
        # the second batch is never attempted, so the whole build fails.
        self.assertEqual(len(page_calls(model)), 2)
        self.assertEqual(model.stages, [
            "topic_plan",
            "page_compilation:批次 1/2",
            "page_compilation:批次 1/2_repair",
        ])


class BatchPromptTests(unittest.TestCase):
    def test_each_section_carries_its_own_index_topic_and_spans(self):
        spans = spans_for(5)
        model = batching_model(spans)
        compile_wiki(model, spans=spans)

        first = next(
            request
            for request in model.requests
            if request.stage.startswith("page_compilation:")
        )
        sections = batch_sections(first)
        self.assertEqual([index for index, _, _ in sections], [1, 2, 3, 4])
        self.assertEqual(
            [topic for _, topic, _ in sections], [topic_name(n) for n in range(1, 5)]
        )
        # A page's spans appear in its own section and nowhere else.
        for position, (_, _, section) in enumerate(sections):
            self.assertIn(spans[position].span_id, section)
            for other in range(len(sections)):
                if other != position:
                    self.assertNotIn(spans[other].span_id, section)
        self.assertIn("必须全部编写并全部返回", first.user)
        # The fifth page is in the next batch, not this one.
        self.assertNotIn(spans[4].span_id, first.user)


class CompatibilityTests(unittest.TestCase):
    def test_compile_page_still_compiles_one_page(self):
        from wiki_maintenance.compiler import PagePlan, compile_page

        spans = spans_for(2)
        model = batching_model(spans)
        page = compile_page(
            model,
            PagePlan(topic=spans[0].heading, source_span_ids=(spans[0].span_id,)),
            span_index={span.span_id: span for span in spans},
            existing_page=None,
        )

        self.assertEqual(page.title, spans[0].heading)
        self.assertEqual(len(page.claims), 1)
        self.assertEqual(page.claims[0].source_span_ids, (spans[0].span_id,))
        # The single-page entry keeps its topic-named stage.
        self.assertEqual(model.stages, [f"page_compilation:{spans[0].heading}"])


if __name__ == "__main__":
    unittest.main()
