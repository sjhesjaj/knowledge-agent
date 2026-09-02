"""Compile Source Spans into Wiki pages with a language model.

The model decides *what* the Wiki should say; this module decides what is
allowed to become a page, and it does not take the model's word for anything it
can check itself:

- **Identifiers are the program's.** The model may ask to reuse an existing
  `page_id` or `claim_id`, and that is all. New ids are digests of the content
  they name, so two runs over the same input address the same page instead of
  producing a Wiki that looks entirely rewritten every time.
- **Citations must resolve.** Every `source_span_id` a claim carries has to be
  one of the spans the model was shown. An invented id is an untraceable claim
  wearing a citation, which is worse than no citation at all.
- **`source` and `locator` come from the span, never from the model.** They are
  the trail back to the original wording; a model-reported locator would be a
  claim about provenance rather than the provenance itself.
- **Arabic numerals must appear in the cited spans.** A model that writes "8 天"
  where the source says "5 天" has invented a policy. Chinese numerals are
  copied rather than computed and are not checked here.

The model is injected. Nothing in this module imports `requests` or knows that
Ollama exists - `ollama_compiler.py` supplies that, and the tests supply a
scripted stand-in.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar

from orchestration.wiki_schema import (
    SPAN_LOCATOR_PREFIX,
    WikiClaim,
    WikiPage,
    validate_collection,
)

from . import prompts
from .models import SourceSpan

PAGE_ID_PREFIX = "wiki-"
PAGE_ID_HEX_LENGTH = 12
CLAIM_ID_HEX_LENGTH = 8
INITIAL_PAGE_VERSION = "1.0"

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)$")
_ARABIC_NUMBER_PATTERN = re.compile(r"\d+")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_CODE_FENCE_PATTERN = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

T = TypeVar("T")


class WikiCompilationError(RuntimeError):
    """The model's output could not be turned into a valid Wiki."""


# --------------------------------------------------------------------------
# Model interface
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ModelRequest:
    """One call to the model. `stage` names it for tests, traces and errors."""

    stage: str
    system: str
    user: str


class WikiModel(Protocol):
    """Anything that can answer a `ModelRequest` with text."""

    def generate(self, request: ModelRequest) -> str: ...


class DocumentAction(str, Enum):
    IGNORE = "ignore"
    UPDATE = "update"


@dataclass(frozen=True, kw_only=True)
class DocumentDecision:
    action: DocumentAction
    reason: str
    supersedes_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PagePlan:
    """One planned page: a topic, the spans behind it, and an id to reuse."""

    topic: str
    source_span_ids: tuple[str, ...]
    existing_page_id: str | None = None


# --------------------------------------------------------------------------
# JSON handling
# --------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        return _CODE_FENCE_PATTERN.sub("", stripped).strip()
    return stripped


def _parse_object(raw: str) -> dict:
    text = _strip_code_fence(raw)
    if not text:
        raise ValueError("the model returned an empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def request_json(
    model: WikiModel, request: ModelRequest, interpret: Callable[[dict], T]
) -> T:
    """Ask the model, then read its answer - with exactly one repair attempt.

    A second failure is reported rather than retried: a model that cannot
    produce the shape twice will not produce it on the third try either, and
    looping here would turn one bad compile into an unbounded bill.
    """
    raw = model.generate(request)
    try:
        return interpret(_parse_object(raw))
    except ValueError as first_error:
        repaired = model.generate(
            ModelRequest(
                stage=f"{request.stage}_repair",
                system=prompts.REPAIR_SYSTEM,
                user=prompts.repair_prompt(
                    original=request.user,
                    invalid_output=raw,
                    error=str(first_error),
                ),
            )
        )
        try:
            return interpret(_parse_object(repaired))
        except ValueError as second_error:
            raise WikiCompilationError(
                f"{request.stage}: unusable model output after one repair "
                f"attempt ({first_error}; then {second_error})"
            ) from second_error


def _require_str(payload: dict, key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _optional_str(payload: dict, key: str, path: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} must be a non-empty string or null")
    return value.strip()


def _require_str_list(payload: dict, key: str, path: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{path}.{key} must be a list")
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}.{key}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


# --------------------------------------------------------------------------
# Deterministic identifiers
# --------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip().lower()


def new_page_id(title: str) -> str:
    """A page's id follows its topic, so recompiling the same topic re-addresses
    the same page instead of orphaning it."""
    digest = hashlib.sha256(_normalize(title).encode("utf-8")).hexdigest()
    return f"{PAGE_ID_PREFIX}{digest[:PAGE_ID_HEX_LENGTH]}"


def new_claim_id(page_id: str, text: str, *, occurrence: int = 0) -> str:
    digest = hashlib.sha256(
        f"{page_id}\x00{_normalize(text)}\x00{occurrence}".encode("utf-8")
    ).hexdigest()
    return f"{page_id}-c{digest[:CLAIM_ID_HEX_LENGTH]}"


def next_page_version(previous: str | None) -> str:
    """Bump the minor version. A page whose content did not change keeps the
    version it had, so a version change always means the page changed."""
    if previous is None:
        return INITIAL_PAGE_VERSION
    match = _VERSION_PATTERN.match(previous)
    if match is None:
        return INITIAL_PAGE_VERSION
    return f"{match.group(1)}.{int(match.group(2)) + 1}"


def _page_fingerprint(
    title: str, summary: str, aliases: Sequence[str], claims: Sequence[WikiClaim]
) -> str:
    """Everything about a page except its version, so the two can be compared."""
    payload = json.dumps(
        {
            "title": title,
            "summary": summary,
            "aliases": list(aliases),
            "claims": [claim.to_dict() for claim in claims],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_fingerprint(page: WikiPage) -> str:
    return _page_fingerprint(page.title, page.summary, page.aliases, page.claims)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _resolve_spans(
    span_ids: Sequence[str], index: dict[str, SourceSpan], path: str
) -> tuple[SourceSpan, ...]:
    if not span_ids:
        raise WikiCompilationError(f"{path} must cite at least one source span")
    resolved = []
    for span_id in span_ids:
        span = index.get(span_id)
        if span is None:
            raise WikiCompilationError(
                f"{path} cites source span {span_id!r}, which is not available here"
            )
        resolved.append(span)
    return tuple(resolved)


def _require_numbers_are_sourced(
    text: str, spans: Sequence[SourceSpan], path: str
) -> None:
    """Every Arabic numeral in the text must appear in the cited spans.

    Whole runs are compared, not substrings: a claim saying "5" when the source
    only says "15" is an invented figure, and a substring test would pass it.
    """
    available = set()
    for span in spans:
        available.update(_ARABIC_NUMBER_PATTERN.findall(span.text))
    invented = sorted(set(_ARABIC_NUMBER_PATTERN.findall(text)) - available)
    if invented:
        raise WikiCompilationError(
            f"{path} contains number(s) {', '.join(invented)} that appear in no "
            f"cited source span"
        )


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------


def decide_document(
    model: WikiModel,
    *,
    document_id: str,
    filename: str,
    spans: Sequence[SourceSpan],
    active_document_ids: Sequence[str],
) -> DocumentDecision:
    """Stage 1: is this document worth compiling, and does it replace others?"""

    def interpret(payload: dict) -> DocumentDecision:
        action_value = _require_str(payload, "action", "decision")
        try:
            action = DocumentAction(action_value)
        except ValueError as exc:
            raise ValueError(
                f"decision.action {action_value!r} must be 'ignore' or 'update'"
            ) from exc
        return DocumentDecision(
            action=action,
            reason=_require_str(payload, "reason", "decision"),
            supersedes_document_ids=_require_str_list(
                payload, "supersedes_document_ids", "decision"
            ),
        )

    return request_json(
        model,
        ModelRequest(
            stage="document_decision",
            system=prompts.DOCUMENT_DECISION_SYSTEM,
            user=prompts.document_decision_prompt(
                filename=filename,
                document_id=document_id,
                spans=spans,
                active_document_ids=active_document_ids,
            ),
        ),
        interpret,
    )


def plan_topics(
    model: WikiModel,
    *,
    spans: Sequence[SourceSpan],
    existing_pages: Sequence[WikiPage],
) -> tuple[PagePlan, ...]:
    """Stage 2: which pages should exist, and which spans belong to each."""

    def interpret(payload: dict) -> tuple[PagePlan, ...]:
        raw_pages = payload.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            raise ValueError("plan.pages must be a non-empty list")
        plans = []
        for index, raw in enumerate(raw_pages):
            path = f"plan.pages[{index}]"
            if not isinstance(raw, dict):
                raise ValueError(f"{path} must be an object")
            plans.append(
                PagePlan(
                    topic=_require_str(raw, "topic", path),
                    source_span_ids=_require_str_list(raw, "source_span_ids", path),
                    existing_page_id=_optional_str(raw, "existing_page_id", path),
                )
            )
        return tuple(plans)

    return request_json(
        model,
        ModelRequest(
            stage="topic_plan",
            system=prompts.TOPIC_PLAN_SYSTEM,
            user=prompts.topic_plan_prompt(spans=spans, existing_pages=existing_pages),
        ),
        interpret,
    )


@dataclass(frozen=True, kw_only=True)
class _DraftClaim:
    text: str
    source_span_ids: tuple[str, ...]
    existing_claim_id: str | None


def compile_page(
    model: WikiModel,
    plan: PagePlan,
    *,
    span_index: dict[str, SourceSpan],
    existing_page: WikiPage | None,
) -> WikiPage:
    """Stage 3: write one page, then check what the model claimed about it."""
    page_spans = _resolve_spans(
        plan.source_span_ids, span_index, f"plan for topic {plan.topic!r}"
    )
    # Claims are resolved against this page's planned spans, not the whole
    # document set: a claim citing a span the page was never given is reading
    # from material the compiler did not put in front of it.
    page_span_index = {span.span_id: span for span in page_spans}

    def interpret(payload: dict) -> tuple[str, str, tuple[str, ...], tuple[_DraftClaim, ...]]:
        path = f"page[{plan.topic}]"
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            raise ValueError(f"{path}.claims must be a non-empty list")
        drafts = []
        for index, raw in enumerate(raw_claims):
            claim_path = f"{path}.claims[{index}]"
            if not isinstance(raw, dict):
                raise ValueError(f"{claim_path} must be an object")
            drafts.append(
                _DraftClaim(
                    text=_require_str(raw, "text", claim_path),
                    source_span_ids=_require_str_list(
                        raw, "source_span_ids", claim_path
                    ),
                    existing_claim_id=_optional_str(
                        raw, "existing_claim_id", claim_path
                    ),
                )
            )
        return (
            _require_str(payload, "title", path),
            _require_str(payload, "summary", path),
            _require_str_list(payload, "aliases", path),
            tuple(drafts),
        )

    title, summary, aliases, drafts = request_json(
        model,
        ModelRequest(
            stage=f"page_compilation:{plan.topic}",
            system=prompts.PAGE_COMPILATION_SYSTEM,
            user=prompts.page_compilation_prompt(
                topic=plan.topic, spans=page_spans, existing_page=existing_page
            ),
        ),
        interpret,
    )

    page_id = (
        existing_page.page_id if existing_page is not None else new_page_id(title)
    )
    path = f"page[{plan.topic}]"
    _require_numbers_are_sourced(summary, page_spans, f"{path}.summary")

    reusable_claim_ids = (
        {claim.claim_id for claim in existing_page.claims}
        if existing_page is not None
        else set()
    )
    claims: list[WikiClaim] = []
    used_ids: set[str] = set()
    for index, draft in enumerate(drafts):
        claim_path = f"{path}.claims[{index}]"
        cited = _resolve_spans(draft.source_span_ids, page_span_index, claim_path)
        _require_numbers_are_sourced(draft.text, cited, claim_path)

        claim_id = _claim_id_for(draft, page_id, reusable_claim_ids, used_ids)
        used_ids.add(claim_id)
        claims.append(
            WikiClaim(
                claim_id=claim_id,
                text=draft.text,
                # Provenance is read off the span, never off the model.
                source=cited[0].source,
                locator=locator_for(cited[0]),
                source_span_ids=tuple(dict.fromkeys(draft.source_span_ids)),
            )
        )

    fingerprint = _page_fingerprint(title, summary, aliases, claims)
    if existing_page is not None and _existing_fingerprint(existing_page) == fingerprint:
        version = existing_page.version
    else:
        version = next_page_version(
            existing_page.version if existing_page is not None else None
        )

    return WikiPage(
        page_id=page_id,
        title=title,
        summary=summary,
        version=version,
        aliases=aliases,
        claims=tuple(claims),
    )


def locator_for(span: SourceSpan) -> str:
    """The place a reader goes to check this claim.

    A heading is the better address because a person can find it by eye, so it
    wins when the document has one. A plain TXT or an unbroken PDF extract has
    no headings at all; there the span is the finest address the document
    actually has, and naming it keeps the claim checkable instead of leaving the
    document uncompilable or inventing a section that does not exist.
    """
    if span.locator is not None:
        return span.locator
    return f"{SPAN_LOCATOR_PREFIX}{span.span_id}"


def _claim_id_for(
    draft: _DraftClaim,
    page_id: str,
    reusable: set[str],
    used: set[str],
) -> str:
    """Reuse the id the model asked for when it is real and still free."""
    requested = draft.existing_claim_id
    if requested is not None and requested in reusable and requested not in used:
        return requested
    occurrence = 0
    candidate = new_claim_id(page_id, draft.text, occurrence=occurrence)
    while candidate in used:
        occurrence += 1
        candidate = new_claim_id(page_id, draft.text, occurrence=occurrence)
    return candidate


def _require_plan_partitions_spans(
    plans: Sequence[PagePlan], span_index: dict[str, SourceSpan]
) -> None:
    """The plan must assign every span to exactly one page.

    A span used twice states the same rule on two pages, which is how a Wiki
    starts contradicting itself after one of them is later edited. A span left
    out is source material the Wiki silently does not cover, and silence is the
    one failure a reader cannot detect.
    """
    seen: dict[str, str] = {}
    for plan in plans:
        for span_id in plan.source_span_ids:
            if span_id not in span_index:
                raise WikiCompilationError(
                    f"plan for topic {plan.topic!r} cites unknown source span "
                    f"{span_id!r}"
                )
            if span_id in seen:
                raise WikiCompilationError(
                    f"plan assigns source span {span_id!r} to both "
                    f"{seen[span_id]!r} and {plan.topic!r}; each span belongs to "
                    "exactly one page"
                )
            seen[span_id] = plan.topic

    missing = sorted(set(span_index) - set(seen))
    if missing:
        raise WikiCompilationError(
            f"plan leaves {len(missing)} source span(s) unassigned, starting with "
            f"{missing[0]!r}; every span must belong to a page"
        )


def _existing_page_for(
    plan: PagePlan, pages_by_id: dict[str, WikiPage]
) -> WikiPage | None:
    """Resolve a requested page reuse, refusing one that names nothing.

    Treating an unknown `existing_page_id` as "then make a new page" would turn
    a model mistake into a silently orphaned page: the old one keeps its id and
    disappears from the build, and nothing in the output says so.
    """
    if plan.existing_page_id is None:
        return None
    existing = pages_by_id.get(plan.existing_page_id)
    if existing is None:
        raise WikiCompilationError(
            f"plan for topic {plan.topic!r} asks to reuse page_id "
            f"{plan.existing_page_id!r}, which is not an existing page"
        )
    return existing


def compile_wiki(
    model: WikiModel,
    *,
    spans: Sequence[SourceSpan],
    existing_pages: Sequence[WikiPage] = (),
) -> tuple[WikiPage, ...]:
    """Plan topics, compile every page, and return a valid Wiki collection.

    Existing pages are consulted only for id reuse. Their text is never carried
    into the result: the spans are the facts, and a page that survived only
    because nobody re-derived it would be a stale claim with a fresh timestamp.
    """
    if not spans:
        raise WikiCompilationError("cannot compile a Wiki from zero source spans")
    span_index = {span.span_id: span for span in spans}
    pages_by_id = {page.page_id: page for page in existing_pages}

    plans = plan_topics(model, spans=spans, existing_pages=existing_pages)
    _require_plan_partitions_spans(plans, span_index)

    pages: list[WikiPage] = []
    claimed_page_ids: set[str] = set()
    for plan in plans:
        existing = _existing_page_for(plan, pages_by_id)
        if existing is not None and existing.page_id in claimed_page_ids:
            raise WikiCompilationError(
                f"plan reuses page_id {existing.page_id!r} for more than one topic"
            )
        page = compile_page(
            model, plan, span_index=span_index, existing_page=existing
        )
        claimed_page_ids.add(page.page_id)
        pages.append(page)

    try:
        return validate_collection(pages, path="compiled pages")
    except ValueError as exc:
        raise WikiCompilationError(f"compiled Wiki is not a valid collection: {exc}") from exc
