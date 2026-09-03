"""Wiki lifecycle: source spans, document snapshots, builds, publish and rollback.

Offline and independent of the online query path. `orchestration.wiki_query`
still reads `wiki_pages/sample_company_wiki.json`; switching it to read the
published build is a later milestone, so this package can be exercised end to
end without touching a live answer.

Re-exports only. This package performs no work at import time - no directory is
created and no file is read until a `WikiRepository` is constructed.

`ollama_compiler` is deliberately not re-exported: the compiler takes an injected
model, so nothing here binds the Wiki to one backend. Import
`wiki_maintenance.ollama_compiler.OllamaWikiModel` when you want that one.
"""

from __future__ import annotations

from .bootstrap import bootstrap_from_wiki_file
from .compiler import (
    PAGE_BATCH_SIZE,
    DocumentAction,
    DocumentDecision,
    ModelRequest,
    PagePlan,
    WikiCompilationError,
    WikiModel,
    assemble_page_from_spans,
    compile_wiki,
    compile_wiki_fast,
    decide_document,
    new_claim_id,
    new_page_id,
    next_page_version,
    plan_topics,
)
from .diff import WikiDiff, diff_builds
from .maintainer import MaintenanceOutcome, WikiMaintainer
from .models import (
    BuildRecord,
    BuildStatus,
    CurrentPointer,
    DocumentSnapshot,
    DocumentVersion,
    SourceSpan,
    WikiBuild,
    document_version_for,
    format_build_id,
    parse_build_number,
    utc_now,
)
from .repository import (
    DEFAULT_WIKI_DATA_ROOT,
    BuildNotFoundError,
    BuildNotPublishableError,
    WikiRepository,
    WikiRepositoryError,
)
from .source_spans import (
    SourceBlock,
    build_document_snapshot,
    build_document_snapshot_from_text,
    build_source_spans,
    build_source_spans_from_text,
    compute_content_hash,
    compute_document_content_hash,
    compute_span_id,
    derive_document_id,
    heading_of,
    normalize_span_text,
    source_blocks,
)

__all__ = [
    "DEFAULT_WIKI_DATA_ROOT",
    "PAGE_BATCH_SIZE",
    "BuildNotFoundError",
    "BuildNotPublishableError",
    "BuildRecord",
    "BuildStatus",
    "CurrentPointer",
    "DocumentAction",
    "DocumentDecision",
    "DocumentSnapshot",
    "DocumentVersion",
    "MaintenanceOutcome",
    "ModelRequest",
    "PagePlan",
    "SourceBlock",
    "SourceSpan",
    "WikiBuild",
    "WikiCompilationError",
    "WikiDiff",
    "WikiMaintainer",
    "WikiModel",
    "WikiRepository",
    "WikiRepositoryError",
    "bootstrap_from_wiki_file",
    "build_document_snapshot",
    "build_document_snapshot_from_text",
    "build_source_spans",
    "build_source_spans_from_text",
    "assemble_page_from_spans",
    "compile_wiki",
    "compile_wiki_fast",
    "compute_content_hash",
    "compute_document_content_hash",
    "compute_span_id",
    "decide_document",
    "derive_document_id",
    "diff_builds",
    "document_version_for",
    "format_build_id",
    "heading_of",
    "new_claim_id",
    "new_page_id",
    "next_page_version",
    "normalize_span_text",
    "parse_build_number",
    "plan_topics",
    "source_blocks",
    "utc_now",
]
