"""Load compiled Wiki pages and answer queries against them.

Tokenizer (applied identically to the query and to every searched field, so
punctuation behaves the same on both sides):

1. lowercase, collapse whitespace runs to one space;
2. every character that is neither ASCII alphanumeric nor a CJK ideograph
   (`U+4E00`-`U+9FFF`) is a separator;
3. each maximal `[a-z0-9]+` run is one token;
4. each maximal CJK run emits its adjacent character bigrams; a run of length 1
   emits that single character, so a one-character term is not dropped. Bigrams
   never span a separator, so `年假、调休` yields `年假` and `调休`, never `假调`;
5. tokens are deduplicated, preserving first-appearance order.

Scoring counts *distinct query terms present in a field*, so repetition never
accumulates. Aliases are one merged logical field.

    score = title_matches * 4 + alias_matches * 3
          + summary_matches * 2 + claim_matches * 1

That score is what `retrieval_score` reports and what eligibility uses
(`score > 0`).

Ordering is a total order - each matched term counted **once**, at its
highest-weighted field and scaled by how few pages contain it, then the score
above, then `page_id`, then `claim_id`. Ties are fully deterministic. Changing
the weights, the term scaling, the per-term merge or the tie-break is a visible
decision, not an accident.

Specificity leads because the page part of the score is added identically to
every claim on the page, and a handbook says `员工` on nearly every page and
`年假` on one. Counting both matches alike let a page *titled* with the common
word win: `概览一下正式员工的年假规定` returned the grievance claim first,
because `员工` in that page's title scored +4 against the leave claim that
actually named `年假`. Scaling by rarity fixes that while keeping the field
weights meaningful - a title match is still worth four claim matches *of the
same term*, so `员工报销的规定` still puts the page titled `费用报销` first.
Recall is unchanged; only the order of what was already retrieved moves.

Retrieval is offline and deterministic: no Ollama, no network, no RAG, no
database, no clock, no randomness.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Sequence

from .contracts import Evidence, SourceType, ToolResult, ToolStatus
from .wiki_schema import WikiClaim, WikiPage, require_locator, validate_collection

WIKI_TOOL_NAME = "wiki_query"
# Below the document adapter's 80: compiled knowledge never outranks the source
# document it was derived from.
WIKI_AUTHORITY = 60

TITLE_WEIGHT = 4
ALIAS_WEIGHT = 3
SUMMARY_WEIGHT = 2
CLAIM_WEIGHT = 1

SUPPORTED_SCHEMA_VERSION = "1.0"
DEFAULT_WIKI_PATH = (
    Path(__file__).resolve().parent.parent / "wiki_pages" / "sample_company_wiki.json"
)

_TOP_LEVEL_FIELDS = frozenset({"schema_version", "pages"})
_PAGE_FIELDS = frozenset({"page_id", "title", "summary", "aliases", "version", "claims"})
_CLAIM_FIELDS = frozenset(
    {"claim_id", "text", "source", "locator", "source_span_ids"}
)

# CJK Unified Ideographs U+4E00-U+9FFF. Built from ordinals so the range stays
# reviewable without depending on the editor's font.
CJK_FIRST = 0x4E00
CJK_LAST = 0x9FFF
_TOKEN_PATTERN = re.compile(
    "[a-z0-9]+|[{}-{}]+".format(chr(CJK_FIRST), chr(CJK_LAST))
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Tokenization and scoring
# --------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Deterministic tokens, deduplicated in first-appearance order."""
    normalized = _WHITESPACE_PATTERN.sub(" ", text).strip().lower()
    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        if token not in seen:
            seen.add(token)
            tokens.append(token)

    for match in _TOKEN_PATTERN.finditer(normalized):
        run = match.group()
        if run[0].isascii():
            add(run)
        elif len(run) == 1:
            add(run)
        else:
            for start in range(len(run) - 1):
                add(run[start : start + 2])
    return tokens


def _term_specificity(
    query_terms: Sequence[str], pages: Sequence[WikiPage]
) -> dict[str, float]:
    """How much a match on each query term is worth, by how rare the term is.

    A handbook mentions `员工` on nearly every page and `年假` on one. Counting
    both as one match makes the common word decide the ranking, which is exactly
    how a page titled `员工建议与申诉` came back ahead of the annual-leave fact.

    This is the usual inverse-document-frequency shape, computed over the pages
    in this very call - no corpus statistics are stored, and the result stays
    deterministic and offline. A term in every page still counts, just little.
    """
    total = len(pages) or 1
    specificity: dict[str, float] = {}
    for term in query_terms:
        if term in specificity:
            continue
        containing = sum(
            1
            for page in pages
            if term in tokenize(page.title)
            or term in _alias_tokens(page)
            or term in tokenize(page.summary)
            or any(term in tokenize(claim.text) for claim in page.claims)
        )
        specificity[term] = math.log(1 + total / (1 + containing))
    return specificity


def _weighted_match(
    query_terms: Sequence[str],
    field_tokens: frozenset[str],
    specificity: dict[str, float],
) -> float:
    """`_match_count`, but each distinct matched term counts by its specificity."""
    return sum(
        specificity.get(term, 0.0)
        for term in dict.fromkeys(query_terms)
        if term in field_tokens
    )


def _claim_rank(
    query_terms: Sequence[str],
    claim_tokens: frozenset[str],
    specificity: dict[str, float],
) -> float:
    """What *this* claim adds, always counted, never merged into the page's.

    Folding the claim into the page-level maximum destroyed the ranking *within*
    a page: `CLAIM_WEIGHT` is the smallest weight, so any term that also appeared
    in the title or summary contributed nothing from the claim, and every claim
    on the page scored the same - including claims matching no query term at all.
    Five of twenty questions came back with a tied first place, decided by the
    order the claims happened to be written in.

    `正式员工和实习生是否都享有带薪年假？` is the case that shows it: the page
    scores identically for every claim, so only `实习生不享有带薪年假` versus
    `正式员工…每年享有 5 天带薪年假` can separate them, and it can only do that
    if the claim is scored on its own terms.
    """
    return sum(
        specificity.get(term, 0.0) * CLAIM_WEIGHT
        for term in dict.fromkeys(query_terms)
        if term in claim_tokens
    )


def _merged_rank(
    query_terms: Sequence[str],
    fields: Sequence[tuple[frozenset[str], int]],
    specificity: dict[str, float],
) -> float:
    """Score each distinct term **once**, at its highest-weighted field.

    The published score adds the fields up, so one word appearing in the title,
    the summary and a claim is counted three times - at 4 + 2 + 1, seven times
    the weight of a claim match. That is a statement about how many places the
    word occurs, not about how well the page answers the question.

    The compiled Wiki makes it worse: its generated summaries restate the title
    (`本页涵盖：员工建议与申诉。`), so the title's contribution is doubled by a
    field carrying no new information. Ranking `老员工到岗三年以后，带薪假期额度
    有什么变化？`, the grievance page scored 5.68 on `员工` alone while the
    annual-leave page scored 5.61 on `员工` + `三年` + `带薪` - and lost by 0.07.
    The hand-written summaries in the static collection do not repeat the title,
    which is why the same ranking behaved differently on the two paths.

    Taking the best field per term keeps what the weights are for - a title
    match still outranks a claim match *for the same term* - while a word cannot
    accumulate weight merely by being repeated across fields.
    """
    best: dict[str, int] = {}
    for tokens, weight in fields:
        for term in dict.fromkeys(query_terms):
            if term in tokens and weight > best.get(term, 0):
                best[term] = weight
    return sum(specificity.get(term, 0.0) * weight for term, weight in best.items())


def _match_count(query_terms: Sequence[str], field_tokens: frozenset[str]) -> int:
    return sum(1 for term in query_terms if term in field_tokens)


def _alias_tokens(page: WikiPage) -> frozenset[str]:
    """Aliases score as one merged field: hitting three aliases still counts once."""
    merged: set[str] = set()
    for alias in page.aliases:
        merged.update(tokenize(alias))
    return frozenset(merged)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _require_object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object, got {type(value).__name__}")
    return value


def _reject_unknown_fields(raw: dict, allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown field(s): {', '.join(unknown)}")


def _require_text_field(raw: dict, name: str, path: str) -> str:
    if name not in raw:
        raise ValueError(f"{path}.{name} is required")
    value = raw[name]
    if not isinstance(value, str):
        raise ValueError(
            f"{path}.{name} must be a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise ValueError(f"{path}.{name} must not be empty")
    return value


def _claim_from_json(raw: object, path: str) -> WikiClaim:
    data = _require_object(raw, path)
    _reject_unknown_fields(data, _CLAIM_FIELDS, path)
    claim_id = _require_text_field(data, "claim_id", path)
    text = _require_text_field(data, "text", path)
    source = _require_text_field(data, "source", path)
    locator = _require_text_field(data, "locator", path)
    # Checked here so the shape failure carries the indexed path rather than the
    # dataclass's bare class path.
    require_locator(f"{path}.locator", locator)

    # Optional: a file written before span provenance existed simply omits it.
    raw_span_ids = data.get("source_span_ids", [])
    if not isinstance(raw_span_ids, list):
        raise ValueError(
            f"{path}.source_span_ids must be a list, got {type(raw_span_ids).__name__}"
        )
    for index, span_id in enumerate(raw_span_ids):
        span_path = f"{path}.source_span_ids[{index}]"
        if not isinstance(span_id, str):
            raise ValueError(
                f"{span_path} must be a string, got {type(span_id).__name__}"
            )
        if not span_id.strip():
            raise ValueError(f"{span_path} must not be empty")

    try:
        return WikiClaim(
            claim_id=claim_id,
            text=text,
            source=source,
            locator=locator,
            source_span_ids=tuple(raw_span_ids),
        )
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _page_from_json(raw: object, path: str) -> WikiPage:
    data = _require_object(raw, path)
    _reject_unknown_fields(data, _PAGE_FIELDS, path)

    if "aliases" not in data:
        raise ValueError(f"{path}.aliases is required")
    raw_aliases = data["aliases"]
    if not isinstance(raw_aliases, list):
        raise ValueError(
            f"{path}.aliases must be a list, got {type(raw_aliases).__name__}"
        )
    aliases = []
    for index, alias in enumerate(raw_aliases):
        alias_path = f"{path}.aliases[{index}]"
        if not isinstance(alias, str):
            raise ValueError(
                f"{alias_path} must be a string, got {type(alias).__name__}"
            )
        if not alias.strip():
            raise ValueError(f"{alias_path} must not be empty")
        aliases.append(alias)

    if "claims" not in data:
        raise ValueError(f"{path}.claims is required")
    raw_claims = data["claims"]
    if not isinstance(raw_claims, list):
        raise ValueError(
            f"{path}.claims must be a list, got {type(raw_claims).__name__}"
        )
    if not raw_claims:
        raise ValueError(f"{path}.claims must contain at least one claim")
    claims = tuple(
        _claim_from_json(claim, f"{path}.claims[{index}]")
        for index, claim in enumerate(raw_claims)
    )

    # The dataclass re-validates; surface the indexed path rather than the bare
    # class path so the caller can find the element in the file.
    try:
        return WikiPage(
            page_id=_require_text_field(data, "page_id", path),
            title=_require_text_field(data, "title", path),
            summary=_require_text_field(data, "summary", path),
            version=_require_text_field(data, "version", path),
            aliases=tuple(aliases),
            claims=claims,
        )
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def page_from_json(raw: object, path: str = "page") -> WikiPage:
    """Parse one page object, for callers holding pages outside a Wiki file.

    Additive: `load_wiki_pages` and `wiki_query` are unchanged. It exists so a
    stored Wiki build reads its pages through this parser rather than a second
    one, which is the only way the two cannot drift into different page formats.
    """
    return _page_from_json(raw, path)


def load_wiki_pages(path: str | Path = DEFAULT_WIKI_PATH) -> tuple[WikiPage, ...]:
    """Read and validate a Wiki collection.

    A missing file or unparseable JSON is an infrastructure error and is not
    downgraded to an empty Wiki.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    data = _require_object(raw, "<root>")
    _reject_unknown_fields(data, _TOP_LEVEL_FIELDS, "<root>")

    schema_version = _require_text_field(data, "schema_version", "<root>")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"<root>.schema_version {schema_version!r} is unsupported; "
            f"expected {SUPPORTED_SCHEMA_VERSION!r}"
        )

    if "pages" not in data:
        raise ValueError("<root>.pages is required")
    raw_pages = data["pages"]
    if not isinstance(raw_pages, list):
        raise ValueError(f"<root>.pages must be a list, got {type(raw_pages).__name__}")
    if not raw_pages:
        raise ValueError("<root>.pages must contain at least one page")

    pages = tuple(
        _page_from_json(page, f"pages[{index}]")
        for index, page in enumerate(raw_pages)
    )
    return validate_collection(pages)


# --------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------


def wiki_query(
    question: str,
    pages: Sequence[WikiPage],
    *,
    top_k: int = 3,
    trace: dict | None = None,
) -> ToolResult:
    """Return Wiki evidence for `question`, ranked deterministically.

    `top_k` bounds the number of Evidence items (claims), not the number of
    pages. The caller's `trace` is never mutated; the returned trace is a
    shallow copy plus this adapter's fields, so only top-level isolation is
    promised.
    """
    if not question or not question.strip():
        raise ValueError("question must not be blank")
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise ValueError("top_k must be an integer")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    validated_pages = validate_collection(pages)

    query_terms = tokenize(question)
    specificity = _term_specificity(query_terms, validated_pages)
    scored: list[tuple[float, float, int, str, str, WikiPage, WikiClaim]] = []
    for page in validated_pages:
        title_tokens = frozenset(tokenize(page.title))
        alias_tokens = _alias_tokens(page)
        summary_tokens = frozenset(tokenize(page.summary))
        title_score = _match_count(query_terms, title_tokens) * TITLE_WEIGHT
        alias_score = _match_count(query_terms, alias_tokens) * ALIAS_WEIGHT
        summary_score = _match_count(query_terms, summary_tokens) * SUMMARY_WEIGHT
        page_score = title_score + alias_score + summary_score
        page_fields = (
            (title_tokens, TITLE_WEIGHT),
            (alias_tokens, ALIAS_WEIGHT),
            (summary_tokens, SUMMARY_WEIGHT),
        )
        for claim in page.claims:
            claim_tokens = frozenset(tokenize(claim.text))
            claim_score = _match_count(query_terms, claim_tokens) * CLAIM_WEIGHT
            total = page_score + claim_score
            rank_total = _merged_rank(
                query_terms, page_fields + ((claim_tokens, CLAIM_WEIGHT),), specificity
            )
            # The merged rank decides *between pages*. Within one page it is
            # often identical for every claim - the page part is shared, and a
            # term already present in the title or summary adds nothing from the
            # claim - so the claim's own weighted match breaks that tie. It is a
            # tie-break, not a component: it cannot move a claim past one that
            # ranked higher, so the cross-page ordering is untouched.
            claim_rank = _claim_rank(query_terms, claim_tokens, specificity)
            if total > 0:
                scored.append(
                    (rank_total, claim_rank, total, page.page_id, claim.claim_id, page, claim)
                )

    # Total order: specificity-weighted relevance first, then the published
    # score, then page_id, then claim_id.
    #
    # The plain match count used to lead, and every match counted the same. But
    # `员工` occurs in most pages of a company handbook while `年假` occurs in
    # one, so treating them alike let a page *titled* with the common word beat
    # the claim that actually stated the fact: asking
    # `概览一下正式员工的年假规定` returned the grievance claim first, because
    # `员工` matched its title (+4) and outweighed the leave claim naming `年假`.
    #
    # Weighting each matched term by how few pages contain it fixes that without
    # discarding the field weights - a title match is still worth four times a
    # claim match *for the same term*, which is why `员工报销的规定` still puts
    # the page titled `费用报销` first rather than a claim that merely says
    # `员工`. Eligibility is untouched (`total > 0`), so recall is unchanged, and
    # `retrieval_score` still reports the documented weighted total.
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4]))
    eligible_count = len(scored)

    evidence = tuple(
        _to_evidence(page, claim, score, rank)
        for rank, (
            _merged, _claim_rank, score, _page_id, _claim_id, page, claim
        ) in enumerate(scored[:top_k], start=1)
    )

    trace_copy = dict(trace) if trace is not None else {}
    result_trace: dict[str, object] = {
        **trace_copy,
        "wiki_scanned_pages": len(validated_pages),
        "wiki_matched_claims": eligible_count,
        "wiki_returned_evidence": len(evidence),
        "wiki_top_k": top_k,
    }

    status = ToolStatus.OK if evidence else ToolStatus.EMPTY
    return ToolResult(
        tool_name=WIKI_TOOL_NAME,
        status=status,
        evidence=evidence,
        trace=result_trace,
    )


def _to_evidence(page: WikiPage, claim: WikiClaim, score: int, rank: int) -> Evidence:
    return Evidence(
        content=claim.text,
        source_type=SourceType.WIKI,
        # source/locator keep the source-document trail; the Wiki trail lives in
        # metadata, so a reader can always get back to the original section.
        source=claim.source,
        locator=claim.locator,
        version=page.version,
        observed_at=None,
        authority=WIKI_AUTHORITY,
        confidence=None,
        metadata={
            "rank": rank,
            # A term-overlap score is not a calibrated probability.
            "retrieval_score": score,
            "page_id": page.page_id,
            "page_title": page.title,
            "claim_id": claim.claim_id,
            "wiki_locator": f"page:{page.page_id}#claim:{claim.claim_id}",
        },
    )
