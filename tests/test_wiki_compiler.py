"""M9B: the Wiki Maintainer Agent.

Every test drives a scripted model. Nothing here reaches Ollama, the network,
`data/`, or the product database, and every repository lives in a
`TemporaryDirectory`.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wiki_maintenance import ollama_compiler

from orchestration.wiki_adapter import DEFAULT_WIKI_PATH, load_wiki_pages
from orchestration.wiki_schema import WikiClaim, WikiPage

from wiki_maintenance import (
    DocumentAction,
    ModelRequest,
    WikiCompilationError,
    WikiMaintainer,
    WikiRepository,
    WikiRepositoryError,
    bootstrap_from_wiki_file,
    build_document_snapshot_from_text,
    compile_wiki,
    derive_document_id,
    diff_builds,
    new_page_id,
    next_page_version,
)
from wiki_maintenance.compiler import PagePlan, compile_page, request_json

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RULES = "sample_company_rules.md"

LEAVE_DOC = """# 员工手册

## 请假制度

正式员工入职满一年后，每年享有 5 天带薪年假。

请假需提前在系统提交申请，1 天以内由直属主管审批。
"""

# A single span, for tests whose point is one page rather than the partition.
LEAVE_DOC_ONE_SPAN = """# 员工手册

## 请假制度

正式员工入职满一年后，每年享有 5 天带薪年假。
"""

# No Markdown headings at all: a plain TXT, or a PDF that extracts as prose.
PLAIN_TEXT_DOC = """员工每周最多申请 2 天远程办公。

远程办公须至少提前一个工作日获得直属主管批准。
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


def dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


class ScriptedModel:
    """A `WikiModel` that answers from a script and records what it was asked."""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return self._handler(request)

    @property
    def stages(self) -> list[str]:
        return [request.stage for request in self.requests]


def topic_of(request: ModelRequest) -> str:
    return request.stage.split(":", 1)[1]


def batch_sections(request: ModelRequest) -> list[tuple[int, str, str]]:
    """`(index, topic, section text)` for every page in a batch prompt.

    A stand-in model has to read the batch the way a real one does: each page's
    spans live in its own section, so a fake that looked at the whole prompt
    could cite a sibling page's material and never notice.
    """
    sections = []
    for chunk in request.user.split("===== 第 ")[1:]:
        index = int(chunk.split(" ", 1)[0])
        topic = re.search(r"^topic：(.+)$", chunk, re.M).group(1).strip()
        sections.append((index, topic, chunk))
    return sections


def batch_reply(request: ModelRequest, page_for) -> str:
    """Answer a page batch by calling `page_for(topic, section)` per page."""
    return dumps(
        {
            "pages": [
                {"index": index, "topic": topic, **page_for(topic, section)}
                for index, topic, section in batch_sections(request)
            ]
        }
    )


def script(*, decision=None, plan=None, pages=None):
    """A handler answering each stage from a fixed payload."""
    decision = decision or {
        "action": "update",
        "reason": "公司制度文档",
        "supersedes_document_ids": [],
    }

    def handler(request: ModelRequest) -> str:
        if request.stage == "document_decision":
            return dumps(decision)
        if request.stage == "topic_plan":
            return dumps(plan)
        if request.stage.startswith("page_compilation:"):
            return batch_reply(request, lambda topic, section: pages[topic])
        raise AssertionError(f"unscripted stage {request.stage!r}")

    return handler


def heading_script(spans):
    """A deterministic stand-in for the model: one page per source heading.

    Claims quote their span verbatim, so this exercises the real pipeline -
    planning, id generation, provenance, number checking - without a model.
    """
    grouped: dict[str, list] = {}
    for span in spans:
        grouped.setdefault(span.heading or "未分类", []).append(span)

    def handler(request: ModelRequest) -> str:
        if request.stage == "document_decision":
            return dumps(
                {"action": "update", "reason": "员工手册", "supersedes_document_ids": []}
            )
        if request.stage == "topic_plan":
            return dumps(
                {
                    "pages": [
                        {
                            "topic": heading,
                            "existing_page_id": None,
                            "source_span_ids": [span.span_id for span in group],
                        }
                        for heading, group in grouped.items()
                    ]
                }
            )
        if request.stage.startswith("page_compilation:"):

            def page_for(topic, section):
                group = grouped[topic]
                return {
                    "title": topic,
                    "summary": f"{topic}：{group[0].text}",
                    "aliases": [],
                    "claims": [
                        {
                            "text": span.text,
                            "source_span_ids": [span.span_id],
                            "existing_claim_id": None,
                        }
                        for span in group
                    ],
                }

            return batch_reply(request, page_for)
        raise AssertionError(f"unscripted stage {request.stage!r}")

    return handler


class TempRepository:
    def __enter__(self) -> WikiRepository:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        return WikiRepository(self.root)

    def __exit__(self, *exc_info) -> None:
        self._directory.cleanup()


def save_document(repository, *, filename: str, text: str):
    snapshot = build_document_snapshot_from_text(
        document_id=derive_document_id(filename), filename=filename, text=text
    )
    repository.save_document_snapshot(snapshot)
    return snapshot


def sample_spans():
    text = REPO_ROOT.joinpath(SAMPLE_RULES).read_text(encoding="utf-8")
    return build_document_snapshot_from_text(
        document_id=derive_document_id(SAMPLE_RULES), filename=SAMPLE_RULES, text=text
    )


# --------------------------------------------------------------------------
# Document decision
# --------------------------------------------------------------------------


class DocumentDecisionTests(unittest.TestCase):
    def test_ignore_creates_no_build_and_leaves_current_alone(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="notes.md", text=LEAVE_DOC)
            model = ScriptedModel(
                script(
                    decision={
                        "action": "ignore",
                        "reason": "个人笔记，与制度无关",
                        "supersedes_document_ids": [],
                    }
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            self.assertIs(outcome.action, DocumentAction.IGNORE)
            self.assertEqual(outcome.reason, "个人笔记，与制度无关")
            self.assertFalse(outcome.created_build)
            self.assertIsNone(outcome.build)
            self.assertEqual(repository.list_builds(), ())
            self.assertIsNone(repository.get_current_build_id())
            # It stopped after the decision: no planning, no compilation.
            self.assertEqual(model.stages, ["document_decision"])

    def test_update_creates_a_draft_without_publishing(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            model = ScriptedModel(heading_script(snapshot.spans))

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            self.assertIs(outcome.action, DocumentAction.UPDATE)
            self.assertTrue(outcome.created_build)
            self.assertEqual(outcome.build.build_id, "build-0001")
            self.assertEqual(outcome.page_count, 1)
            record = repository.get_build_record("build-0001")
            self.assertEqual(record.status.value, "draft")
            self.assertIsNone(repository.get_current_build_id())
            self.assertEqual(
                outcome.build.document_version_map,
                {snapshot.document_id: snapshot.version},
            )

    def test_supersedes_retires_the_named_documents(self):
        with TempRepository() as repository:
            old = save_document(repository, filename="old.md", text=REMOTE_DOC)
            first = WikiMaintainer(
                repository, ScriptedModel(heading_script(old.spans))
            ).ingest(old)
            repository.publish(first.build.build_id)

            new = save_document(repository, filename="new.md", text=LEAVE_DOC_ONE_SPAN)
            model = ScriptedModel(
                script(
                    decision={
                        "action": "update",
                        "reason": "新版制度取代旧文件",
                        "supersedes_document_ids": [old.document_id],
                    },
                    plan={
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": None,
                                "source_span_ids": [new.spans[0].span_id],
                            }
                        ]
                    },
                    pages={
                        "请假制度": {
                            "title": "请假制度",
                            "summary": "年假天数规定。",
                            "aliases": ["年假"],
                            "claims": [
                                {
                                    "text": new.spans[0].text,
                                    "source_span_ids": [new.spans[0].span_id],
                                    "existing_claim_id": None,
                                }
                            ],
                        }
                    },
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(new)

            self.assertEqual(outcome.superseded_document_ids, (old.document_id,))
            self.assertEqual(
                [entry.document_id for entry in outcome.effective_documents],
                [new.document_id],
            )
            self.assertEqual(
                outcome.build.document_version_map,
                {new.document_id: new.version},
            )


# --------------------------------------------------------------------------
# Effective document set
# --------------------------------------------------------------------------


class EffectiveDocumentSetTests(unittest.TestCase):
    def test_a_new_version_replaces_the_old_version_of_the_same_document(self):
        with TempRepository() as repository:
            first = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            outcome = WikiMaintainer(
                repository, ScriptedModel(heading_script(first.spans))
            ).ingest(first)
            repository.publish(outcome.build.build_id)

            second = save_document(repository, filename="rules.md", text=LEAVE_DOC_V2)
            self.assertEqual(second.document_id, first.document_id)
            self.assertNotEqual(second.version, first.version)

            updated = WikiMaintainer(
                repository, ScriptedModel(heading_script(second.spans))
            ).ingest(second)

            versions = updated.build.document_version_map
            self.assertEqual(versions, {second.document_id: second.version})
            self.assertNotIn(first.version, versions.values())
            # The Wiki now states the new figure and no longer the old one.
            claims = [claim.text for page in updated.build.pages for claim in page.claims]
            self.assertTrue(any("8 天" in text for text in claims))
            self.assertFalse(any("5 天" in text for text in claims))

    def test_two_documents_can_be_merged_into_one_page(self):
        with TempRepository() as repository:
            leave = save_document(
                repository, filename="leave.md", text=LEAVE_DOC_ONE_SPAN
            )
            first = WikiMaintainer(
                repository, ScriptedModel(heading_script(leave.spans))
            ).ingest(leave)
            repository.publish(first.build.build_id)

            remote = save_document(repository, filename="remote.md", text=REMOTE_DOC)
            merged_spans = [leave.spans[0].span_id, remote.spans[0].span_id]
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "考勤与办公方式",
                                "existing_page_id": None,
                                "source_span_ids": merged_spans,
                            }
                        ]
                    },
                    pages={
                        "考勤与办公方式": {
                            "title": "考勤与办公方式",
                            "summary": "年假与远程办公的基本规定。",
                            "aliases": ["年假", "远程"],
                            "claims": [
                                {
                                    "text": leave.spans[0].text,
                                    "source_span_ids": [leave.spans[0].span_id],
                                    "existing_claim_id": None,
                                },
                                {
                                    "text": remote.spans[0].text,
                                    "source_span_ids": [remote.spans[0].span_id],
                                    "existing_claim_id": None,
                                },
                            ],
                        }
                    },
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(remote)

            self.assertEqual(len(outcome.build.pages), 1)
            page = outcome.build.pages[0]
            self.assertEqual(len(page.claims), 2)
            self.assertEqual(
                {claim.source for claim in page.claims}, {"leave.md", "remote.md"}
            )
            # Both documents stay in the effective set.
            self.assertEqual(
                sorted(outcome.build.document_version_map),
                sorted([leave.document_id, remote.document_id]),
            )

    def test_one_document_can_be_split_into_several_pages(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "年假天数",
                                "existing_page_id": None,
                                "source_span_ids": [snapshot.spans[0].span_id],
                            },
                            {
                                "topic": "请假审批",
                                "existing_page_id": None,
                                "source_span_ids": [snapshot.spans[1].span_id],
                            },
                        ]
                    },
                    pages={
                        "年假天数": {
                            "title": "年假天数",
                            "summary": "正式员工的年假天数。",
                            "aliases": [],
                            "claims": [
                                {
                                    "text": snapshot.spans[0].text,
                                    "source_span_ids": [snapshot.spans[0].span_id],
                                    "existing_claim_id": None,
                                }
                            ],
                        },
                        "请假审批": {
                            "title": "请假审批",
                            "summary": "请假的审批层级。",
                            "aliases": [],
                            "claims": [
                                {
                                    "text": snapshot.spans[1].text,
                                    "source_span_ids": [snapshot.spans[1].span_id],
                                    "existing_claim_id": None,
                                }
                            ],
                        },
                    },
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            self.assertEqual(
                [page.title for page in outcome.build.pages], ["年假天数", "请假审批"]
            )
            self.assertEqual(len({page.page_id for page in outcome.build.pages}), 2)


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


class IdentifierTests(unittest.TestCase):
    def test_ids_follow_content_not_the_model(self):
        self.assertEqual(new_page_id("请假制度"), new_page_id(" 请假制度 "))
        self.assertNotEqual(new_page_id("请假制度"), new_page_id("远程办公"))
        self.assertTrue(new_page_id("请假制度").startswith("wiki-"))

    def test_page_versions_bump_only_when_content_changes(self):
        self.assertEqual(next_page_version(None), "1.0")
        self.assertEqual(next_page_version("1.0"), "1.1")
        self.assertEqual(next_page_version("2.9"), "2.10")
        self.assertEqual(next_page_version("not-a-version"), "1.0")

    def test_recompiling_the_same_input_produces_the_same_ids(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            spans = snapshot.spans

            def compile_once():
                return compile_wiki(
                    ScriptedModel(heading_script(spans)), spans=spans
                )

            first, second = compile_once(), compile_once()
            self.assertEqual(
                [page.page_id for page in first], [page.page_id for page in second]
            )
            self.assertEqual(
                [claim.claim_id for page in first for claim in page.claims],
                [claim.claim_id for page in second for claim in page.claims],
            )

    def test_page_and_claim_ids_are_reused_when_the_plan_asks(self):
        with TempRepository() as repository:
            first_doc = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            first = WikiMaintainer(
                repository, ScriptedModel(heading_script(first_doc.spans))
            ).ingest(first_doc)
            repository.publish(first.build.build_id)

            old_page = first.build.pages[0]
            kept_claim_id = old_page.claims[1].claim_id
            self.assertEqual(old_page.version, "1.0")

            second_doc = save_document(repository, filename="rules.md", text=LEAVE_DOC_V2)
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": old_page.page_id,
                                "source_span_ids": [
                                    span.span_id for span in second_doc.spans
                                ],
                            }
                        ]
                    },
                    pages={
                        "请假制度": {
                            "title": "请假制度",
                            "summary": "年假与审批规定。",
                            "aliases": [],
                            "claims": [
                                {
                                    "text": second_doc.spans[0].text,
                                    "source_span_ids": [second_doc.spans[0].span_id],
                                    "existing_claim_id": None,
                                },
                                {
                                    "text": second_doc.spans[1].text,
                                    "source_span_ids": [second_doc.spans[1].span_id],
                                    "existing_claim_id": kept_claim_id,
                                },
                            ],
                        }
                    },
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(second_doc)

            page = outcome.build.pages[0]
            self.assertEqual(page.page_id, old_page.page_id)
            self.assertIn(kept_claim_id, [claim.claim_id for claim in page.claims])
            # Content changed, so the version moved.
            self.assertEqual(page.version, "1.1")

    def test_a_claim_id_the_model_invents_is_not_honoured(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": None,
                                "source_span_ids": [snapshot.spans[0].span_id],
                            }
                        ]
                    },
                    pages={
                        "请假制度": {
                            "title": "请假制度",
                            "summary": "年假天数。",
                            "aliases": [],
                            "claims": [
                                {
                                    "text": snapshot.spans[0].text,
                                    "source_span_ids": [snapshot.spans[0].span_id],
                                    "existing_claim_id": "claim-i-made-up",
                                }
                            ],
                        }
                    },
                )
            )

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            claim_id = outcome.build.pages[0].claims[0].claim_id
            self.assertNotEqual(claim_id, "claim-i-made-up")
            self.assertTrue(claim_id.startswith(outcome.build.pages[0].page_id))


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


class ProvenanceTests(unittest.TestCase):
    def _single_page_model(self, snapshot, *, claim_overrides=None):
        claim = {
            "text": snapshot.spans[0].text,
            "source_span_ids": [snapshot.spans[0].span_id],
            "existing_claim_id": None,
        }
        claim.update(claim_overrides or {})
        return ScriptedModel(
            script(
                plan={
                    "pages": [
                        {
                            "topic": "请假制度",
                            "existing_page_id": None,
                            "source_span_ids": [snapshot.spans[0].span_id],
                        }
                    ]
                },
                pages={
                    "请假制度": {
                        "title": "请假制度",
                        "summary": "年假天数规定。",
                        "aliases": [],
                        "claims": [claim],
                    }
                },
            )
        )

    def test_source_and_locator_come_from_the_span_not_the_model(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            # The model volunteers provenance; it must be ignored entirely.
            model = self._single_page_model(
                snapshot,
                claim_overrides={
                    "source": "别的文件.md",
                    "locator": "section:我编的小节",
                },
            )

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            claim = outcome.build.pages[0].claims[0]
            self.assertEqual(claim.source, snapshot.spans[0].source)
            self.assertEqual(claim.locator, snapshot.spans[0].locator)
            self.assertEqual(claim.locator, "section:请假制度")

    def test_source_span_ids_survive_a_save_and_load(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            outcome = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)

            expected = [span.span_id for span in snapshot.spans]
            reloaded = repository.load_build(outcome.build.build_id)
            self.assertEqual(
                [
                    list(claim.source_span_ids)
                    for page in reloaded.pages
                    for claim in page.claims
                ],
                [[span_id] for span_id in expected],
            )
            # And they really are on disk, not just in memory.
            payload = json.loads(
                repository.build_path(outcome.build.build_id).read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["pages"][0]["claims"][0]["source_span_ids"], [expected[0]]
            )

    def test_an_unknown_span_reference_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = self._single_page_model(
                snapshot, claim_overrides={"source_span_ids": ["span-doesnotexist"]}
            )

            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(snapshot)
            self.assertIn("span-doesnotexist", str(raised.exception))
            self.assertEqual(repository.list_builds(), ())

    def test_a_claim_citing_nothing_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = self._single_page_model(
                snapshot, claim_overrides={"source_span_ids": []}
            )
            with self.assertRaises(WikiCompilationError):
                WikiMaintainer(repository, model).ingest(snapshot)

    def test_an_invented_number_in_a_claim_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = self._single_page_model(
                snapshot,
                claim_overrides={"text": "正式员工每年享有 15 天带薪年假。"},
            )

            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(snapshot)
            self.assertIn("15", str(raised.exception))
            self.assertEqual(repository.list_builds(), ())

    def test_an_invented_number_in_a_summary_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": None,
                                "source_span_ids": [snapshot.spans[0].span_id],
                            }
                        ]
                    },
                    pages={
                        "请假制度": {
                            "title": "请假制度",
                            "summary": "员工每年享有 30 天带薪年假。",
                            "aliases": [],
                            "claims": [
                                {
                                    "text": snapshot.spans[0].text,
                                    "source_span_ids": [snapshot.spans[0].span_id],
                                    "existing_claim_id": None,
                                }
                            ],
                        }
                    },
                )
            )

            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(snapshot)
            self.assertIn("30", str(raised.exception))

    def test_a_number_that_is_in_the_source_passes(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            model = self._single_page_model(
                snapshot, claim_overrides={"text": "年假为 5 天。"}
            )
            outcome = WikiMaintainer(repository, model).ingest(snapshot)
            self.assertEqual(outcome.build.pages[0].claims[0].text, "年假为 5 天。")

    def test_a_headingless_document_cites_its_span(self):
        """A plain TXT or a PDF that extracts as prose has no sections, so the
        span is the finest address it really has."""
        with TempRepository() as repository:
            snapshot = save_document(
                repository, filename="notes.txt", text=PLAIN_TEXT_DOC
            )
            self.assertTrue(all(span.heading is None for span in snapshot.spans))

            outcome = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)

            claims = [claim for page in outcome.build.pages for claim in page.claims]
            self.assertEqual(len(claims), len(snapshot.spans))
            for claim, span in zip(claims, snapshot.spans):
                self.assertEqual(claim.locator, f"span:{span.span_id}")
                self.assertEqual(claim.source_span_ids, (span.span_id,))
            # And it round-trips: the schema accepts the span form on load.
            reloaded = repository.load_build(outcome.build.build_id)
            self.assertEqual(reloaded, outcome.build)

    def test_the_section_form_still_wins_when_the_document_has_headings(self):
        with TempRepository() as repository:
            snapshot = save_document(
                repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN
            )
            outcome = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)
            self.assertEqual(
                outcome.build.pages[0].claims[0].locator, "section:请假制度"
            )


class LocatorFormTests(unittest.TestCase):
    def test_the_schema_accepts_both_locator_forms(self):
        for locator in ("section:请假制度", "span:span-abc123"):
            with self.subTest(locator=locator):
                claim = WikiClaim(
                    claim_id="c1",
                    text="年假为 5 天。",
                    source="rules.md",
                    locator=locator,
                )
                self.assertEqual(claim.locator, locator)

    def test_malformed_locators_are_still_rejected(self):
        for bad in ("span:", "span:   ", "Span:span-abc", "bad-locator", "section:"):
            with self.subTest(locator=bad):
                with self.assertRaises(ValueError):
                    WikiClaim(
                        claim_id="c1", text="年假。", source="rules.md", locator=bad
                    )

    def test_the_committed_wiki_still_uses_only_the_section_form(self):
        for page in load_wiki_pages(DEFAULT_WIKI_PATH):
            for claim in page.claims:
                self.assertTrue(claim.locator.startswith("section:"))


# --------------------------------------------------------------------------
# Topic plan validation
# --------------------------------------------------------------------------


class TopicPlanValidationTests(unittest.TestCase):
    def _ingest(self, repository, snapshot, plan, pages=None):
        model = ScriptedModel(script(plan=plan, pages=pages or {}))
        return WikiMaintainer(repository, model).ingest(snapshot)

    def _page(self, topic, span):
        return {
            "title": topic,
            "summary": "年假规定。",
            "aliases": [],
            "claims": [
                {
                    "text": span.text,
                    "source_span_ids": [span.span_id],
                    "existing_claim_id": None,
                }
            ],
        }

    def test_a_span_assigned_to_two_pages_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            shared = snapshot.spans[0].span_id
            plan = {
                "pages": [
                    {"topic": "甲", "existing_page_id": None, "source_span_ids": [shared]},
                    {
                        "topic": "乙",
                        "existing_page_id": None,
                        "source_span_ids": [shared, snapshot.spans[1].span_id],
                    },
                ]
            }

            with self.assertRaises(WikiCompilationError) as raised:
                self._ingest(repository, snapshot, plan)
            message = str(raised.exception)
            self.assertIn(shared, message)
            self.assertIn("exactly one page", message)
            self.assertEqual(repository.list_builds(), ())

    def test_an_unassigned_span_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            plan = {
                "pages": [
                    {
                        "topic": "请假制度",
                        "existing_page_id": None,
                        "source_span_ids": [snapshot.spans[0].span_id],
                    }
                ]
            }

            with self.assertRaises(WikiCompilationError) as raised:
                self._ingest(repository, snapshot, plan)
            message = str(raised.exception)
            self.assertIn("unassigned", message)
            self.assertIn(snapshot.spans[1].span_id, message)
            self.assertEqual(repository.list_builds(), ())

    def test_an_unknown_span_in_the_plan_is_rejected(self):
        with TempRepository() as repository:
            snapshot = save_document(
                repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN
            )
            plan = {
                "pages": [
                    {
                        "topic": "请假制度",
                        "existing_page_id": None,
                        "source_span_ids": [
                            snapshot.spans[0].span_id,
                            "span-invented",
                        ],
                    }
                ]
            }

            with self.assertRaises(WikiCompilationError) as raised:
                self._ingest(repository, snapshot, plan)
            self.assertIn("span-invented", str(raised.exception))
            self.assertEqual(repository.list_builds(), ())

    def test_a_claim_may_not_cite_a_span_outside_its_own_page(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            first, second = snapshot.spans
            plan = {
                "pages": [
                    {
                        "topic": "甲",
                        "existing_page_id": None,
                        "source_span_ids": [first.span_id],
                    },
                    {
                        "topic": "乙",
                        "existing_page_id": None,
                        "source_span_ids": [second.span_id],
                    },
                ]
            }
            pages = {
                # Page 甲 reaches for the span that belongs to page 乙.
                "甲": {
                    "title": "甲",
                    "summary": "概述。",
                    "aliases": [],
                    "claims": [
                        {
                            "text": second.text,
                            "source_span_ids": [second.span_id],
                            "existing_claim_id": None,
                        }
                    ],
                },
                "乙": self._page("乙", second),
            }

            with self.assertRaises(WikiCompilationError) as raised:
                self._ingest(repository, snapshot, plan, pages)
            self.assertIn(second.span_id, str(raised.exception))
            self.assertEqual(repository.list_builds(), ())

    def test_a_claim_may_cite_a_subset_of_its_page_spans(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            plan = {
                "pages": [
                    {
                        "topic": "请假制度",
                        "existing_page_id": None,
                        "source_span_ids": [
                            span.span_id for span in snapshot.spans
                        ],
                    }
                ]
            }
            pages = {"请假制度": self._page("请假制度", snapshot.spans[0])}

            outcome = self._ingest(repository, snapshot, plan, pages)

            self.assertEqual(
                outcome.build.pages[0].claims[0].source_span_ids,
                (snapshot.spans[0].span_id,),
            )

    def test_two_topics_may_not_reuse_the_same_existing_page(self):
        """Caught before any page is compiled: two topics sharing one page id
        would collapse into a single page and silently lose one of them."""
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
            reused = load_wiki_pages(DEFAULT_WIKI_PATH)[0].page_id
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            plan = {
                "pages": [
                    {
                        "topic": "甲",
                        "existing_page_id": reused,
                        "source_span_ids": [snapshot.spans[0].span_id],
                    },
                    {
                        "topic": "乙",
                        "existing_page_id": reused,
                        "source_span_ids": [snapshot.spans[1].span_id],
                    },
                ]
            }
            model = ScriptedModel(script(plan=plan, pages={}))

            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(snapshot)

            self.assertIn("more than one topic", str(raised.exception))
            self.assertNotIn(
                "page_compilation",
                " ".join(model.stages),
                "no page should be compiled once the plan is known bad",
            )

    def test_an_unknown_existing_page_id_is_rejected_not_silently_new(self):
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
            snapshot = save_document(
                repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN
            )
            plan = {
                "pages": [
                    {
                        "topic": "请假制度",
                        "existing_page_id": "wiki-does-not-exist",
                        "source_span_ids": [snapshot.spans[0].span_id],
                    }
                ]
            }

            with self.assertRaises(WikiCompilationError) as raised:
                self._ingest(
                    repository,
                    snapshot,
                    plan,
                    {"请假制度": self._page("请假制度", snapshot.spans[0])},
                )
            message = str(raised.exception)
            self.assertIn("wiki-does-not-exist", message)
            self.assertIn("not an existing page", message)
            self.assertEqual(
                [record.build_id for record in repository.list_builds()], ["build-0001"]
            )


# --------------------------------------------------------------------------
# Supersede validation
# --------------------------------------------------------------------------


class SupersedeValidationTests(unittest.TestCase):
    def _run(self, supersedes):
        with TempRepository() as repository:
            old = save_document(repository, filename="old.md", text=REMOTE_DOC)
            first = WikiMaintainer(
                repository, ScriptedModel(heading_script(old.spans))
            ).ingest(old)
            repository.publish(first.build.build_id)

            new = save_document(
                repository, filename="new.md", text=LEAVE_DOC_ONE_SPAN
            )
            model = ScriptedModel(
                script(
                    decision={
                        "action": "update",
                        "reason": "取代旧文件",
                        "supersedes_document_ids": [
                            entry.format(old=old.document_id, new=new.document_id)
                            for entry in supersedes
                        ],
                    }
                )
            )
            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(new)

            # The failure happened before any build was written.
            self.assertEqual(
                [record.build_id for record in repository.list_builds()], ["build-0001"]
            )
            self.assertEqual(repository.get_current_build_id(), "build-0001")
            return str(raised.exception)

    def test_an_unknown_document_id_is_rejected(self):
        message = self._run(["never-collected"])
        self.assertIn("never-collected", message)
        self.assertIn("not an active document", message)

    def test_the_incoming_document_may_not_supersede_itself(self):
        message = self._run(["{new}"])
        self.assertIn("incoming document", message)

    def test_a_repeated_document_id_is_rejected(self):
        message = self._run(["{old}", "{old}"])
        self.assertIn("more than once", message)


# --------------------------------------------------------------------------
# Malformed model output
# --------------------------------------------------------------------------


class RepairTests(unittest.TestCase):
    def test_one_repair_attempt_recovers_a_bad_response(self):
        calls = []

        def handler(request):
            calls.append(request.stage)
            if request.stage == "document_decision":
                return "这不是 JSON"
            if request.stage == "document_decision_repair":
                return dumps(
                    {"action": "ignore", "reason": "已修复", "supersedes_document_ids": []}
                )
            raise AssertionError(request.stage)

        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            outcome = WikiMaintainer(repository, ScriptedModel(handler)).ingest(snapshot)

        self.assertIs(outcome.action, DocumentAction.IGNORE)
        self.assertEqual(calls, ["document_decision", "document_decision_repair"])

    def test_a_second_failure_is_not_retried_again(self):
        model = ScriptedModel(lambda request: "还是不是 JSON")

        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            with self.assertRaises(WikiCompilationError) as raised:
                WikiMaintainer(repository, model).ingest(snapshot)

        self.assertEqual(
            model.stages, ["document_decision", "document_decision_repair"]
        )
        self.assertIn("one repair attempt", str(raised.exception))

    def test_repair_also_covers_a_valid_json_object_of_the_wrong_shape(self):
        def handler(request):
            if request.stage == "topic_plan":
                return dumps({"pages": []})
            if request.stage == "topic_plan_repair":
                return dumps(
                    {
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": None,
                                "source_span_ids": [handler.span_id],
                            }
                        ]
                    }
                )
            if request.stage.startswith("page_compilation:"):
                return batch_reply(
                    request,
                    lambda topic, section: {
                        "title": "请假制度",
                        "summary": "年假规定。",
                        "aliases": [],
                        "claims": [
                            {
                                "text": handler.text,
                                "source_span_ids": [handler.span_id],
                                "existing_claim_id": None,
                            }
                        ],
                    },
                )
            return dumps(
                {"action": "update", "reason": "制度", "supersedes_document_ids": []}
            )

        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC_ONE_SPAN)
            handler.span_id = snapshot.spans[0].span_id
            handler.text = snapshot.spans[0].text
            model = ScriptedModel(handler)

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

        self.assertIn("topic_plan_repair", model.stages)
        self.assertEqual(outcome.page_count, 1)

    def test_a_fenced_json_block_is_still_read(self):
        payload = dumps({"action": "ignore", "reason": "无关", "supersedes_document_ids": []})
        model = ScriptedModel(lambda request: f"```json\n{payload}\n```")

        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            outcome = WikiMaintainer(repository, model).ingest(snapshot)

        self.assertIs(outcome.action, DocumentAction.IGNORE)
        self.assertEqual(model.stages, ["document_decision"])


# --------------------------------------------------------------------------
# Failure leaves the live Wiki alone
# --------------------------------------------------------------------------


class FailureIsolationTests(unittest.TestCase):
    def test_a_failed_compile_changes_neither_current_nor_the_build_list(self):
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
            before_current = repository.get_current_build_id()
            before_builds = [record.build_id for record in repository.list_builds()]
            before_bytes = repository.build_path("build-0001").read_bytes()

            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            model = ScriptedModel(
                script(
                    plan={
                        "pages": [
                            {
                                "topic": "请假制度",
                                "existing_page_id": None,
                                "source_span_ids": ["span-nonexistent"],
                            }
                        ]
                    },
                    pages={},
                )
            )

            with self.assertRaises(WikiCompilationError):
                WikiMaintainer(repository, model).ingest(snapshot)

            self.assertEqual(repository.get_current_build_id(), before_current)
            self.assertEqual(
                [record.build_id for record in repository.list_builds()], before_builds
            )
            self.assertEqual(repository.build_path("build-0001").read_bytes(), before_bytes)

    def test_an_unsaved_snapshot_is_refused_before_any_model_call(self):
        with TempRepository() as repository:
            snapshot = build_document_snapshot_from_text(
                document_id="rules-md", filename="rules.md", text=LEAVE_DOC
            )
            model = ScriptedModel(lambda request: dumps({}))

            with self.assertRaises(WikiRepositoryError):
                WikiMaintainer(repository, model).ingest(snapshot)
            self.assertEqual(model.stages, [])

    def test_compiling_from_an_existing_build_reuses_nothing_it_cannot_re_derive(self):
        """Old pages inform ids, never facts: a topic dropped from the effective
        documents disappears from the new build."""
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
            sample_page_ids = {page.page_id for page in load_wiki_pages(DEFAULT_WIKI_PATH)}

            snapshot = save_document(repository, filename="rules.md", text=REMOTE_DOC)
            outcome = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)

            self.assertEqual(outcome.build.base_build_id, "build-0001")
            new_ids = {page.page_id for page in outcome.build.pages}
            self.assertTrue(new_ids.isdisjoint(sample_page_ids))
            self.assertEqual(len(outcome.build.pages), 1)


# --------------------------------------------------------------------------
# End to end on the real document
# --------------------------------------------------------------------------


class RealDocumentCompilationTests(unittest.TestCase):
    def test_the_sample_rules_compile_into_many_traceable_pages(self):
        with TempRepository() as repository:
            snapshot = sample_spans()
            repository.save_document_snapshot(snapshot)
            model = ScriptedModel(heading_script(snapshot.spans))

            outcome = WikiMaintainer(repository, model).ingest(snapshot)

            self.assertGreaterEqual(len(outcome.build.pages), 4)
            span_ids = {span.span_id for span in snapshot.spans}
            for page in outcome.build.pages:
                self.assertTrue(page.claims)
                self.assertEqual(page.version, "1.0")
                for claim in page.claims:
                    self.assertTrue(claim.source_span_ids)
                    self.assertTrue(set(claim.source_span_ids) <= span_ids)
                    self.assertEqual(claim.source, SAMPLE_RULES)
                    self.assertTrue(claim.locator.startswith("section:"))

            # Every claim's locator names a heading that really is in the file.
            headings = {
                line[3:].strip()
                for line in REPO_ROOT.joinpath(SAMPLE_RULES)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.startswith("## ")
            }
            for page in outcome.build.pages:
                for claim in page.claims:
                    self.assertIn(claim.locator[len("section:") :], headings)

            # And the whole thing round-trips through the repository.
            reloaded = repository.load_build(outcome.build.build_id)
            self.assertEqual(reloaded, outcome.build)

    def test_recompiling_an_unchanged_document_produces_an_identical_wiki(self):
        """The end-to-end payoff of program-assigned ids: a rebuild that changed
        nothing must not look like a rewrite."""
        with TempRepository() as repository:
            snapshot = sample_spans()
            repository.save_document_snapshot(snapshot)

            first = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)
            repository.publish(first.build.build_id)
            second = WikiMaintainer(
                repository, ScriptedModel(heading_script(snapshot.spans))
            ).ingest(snapshot)

            self.assertNotEqual(first.build.build_id, second.build.build_id)
            self.assertTrue(diff_builds(first.build, second.build).is_empty)
            # The recompile is a draft; the published build did not move.
            self.assertEqual(repository.get_current_build_id(), first.build.build_id)

    def test_the_committed_sample_wiki_still_loads_without_span_provenance(self):
        pages = load_wiki_pages(DEFAULT_WIKI_PATH)
        self.assertTrue(pages)
        for page in pages:
            for claim in page.claims:
                self.assertEqual(claim.source_span_ids, ())
                # Serializing a claim with no spans is byte-identical to before.
                self.assertNotIn("source_span_ids", claim.to_dict())

    def test_a_claim_with_spans_serializes_them(self):
        claim = WikiClaim(
            claim_id="c1",
            text="员工每周最多申请 2 天远程办公。",
            source="rules.md",
            locator="section:远程办公",
            source_span_ids=("span-aaa", "span-bbb"),
        )
        self.assertEqual(claim.to_dict()["source_span_ids"], ["span-aaa", "span-bbb"])


# --------------------------------------------------------------------------
# Prompts and unit-level helpers
# --------------------------------------------------------------------------


class PromptTests(unittest.TestCase):
    def test_prompts_carry_the_span_ids_the_model_must_cite(self):
        with TempRepository() as repository:
            snapshot = save_document(repository, filename="rules.md", text=LEAVE_DOC)
            model = ScriptedModel(heading_script(snapshot.spans))
            WikiMaintainer(repository, model).ingest(snapshot)

        plan_request = next(r for r in model.requests if r.stage == "topic_plan")
        for span in snapshot.spans:
            self.assertIn(span.span_id, plan_request.user)

        page_request = next(
            r for r in model.requests if r.stage.startswith("page_compilation:")
        )
        # Compilation sees the full text, so numbers can be copied not guessed.
        self.assertIn(snapshot.spans[0].text, page_request.user)
        self.assertIn("不得改写、换算", page_request.system)

    def test_compile_page_rejects_a_plan_citing_an_unknown_span(self):
        model = ScriptedModel(lambda request: dumps({}))
        with self.assertRaises(WikiCompilationError):
            compile_page(
                model,
                PagePlan(topic="请假制度", source_span_ids=("span-missing",)),
                span_index={},
                existing_page=None,
            )
        self.assertEqual(model.stages, [])

    def test_request_json_reports_both_failures(self):
        model = ScriptedModel(lambda request: "nope")
        with self.assertRaises(WikiCompilationError) as raised:
            request_json(
                model,
                ModelRequest(stage="probe", system="s", user="u"),
                lambda payload: payload,
            )
        message = str(raised.exception)
        self.assertIn("probe", message)
        self.assertIn("one repair attempt", message)


class OllamaRequestShapeTests(unittest.TestCase):
    """The settings that made this model usable, pinned. No Ollama contacted."""

    def payload(self, stage: str) -> dict:
        return ollama_compiler.request_payload(
            "qwen3:4b", ModelRequest(stage=stage, system="SYS", user="USER")
        )

    def test_thinking_is_off_for_every_stage(self):
        """With thinking on, `topic_plan` over 20 spans did not finish in 1800s;
        off, it took 144s. This is the setting that made the pipeline usable."""
        for stage in (
            "document_decision",
            "topic_plan",
            "page_compilation:批次 1/5",
            "document_decision_repair",
            "topic_plan_repair",
            "page_compilation:批次 1/5_repair",
        ):
            with self.subTest(stage=stage):
                payload = self.payload(stage)
                self.assertIs(payload["think"], False)
                self.assertEqual(payload["options"]["temperature"], 0)
                self.assertFalse(payload["stream"])
                # Loose JSON mode, not a strict schema: the compiler's own
                # checks are what guard quality.
                self.assertEqual(payload["format"], "json")

    def test_each_stage_caps_its_output(self):
        for stage, expected in (
            ("document_decision", 256),
            ("topic_plan", 2048),
            ("page_compilation:批次 1/5", 2048),
            ("page_compilation:请假制度", 2048),
        ):
            with self.subTest(stage=stage):
                self.assertEqual(
                    self.payload(stage)["options"]["num_predict"], expected
                )

    def test_a_repair_keeps_its_stage_cap(self):
        """A smaller cap would truncate the answer it was asked to fix, and only
        one repair is allowed."""
        for stage in ("document_decision", "topic_plan", "page_compilation:批次 2/3"):
            with self.subTest(stage=stage):
                self.assertEqual(
                    ollama_compiler.num_predict_for(f"{stage}_repair"),
                    ollama_compiler.num_predict_for(stage),
                )

    def test_an_unknown_stage_gets_the_default_cap(self):
        self.assertEqual(
            ollama_compiler.num_predict_for("something_new"),
            ollama_compiler.DEFAULT_NUM_PREDICT,
        )


class OllamaBackendTests(unittest.TestCase):
    """Pins the request shape without contacting Ollama."""

    def test_the_request_is_non_streaming_deterministic_json(self):
        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": '{"ok": true}'}}

        def fake_post(url, json=None, timeout=None):
            captured.update(url=url, payload=json, timeout=timeout)
            return Response()

        with mock.patch.object(ollama_compiler.requests, "post", fake_post):
            answer = ollama_compiler.OllamaWikiModel().generate(
                ModelRequest(stage="topic_plan", system="SYS", user="USER")
            )

        self.assertEqual(answer, '{"ok": true}')
        self.assertTrue(captured["url"].endswith("/api/chat"))
        payload = captured["payload"]
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "USER"},
            ],
        )
        # Thinking off and the output capped: measured, not assumed. The
        # response format stays loose - no strict schema.
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], 2048)

    def test_the_endpoint_and_model_are_injectable(self):
        model = ollama_compiler.OllamaWikiModel(
            model="qwen3:8b", url="http://example.invalid:1234"
        )
        self.assertEqual(model.model, "qwen3:8b")
        self.assertEqual(model.url, "http://example.invalid:1234")


if __name__ == "__main__":
    unittest.main()
