"""Cut a document into stable, addressable source spans.

A span is identified by *what it says*, not by where it sits: `span_id` is a
digest of the document id, the heading it falls under, and its normalized text.
Insert a paragraph at the top of a section and every span below it keeps its id -
only the new one is unrecognised. Without that, a re-upload would look like a
total rewrite and no incremental Wiki rebuild would be possible.

Two entry points, and the difference between them matters:

`build_document_snapshot_from_text` - **canonical**. Segments the raw document
    into logical blocks (paragraphs under their heading) before RAG ever touches
    it, so a span boundary is a paragraph boundary and survives editing
    elsewhere in the file.
`build_document_snapshot` - compatibility. Takes `rag.Chunk` values and promises
    only what it can: an unchanged *chunk* keeps its span id. `rag.split_text`
    slices a section into fixed-length windows, so inserting a sentence near the
    top of a long section shifts every later window and re-identifies spans that
    were never edited. Useful for chunks you already hold; not a basis for
    incremental rebuilds.

M9B's upload integration must use the raw-text entry. The same raw text still
goes to `rag.split_text` separately for the retrieval index - the two
segmentations are independent and are not expected to agree, so span ids from
one entry point never match the other's for the same document.

Two spans in one document that are byte-identical under the same heading would
otherwise collide, so repeats past the first carry an occurrence counter. The
first occurrence keeps the plain digest, which is what preserves stability for
the ordinary case.

Normalization before hashing runs in two steps: whitespace between two CJK
ideographs is dropped, then every remaining whitespace run collapses to one
space and the ends are stripped. So a re-indent, a CRLF/LF change, an extra
blank line, and a hard wrap inside a Chinese paragraph are all not edits, while
`remote work` stays distinct from `remotework`. A space at a Chinese/Latin
boundary survives - `中文 work` keeps its space - because there it separates two
words rather than padding two characters that never needed separating.

This is a character-class rule, not a line-breaking engine. The CJK class is the
`U+4E00-U+9FFF` range the Wiki tokenizer already uses, so a wrap landing
directly after fullwidth punctuation - `，` or `；` - still reads as an edit. The
span keeps its original text; only the digest sees the normalized form.

`ordinal` is document-local and 1-based. `Chunk.index` is global across every
uploaded file and would not survive a re-upload of a different file set, so it
is not used here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import NamedTuple

from rag import Chunk

from orchestration.wiki_adapter import CJK_FIRST, CJK_LAST

from .models import (
    DocumentSnapshot,
    SourceSpan,
    document_version_for,
    require_document_id,
    utc_now,
)

SPAN_ID_PREFIX = "span-"
SPAN_ID_HEX_LENGTH = 16
SECTION_HEADING_PREFIX = "## "
# ATX headings only. `rag.split_text` recognises the same syntax, so both
# segmentations read one document the same way.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
# Field separator inside a digest, so ("ab", "c") and ("a", "bc") cannot hash
# to the same value.
_FIELD_SEPARATOR = b"\x00"
_WHITESPACE_PATTERN = re.compile(r"\s+")
# The same CJK Unified Ideographs range the Wiki tokenizer uses, imported rather
# than restated so one convention covers both.
_CJK_CLASS = "[{}-{}]".format(chr(CJK_FIRST), chr(CJK_LAST))
_CJK_JOINED_PATTERN = re.compile(
    r"(?<={cjk})\s+(?={cjk})".format(cjk=_CJK_CLASS)
)
_DOCUMENT_ID_UNSAFE_PATTERN = re.compile(r"[^A-Za-z0-9]+")
_FILENAME_SLUG_LIMIT = 40


def normalize_span_text(text: str) -> str:
    """Whitespace-insensitive form used for every digest in this module.

    Two steps, in this order. Whitespace between two CJK ideographs is dropped,
    because Chinese puts no space there and a hard wrap must not read as an
    edit. Every remaining whitespace run then collapses to one space, which is
    what keeps `remote work` distinct from `remotework` and leaves the single
    space at a `中文 work` boundary intact.
    """
    return _WHITESPACE_PATTERN.sub(" ", _CJK_JOINED_PATTERN.sub("", text)).strip()


def heading_of(text: str) -> str | None:
    """The Markdown section heading a chunk opens with, if it has one."""
    for line in text.splitlines():
        if line.startswith(SECTION_HEADING_PREFIX):
            heading = line[len(SECTION_HEADING_PREFIX) :].strip()
            return heading or None
    return None


def derive_document_id(filename: str) -> str:
    """A path-safe, stable id for an uploaded file name.

    The readable part is the ASCII of the file name; the digest suffix keeps two
    files that differ only outside ASCII - `制度.md` and `规则.md` - apart. A
    fully non-ASCII name degrades to `doc-<digest>` rather than failing.
    """
    if not filename or not filename.strip():
        raise ValueError("filename must not be empty")
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]
    slug = _DOCUMENT_ID_UNSAFE_PATTERN.sub("-", filename).strip("-")
    slug = slug[:_FILENAME_SLUG_LIMIT].strip("-")
    return f"{slug or 'doc'}-{digest}"


def compute_content_hash(text: str) -> str:
    """The digest of one span's text.

    Deliberately free of the document id: identical wording in two documents
    shares a `content_hash` while keeping distinct `span_id`s, which is what
    lets a later milestone notice that a clause moved rather than changed.
    """
    return hashlib.sha256(normalize_span_text(text).encode("utf-8")).hexdigest()


def compute_span_id(
    document_id: str,
    heading: str | None,
    text: str,
    *,
    occurrence: int = 0,
) -> str:
    digest = hashlib.sha256()
    for part in (document_id, heading or "", normalize_span_text(text), str(occurrence)):
        digest.update(part.encode("utf-8"))
        digest.update(_FIELD_SEPARATOR)
    return f"{SPAN_ID_PREFIX}{digest.hexdigest()[:SPAN_ID_HEX_LENGTH]}"


def compute_document_content_hash(span_digests: Sequence[tuple[str, str]]) -> str:
    """Digest of `(span_id, content_hash)` pairs in reading order.

    Order is part of the version: moving a section rewrites the document even
    though every span survives with its own id.
    """
    digest = hashlib.sha256()
    for span_id, content_hash in span_digests:
        digest.update(span_id.encode("utf-8"))
        digest.update(_FIELD_SEPARATOR)
        digest.update(content_hash.encode("utf-8"))
        digest.update(_FIELD_SEPARATOR)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------


class SourceBlock(NamedTuple):
    """One logical piece of a document, with the heading it sits under."""

    source: str
    heading: str | None
    text: str


def source_blocks(text: str, source: str) -> tuple[SourceBlock, ...]:
    """Paragraph-level blocks of a raw document, in reading order.

    A blank line ends a block, so plain text with no Markdown at all still
    segments by paragraph. A heading line ends the block before it and becomes
    the heading every following block carries, until the next heading.

    The first heading is treated as the document's own title when it is level 1,
    matching `rag.split_text`, which strips exactly that line and splits sections
    on `## `. A long paragraph stays one block: splitting it would put a
    boundary somewhere the author did not, and that boundary would then move
    whenever the paragraph was edited.
    """
    blocks: list[SourceBlock] = []
    heading: str | None = None
    seen_heading = False
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            blocks.append(
                SourceBlock(source=source, heading=heading, text="\n".join(buffer))
            )
            buffer.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = HEADING_PATTERN.match(line)
        if match is not None:
            flush()
            level, title = len(match.group(1)), match.group(2)
            if seen_heading or level > 1:
                heading = title
            seen_heading = True
            continue
        if not line:
            flush()
            continue
        buffer.append(line)
    flush()
    return tuple(blocks)


def _blocks_from_chunks(chunks: Sequence[Chunk]) -> list[SourceBlock]:
    """Blocks that mirror the chunk boundaries the caller already has."""
    blocks: list[SourceBlock] = []
    carried_heading: str | None = None
    carried_source: str | None = None
    for chunk in chunks:
        text = chunk.text.strip()
        if not text:
            continue
        # Chunks of one document normally share a source; resetting on a change
        # keeps a heading from leaking across a file boundary if they do not.
        if chunk.source != carried_source:
            carried_source = chunk.source
            carried_heading = None
        heading = heading_of(chunk.text)
        if heading is None:
            heading = carried_heading
        else:
            carried_heading = heading
        blocks.append(SourceBlock(source=chunk.source, heading=heading, text=text))
    return blocks


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


class _SpanContent(NamedTuple):
    """One span before the document version it belongs to is known."""

    ordinal: int
    source: str
    heading: str | None
    text: str
    content_hash: str
    span_id: str


def _span_contents(
    document_id: str, blocks: Sequence[SourceBlock]
) -> list[_SpanContent]:
    contents: list[_SpanContent] = []
    occurrences: dict[str, int] = {}
    # `ordinal` counts spans, not blocks: an empty block contributes no span and
    # must not leave a gap in the numbering.
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        key = f"{block.heading or ''}\x00{normalize_span_text(text)}"
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        contents.append(
            _SpanContent(
                ordinal=len(contents) + 1,
                source=block.source,
                heading=block.heading,
                text=text,
                content_hash=compute_content_hash(text),
                span_id=compute_span_id(
                    document_id, block.heading, text, occurrence=occurrence
                ),
            )
        )
    return contents


def _versioned_spans(
    document_id: str, blocks: Sequence[SourceBlock]
) -> tuple[str, tuple[SourceSpan, ...]]:
    """`(content_hash, spans)`.

    The document version can only be known once every span is identified, so
    this runs a second pass rather than letting a span exist without the version
    it belongs to.
    """
    require_document_id("document_id", document_id)
    contents = _span_contents(document_id, blocks)
    content_hash = compute_document_content_hash(
        [(item.span_id, item.content_hash) for item in contents]
    )
    version = document_version_for(content_hash)
    spans = tuple(
        SourceSpan(
            span_id=item.span_id,
            document_id=document_id,
            document_version=version,
            ordinal=item.ordinal,
            source=item.source,
            heading=item.heading,
            text=item.text,
            content_hash=item.content_hash,
        )
        for item in contents
    )
    return content_hash, spans


def _snapshot(
    *,
    document_id: str,
    filename: str,
    blocks: Sequence[SourceBlock],
    created_at: str | None,
) -> DocumentSnapshot:
    content_hash, spans = _versioned_spans(document_id, blocks)
    return DocumentSnapshot(
        document_id=document_id,
        filename=filename,
        version=document_version_for(content_hash),
        content_hash=content_hash,
        spans=spans,
        created_at=created_at if created_at is not None else utc_now(),
    )


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def build_source_spans_from_text(
    document_id: str, text: str, source: str
) -> tuple[SourceSpan, ...]:
    """Paragraph-level spans of a raw document. The canonical segmentation."""
    return _versioned_spans(document_id, source_blocks(text, source))[1]


def build_document_snapshot_from_text(
    *,
    document_id: str,
    filename: str,
    text: str,
    source: str | None = None,
    created_at: str | None = None,
) -> DocumentSnapshot:
    """Snapshot a document from its raw text. **Use this for uploads.**

    Segmenting before RAG is what makes the ids hold: editing one paragraph
    leaves every other paragraph's span id untouched, however the retrieval
    chunker happens to slice the file afterwards.

    `source` is what each span cites and defaults to `filename`, matching the
    `source` the upload path hands to `rag.split_text`. Same text in, same
    `version` and same span ids out, whatever the clock says.
    """
    resolved_source = source if source is not None else filename
    return _snapshot(
        document_id=document_id,
        filename=filename,
        blocks=source_blocks(text, resolved_source),
        created_at=created_at,
    )


def build_source_spans(
    document_id: str, chunks: Sequence[Chunk]
) -> tuple[SourceSpan, ...]:
    """Spans for chunks you already hold.

    Stable only per chunk: a chunk whose text is unchanged keeps its span id.
    Because `rag.split_text` re-slices a section into fixed-length windows, an
    edit anywhere in a long section moves the later boundaries and re-identifies
    untouched prose. Prefer `build_source_spans_from_text`.
    """
    return _versioned_spans(document_id, _blocks_from_chunks(chunks))[1]


def build_document_snapshot(
    *,
    document_id: str,
    filename: str,
    chunks: Sequence[Chunk],
    created_at: str | None = None,
) -> DocumentSnapshot:
    """Snapshot a document from chunks. Compatibility entry.

    Guarantees only that an unchanged chunk keeps its span id - not that an
    unchanged paragraph does, because `rag.split_text` does not preserve chunk
    boundaries across an edit. `build_document_snapshot_from_text` is the
    canonical entry and the one M9B's upload integration must use.
    """
    return _snapshot(
        document_id=document_id,
        filename=filename,
        blocks=_blocks_from_chunks(chunks),
        created_at=created_at,
    )
