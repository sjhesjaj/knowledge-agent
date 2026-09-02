"""Read-only, versioned Wiki page schema.

Wiki pages are *derived* knowledge. Every claim carries a locator back to the
section of the source document it was compiled from, so a reader can always
leave the Wiki and verify the original wording.

Validation checks types before using them. A malformed page must surface as a
`ValueError` naming the offending field, never as an `AttributeError` escaping
from `.strip()` - that would mean the code trusted the input's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


SECTION_LOCATOR_PREFIX = "section:"
# For sources with no headings at all - a plain TXT, or a PDF that extracts as
# unbroken prose. The span is then the finest real address the document has, and
# naming it beats inventing a section that does not exist.
SPAN_LOCATOR_PREFIX = "span:"
LOCATOR_PREFIXES = (SECTION_LOCATOR_PREFIX, SPAN_LOCATOR_PREFIX)

# The original name, kept because `section:` is still the preferred form.
LOCATOR_PREFIX = SECTION_LOCATOR_PREFIX


def _require_text(path: str, value: object) -> None:
    """Type first, then emptiness. `isinstance(True, str)` is False, so bools fail here."""
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{path} must not be empty")


def require_locator(path: str, value: object) -> None:
    """A locator must name a place in the source, not merely be a non-empty string.

    Two forms, both addressing something that really exists: `section:<heading>`
    for a document with headings, and `span:<span_id>` for one without. Whether
    the target actually exists is checked by the committed data's
    source-fidelity test; the shape is a schema invariant so a malformed locator
    cannot enter the collection at all.
    """
    _require_text(path, value)
    assert isinstance(value, str)  # narrowed by _require_text
    for prefix in LOCATOR_PREFIXES:
        if value.startswith(prefix):
            if not value[len(prefix) :].strip():
                raise ValueError(
                    f"{path} must name a non-empty target after {prefix!r}"
                )
            return
    allowed = " or ".join(repr(prefix) for prefix in LOCATOR_PREFIXES)
    raise ValueError(f"{path} must start with {allowed}")


@dataclass(frozen=True, kw_only=True)
class WikiClaim:
    """One compiled statement, traceable to a source-document section.

    `source_span_ids` names the exact spans a compiler read to write this claim.
    It is optional so that a hand-authored claim - which cites its section and
    nothing finer - stays valid, and so that a page written before spans existed
    still loads.
    """

    claim_id: str
    text: str
    source: str
    locator: str
    source_span_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("WikiClaim.claim_id", self.claim_id)
        _require_text("WikiClaim.text", self.text)
        _require_text("WikiClaim.source", self.source)
        require_locator("WikiClaim.locator", self.locator)

        if not isinstance(self.source_span_ids, tuple):
            raise ValueError(
                "WikiClaim.source_span_ids must be a tuple, got "
                f"{type(self.source_span_ids).__name__}"
            )
        for index, span_id in enumerate(self.source_span_ids):
            _require_text(f"WikiClaim.source_span_ids[{index}]", span_id)
        if len(set(self.source_span_ids)) != len(self.source_span_ids):
            raise ValueError("WikiClaim.source_span_ids must not contain duplicates")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "claim_id": self.claim_id,
            "text": self.text,
            "source": self.source,
            "locator": self.locator,
        }
        # Omitted when empty, so a claim carrying no span provenance serializes
        # exactly as it did before this field existed.
        if self.source_span_ids:
            payload["source_span_ids"] = list(self.source_span_ids)
        return payload


@dataclass(frozen=True, kw_only=True)
class WikiPage:
    """A compiled topic page holding at least one traceable claim."""

    page_id: str
    title: str
    summary: str
    version: str
    aliases: tuple[str, ...] = ()
    claims: tuple[WikiClaim, ...] = ()

    def __post_init__(self) -> None:
        _require_text("WikiPage.page_id", self.page_id)
        _require_text("WikiPage.title", self.title)
        _require_text("WikiPage.summary", self.summary)
        _require_text("WikiPage.version", self.version)

        if not isinstance(self.aliases, tuple):
            raise ValueError(
                f"WikiPage.aliases must be a tuple, got {type(self.aliases).__name__}"
            )
        for index, alias in enumerate(self.aliases):
            _require_text(f"WikiPage.aliases[{index}]", alias)
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("WikiPage.aliases must not contain duplicates")

        if not isinstance(self.claims, tuple):
            raise ValueError(
                f"WikiPage.claims must be a tuple, got {type(self.claims).__name__}"
            )
        if not self.claims:
            raise ValueError("WikiPage.claims must contain at least one claim")
        for index, claim in enumerate(self.claims):
            if not isinstance(claim, WikiClaim):
                raise ValueError(
                    f"WikiPage.claims[{index}] must be a WikiClaim, "
                    f"got {type(claim).__name__}"
                )
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("WikiPage.claims must not contain duplicate claim_id values")

    def to_dict(self) -> dict[str, object]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "summary": self.summary,
            "aliases": list(self.aliases),
            "version": self.version,
            "claims": [claim.to_dict() for claim in self.claims],
        }


def validate_collection(pages: Sequence[WikiPage], *, path: str = "pages") -> tuple[WikiPage, ...]:
    """Enforce the invariants that only hold across a whole collection.

    A `claim_id` is unique across every page, not merely within one, so a claim
    id on its own identifies exactly one claim.
    """
    if isinstance(pages, (str, bytes)) or not isinstance(pages, Sequence):
        raise ValueError(f"{path} must be a sequence of WikiPage")
    for index, page in enumerate(pages):
        if not isinstance(page, WikiPage):
            raise ValueError(
                f"{path}[{index}] must be a WikiPage, got {type(page).__name__}"
            )
    if not pages:
        raise ValueError(f"{path} must contain at least one page")

    seen_pages: dict[str, int] = {}
    seen_claims: dict[str, str] = {}
    for index, page in enumerate(pages):
        if page.page_id in seen_pages:
            raise ValueError(
                f"{path}[{index}].page_id duplicates {path}[{seen_pages[page.page_id]}].page_id: "
                f"{page.page_id!r}"
            )
        seen_pages[page.page_id] = index
        for claim_index, claim in enumerate(page.claims):
            if claim.claim_id in seen_claims:
                raise ValueError(
                    f"{path}[{index}].claims[{claim_index}].claim_id duplicates "
                    f"{seen_claims[claim.claim_id]}: {claim.claim_id!r}"
                )
            seen_claims[claim.claim_id] = f"{path}[{index}].claims[{claim_index}].claim_id"
    return tuple(pages)
