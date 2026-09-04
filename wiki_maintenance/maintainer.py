"""The Wiki Maintainer Agent: one saved document in, one draft build out.

The run is deliberately all-or-nothing and never touches what is live:

- an `ignore` decision writes nothing at all;
- an `update` recompiles the whole Wiki from the effective document set and
  saves it as a **draft**;
- any failure raises before `create_build`, so there is no half-built Wiki and
  `current.json` is untouched either way.

Publishing stays a separate, human act. A compiler that could both write and
publish would make "the model changed the answer" and "we decided to change the
answer" the same event.

The effective document set for an update is:

    active documents  -  superseded  -  this document's old version  +  new version

"Active" comes from the base build's `document_versions`, which is what a build
already records about its own provenance - so the set is read from the Wiki's
own history rather than from a second registry that could disagree with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from orchestration.wiki_schema import WikiPage

from .compiler import (
    DocumentAction,
    DocumentDecision,
    WikiCompilationError,
    WikiModel,
    compile_wiki_fast,
    decide_document,
)
from .models import DocumentSnapshot, DocumentVersion, SourceSpan, WikiBuild
from .repository import WikiRepository, WikiRepositoryError


@dataclass(frozen=True, kw_only=True)
class MaintenanceOutcome:
    """What one ingest run decided and produced."""

    action: DocumentAction
    reason: str
    build: WikiBuild | None = None
    superseded_document_ids: tuple[str, ...] = ()
    effective_documents: tuple[DocumentVersion, ...] = ()

    @property
    def created_build(self) -> bool:
        return self.build is not None

    @property
    def page_count(self) -> int:
        return 0 if self.build is None else len(self.build.pages)


class WikiMaintainer:
    """Turns saved document snapshots into draft Wiki builds.

    Compilation defaults to `compile_wiki_fast`: the model decides the structure
    - which topics exist and which spans belong together - and the program
    builds the pages from that plan. Claim text is its span's text verbatim,
    with `source`, `locator` and `source_span_ids` derived from the span; page
    titles are the model's topics, while summaries and aliases are generated
    from those topics and the spans' headings. Two model calls for a whole
    corpus, and no business fact the model could have reworded - though a topic
    it named or grouped poorly remains possible.

    `compile_wiki` can be injected instead for a model-written Wiki, at a call
    per batch of pages.
    """

    def __init__(
        self,
        repository: WikiRepository,
        model: WikiModel,
        *,
        compile_pages=compile_wiki_fast,
    ) -> None:
        self.repository = repository
        self.model = model
        self.compile_pages = compile_pages

    def ingest(
        self, snapshot: DocumentSnapshot, *, base_build_id: str | None = None
    ) -> MaintenanceOutcome:
        """Decide on `snapshot`, and on `update` compile a new draft build.

        `base_build_id` defaults to the published build, so an ingest normally
        extends what readers currently see. Passing one explicitly lets a caller
        branch from an older build without publishing anything.
        """
        # Fail here rather than after a compile: the build would be rejected for
        # unresolvable provenance anyway, and this way it costs no model calls.
        self._require_saved(snapshot)

        base_id = (
            base_build_id
            if base_build_id is not None
            else self.repository.get_current_build_id()
        )
        base_build = self.repository.load_build(base_id) if base_id is not None else None
        active = base_build.document_versions if base_build is not None else ()

        decision = decide_document(
            self.model,
            document_id=snapshot.document_id,
            filename=snapshot.filename,
            spans=snapshot.spans,
            active_document_ids=[entry.document_id for entry in active],
        )
        if decision.action is DocumentAction.IGNORE:
            return MaintenanceOutcome(
                action=decision.action,
                reason=decision.reason,
                effective_documents=active,
            )

        superseded = _superseded(decision, active, snapshot.document_id)
        effective = _effective_documents(active, snapshot, superseded)
        spans = self._load_spans(effective)

        existing_pages: Sequence[WikiPage] = (
            base_build.pages if base_build is not None else ()
        )
        pages = self.compile_pages(
            self.model, spans=spans, existing_pages=existing_pages
        )

        build = self.repository.create_build(
            pages,
            document_versions={entry.document_id: entry.version for entry in effective},
            base_build_id=base_id,
        )
        return MaintenanceOutcome(
            action=decision.action,
            reason=decision.reason,
            build=build,
            superseded_document_ids=superseded,
            effective_documents=effective,
        )

    def _require_saved(self, snapshot: DocumentSnapshot) -> None:
        stored = self.repository.load_document_snapshot(
            snapshot.document_id, snapshot.version
        )
        if stored.content_hash != snapshot.content_hash:
            raise WikiRepositoryError(
                f"the saved snapshot for {snapshot.document_id!r} at "
                f"{snapshot.version!r} holds different content than the one passed in"
            )

    def _load_spans(
        self, effective: Sequence[DocumentVersion]
    ) -> tuple[SourceSpan, ...]:
        """Every effective document's spans, in a deterministic order.

        Sorted by document then reading position, so the prompt the model sees
        does not depend on which document happened to arrive last.
        """
        spans: list[SourceSpan] = []
        for entry in sorted(effective, key=lambda item: item.document_id):
            snapshot = self.repository.load_document_snapshot(
                entry.document_id, entry.version
            )
            spans.extend(sorted(snapshot.spans, key=lambda span: span.ordinal))
        if not spans:
            raise WikiCompilationError(
                "the effective document set contains no source spans"
            )
        return tuple(spans)


def _superseded(
    decision: DocumentDecision,
    active: Sequence[DocumentVersion],
    incoming_document_id: str,
) -> tuple[str, ...]:
    """The documents this update retires, or an error naming why it cannot.

    Retiring a document is how knowledge leaves the Wiki, so a request to retire
    one is checked rather than tidied up. Quietly dropping an id we do not
    recognise would report a successful supersede that did not happen, and the
    document the model meant to retire would keep answering questions.
    """
    active_ids = {entry.document_id for entry in active}
    seen: set[str] = set()
    for document_id in decision.supersedes_document_ids:
        if document_id == incoming_document_id:
            raise WikiCompilationError(
                f"supersedes_document_ids names the incoming document "
                f"{document_id!r}; a new version replaces its own predecessor "
                "and must not be listed"
            )
        if document_id not in active_ids:
            raise WikiCompilationError(
                f"supersedes_document_ids names {document_id!r}, which is not an "
                f"active document ({', '.join(sorted(active_ids)) or 'none'})"
            )
        if document_id in seen:
            raise WikiCompilationError(
                f"supersedes_document_ids lists {document_id!r} more than once"
            )
        seen.add(document_id)
    return tuple(sorted(seen))


def _effective_documents(
    active: Sequence[DocumentVersion],
    snapshot: DocumentSnapshot,
    superseded: Sequence[str],
) -> tuple[DocumentVersion, ...]:
    dropped = set(superseded) | {snapshot.document_id}
    kept = [entry for entry in active if entry.document_id not in dropped]
    kept.append(
        DocumentVersion(document_id=snapshot.document_id, version=snapshot.version)
    )
    return tuple(sorted(kept, key=lambda entry: entry.document_id))
