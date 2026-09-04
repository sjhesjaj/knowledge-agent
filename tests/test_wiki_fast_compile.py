"""M9D-C: the model plans the structure, the program fills it in.

`page_compilation` is gone from the production path. Two model calls compile a
whole corpus: the model decides which topics exist and which spans belong
together, and the program builds the pages from that plan.

What that buys, stated precisely:

- a claim's text is its span's text verbatim, and its `source`, `locator` and
  `source_span_ids` are derived from the span, so no business fact passes
  through the model's prose;
- page titles are the model's topics; summaries and aliases are generated from
  those topics and the spans' headings - navigation text, not quotations;
- a poorly chosen or poorly grouped topic is still possible. This rules out
  misstatement, not misfiling.

Scripted models throughout. No Ollama, no network, no writes outside temp dirs.
"""

import unittest
from pathlib import Path

from tests.test_wiki_compiler import ScriptedModel, TempRepository, dumps
from wiki_maintenance import (
    WikiCompilationError,
    WikiMaintainer,
    build_document_snapshot_from_text,
    build_source_spans_from_text,
    compile_wiki,
    compile_wiki_fast,
    derive_document_id,
    diff_builds,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RULES = "sample_company_rules.md"
DOCUMENT_ID = "handbook-md"
SOURCE = "handbook.md"

LEAVE_DOC = """# 员工手册

## 请假制度

正式员工入职满一年后，每年享有 5 天带薪年假。

请假需提前在系统提交申请，1 天以内由直属主管审批。
"""

LEAVE_DOC_V2 = """# 员工手册

## 请假制度

正式员工入职满一年后，每年享有 8 天带薪年假。

请假需提前在系统提交申请，1 天以内由直属主管审批。
"""

REMOTE_DOC = """# 远程办公规范

## 远程办公

员工每周最多申请 2 天远程办公。
"""

PLAIN_TEXT_DOC = """员工每周最多申请 2 天远程办公。

远程办公须至少提前一个工作日获得直属主管批准。
"""


def spans_of(text: str, *, document_id=DOCUMENT_ID, source=SOURCE):
    return build_source_spans_from_text(document_id, text, source)


def sample_spans():
    text = REPO_ROOT.joinpath(SAMPLE_RULES).read_text(encoding="utf-8")
    return build_source_spans_from_text(
        derive_document_id(SAMPLE_RULES), text, SAMPLE_RULES
    )


def planning_model(*, pages=None, spans=None, existing_page_ids=None, merge=False):
    """Answers the two stages a fast compile needs, and nothing else.

    Any `page_compilation` request is an assertion failure: reaching that stage
    means the production path regressed to a model-written Wiki.
    """

    def handler(request):
        if request.stage == "document_decision":
            return dumps(
                {"action": "update", "reason": "制度", "supersedes_document_ids": []}
            )
        if request.stage == "topic_plan":
            if pages is not None:
                return dumps({"pages": pages})
            return dumps(
                {"pages": heading_plan(spans, existing_page_ids, merge=merge)}
            )
        raise AssertionError(
            f"the fast path must not call the model for {request.stage!r}"
        )

    return ScriptedModel(handler)


def heading_plan(spans, existing_page_ids=None, *, merge=False):
    """One page per heading, or everything merged onto one page.

    Both are legal plans: the page count is the model's decision, bounded only
    by the partition rules.
    """
    grouped: dict[str, list] = {}
    for span in spans:
        grouped.setdefault(span.heading or "未分类", []).append(span)
    groups = list(grouped.items())
    if merge:
        topic = groups[0][0]
        groups = [(topic, [span for _, group in groups for span in group])]
    existing_page_ids = existing_page_ids or {}
    return [
        {
            "topic": topic,
            "existing_page_id": existing_page_ids.get(topic),
            "source_span_ids": [span.span_id for span in group],
        }
        for topic, group in groups
    ]


class PageCountIsThePlannersTests(unittest.TestCase):
    """No hard budget: a page per heading and a single merged page are both
    valid, and neither costs a page-compilation call."""

    def test_one_page_per_span_is_legal(self):
        spans = sample_spans()
        self.assertEqual(len(spans), 20)
        model = planning_model(
            pages=[
                {
                    "topic": f"{span.heading}-{index}",
                    "existing_page_id": None,
                    "source_span_ids": [span.span_id],
                }
                for index, span in enumerate(spans)
            ]
        )

        pages = compile_wiki_fast(model, spans=spans)

        self.assertEqual(len(pages), 20)
        self.assertEqual(model.stages, ["topic_plan"])
        self.assertEqual(sum(len(page.claims) for page in pages), 20)

    def test_a_merged_plan_with_fewer_pages_is_equally_legal(self):
        spans = sample_spans()
        model = planning_model(spans=spans, merge=True)

        pages = compile_wiki_fast(model, spans=spans)

        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0].claims), 20)
        self.assertEqual(model.stages, ["topic_plan"])

    def test_one_page_per_heading_is_legal(self):
        spans = sample_spans()
        model = planning_model(spans=spans)

        pages = compile_wiki_fast(model, spans=spans)

        self.assertEqual(len(pages), len({span.heading for span in spans}))
        self.assertEqual(model.stages, ["topic_plan"])

    def test_the_planner_prompt_asks_for_judgement_not_a_cap(self):
        spans = sample_spans()
        model = planning_model(spans=spans)
        compile_wiki_fast(model, spans=spans)

        request = model.requests[0]
        self.assertIn("页数没有上限，由内容本身决定", request.system)
        self.assertIn("不要为了减少页数", request.system)
        self.assertNotIn("最多只能生成", request.user)

    def test_a_page_with_no_spans_is_still_rejected(self):
        spans = sample_spans()
        model = planning_model(
            pages=[
                {
                    "topic": "空页",
                    "existing_page_id": None,
                    "source_span_ids": [],
                }
            ]
        )
        with self.assertRaises(WikiCompilationError):
            compile_wiki_fast(model, spans=spans)

    def test_pages_can_never_outnumber_spans(self):
        """Not a cap, a consequence: every page needs a span and every span is
        assigned once."""
        spans = sample_spans()
        model = planning_model(spans=spans)
        pages = compile_wiki_fast(model, spans=spans)
        self.assertLessEqual(len(pages), len(spans))


class FaithfulContentTests(unittest.TestCase):
    def test_every_span_becomes_exactly_one_claim_with_its_own_text(self):
        spans = sample_spans()
        model = planning_model(spans=spans)

        pages = compile_wiki_fast(model, spans=spans)

        claims = [claim for page in pages for claim in page.claims]
        self.assertEqual(len(claims), len(spans))
        # Each span appears once, and the claim is the span verbatim.
        by_span = {}
        for claim in claims:
            self.assertEqual(len(claim.source_span_ids), 1)
            span_id = claim.source_span_ids[0]
            self.assertNotIn(span_id, by_span, "a span was used twice")
            by_span[span_id] = claim
        self.assertEqual(set(by_span), {span.span_id for span in spans})
        for span in spans:
            claim = by_span[span.span_id]
            self.assertEqual(claim.text, span.text)
            self.assertEqual(claim.source, span.source)
            self.assertEqual(claim.locator, f"section:{span.heading}")

    def test_a_summary_names_headings_and_asserts_nothing_else(self):
        spans = spans_of(LEAVE_DOC)
        model = planning_model(spans=spans)
        page = compile_wiki_fast(model, spans=spans)[0]

        self.assertEqual(page.summary, "本页涵盖：请假制度。")
        self.assertEqual(page.title, "请假制度")
        # The heading equals the title, so it is not repeated as an alias.
        self.assertEqual(page.aliases, ())

    def test_merged_pages_keep_their_headings_as_aliases(self):
        spans = list(spans_of(LEAVE_DOC)) + list(
            spans_of(REMOTE_DOC, document_id="remote-md", source="remote.md")
        )
        # The planner chose to merge these two topics onto one page.
        model = planning_model(spans=spans, merge=True)

        pages = compile_wiki_fast(model, spans=spans)

        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page.title, "请假制度")
        self.assertEqual(page.aliases, ("远程办公",))
        self.assertEqual(page.summary, "本页涵盖：请假制度、远程办公。")
        self.assertEqual(
            sorted(claim.source for claim in page.claims),
            ["handbook.md", "handbook.md", "remote.md"],
        )

    def test_a_headingless_document_cites_spans_and_titles_by_topic(self):
        spans = spans_of(PLAIN_TEXT_DOC, source="notes.txt")
        self.assertTrue(all(span.heading is None for span in spans))
        model = planning_model(
            pages=[
                {
                    "topic": "远程办公",
                    "existing_page_id": None,
                    "source_span_ids": [span.span_id for span in spans],
                }
            ]
        )

        page = compile_wiki_fast(model, spans=spans)[0]

        self.assertEqual(page.title, "远程办公")
        self.assertEqual(page.aliases, ())
        self.assertEqual(page.summary, "本页涵盖：远程办公。")
        for claim, span in zip(page.claims, spans):
            self.assertEqual(claim.locator, f"span:{span.span_id}")
            self.assertEqual(claim.text, span.text)

    def test_repeated_text_gets_distinct_stable_claim_ids(self):
        repeated = "## 通用\n\n本条适用于全体员工。\n\n本条适用于全体员工。\n"
        spans = spans_of(repeated)
        self.assertEqual(len({span.text for span in spans}), 1)
        model = planning_model(spans=spans)

        page = compile_wiki_fast(model, spans=spans)[0]

        ids = [claim.claim_id for claim in page.claims]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, "duplicate text must not collide")
        # And the same input produces the same ids again.
        again = compile_wiki_fast(planning_model(spans=spans), spans=spans)[0]
        self.assertEqual([claim.claim_id for claim in again.claims], ids)


class DeterminismTests(unittest.TestCase):
    def test_recompiling_the_same_spans_produces_an_identical_wiki(self):
        spans = sample_spans()
        first = compile_wiki_fast(planning_model(spans=spans), spans=spans)
        second = compile_wiki_fast(planning_model(spans=spans), spans=spans)

        self.assertEqual(first, second)

    def test_reusing_a_page_id_keeps_it_and_bumps_only_on_change(self):
        spans = spans_of(LEAVE_DOC)
        original = compile_wiki_fast(planning_model(spans=spans), spans=spans)[0]
        self.assertEqual(original.version, "1.0")

        # Same content, reusing the page: nothing changed, so the version holds.
        unchanged = compile_wiki_fast(
            planning_model(spans=spans, existing_page_ids={"请假制度": original.page_id}),
            spans=spans,
            existing_pages=(original,),
        )[0]
        self.assertEqual(unchanged.page_id, original.page_id)
        self.assertEqual(unchanged.version, "1.0")

        # Edited document, same page: the version moves.
        edited = spans_of(LEAVE_DOC_V2)
        changed = compile_wiki_fast(
            planning_model(spans=edited, existing_page_ids={"请假制度": original.page_id}),
            spans=edited,
            existing_pages=(original,),
        )[0]
        self.assertEqual(changed.page_id, original.page_id)
        self.assertEqual(changed.version, "1.1")
        self.assertIn("8 天带薪年假", " ".join(c.text for c in changed.claims))
        self.assertNotIn("5 天带薪年假", " ".join(c.text for c in changed.claims))


class MaintainerUsesTheFastPathTests(unittest.TestCase):
    def ingest(self, repository, text, *, filename=SOURCE, model=None):
        snapshot = build_document_snapshot_from_text(
            document_id=derive_document_id(filename), filename=filename, text=text
        )
        repository.save_document_snapshot(snapshot)
        model = model or planning_model(spans=snapshot.spans)
        return WikiMaintainer(repository, model).ingest(snapshot), model, snapshot

    def test_only_two_model_calls_and_no_page_compilation(self):
        with TempRepository() as repository:
            outcome, model, snapshot = self.ingest(repository, LEAVE_DOC)

            self.assertEqual(model.stages, ["document_decision", "topic_plan"])
            self.assertFalse(
                any(stage.startswith("page_compilation") for stage in model.stages)
            )
            self.assertEqual(outcome.page_count, 1)

    def test_the_full_sample_document_compiles_in_two_calls(self):
        with TempRepository() as repository:
            text = REPO_ROOT.joinpath(SAMPLE_RULES).read_text(encoding="utf-8")
            outcome, model, snapshot = self.ingest(
                repository, text, filename=SAMPLE_RULES
            )

            self.assertEqual(model.stages, ["document_decision", "topic_plan"])
            self.assertEqual(len(snapshot.spans), 20)
            # One page per heading here, because that is what this planner
            # chose - not because a budget forced a number.
            self.assertEqual(
                outcome.page_count, len({s.heading for s in snapshot.spans})
            )

            claims = [c for p in outcome.build.pages for c in p.claims]
            self.assertEqual(len(claims), 20)
            cited = [c.source_span_ids[0] for c in claims]
            self.assertEqual(sorted(cited), sorted(s.span_id for s in snapshot.spans))
            self.assertEqual(len(set(cited)), 20, "each span exactly once")

            # The build round-trips, and every claim is its span verbatim.
            by_id = {s.span_id: s for s in snapshot.spans}
            for claim in claims:
                self.assertEqual(claim.text, by_id[claim.source_span_ids[0]].text)
            self.assertEqual(
                repository.load_build(outcome.build.build_id), outcome.build
            )

    def test_a_second_document_merges_into_the_effective_corpus(self):
        with TempRepository() as repository:
            first, _, first_snapshot = self.ingest(
                repository, LEAVE_DOC, filename="leave.md"
            )
            repository.publish(first.build.build_id)

            remote_snapshot = build_document_snapshot_from_text(
                document_id=derive_document_id("remote.md"),
                filename="remote.md",
                text=REMOTE_DOC,
            )
            repository.save_document_snapshot(remote_snapshot)
            combined = list(first_snapshot.spans) + list(remote_snapshot.spans)
            model = planning_model(spans=combined)
            outcome = WikiMaintainer(repository, model).ingest(remote_snapshot)

            self.assertEqual(model.stages, ["document_decision", "topic_plan"])
            sources = {c.source for p in outcome.build.pages for c in p.claims}
            self.assertEqual(sources, {"leave.md", "remote.md"})
            self.assertEqual(
                sorted(outcome.build.document_version_map),
                sorted([first_snapshot.document_id, remote_snapshot.document_id]),
            )

    def test_a_new_version_replaces_the_old_facts(self):
        with TempRepository() as repository:
            first, _, _ = self.ingest(repository, LEAVE_DOC, filename="leave.md")
            repository.publish(first.build.build_id)

            outcome, _, _ = self.ingest(repository, LEAVE_DOC_V2, filename="leave.md")

            texts = " ".join(c.text for p in outcome.build.pages for c in p.claims)
            self.assertIn("8 天带薪年假", texts)
            self.assertNotIn("5 天带薪年假", texts)

    def test_recompiling_an_unchanged_document_yields_an_empty_diff(self):
        with TempRepository() as repository:
            first, _, snapshot = self.ingest(repository, LEAVE_DOC)
            repository.publish(first.build.build_id)
            second = WikiMaintainer(
                repository, planning_model(spans=snapshot.spans)
            ).ingest(snapshot)

            self.assertNotEqual(first.build.build_id, second.build.build_id)
            self.assertTrue(diff_builds(first.build, second.build).is_empty)

    def test_the_batch_compiler_is_still_available_when_asked_for(self):
        """M9D-B is kept, not deleted - it is simply no longer the default."""
        with TempRepository() as repository:
            model = planning_model(pages=[])
            self.assertIs(
                WikiMaintainer(repository, model).compile_pages, compile_wiki_fast
            )
            self.assertIs(
                WikiMaintainer(
                    repository, model, compile_pages=compile_wiki
                ).compile_pages,
                compile_wiki,
            )


if __name__ == "__main__":
    unittest.main()
