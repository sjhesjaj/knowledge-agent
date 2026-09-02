"""M9A: source spans, document snapshots, and the build publish/rollback base.

Every test writes inside a `TemporaryDirectory`. Nothing here touches
`data/`, the product SQLite database, `wiki_pages/`, Ollama, or the network.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from rag import Chunk, split_text

from orchestration.wiki_adapter import DEFAULT_WIKI_PATH, load_wiki_pages
from orchestration.wiki_schema import WikiClaim, WikiPage

from wiki_maintenance import repository as repository_module
from wiki_maintenance import (
    DEFAULT_WIKI_DATA_ROOT,
    BuildNotFoundError,
    BuildNotPublishableError,
    BuildStatus,
    DocumentSnapshot,
    SourceSpan,
    WikiRepository,
    WikiRepositoryError,
    bootstrap_from_wiki_file,
    build_document_snapshot,
    build_document_snapshot_from_text,
    build_source_spans,
    build_source_spans_from_text,
    derive_document_id,
    diff_builds,
    source_blocks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DOCUMENT = "sample_company_rules.md"
DOCUMENT_ID = "rules-md"

LEAVE_TEXT = "正式员工入职满一年后，每年享有 5 天带薪年假。"
REMOTE_TEXT = "员工每周最多申请 2 天远程办公。"
SECURITY_TEXT = "内部资料不得上传至未经公司批准的公共网盘。"

HANDBOOK_FILENAME = "handbook.md"
# Sections deliberately longer than `rag.split_text`'s 220-character window, so
# the retrieval chunker really does slice inside a section rather than handing
# back one chunk per heading.
LEAVE_PARAGRAPHS = (
    "正式员工入职满一年后，每年享有五天带薪年假；工作满三年后年假增加至八天，未休完的年假可以顺延到次年第一季度使用，逾期视为自动放弃。",
    "实习生不享有带薪年假，但每月可以申请一天事假。事假需要提前三个工作日在系统中提交申请，并由带教导师和部门负责人共同确认后方可生效。",
    "请假应提前在系统提交申请，一天以内由直属主管审批，超过一天还需要部门负责人审批，连续超过五天的假期需要人力资源部门备案后才能生效。",
    "病假需要在返岗当天补交二级及以上医院开具的病假证明，未在规定时间内补交证明的，该次缺勤按事假处理并相应扣减当月绩效。",
)
REMOTE_PARAGRAPHS = (
    "员工每周最多申请两天远程办公，远程办公期间需要保持通讯畅通，并按时参加团队例会与项目同步会议，不得影响协作效率。",
    "远程办公须至少提前一个工作日获得直属主管批准，临时的远程办公申请需要在当天上午十点之前说明具体原因并抄送部门负责人。",
    "涉及客户现场支持、机房值守和涉密数据处理的岗位不适用远程办公政策，确有需要的应当逐级申请并经信息安全团队评估。",
)
INSERTED_PARAGRAPH = (
    "本节说明公司的请假类型、天数标准和审批流程，适用于全体正式员工与实习生，"
    "与国家法定节假日的规定并行执行，冲突时以国家规定为准。"
)


def handbook(*, inserted: bool = False) -> str:
    """A realistic Markdown document, optionally with one paragraph inserted
    near the top of its first section."""
    leave = list(LEAVE_PARAGRAPHS)
    if inserted:
        leave.insert(0, INSERTED_PARAGRAPH)
    return (
        "# 员工手册\n\n"
        "## 请假制度\n\n" + "\n\n".join(leave) + "\n\n"
        "## 远程办公\n\n" + "\n\n".join(REMOTE_PARAGRAPHS) + "\n"
    )


def make_chunks(*texts: str, source: str = SOURCE_DOCUMENT) -> list[Chunk]:
    return [
        Chunk(text=text, source=source, index=index)
        for index, text in enumerate(texts, start=1)
    ]


def make_claim(claim_id: str, text: str = LEAVE_TEXT, **overrides) -> WikiClaim:
    defaults = {
        "claim_id": claim_id,
        "text": text,
        "source": SOURCE_DOCUMENT,
        "locator": "section:请假制度",
    }
    defaults.update(overrides)
    return WikiClaim(**defaults)


def make_page(page_id: str, *, claims=None, **overrides) -> WikiPage:
    defaults = {
        "page_id": page_id,
        "title": f"页面 {page_id}",
        "summary": f"{page_id} 的摘要。",
        "version": "1.0",
        "aliases": (),
        "claims": claims if claims is not None else (make_claim(f"{page_id}-c1"),),
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


def sample_pages() -> tuple[WikiPage, ...]:
    return (
        make_page("p-leave", claims=(make_claim("c-leave", LEAVE_TEXT),)),
        make_page("p-remote", claims=(make_claim("c-remote", REMOTE_TEXT),)),
    )


class TempRepository:
    """A repository under a temporary root, removed when the test ends."""

    def __enter__(self) -> WikiRepository:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        return WikiRepository(self.root)

    def __exit__(self, *exc_info) -> None:
        self._directory.cleanup()


# --------------------------------------------------------------------------
# Source spans and document versions
# --------------------------------------------------------------------------


class SourceSpanTests(unittest.TestCase):
    def test_identical_content_produces_identical_version_and_span_ids(self):
        chunks = make_chunks(LEAVE_TEXT, REMOTE_TEXT, SECURITY_TEXT)
        first = build_document_snapshot(
            document_id=DOCUMENT_ID, filename=SOURCE_DOCUMENT, chunks=chunks
        )
        second = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=make_chunks(LEAVE_TEXT, REMOTE_TEXT, SECURITY_TEXT),
        )
        self.assertEqual(first.version, second.version)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(
            [span.span_id for span in first.spans],
            [span.span_id for span in second.spans],
        )

    def test_created_at_does_not_participate_in_the_version(self):
        chunks = make_chunks(LEAVE_TEXT, REMOTE_TEXT)
        early = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=chunks,
            created_at="2020-01-01T00:00:00.000+00:00",
        )
        late = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=chunks,
            created_at="2030-12-31T23:59:59.999+00:00",
        )
        self.assertNotEqual(early.created_at, late.created_at)
        self.assertEqual(early.version, late.version)

    def test_editing_one_span_changes_only_that_span_and_the_version(self):
        before = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=make_chunks(LEAVE_TEXT, REMOTE_TEXT, SECURITY_TEXT),
        )
        after = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=make_chunks(
                LEAVE_TEXT, "员工每周最多申请 3 天远程办公。", SECURITY_TEXT
            ),
        )
        self.assertNotEqual(before.version, after.version)
        before_ids = [span.span_id for span in before.spans]
        after_ids = [span.span_id for span in after.spans]
        self.assertEqual(before_ids[0], after_ids[0])
        self.assertEqual(before_ids[2], after_ids[2])
        self.assertNotEqual(before_ids[1], after_ids[1])

    def test_inserting_a_span_keeps_the_ids_below_it(self):
        before = build_source_spans(DOCUMENT_ID, make_chunks(LEAVE_TEXT, REMOTE_TEXT))
        after = build_source_spans(
            DOCUMENT_ID, make_chunks("新增的一段说明。", LEAVE_TEXT, REMOTE_TEXT)
        )
        self.assertEqual(
            [span.span_id for span in before],
            [span.span_id for span in after[1:]],
        )
        # Position moved even though identity did not.
        self.assertEqual([span.ordinal for span in after], [1, 2, 3])

    def test_different_document_ids_never_share_a_span_id(self):
        chunks = make_chunks(LEAVE_TEXT, REMOTE_TEXT)
        first = build_source_spans("rules-a", chunks)
        second = build_source_spans("rules-b", chunks)
        self.assertTrue(
            set(span.span_id for span in first).isdisjoint(
                span.span_id for span in second
            )
        )
        # Identical wording still shares a content hash across documents.
        self.assertEqual(first[0].content_hash, second[0].content_hash)

    def test_whitespace_differences_are_not_an_edit(self):
        plain = build_document_snapshot(
            document_id=DOCUMENT_ID, filename=SOURCE_DOCUMENT, chunks=make_chunks(LEAVE_TEXT)
        )
        respaced = build_document_snapshot(
            document_id=DOCUMENT_ID,
            filename=SOURCE_DOCUMENT,
            chunks=make_chunks(f"  {LEAVE_TEXT}\r\n\r\n"),
        )
        self.assertEqual(plain.version, respaced.version)
        self.assertEqual(plain.spans[0].span_id, respaced.spans[0].span_id)

    def test_repeated_identical_text_still_gets_distinct_span_ids(self):
        spans = build_source_spans(DOCUMENT_ID, make_chunks(LEAVE_TEXT, LEAVE_TEXT))
        self.assertEqual(len({span.span_id for span in spans}), 2)
        # The first occurrence keeps the id it would have had on its own.
        alone = build_source_spans(DOCUMENT_ID, make_chunks(LEAVE_TEXT))
        self.assertEqual(spans[0].span_id, alone[0].span_id)

    def test_headings_are_read_from_chunks_and_carried_forward(self):
        spans = build_source_spans(
            DOCUMENT_ID,
            make_chunks(f"## 请假制度\n{LEAVE_TEXT}", "年假需提前在系统提交申请。"),
        )
        self.assertEqual([span.heading for span in spans], ["请假制度", "请假制度"])
        self.assertEqual(spans[0].locator, "section:请假制度")

    def test_real_rag_chunks_produce_spans(self):
        """Pins the `rag.Chunk` contract this module reads."""
        text = REPO_ROOT.joinpath(SOURCE_DOCUMENT).read_text(encoding="utf-8")
        chunks = split_text(text, SOURCE_DOCUMENT)
        self.assertTrue(chunks)
        self.assertIsInstance(chunks[0], Chunk)

        snapshot = build_document_snapshot(
            document_id=derive_document_id(SOURCE_DOCUMENT),
            filename=SOURCE_DOCUMENT,
            chunks=chunks,
        )
        self.assertEqual(len(snapshot.spans), len(chunks))
        self.assertEqual([span.ordinal for span in snapshot.spans], list(range(1, len(chunks) + 1)))
        for span in snapshot.spans:
            self.assertEqual(span.source, SOURCE_DOCUMENT)
            self.assertEqual(span.document_version, snapshot.version)
        self.assertTrue(any(span.locator is not None for span in snapshot.spans))

    def test_derive_document_id_is_path_safe_and_stable(self):
        ascii_id = derive_document_id(SOURCE_DOCUMENT)
        self.assertEqual(ascii_id, derive_document_id(SOURCE_DOCUMENT))
        self.assertIn("sample", ascii_id)
        for filename in (SOURCE_DOCUMENT, "公司制度.md", "../../etc/passwd"):
            with self.subTest(filename=filename):
                document_id = derive_document_id(filename)
                self.assertRegex(document_id, r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
                # Constructing a span proves the id passes the persistence check.
                build_source_spans(document_id, make_chunks(LEAVE_TEXT))
        self.assertNotEqual(derive_document_id("公司制度.md"), derive_document_id("公司规则.md"))


# --------------------------------------------------------------------------
# Raw-text segmentation: the canonical entry
# --------------------------------------------------------------------------


class RawTextSpanTests(unittest.TestCase):
    def _snapshot(self, text: str):
        return build_document_snapshot_from_text(
            document_id=DOCUMENT_ID, filename=HANDBOOK_FILENAME, text=text
        )

    def test_the_fixture_is_long_enough_to_be_re_chunked(self):
        """Guards the premise of the test below: if `split_text` returned one
        chunk per section, the insertion would not reshape anything."""
        chunks = split_text(handbook(), HANDBOOK_FILENAME)
        self.assertGreater(len(chunks), 2)

    def test_inserting_a_paragraph_keeps_every_other_span_id(self):
        before = self._snapshot(handbook())
        after = self._snapshot(handbook(inserted=True))

        before_ids = {span.span_id for span in before.spans}
        after_ids = {span.span_id for span in after.spans}

        self.assertEqual(before_ids - after_ids, set(), "no span id may be lost")
        self.assertEqual(len(after_ids - before_ids), 1, "only the new paragraph is new")
        self.assertEqual(len(after.spans), len(before.spans) + 1)
        self.assertNotEqual(before.version, after.version)

        new_span = next(span for span in after.spans if span.span_id not in before_ids)
        self.assertEqual(new_span.text, INSERTED_PARAGRAPH)
        self.assertEqual(new_span.heading, "请假制度")

    def test_the_chunk_entry_cannot_promise_that(self):
        """Why the raw-text entry exists.

        `rag.split_text` slices a section into fixed-length windows, so an
        insertion moves every later boundary and re-identifies prose nobody
        edited. Characterizing it here keeps the compatibility entry's weaker
        promise honest.
        """
        before = {
            span.span_id
            for span in build_source_spans(
                DOCUMENT_ID, split_text(handbook(), HANDBOOK_FILENAME)
            )
        }
        after = {
            span.span_id
            for span in build_source_spans(
                DOCUMENT_ID, split_text(handbook(inserted=True), HANDBOOK_FILENAME)
            )
        }
        self.assertTrue(
            before - after,
            "the chunk entry unexpectedly kept every id; if split_text became "
            "boundary-stable, this characterization can go",
        )

    def test_identical_text_produces_identical_spans(self):
        first = self._snapshot(handbook())
        second = self._snapshot(handbook())
        self.assertEqual(first.version, second.version)
        self.assertEqual(
            [span.span_id for span in first.spans],
            [span.span_id for span in second.spans],
        )

    def test_headings_are_tracked_and_the_title_is_not_one(self):
        spans = build_source_spans_from_text(
            DOCUMENT_ID, handbook(), HANDBOOK_FILENAME
        )
        headings = [span.heading for span in spans]
        self.assertEqual(headings[: len(LEAVE_PARAGRAPHS)], ["请假制度"] * len(LEAVE_PARAGRAPHS))
        self.assertEqual(headings[len(LEAVE_PARAGRAPHS) :], ["远程办公"] * len(REMOTE_PARAGRAPHS))
        self.assertEqual(spans[0].locator, "section:请假制度")
        # The `# 员工手册` title line is neither a span nor a section heading.
        self.assertNotIn("员工手册", headings)
        self.assertTrue(all("员工手册" not in span.text for span in spans))

    def test_plain_text_without_markdown_splits_on_blank_lines(self):
        spans = build_source_spans_from_text(
            DOCUMENT_ID, f"{LEAVE_TEXT}\n\n{REMOTE_TEXT}\n\n\n{SECURITY_TEXT}\n", "notes.txt"
        )
        self.assertEqual([span.text for span in spans], [LEAVE_TEXT, REMOTE_TEXT, SECURITY_TEXT])
        self.assertEqual([span.heading for span in spans], [None, None, None])
        self.assertEqual([span.ordinal for span in spans], [1, 2, 3])
        self.assertEqual([span.locator for span in spans], [None, None, None])

    def test_a_long_paragraph_stays_one_span(self):
        paragraph = "。".join(LEAVE_PARAGRAPHS)
        spans = build_source_spans_from_text(DOCUMENT_ID, paragraph, HANDBOOK_FILENAME)
        self.assertEqual(len(spans), 1)
        self.assertGreater(len(spans[0].text), 220)

    def test_line_endings_and_indentation_are_not_edits(self):
        base = handbook()
        for label, variant in (
            ("crlf", base.replace("\n", "\r\n")),
            ("indented", base.replace("\n\n", "\n\n    ")),
            ("extra blank lines", base.replace("\n\n", "\n\n\n")),
        ):
            with self.subTest(variant=label):
                self.assertNotEqual(variant, base)
                self.assertEqual(self._snapshot(base).version, self._snapshot(variant).version)

    def test_a_hard_wrap_inside_a_chinese_paragraph_is_not_an_edit(self):
        """Chinese puts no space between characters, so a wrap must not read as
        an edit."""
        base = "中文制度"
        wrapped = "中文\n制度"
        self.assertEqual(
            build_source_spans_from_text(DOCUMENT_ID, base, HANDBOOK_FILENAME)[0].span_id,
            build_source_spans_from_text(DOCUMENT_ID, wrapped, HANDBOOK_FILENAME)[0].span_id,
        )

        # And at paragraph scale, inside a real document.
        wrapped_handbook = handbook().replace(
            LEAVE_PARAGRAPHS[0], LEAVE_PARAGRAPHS[0].replace("年假", "年\n假", 1)
        )
        self.assertNotEqual(wrapped_handbook, handbook())
        self.assertEqual(
            self._snapshot(handbook()).version, self._snapshot(wrapped_handbook).version
        )

    def test_latin_word_spacing_still_carries_meaning(self):
        def span_id(text: str) -> str:
            return build_source_spans_from_text(DOCUMENT_ID, text, HANDBOOK_FILENAME)[0].span_id

        # A wrap between two English words is still one space.
        self.assertEqual(span_id("remote\nwork"), span_id("remote work"))
        # But the space itself is content: it is what separates the words.
        self.assertNotEqual(span_id("remotework"), span_id("remote work"))
        # A Chinese/Latin boundary keeps its single space rather than closing up.
        self.assertNotEqual(span_id("远程 work"), span_id("远程work"))
        self.assertEqual(span_id("远程 work"), span_id("远程   work"))

    def test_the_rule_is_a_character_class_not_a_line_breaker(self):
        """Accurate about its own edge: fullwidth punctuation is outside the
        `U+4E00-U+9FFF` class, so a wrap landing right after it still counts."""
        def span_id(text: str) -> str:
            return build_source_spans_from_text(DOCUMENT_ID, text, HANDBOOK_FILENAME)[0].span_id

        self.assertNotEqual(span_id("制度；执行"), span_id("制度；\n执行"))

    def test_source_defaults_to_the_filename(self):
        spans = build_document_snapshot_from_text(
            document_id=DOCUMENT_ID, filename=HANDBOOK_FILENAME, text=handbook()
        ).spans
        self.assertTrue(all(span.source == HANDBOOK_FILENAME for span in spans))

        relabelled = build_document_snapshot_from_text(
            document_id=DOCUMENT_ID,
            filename=HANDBOOK_FILENAME,
            text=handbook(),
            source="uploads/handbook.md",
        ).spans
        self.assertTrue(all(span.source == "uploads/handbook.md" for span in relabelled))

    def test_blocks_are_exposed_for_later_compilation(self):
        blocks = source_blocks(handbook(), HANDBOOK_FILENAME)
        self.assertEqual(len(blocks), len(LEAVE_PARAGRAPHS) + len(REMOTE_PARAGRAPHS))
        self.assertEqual(blocks[0].heading, "请假制度")
        self.assertEqual(blocks[0].source, HANDBOOK_FILENAME)

    def test_the_two_entries_segment_independently(self):
        """They read one document differently on purpose; ids must not be mixed."""
        raw = {span.span_id for span in build_source_spans_from_text(
            DOCUMENT_ID, handbook(), HANDBOOK_FILENAME
        )}
        chunked = {span.span_id for span in build_source_spans(
            DOCUMENT_ID, split_text(handbook(), HANDBOOK_FILENAME)
        )}
        self.assertTrue(raw.isdisjoint(chunked))


# --------------------------------------------------------------------------
# Document snapshot persistence
# --------------------------------------------------------------------------


class DocumentSnapshotStorageTests(unittest.TestCase):
    def test_snapshot_saves_and_loads_unchanged(self):
        with TempRepository() as repository:
            snapshot = build_document_snapshot(
                document_id=DOCUMENT_ID,
                filename=SOURCE_DOCUMENT,
                chunks=make_chunks(f"## 请假制度\n{LEAVE_TEXT}", REMOTE_TEXT),
            )
            path = repository.save_document_snapshot(snapshot)
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent.name, DOCUMENT_ID)
            self.assertEqual(path.stem, snapshot.version)

            loaded = repository.load_document_snapshot(DOCUMENT_ID, snapshot.version)
            self.assertEqual(loaded, snapshot)
            self.assertIsInstance(loaded.spans[0], SourceSpan)
            self.assertEqual(loaded.spans[0].heading, "请假制度")

    def test_two_versions_of_one_document_coexist(self):
        with TempRepository() as repository:
            first = build_document_snapshot(
                document_id=DOCUMENT_ID, filename=SOURCE_DOCUMENT, chunks=make_chunks(LEAVE_TEXT)
            )
            second = build_document_snapshot(
                document_id=DOCUMENT_ID,
                filename=SOURCE_DOCUMENT,
                chunks=make_chunks(LEAVE_TEXT, REMOTE_TEXT),
            )
            repository.save_document_snapshot(first)
            repository.save_document_snapshot(second)

            self.assertEqual(
                repository.list_document_versions(DOCUMENT_ID),
                tuple(sorted({first.version, second.version})),
            )
            self.assertEqual(repository.list_document_ids(), (DOCUMENT_ID,))
            self.assertEqual(
                repository.load_document_snapshot(DOCUMENT_ID, first.version), first
            )

    def test_missing_snapshot_raises(self):
        with TempRepository() as repository:
            with self.assertRaises(WikiRepositoryError):
                repository.load_document_snapshot(DOCUMENT_ID, "v-000000000000")
            self.assertEqual(repository.list_document_versions(DOCUMENT_ID), ())


class SnapshotImmutabilityTests(unittest.TestCase):
    """A saved version records when content was first seen and what it was
    called. A second upload of the same bytes must not rewrite that."""

    def _snapshot(self, *, filename: str, created_at: str, text: str = None):
        return build_document_snapshot_from_text(
            document_id=DOCUMENT_ID,
            filename=filename,
            text=handbook() if text is None else text,
            source=filename,
            created_at=created_at,
        )

    def test_resaving_identical_content_writes_nothing_and_keeps_the_first_metadata(self):
        with TempRepository() as repository:
            first = self._snapshot(
                filename="handbook.md", created_at="2020-01-01T00:00:00.000+00:00"
            )
            path = repository.save_document_snapshot(first)
            original = path.read_bytes()

            # Same document, uploaded again under a different name, later.
            second = self._snapshot(
                filename="handbook-copy.md", created_at="2030-12-31T23:59:59.999+00:00"
            )
            self.assertEqual(first.version, second.version)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertNotEqual(first.filename, second.filename)

            with mock.patch.object(
                repository_module, "_write_json_atomically"
            ) as write_json:
                again = repository.save_document_snapshot(second)

            write_json.assert_not_called()
            self.assertEqual(again, path)
            self.assertEqual(path.read_bytes(), original)

            loaded = repository.load_document_snapshot(DOCUMENT_ID, first.version)
            self.assertEqual(loaded.filename, "handbook.md")
            self.assertEqual(loaded.created_at, "2020-01-01T00:00:00.000+00:00")
            self.assertTrue(all(span.source == "handbook.md" for span in loaded.spans))

    def test_different_content_at_the_same_version_path_is_refused(self):
        """The 12-hex version is a truncated digest; two documents could land on
        one path. The full content_hash is what decides."""
        with TempRepository() as repository:
            original = self._snapshot(
                filename="handbook.md", created_at="2020-01-01T00:00:00.000+00:00"
            )
            path = repository.save_document_snapshot(original)

            other = self._snapshot(
                filename="other.md",
                created_at="2020-01-01T00:00:00.000+00:00",
                text=handbook(inserted=True),
            )
            self.assertNotEqual(other.content_hash, original.content_hash)
            # Park the other document's content where the first one lives.
            path.write_text(
                json.dumps(other.to_dict(), ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaises(WikiRepositoryError):
                repository.save_document_snapshot(original)

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["content_hash"],
                other.content_hash,
            )

    def test_an_unreadable_file_at_the_path_is_not_overwritten(self):
        with TempRepository() as repository:
            snapshot = self._snapshot(
                filename="handbook.md", created_at="2020-01-01T00:00:00.000+00:00"
            )
            path = repository.snapshot_path(DOCUMENT_ID, snapshot.version)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ broken", encoding="utf-8")

            with self.assertRaises(WikiRepositoryError):
                repository.save_document_snapshot(snapshot)
            self.assertEqual(path.read_text(encoding="utf-8"), "{ broken")


# --------------------------------------------------------------------------
# Build creation, publish and rollback
# --------------------------------------------------------------------------


class BuildLifecycleTests(unittest.TestCase):
    def test_builds_are_numbered_in_order(self):
        with TempRepository() as repository:
            first = repository.create_build(sample_pages())
            second = repository.create_build(
                sample_pages(), base_build_id=first.build_id
            )
            self.assertEqual(first.build_id, "build-0001")
            self.assertEqual(second.build_id, "build-0002")
            self.assertEqual(second.base_build_id, "build-0001")
            self.assertEqual(
                [record.build_id for record in repository.list_builds()],
                ["build-0001", "build-0002"],
            )
            self.assertEqual(
                [record.status for record in repository.list_builds()],
                [BuildStatus.DRAFT, BuildStatus.DRAFT],
            )

    def test_created_build_loads_back_complete(self):
        with TempRepository() as repository:
            snapshot = build_document_snapshot(
                document_id=DOCUMENT_ID, filename=SOURCE_DOCUMENT, chunks=make_chunks(LEAVE_TEXT)
            )
            repository.save_document_snapshot(snapshot)
            created = repository.create_build(
                sample_pages(), document_versions={DOCUMENT_ID: snapshot.version}
            )
            loaded = repository.load_build(created.build_id)
            self.assertEqual(loaded, created)
            self.assertEqual(loaded.document_version_map, {DOCUMENT_ID: snapshot.version})
            self.assertEqual([page.page_id for page in loaded.pages], ["p-leave", "p-remote"])
            self.assertEqual(loaded.pages[0].claims[0].text, LEAVE_TEXT)
            self.assertEqual(loaded.pages[0].claims[0].locator, "section:请假制度")

    def test_publishing_the_first_build_sets_current(self):
        with TempRepository() as repository:
            build = repository.create_build(sample_pages())
            record = repository.publish(build.build_id)

            self.assertIs(record.status, BuildStatus.PUBLISHED)
            self.assertEqual(repository.get_current_build_id(), build.build_id)
            self.assertEqual(repository.load_current_build(), build)
            pointer = json.loads(repository.current_path.read_text(encoding="utf-8"))
            self.assertEqual(pointer["build_id"], build.build_id)

    def test_publishing_a_second_build_switches_current_and_archives_the_first(self):
        with TempRepository() as repository:
            first = repository.create_build(sample_pages())
            repository.publish(first.build_id)
            second = repository.create_build(
                sample_pages(), base_build_id=first.build_id
            )
            repository.publish(second.build_id)

            self.assertEqual(repository.get_current_build_id(), second.build_id)
            statuses = {
                record.build_id: record.status for record in repository.list_builds()
            }
            self.assertEqual(
                statuses,
                {first.build_id: BuildStatus.ARCHIVED, second.build_id: BuildStatus.PUBLISHED},
            )

    def test_only_one_build_is_published_at_a_time(self):
        with TempRepository() as repository:
            builds = [repository.create_build(sample_pages()) for _ in range(3)]
            for build in builds:
                repository.publish(build.build_id)
                published = [
                    record.build_id
                    for record in repository.list_builds()
                    if record.status is BuildStatus.PUBLISHED
                ]
                self.assertEqual(published, [build.build_id])

    def test_publish_never_rewrites_a_historical_build(self):
        with TempRepository() as repository:
            first = repository.create_build(sample_pages())
            repository.publish(first.build_id)
            before = repository.build_path(first.build_id).read_bytes()

            second = repository.create_build(
                (make_page("p-new"),), base_build_id=first.build_id
            )
            repository.publish(second.build_id)
            repository.rollback(first.build_id)

            self.assertEqual(repository.build_path(first.build_id).read_bytes(), before)
            self.assertEqual(repository.load_build(first.build_id), first)

    def test_rollback_restores_the_earlier_build(self):
        with TempRepository() as repository:
            first = repository.create_build(sample_pages())
            repository.publish(first.build_id)
            second = repository.create_build(
                (make_page("p-new"),), base_build_id=first.build_id
            )
            repository.publish(second.build_id)

            record = repository.rollback(first.build_id)

            self.assertIs(record.status, BuildStatus.PUBLISHED)
            self.assertEqual(repository.get_current_build_id(), first.build_id)
            self.assertEqual(repository.load_current_build(), first)
            statuses = {
                item.build_id: item.status for item in repository.list_builds()
            }
            self.assertEqual(
                statuses,
                {first.build_id: BuildStatus.PUBLISHED, second.build_id: BuildStatus.ARCHIVED},
            )

    def test_rollback_requires_a_previously_published_build(self):
        with TempRepository() as repository:
            first = repository.create_build(sample_pages())
            repository.publish(first.build_id)
            draft = repository.create_build(sample_pages())

            with self.assertRaises(BuildNotPublishableError):
                repository.rollback(draft.build_id)
            self.assertEqual(repository.get_current_build_id(), first.build_id)

    def test_a_failed_build_cannot_be_published_or_rolled_back_to(self):
        with TempRepository() as repository:
            build = repository.create_build(sample_pages())
            record = repository.mark_failed(build.build_id, "编译中断")

            self.assertIs(record.status, BuildStatus.FAILED)
            self.assertEqual(record.note, "编译中断")
            with self.assertRaises(BuildNotPublishableError):
                repository.publish(build.build_id)
            with self.assertRaises(BuildNotPublishableError):
                repository.rollback(build.build_id)
            self.assertIsNone(repository.get_current_build_id())

    def test_marking_the_live_build_failed_is_refused(self):
        with TempRepository() as repository:
            build = repository.create_build(sample_pages())
            repository.publish(build.build_id)

            with self.assertRaises(BuildNotPublishableError):
                repository.mark_failed(build.build_id, "误报")
            self.assertIs(
                repository.get_build_record(build.build_id).status, BuildStatus.PUBLISHED
            )

    def test_an_empty_repository_reports_an_empty_current_state(self):
        with TempRepository() as repository:
            self.assertIsNone(repository.get_current_build_id())
            self.assertIsNone(repository.load_current_build())
            self.assertIsNone(repository.load_current_pointer())
            self.assertEqual(repository.list_builds(), ())
            self.assertFalse(repository.current_path.exists())

    def test_a_created_but_unpublished_build_leaves_current_empty(self):
        with TempRepository() as repository:
            repository.create_build(sample_pages())
            self.assertIsNone(repository.get_current_build_id())
            self.assertIsNone(repository.load_current_build())

    def test_unknown_builds_are_rejected_by_every_lifecycle_call(self):
        with TempRepository() as repository:
            for call in (
                repository.publish,
                repository.rollback,
                repository.load_build,
                repository.get_build_record,
            ):
                with self.subTest(call=call.__name__):
                    with self.assertRaises(BuildNotFoundError):
                        call("build-0404")
            with self.assertRaises(BuildNotFoundError):
                repository.mark_failed("build-0404", "缺失")
            with self.assertRaises(BuildNotFoundError):
                repository.create_build(sample_pages(), base_build_id="build-0404")

    def test_a_build_whose_file_is_unreadable_is_not_published(self):
        with TempRepository() as repository:
            build = repository.create_build(sample_pages())
            repository.build_path(build.build_id).write_text("{ broken", encoding="utf-8")

            with self.assertRaises(BuildNotPublishableError):
                repository.publish(build.build_id)
            self.assertIsNone(repository.get_current_build_id())

    def test_a_duplicate_claim_id_is_rejected_before_anything_is_written(self):
        with TempRepository() as repository:
            duplicated = (
                make_page("p-one", claims=(make_claim("c-same"),)),
                make_page("p-two", claims=(make_claim("c-same"),)),
            )
            with self.assertRaises(ValueError):
                repository.create_build(duplicated)

            self.assertEqual(repository.list_builds(), ())
            self.assertEqual(list(repository.builds_directory.glob("*.json")), [])

    def test_reserved_build_numbers_are_never_reused(self):
        """An orphaned build file from an interrupted create must not be clobbered."""
        with TempRepository() as repository:
            repository.create_build(sample_pages())
            orphan = repository.build_path("build-0007")
            orphan.write_text("{}", encoding="utf-8")

            self.assertEqual(repository.create_build(sample_pages()).build_id, "build-0008")


class BuildProvenanceTests(unittest.TestCase):
    """A build says which document versions it was compiled from. If one of
    them was never saved, that sentence is false and the trail from a claim
    back to its source is broken before the build even exists."""

    def _saved_snapshot(self, repository, *, filename: str, text: str):
        snapshot = build_document_snapshot_from_text(
            document_id=derive_document_id(filename), filename=filename, text=text
        )
        repository.save_document_snapshot(snapshot)
        return snapshot

    def test_a_saved_snapshot_can_be_cited(self):
        with TempRepository() as repository:
            snapshot = self._saved_snapshot(
                repository, filename=HANDBOOK_FILENAME, text=handbook()
            )
            build = repository.create_build(
                sample_pages(),
                document_versions={snapshot.document_id: snapshot.version},
            )
            self.assertEqual(
                repository.load_build(build.build_id).document_version_map,
                {snapshot.document_id: snapshot.version},
            )

    def test_citing_a_missing_snapshot_leaves_no_build_and_no_manifest(self):
        with TempRepository() as repository:
            with self.assertRaises(WikiRepositoryError):
                repository.create_build(
                    sample_pages(),
                    document_versions={"missing-doc": "v-0123456789ab"},
                )

            self.assertEqual(list(repository.builds_directory.glob("*.json")), [])
            self.assertFalse(repository.manifest_path.exists())
            self.assertEqual(repository.list_builds(), ())

    def test_one_bad_reference_among_several_fails_the_whole_build(self):
        with TempRepository() as repository:
            good = self._saved_snapshot(
                repository, filename=HANDBOOK_FILENAME, text=handbook()
            )
            unsaved = build_document_snapshot_from_text(
                document_id=derive_document_id("rules.md"),
                filename="rules.md",
                text=f"## 信息安全\n\n{SECURITY_TEXT}\n",
            )

            with self.assertRaises(WikiRepositoryError):
                repository.create_build(
                    sample_pages(),
                    document_versions={
                        good.document_id: good.version,
                        unsaved.document_id: unsaved.version,
                    },
                )

            self.assertEqual(list(repository.builds_directory.glob("*.json")), [])
            self.assertFalse(repository.manifest_path.exists())

    def test_citing_a_version_the_document_does_not_have_is_refused(self):
        with TempRepository() as repository:
            snapshot = self._saved_snapshot(
                repository, filename=HANDBOOK_FILENAME, text=handbook()
            )
            with self.assertRaises(WikiRepositoryError):
                repository.create_build(
                    sample_pages(),
                    document_versions={snapshot.document_id: "v-0123456789ab"},
                )
            self.assertEqual(repository.list_builds(), ())

    def test_citing_nothing_is_allowed(self):
        """What the static sample Wiki bootstrap relies on."""
        with TempRepository() as repository:
            build = repository.create_build(sample_pages())
            self.assertEqual(build.document_versions, ())
            self.assertEqual(repository.load_build(build.build_id).document_versions, ())


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


class WikiDiffTests(unittest.TestCase):
    def setUp(self):
        self.base_pages = (
            make_page("p-leave", claims=(make_claim("c-leave", LEAVE_TEXT),)),
            make_page("p-remote", claims=(make_claim("c-remote", REMOTE_TEXT),)),
        )

    def _build_pair(self, repository, target_pages):
        base = repository.create_build(self.base_pages)
        target = repository.create_build(target_pages, base_build_id=base.build_id)
        return base, target

    def test_identical_builds_report_no_change(self):
        with TempRepository() as repository:
            base, target = self._build_pair(repository, self.base_pages)
            difference = diff_builds(base, target)

            self.assertTrue(difference.is_empty)
            self.assertEqual(difference.base_build_id, base.build_id)
            self.assertEqual(difference.target_build_id, target.build_id)

    def test_added_removed_and_updated_pages(self):
        with TempRepository() as repository:
            target_pages = (
                replace(self.base_pages[0], summary="改写后的摘要。"),
                make_page("p-security", claims=(make_claim("c-security", SECURITY_TEXT),)),
            )
            base, target = self._build_pair(repository, target_pages)
            difference = diff_builds(base, target)

            self.assertEqual(difference.added_page_ids, ("p-security",))
            self.assertEqual(difference.removed_page_ids, ("p-remote",))
            self.assertEqual(difference.updated_page_ids, ("p-leave",))

    def test_page_metadata_changes_count_as_updates(self):
        with TempRepository() as repository:
            for field, value in (
                ("title", "新的标题"),
                ("summary", "新的摘要。"),
                ("version", "1.1"),
                ("aliases", ("年假",)),
            ):
                with self.subTest(field=field):
                    target_pages = (
                        replace(self.base_pages[0], **{field: value}),
                        self.base_pages[1],
                    )
                    base, target = self._build_pair(repository, target_pages)
                    difference = diff_builds(base, target)
                    self.assertEqual(difference.updated_page_ids, ("p-leave",))
                    self.assertEqual(difference.updated_claim_ids, ())

    def test_added_removed_and_updated_claims(self):
        with TempRepository() as repository:
            target_pages = (
                replace(
                    self.base_pages[0],
                    claims=(
                        make_claim("c-leave", "正式员工每年享有 8 天带薪年假。"),
                        make_claim("c-leave-extra", "实习生每月可申请 1 天事假。"),
                    ),
                ),
                replace(self.base_pages[1], claims=(make_claim("c-remote-new", REMOTE_TEXT),)),
            )
            base, target = self._build_pair(repository, target_pages)
            difference = diff_builds(base, target)

            self.assertEqual(difference.added_claim_ids, ("c-leave-extra", "c-remote-new"))
            self.assertEqual(difference.removed_claim_ids, ("c-remote",))
            self.assertEqual(difference.updated_claim_ids, ("c-leave",))
            self.assertEqual(difference.updated_page_ids, ("p-leave", "p-remote"))

    def test_claim_source_and_locator_changes_count_as_updates(self):
        with TempRepository() as repository:
            for field, value in (
                ("source", "another_document.md"),
                ("locator", "section:远程办公"),
            ):
                with self.subTest(field=field):
                    target_pages = (
                        replace(
                            self.base_pages[0],
                            claims=(replace(self.base_pages[0].claims[0], **{field: value}),),
                        ),
                        self.base_pages[1],
                    )
                    base, target = self._build_pair(repository, target_pages)
                    difference = diff_builds(base, target)
                    self.assertEqual(difference.updated_claim_ids, ("c-leave",))

    def test_output_order_does_not_depend_on_page_order(self):
        with TempRepository() as repository:
            extra = make_page("p-security", claims=(make_claim("c-security", SECURITY_TEXT),))
            forward = (self.base_pages[0], self.base_pages[1], extra)
            reversed_order = (extra, self.base_pages[1], self.base_pages[0])

            base = repository.create_build(self.base_pages)
            one = repository.create_build(forward)
            two = repository.create_build(reversed_order)

            first = diff_builds(base, one).to_dict()
            second = diff_builds(base, two).to_dict()
            first.pop("target_build_id")
            second.pop("target_build_id")
            self.assertEqual(first, second)
            self.assertEqual(first["added_page_ids"], ["p-security"])

    def test_a_claim_that_moves_between_pages_is_not_a_claim_change(self):
        with TempRepository() as repository:
            moved = (
                replace(self.base_pages[0], claims=(make_claim("c-remote", REMOTE_TEXT),)),
                replace(self.base_pages[1], claims=(make_claim("c-leave", LEAVE_TEXT),)),
            )
            base, target = self._build_pair(repository, moved)
            difference = diff_builds(base, target)

            self.assertEqual(difference.updated_claim_ids, ())
            self.assertEqual(difference.added_claim_ids, ())
            self.assertEqual(difference.removed_claim_ids, ())
            # The move is visible where it happened: on both pages.
            self.assertEqual(difference.updated_page_ids, ("p-leave", "p-remote"))


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_creates_and_publishes_the_first_build(self):
        with TempRepository() as repository:
            build = bootstrap_from_wiki_file(repository)

            self.assertEqual(build.build_id, "build-0001")
            self.assertIsNone(build.base_build_id)
            self.assertEqual(build.pages, load_wiki_pages(DEFAULT_WIKI_PATH))
            self.assertEqual(repository.get_current_build_id(), "build-0001")
            self.assertIs(
                repository.get_build_record("build-0001").status, BuildStatus.PUBLISHED
            )
            self.assertEqual(repository.load_current_build(), build)

    def test_bootstrap_can_leave_the_build_unpublished(self):
        with TempRepository() as repository:
            build = bootstrap_from_wiki_file(repository, publish=False)

            self.assertIs(
                repository.get_build_record(build.build_id).status, BuildStatus.DRAFT
            )
            self.assertIsNone(repository.get_current_build_id())

    def test_bootstrapping_twice_is_refused(self):
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
            with self.assertRaises(WikiRepositoryError):
                bootstrap_from_wiki_file(repository)
            self.assertEqual(
                [record.build_id for record in repository.list_builds()], ["build-0001"]
            )

    def test_bootstrap_leaves_the_source_file_untouched(self):
        before = DEFAULT_WIKI_PATH.read_bytes()
        with TempRepository() as repository:
            bootstrap_from_wiki_file(repository)
        self.assertEqual(DEFAULT_WIKI_PATH.read_bytes(), before)

    def test_the_full_demo_sequence(self):
        """build-0001 -> publish -> build-0002 -> diff -> publish -> rollback."""
        with TempRepository() as repository:
            first = bootstrap_from_wiki_file(repository)

            snapshot = build_document_snapshot(
                document_id=derive_document_id(SOURCE_DOCUMENT),
                filename=SOURCE_DOCUMENT,
                chunks=split_text(
                    REPO_ROOT.joinpath(SOURCE_DOCUMENT).read_text(encoding="utf-8"),
                    SOURCE_DOCUMENT,
                ),
            )
            repository.save_document_snapshot(snapshot)

            revised = list(first.pages)
            revised[0] = replace(revised[0], summary=revised[0].summary + "（已更新）")
            second = repository.create_build(
                revised,
                document_versions={snapshot.document_id: snapshot.version},
                base_build_id=first.build_id,
            )

            difference = diff_builds(first, second)
            self.assertEqual(difference.updated_page_ids, (first.pages[0].page_id,))
            self.assertEqual(difference.added_page_ids, ())

            repository.publish(second.build_id)
            self.assertEqual(repository.get_current_build_id(), second.build_id)

            repository.rollback(first.build_id)
            self.assertEqual(repository.load_current_build(), first)
            self.assertEqual(
                [(record.build_id, record.status) for record in repository.list_builds()],
                [
                    (first.build_id, BuildStatus.PUBLISHED),
                    (second.build_id, BuildStatus.ARCHIVED),
                ],
            )


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


class IsolationTests(unittest.TestCase):
    def test_importing_the_package_creates_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", "import wiki_maintenance"],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(Path(directory).iterdir()), [])

    def test_the_default_data_root_is_never_created_by_this_suite(self):
        self.assertFalse(
            DEFAULT_WIKI_DATA_ROOT.exists(),
            f"{DEFAULT_WIKI_DATA_ROOT} must stay a runtime-only location",
        )

    def test_a_repository_writes_only_under_its_own_root(self):
        with TempRepository() as repository:
            build = bootstrap_from_wiki_file(repository)
            snapshot = build_document_snapshot(
                document_id=DOCUMENT_ID,
                filename=SOURCE_DOCUMENT,
                chunks=make_chunks(LEAVE_TEXT),
            )
            repository.save_document_snapshot(snapshot)
            repository.publish(build.build_id)

            written = {
                path.relative_to(repository.root).as_posix()
                for path in repository.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                written,
                {
                    "manifest.json",
                    "current.json",
                    "builds/build-0001.json",
                    f"documents/{DOCUMENT_ID}/{snapshot.version}.json",
                },
            )

    def test_snapshot_and_build_paths_reject_traversal(self):
        with TempRepository() as repository:
            with self.assertRaises(ValueError):
                repository.snapshot_path("../escape", "v-000000000000")
            with self.assertRaises(ValueError):
                repository.snapshot_path(DOCUMENT_ID, "../escape")
            with self.assertRaises(ValueError):
                repository.build_path("../escape")

    def test_persisted_models_reject_a_mismatched_version(self):
        snapshot = build_document_snapshot(
            document_id=DOCUMENT_ID, filename=SOURCE_DOCUMENT, chunks=make_chunks(LEAVE_TEXT)
        )
        payload = snapshot.to_dict()
        payload["version"] = "v-000000000000"
        with self.assertRaises(ValueError):
            DocumentSnapshot.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
