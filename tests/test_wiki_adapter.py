import json
import re
import tempfile
import unittest
from pathlib import Path

from orchestration.contracts import SourceType, ToolStatus
from orchestration.document_adapter import DOCUMENT_AUTHORITY
from orchestration.wiki_adapter import (
    ALIAS_WEIGHT,
    CLAIM_WEIGHT,
    DEFAULT_WIKI_PATH,
    SUMMARY_WEIGHT,
    SUPPORTED_SCHEMA_VERSION,
    TITLE_WEIGHT,
    WIKI_AUTHORITY,
    WIKI_TOOL_NAME,
    load_wiki_pages,
    tokenize,
    wiki_query,
)
from orchestration.wiki_schema import WikiClaim, WikiPage, validate_collection

SOURCE_DOCUMENT = "sample_company_rules.md"
REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO_ROOT / SOURCE_DOCUMENT

CLAIM_STRING_FIELDS = ("claim_id", "text", "source", "locator")
PAGE_STRING_FIELDS = ("page_id", "title", "summary", "version")
NON_STRING_VALUES = (1, True, None, [], {})


def make_claim(**overrides) -> WikiClaim:
    defaults = {
        "claim_id": "c1",
        "text": "员工每周最多申请 2 天远程办公。",
        "source": SOURCE_DOCUMENT,
        "locator": "section:远程办公",
    }
    defaults.update(overrides)
    return WikiClaim(**defaults)


def make_page(**overrides) -> WikiPage:
    defaults = {
        "page_id": "p1",
        "title": "远程办公",
        "summary": "远程办公需要提前申请。",
        "version": "1.0",
        "aliases": ("远程",),
        "claims": (make_claim(),),
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


def valid_document() -> dict:
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "pages": [
            {
                "page_id": "p1",
                "title": "远程办公",
                "summary": "远程办公需要提前申请。",
                "aliases": ["远程"],
                "version": "1.0",
                "claims": [
                    {
                        "claim_id": "c1",
                        "text": "员工每周最多申请 2 天远程办公。",
                        "source": SOURCE_DOCUMENT,
                        "locator": "section:远程办公",
                    }
                ],
            }
        ],
    }


class TempWikiFile:
    """Write a document to a temporary file; never touch wiki_pages/."""

    def __init__(self, payload, *, raw_text: str | None = None):
        self.payload = payload
        self.raw_text = raw_text

    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "wiki.json"
        if self.raw_text is not None:
            path.write_text(self.raw_text, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(self.payload, ensure_ascii=False), encoding="utf-8"
            )
        return path

    def __exit__(self, *exc_info) -> None:
        self._dir.cleanup()


class SchemaConstructionTests(unittest.TestCase):
    def test_claim_and_page_construct_and_serialize(self):
        claim = make_claim()
        page = make_page(claims=(claim,), aliases=("远程", "居家办公"))

        self.assertEqual(
            claim.to_dict(),
            {
                "claim_id": "c1",
                "text": "员工每周最多申请 2 天远程办公。",
                "source": SOURCE_DOCUMENT,
                "locator": "section:远程办公",
            },
        )
        payload = page.to_dict()
        self.assertEqual(payload["aliases"], ["远程", "居家办公"])
        self.assertIsInstance(payload["aliases"], list)
        self.assertEqual(payload["claims"], [claim.to_dict()])
        self.assertIsInstance(payload["claims"], list)
        self.assertEqual(payload["page_id"], "p1")
        self.assertEqual(payload["version"], "1.0")

    def test_blank_string_fields_are_rejected(self):
        for blank in ("", "   ", "\n"):
            for name in CLAIM_STRING_FIELDS:
                with self.subTest(target="claim", field=name, blank=blank):
                    with self.assertRaises(ValueError):
                        make_claim(**{name: blank})
            for name in PAGE_STRING_FIELDS:
                with self.subTest(target="page", field=name, blank=blank):
                    with self.assertRaises(ValueError):
                        make_page(**{name: blank})

    def test_locator_shape_is_a_schema_invariant(self):
        for bad in ("bad-locator", "section:", "section:   ", "Section:请假制度", "请假制度"):
            with self.subTest(locator=bad):
                with self.assertRaises(ValueError) as caught:
                    make_claim(locator=bad)
                self.assertIn("WikiClaim.locator", str(caught.exception))

    def test_well_formed_locator_is_accepted(self):
        self.assertEqual(make_claim(locator="section:请假制度").locator, "section:请假制度")

    def test_page_without_claims_is_rejected(self):
        with self.assertRaises(ValueError):
            make_page(claims=())

    def test_duplicate_claim_id_within_page_is_rejected(self):
        with self.assertRaises(ValueError):
            make_page(claims=(make_claim(claim_id="dup"), make_claim(claim_id="dup")))

    def test_duplicate_aliases_are_rejected_and_empty_aliases_allowed(self):
        with self.assertRaises(ValueError):
            make_page(aliases=("远程", "远程"))
        self.assertEqual(make_page(aliases=()).aliases, ())

    def test_pages_and_claims_are_immutable(self):
        claim = make_claim()
        page = make_page()
        with self.assertRaises(Exception):
            claim.text = "changed"
        with self.assertRaises(Exception):
            page.title = "changed"


class CollectionInvariantTests(unittest.TestCase):
    def test_duplicate_page_id_is_rejected(self):
        first = make_page(page_id="same", claims=(make_claim(claim_id="a"),))
        second = make_page(page_id="same", claims=(make_claim(claim_id="b"),))
        with self.assertRaises(ValueError):
            validate_collection((first, second))

    def test_duplicate_claim_id_across_pages_is_rejected(self):
        first = make_page(page_id="p1", claims=(make_claim(claim_id="shared"),))
        second = make_page(page_id="p2", claims=(make_claim(claim_id="shared"),))
        with self.assertRaises(ValueError):
            validate_collection((first, second))

    def test_empty_collection_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_collection(())

    def test_non_page_element_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_collection((make_page(), "not-a-page"))


class DirectConstructionTypeTests(unittest.TestCase):
    def test_non_string_values_raise_value_error_for_every_string_field(self):
        for value in NON_STRING_VALUES:
            for name in CLAIM_STRING_FIELDS:
                with self.subTest(target="claim", field=name, value=value):
                    with self.assertRaises(ValueError):
                        make_claim(**{name: value})
            for name in PAGE_STRING_FIELDS:
                with self.subTest(target="page", field=name, value=value):
                    with self.assertRaises(ValueError):
                        make_page(**{name: value})

    def test_attribute_error_never_leaks(self):
        for value in NON_STRING_VALUES:
            with self.subTest(value=value):
                try:
                    make_claim(text=value)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001 - the point of the test
                    self.fail(f"expected ValueError, got {type(exc).__name__}: {exc}")
                else:
                    self.fail("expected ValueError")

    def test_error_message_uses_the_dotted_class_path(self):
        with self.assertRaises(ValueError) as caught:
            make_claim(claim_id=1)
        self.assertIn("WikiClaim.claim_id", str(caught.exception))

        with self.assertRaises(ValueError) as caught:
            make_page(title=None)
        self.assertIn("WikiPage.title", str(caught.exception))

    def test_list_containers_are_rejected(self):
        with self.assertRaises(ValueError):
            make_page(aliases=["远程"])
        with self.assertRaises(ValueError):
            make_page(claims=[make_claim()])

    def test_non_claim_element_and_non_string_alias_are_rejected(self):
        with self.assertRaises(ValueError):
            make_page(claims=("not-a-claim",))
        with self.assertRaises(ValueError):
            make_page(aliases=(1,))


class LoaderTests(unittest.TestCase):
    def test_loads_the_committed_file_in_order(self):
        pages = load_wiki_pages()
        self.assertGreaterEqual(len(pages), 3)
        raw = json.loads(DEFAULT_WIKI_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            [page.page_id for page in pages],
            [entry["page_id"] for entry in raw["pages"]],
        )

    def test_missing_file_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_wiki_pages(Path(directory) / "absent.json")

    def test_malformed_json_raises_value_error(self):
        with TempWikiFile(None, raw_text="{not json") as path:
            with self.assertRaises(ValueError):
                load_wiki_pages(path)

    def test_schema_version_problems_are_rejected(self):
        missing = valid_document()
        del missing["schema_version"]
        unsupported = valid_document()
        unsupported["schema_version"] = "9.9"
        for payload in (missing, unsupported):
            with self.subTest(payload=payload.get("schema_version")):
                with TempWikiFile(payload) as path:
                    with self.assertRaises(ValueError):
                        load_wiki_pages(path)

    def test_missing_or_empty_pages_are_rejected(self):
        missing = valid_document()
        del missing["pages"]
        empty = valid_document()
        empty["pages"] = []
        for payload in (missing, empty):
            with self.subTest(pages=payload.get("pages")):
                with TempWikiFile(payload) as path:
                    with self.assertRaises(ValueError):
                        load_wiki_pages(path)

    def test_collection_invariants_are_enforced_on_load(self):
        duplicate_pages = valid_document()
        duplicate_pages["pages"].append(json.loads(json.dumps(duplicate_pages["pages"][0])))

        duplicate_claims = valid_document()
        second = json.loads(json.dumps(duplicate_claims["pages"][0]))
        second["page_id"] = "p2"
        duplicate_claims["pages"].append(second)

        for payload in (duplicate_pages, duplicate_claims):
            with self.subTest(pages=len(payload["pages"])):
                with TempWikiFile(payload) as path:
                    with self.assertRaises(ValueError):
                        load_wiki_pages(path)


class LoaderTypeBoundaryTests(unittest.TestCase):
    """Malformed input must never escape as AttributeError/TypeError/KeyError."""

    def assert_value_error(self, payload=None, *, raw_text=None):
        with TempWikiFile(payload, raw_text=raw_text) as path:
            try:
                load_wiki_pages(path)
            except ValueError as exc:
                return str(exc)
            except Exception as exc:  # noqa: BLE001 - the point of the test
                self.fail(f"expected ValueError, got {type(exc).__name__}: {exc}")
            self.fail("expected ValueError")

    def test_non_object_top_level(self):
        for raw_text in ("[]", '"text"', "42"):
            with self.subTest(raw_text=raw_text):
                self.assert_value_error(raw_text=raw_text)

    def test_pages_not_a_list(self):
        payload = valid_document()
        payload["pages"] = {"p1": {}}
        self.assert_value_error(payload)

    def test_page_element_not_an_object(self):
        payload = valid_document()
        payload["pages"] = ["not-a-page"]
        self.assert_value_error(payload)

    def test_claims_not_a_list(self):
        payload = valid_document()
        payload["pages"][0]["claims"] = {"c1": {}}
        self.assert_value_error(payload)

    def test_claim_element_not_an_object(self):
        payload = valid_document()
        payload["pages"][0]["claims"] = ["not-a-claim"]
        self.assert_value_error(payload)

    def test_non_string_fields_at_every_level(self):
        for value in NON_STRING_VALUES:
            with self.subTest(level="root", value=value):
                payload = valid_document()
                payload["schema_version"] = value
                self.assert_value_error(payload)
            for name in PAGE_STRING_FIELDS:
                with self.subTest(level="page", field=name, value=value):
                    payload = valid_document()
                    payload["pages"][0][name] = value
                    self.assert_value_error(payload)
            for name in CLAIM_STRING_FIELDS:
                with self.subTest(level="claim", field=name, value=value):
                    payload = valid_document()
                    payload["pages"][0]["claims"][0][name] = value
                    self.assert_value_error(payload)

    def test_aliases_container_and_elements(self):
        payload = valid_document()
        payload["pages"][0]["aliases"] = "远程"
        self.assert_value_error(payload)

        payload = valid_document()
        payload["pages"][0]["aliases"] = ["远程", 7]
        self.assert_value_error(payload)

    def test_every_required_page_field_must_be_present(self):
        for name in ("page_id", "title", "summary", "version", "aliases", "claims"):
            with self.subTest(field=name):
                payload = valid_document()
                del payload["pages"][0][name]
                message = self.assert_value_error(payload)
                self.assertIn(f"pages[0].{name}", message)

    def test_every_required_claim_field_must_be_present(self):
        for name in CLAIM_STRING_FIELDS:
            with self.subTest(field=name):
                payload = valid_document()
                del payload["pages"][0]["claims"][0][name]
                message = self.assert_value_error(payload)
                self.assertIn(f"pages[0].claims[0].{name}", message)

    def test_every_required_root_field_must_be_present(self):
        for name in ("schema_version", "pages"):
            with self.subTest(field=name):
                payload = valid_document()
                del payload[name]
                self.assertIn(name, self.assert_value_error(payload))

    def test_empty_aliases_list_is_still_accepted(self):
        payload = valid_document()
        payload["pages"][0]["aliases"] = []
        with TempWikiFile(payload) as path:
            self.assertEqual(load_wiki_pages(path)[0].aliases, ())

    def test_malformed_locator_reports_the_indexed_path(self):
        for bad in ("bad-locator", "section:"):
            with self.subTest(locator=bad):
                payload = valid_document()
                payload["pages"][0]["claims"][0]["locator"] = bad
                self.assertIn(
                    "pages[0].claims[0].locator", self.assert_value_error(payload)
                )

    def test_unknown_fields_rejected_at_all_three_levels(self):
        root = valid_document()
        root["extra"] = 1

        page = valid_document()
        page["pages"][0]["extra"] = 1

        claim = valid_document()
        claim["pages"][0]["claims"][0]["extra"] = 1

        for payload, level in ((root, "root"), (page, "page"), (claim, "claim")):
            with self.subTest(level=level):
                message = self.assert_value_error(payload)
                self.assertIn("unknown field", message)

    def test_error_messages_carry_indexed_field_paths(self):
        payload = valid_document()
        second = json.loads(json.dumps(payload["pages"][0]))
        second["page_id"] = "p2"
        second["claims"][0]["claim_id"] = "c2"
        second["claims"][0]["text"] = 5
        payload["pages"].append(second)
        self.assertIn("pages[1].claims[0].text", self.assert_value_error(payload))

        payload = valid_document()
        payload["pages"][0]["aliases"] = ["a", "b", "c", 4]
        self.assertIn("pages[0].aliases[3]", self.assert_value_error(payload))

        payload = valid_document()
        payload["schema_version"] = 1
        self.assertIn("schema_version", self.assert_value_error(payload))


class SourceFidelityTests(unittest.TestCase):
    """The Wiki is derived knowledge; it must not invent facts."""

    @classmethod
    def setUpClass(cls):
        cls.pages = load_wiki_pages()
        text = SOURCE_PATH.read_text(encoding="utf-8")
        cls.sections: dict[str, str] = {}
        current = None
        buffer: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if current is not None:
                    cls.sections[current] = "\n".join(buffer)
                current = line[3:].strip()
                buffer = []
            elif current is not None:
                buffer.append(line)
        if current is not None:
            cls.sections[current] = "\n".join(buffer)

    def test_every_locator_points_at_a_real_section(self):
        for page in self.pages:
            for claim in page.claims:
                with self.subTest(claim=claim.claim_id):
                    self.assertEqual(claim.source, SOURCE_DOCUMENT)
                    self.assertTrue(
                        claim.locator.startswith("section:"),
                        f"{claim.claim_id} locator must use the section: form",
                    )
                    heading = claim.locator.split(":", 1)[1]
                    self.assertIn(heading, self.sections)

    def test_no_invented_numbers(self):
        for page in self.pages:
            for claim in page.claims:
                heading = claim.locator.split(":", 1)[1]
                section_text = self.sections[heading]
                for number in re.findall(r"\d+", claim.text):
                    with self.subTest(claim=claim.claim_id, number=number):
                        self.assertIn(
                            number,
                            section_text,
                            f"{claim.claim_id} cites {number}, absent from {heading!r}",
                        )


class TokenizerTests(unittest.TestCase):
    def test_bigrams_never_span_a_separator(self):
        self.assertEqual(tokenize("年假、调休"), ["年假", "调休"])
        self.assertNotIn("假调", tokenize("年假、调休"))

    def test_repeated_query_term_is_deduplicated(self):
        self.assertEqual(tokenize("年假、年假、年假"), ["年假"])

    def test_contiguous_repetition_is_not_a_repetition_example(self):
        # Documented in the spec: one six-character run, not three separate ones.
        self.assertEqual(tokenize("年假年假年假"), ["年假", "假年"])

    def test_single_character_run_becomes_its_own_token(self):
        self.assertEqual(tokenize("假"), ["假"])

    def test_ascii_tokens_and_case_folding(self):
        self.assertEqual(tokenize("Remote  Work 2"), ["remote", "work", "2"])

    def test_first_appearance_order_is_preserved(self):
        self.assertEqual(tokenize("远程办公"), ["远程", "程办", "办公"])


class ScoringTests(unittest.TestCase):
    def test_exact_weighted_score(self):
        page = make_page(
            title="年假",
            summary="年假",
            aliases=("年假",),
            claims=(make_claim(claim_id="c1", text="年假"),),
        )
        result = wiki_query("年假", (page,))
        expected = TITLE_WEIGHT + ALIAS_WEIGHT + SUMMARY_WEIGHT + CLAIM_WEIGHT
        self.assertEqual(result.evidence[0].metadata["retrieval_score"], expected)
        self.assertEqual(expected, 10)

    def test_alias_only_match_scores_alias_weight(self):
        page = make_page(
            title="甲乙",
            summary="丙丁",
            aliases=("年假",),
            claims=(make_claim(claim_id="c1", text="戊己"),),
        )
        result = wiki_query("年假", (page,))
        self.assertEqual(result.evidence[0].metadata["retrieval_score"], ALIAS_WEIGHT)

    def test_repeated_aliases_count_once(self):
        merged = make_page(
            page_id="merged",
            title="甲乙",
            summary="丙丁",
            aliases=("年假", "年假额度", "年假申请"),
            claims=(make_claim(claim_id="c1", text="戊己"),),
        )
        single = make_page(
            page_id="single",
            title="甲乙",
            summary="丙丁",
            aliases=("年假",),
            claims=(make_claim(claim_id="c2", text="戊己"),),
        )
        merged_score = wiki_query("年假", (merged,)).evidence[0].metadata["retrieval_score"]
        single_score = wiki_query("年假", (single,)).evidence[0].metadata["retrieval_score"]
        self.assertEqual(merged_score, single_score)
        self.assertEqual(merged_score, ALIAS_WEIGHT)

    def test_repeated_occurrence_in_a_field_counts_once(self):
        once = make_page(
            page_id="once",
            title="甲乙",
            summary="丙丁",
            aliases=(),
            claims=(make_claim(claim_id="c1", text="年假"),),
        )
        many = make_page(
            page_id="many",
            title="甲乙",
            summary="丙丁",
            aliases=(),
            claims=(make_claim(claim_id="c2", text="年假、年假、年假、年假"),),
        )
        self.assertEqual(
            wiki_query("年假", (once,)).evidence[0].metadata["retrieval_score"],
            wiki_query("年假", (many,)).evidence[0].metadata["retrieval_score"],
        )

    def test_repeated_query_term_scores_the_same(self):
        page = make_page(
            title="甲乙",
            summary="丙丁",
            aliases=(),
            claims=(make_claim(claim_id="c1", text="年假"),),
        )
        self.assertEqual(
            wiki_query("年假", (page,)).evidence[0].metadata["retrieval_score"],
            wiki_query("年假、年假、年假", (page,)).evidence[0].metadata["retrieval_score"],
        )

    def test_punctuation_boundary_prevents_a_false_match(self):
        page = make_page(
            title="甲乙",
            summary="丙丁",
            aliases=(),
            claims=(make_claim(claim_id="c1", text="年假、调休"),),
        )
        self.assertEqual(wiki_query("假调", (page,)).status, ToolStatus.EMPTY)
        self.assertEqual(wiki_query("年假", (page,)).status, ToolStatus.OK)
        self.assertEqual(wiki_query("调休", (page,)).status, ToolStatus.OK)

    def test_single_character_term_matches(self):
        page = make_page(
            title="假",
            summary="丙丁",
            aliases=(),
            claims=(make_claim(claim_id="c1", text="戊己"),),
        )
        result = wiki_query("假", (page,))
        self.assertEqual(result.evidence[0].metadata["retrieval_score"], TITLE_WEIGHT)


class OrderingTests(unittest.TestCase):
    def tie_pages(self):
        first = make_page(
            page_id="a-page",
            title="年假",
            summary="丙丁",
            aliases=(),
            claims=(
                make_claim(claim_id="a-c2", text="戊己"),
                make_claim(claim_id="a-c1", text="戊己"),
            ),
        )
        second = make_page(
            page_id="b-page",
            title="年假",
            summary="丙丁",
            aliases=(),
            claims=(
                make_claim(claim_id="b-c2", text="戊己"),
                make_claim(claim_id="b-c1", text="戊己"),
            ),
        )
        return (second, first)

    def test_ties_break_by_page_id_then_claim_id(self):
        result = wiki_query("年假", self.tie_pages(), top_k=10)
        scores = {ev.metadata["retrieval_score"] for ev in result.evidence}
        self.assertEqual(scores, {TITLE_WEIGHT})
        self.assertEqual(
            [ev.metadata["claim_id"] for ev in result.evidence],
            ["a-c1", "a-c2", "b-c1", "b-c2"],
        )

    def test_ordering_is_repeatable(self):
        pages = load_wiki_pages()
        first = wiki_query("年假有多少天", pages, top_k=5)
        second = wiki_query("年假有多少天", pages, top_k=5)
        self.assertEqual(
            [ev.metadata["claim_id"] for ev in first.evidence],
            [ev.metadata["claim_id"] for ev in second.evidence],
        )

    def test_ranks_start_at_one_and_are_contiguous(self):
        result = wiki_query("年假", self.tie_pages(), top_k=10)
        self.assertEqual(
            [ev.metadata["rank"] for ev in result.evidence],
            list(range(1, len(result.evidence) + 1)),
        )


class RepresentativeQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = load_wiki_pages()

    def top_page(self, question: str) -> str:
        result = wiki_query(question, self.pages, top_k=1)
        self.assertEqual(result.status, ToolStatus.OK)
        return result.evidence[0].metadata["page_id"]

    def test_annual_leave_query(self):
        self.assertEqual(self.top_page("年假有多少天"), "wiki-leave")

    def test_remote_work_query(self):
        self.assertEqual(self.top_page("远程办公可以申请几天"), "wiki-remote-work")

    def test_information_security_query(self):
        self.assertEqual(
            self.top_page("内部资料可以上传到公共网盘吗"), "wiki-information-security"
        )


class QueryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = load_wiki_pages()

    def test_blank_question_raises(self):
        for blank in ("", "   ", "\n\t"):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    wiki_query(blank, self.pages)

    def test_invalid_top_k_raises(self):
        for top_k in (0, -1):
            with self.subTest(top_k=top_k):
                with self.assertRaises(ValueError):
                    wiki_query("年假", self.pages, top_k=top_k)

    def test_invalid_pages_raise(self):
        for pages in ((), ["not-a-page"], "pages"):
            with self.subTest(pages=pages):
                with self.assertRaises(ValueError):
                    wiki_query("年假", pages)

    def test_top_k_bounds_evidence_count(self):
        for top_k in (1, 2, 3):
            with self.subTest(top_k=top_k):
                result = wiki_query("年假有多少天", self.pages, top_k=top_k)
                self.assertLessEqual(len(result.evidence), top_k)

    def test_top_k_larger_than_matches_returns_all(self):
        generous = wiki_query("年假有多少天", self.pages, top_k=999)
        self.assertEqual(
            len(generous.evidence), generous.trace["wiki_matched_claims"]
        )

    def test_no_match_returns_empty(self):
        result = wiki_query("今天午饭吃什么", self.pages)
        self.assertEqual(result.status, ToolStatus.EMPTY)
        self.assertEqual(result.evidence, ())
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error_message)
        self.assertEqual(result.tool_name, WIKI_TOOL_NAME)


class EvidenceMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = load_wiki_pages()

    def test_evidence_carries_both_trails(self):
        result = wiki_query("年假有多少天", self.pages, top_k=1)
        evidence = result.evidence[0]
        page = next(p for p in self.pages if p.page_id == evidence.metadata["page_id"])
        claim = next(
            c for c in page.claims if c.claim_id == evidence.metadata["claim_id"]
        )

        self.assertEqual(evidence.source_type, SourceType.WIKI)
        self.assertEqual(evidence.content, claim.text)
        # Source-document trail.
        self.assertEqual(evidence.source, SOURCE_DOCUMENT)
        self.assertEqual(evidence.locator, claim.locator)
        # Wiki trail.
        self.assertEqual(evidence.metadata["page_title"], page.title)
        self.assertEqual(
            evidence.metadata["wiki_locator"],
            f"page:{page.page_id}#claim:{claim.claim_id}",
        )
        self.assertEqual(evidence.version, page.version)
        self.assertIsNone(evidence.observed_at)

    def test_wiki_authority_ranks_below_document(self):
        self.assertLess(WIKI_AUTHORITY, DOCUMENT_AUTHORITY)
        result = wiki_query("年假有多少天", self.pages, top_k=1)
        self.assertEqual(result.evidence[0].authority, WIKI_AUTHORITY)

    def test_retrieval_score_is_metadata_and_confidence_stays_none(self):
        result = wiki_query("年假有多少天", self.pages, top_k=1)
        evidence = result.evidence[0]
        self.assertGreater(evidence.metadata["retrieval_score"], 0)
        self.assertIsNone(evidence.confidence)

    def test_to_dict_metadata_is_detached(self):
        result = wiki_query("年假有多少天", self.pages, top_k=1)
        evidence = result.evidence[0]
        payload = evidence.to_dict()
        payload["metadata"]["rank"] = 99
        self.assertEqual(evidence.metadata["rank"], 1)


class TraceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = load_wiki_pages()

    def test_trace_contains_caller_keys_plus_adapter_fields(self):
        result = wiki_query("年假有多少天", self.pages, top_k=1, trace={"caller": "test"})
        self.assertEqual(
            set(result.trace),
            {
                "caller",
                "wiki_scanned_pages",
                "wiki_matched_claims",
                "wiki_returned_evidence",
                "wiki_top_k",
            },
        )
        self.assertEqual(result.trace["caller"], "test")
        self.assertEqual(result.trace["wiki_scanned_pages"], len(self.pages))
        self.assertEqual(result.trace["wiki_top_k"], 1)
        self.assertEqual(result.trace["wiki_returned_evidence"], 1)
        self.assertGreater(
            result.trace["wiki_matched_claims"], result.trace["wiki_returned_evidence"]
        )

    def test_absent_trace_yields_adapter_fields_only(self):
        result = wiki_query("年假有多少天", self.pages)
        self.assertEqual(
            set(result.trace),
            {
                "wiki_scanned_pages",
                "wiki_matched_claims",
                "wiki_returned_evidence",
                "wiki_top_k",
            },
        )

    def test_adapter_value_overrides_a_colliding_caller_key(self):
        result = wiki_query("年假有多少天", self.pages, top_k=2, trace={"wiki_top_k": 999})
        self.assertEqual(result.trace["wiki_top_k"], 2)

    def test_caller_trace_is_not_mutated(self):
        trace = {"caller": "test"}
        snapshot = dict(trace)
        wiki_query("年假有多少天", self.pages, trace=trace)
        self.assertEqual(trace, snapshot)

    def test_top_level_isolation_after_the_call(self):
        trace = {"caller": "test"}
        result = wiki_query("年假有多少天", self.pages, trace=trace)
        trace["added_later"] = True     # add
        trace["caller"] = "changed"     # replace
        self.assertNotIn("added_later", result.trace)
        self.assertEqual(result.trace["caller"], "test")

        del trace["caller"]             # delete
        self.assertEqual(result.trace["caller"], "test")

    def test_nested_values_are_shared_by_design(self):
        # The documented contract is a shallow copy: only top-level isolation is
        # promised. Locking this keeps a future deepcopy from being silent.
        nested = {"inner": 1}
        trace = {"nested": nested}
        result = wiki_query("年假有多少天", self.pages, trace=trace)
        nested["inner"] = 2
        self.assertEqual(result.trace["nested"]["inner"], 2)


class OfflinePurityTests(unittest.TestCase):
    def test_wiki_modules_do_not_import_infrastructure(self):
        import orchestration.wiki_adapter as adapter_module
        import orchestration.wiki_schema as schema_module

        for module in (adapter_module, schema_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for forbidden in (
                "import agent",
                "import api",
                "import rag",
                "import storage",
                "import planner",
                "import fastapi",
                "import requests",
                "import sqlite3",
            ):
                with self.subTest(module=module.__name__, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
