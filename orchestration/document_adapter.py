"""Adapt the existing document retrieval path to the Evidence contract.

This is a thin boundary: retrieval logic stays in `rag.py`. The one judgement
made here is *what to search for* - a compound request carries clauses the
source documents cannot answer, and `planner.document_focus` removes them
before retrieval so they cannot consume an evidence slot. Ranking, recall and
reranking are still entirely `rag`'s.
"""

from __future__ import annotations

from rag import Chunk, retrieve_fast

from .contracts import Evidence, SourceType, ToolResult, ToolStatus
from .planner import document_focus

TOOL_NAME = "document_search"
# Source documents are authoritative for exact clauses and numbers, but they
# describe policy rather than current operational state.
DOCUMENT_AUTHORITY = 80


def document_search(
    question: str,
    chunks: list[Chunk],
    *,
    top_k: int = 4,
    trace: dict | None = None,
) -> ToolResult:
    """Retrieve source-document evidence for `question`.

    The question is narrowed to the clauses a document can answer (see
    `planner.document_focus`); a request with nothing to drop is passed through
    unchanged.

    `top_k` is forwarded to `rag.retrieve_fast` and is a ceiling on the result:
    every retrieval path returns at most `top_k` pieces of evidence. It is a
    ceiling, not a quota - a multi-clause question can come back with fewer.

    A compound question is split into clauses and merged into at most
    `rag.MAX_SUB_QUESTIONS` (three) sub-queries. That bounds the number of
    retrieval rounds; it is a separate limit from `top_k`, which bounds the
    evidence. `年假多少天？谁审批？多久失效？怎么申请？还能顺延吗？` runs three
    sub-queries, and returns at most three pieces of evidence for `top_k=3` and
    at most four for `top_k=4`.

    Where a path holds more candidates than `top_k`, they are in clause order,
    not relevance order; the cut drops the least relevant clause's evidence, not
    the last clause's (`rag.fit_to_budget`). After a merge, a free slot goes to
    a clause the merge left without evidence - and only when BM25 is confident
    which section that clause means (`rag.cover_merged_clauses`).

    `tests/test_evidence_budget.py` checks each of these on both paths that
    used to overflow, `bm25_multi_fast` and the full chain.

    Retrieval failures propagate unchanged: an infrastructure error must not be
    reported as "no evidence found".
    """
    if not question or not question.strip():
        raise ValueError("question must not be blank")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    focused = document_focus(question)
    results = retrieve_fast(focused, chunks, top_k=top_k, trace=trace)
    if trace is not None and focused != question:
        trace["document_focus_applied"] = True
        trace["document_focus_query"] = focused

    evidence = tuple(
        _to_evidence(chunk, score, rank)
        for rank, (chunk, score) in enumerate(results, start=1)
    )
    # Copied after retrieval so the caller's later mutations cannot rewrite the
    # trace this result was built from.
    trace_snapshot = dict(trace) if trace is not None else {}
    status = ToolStatus.OK if evidence else ToolStatus.EMPTY
    return ToolResult(
        tool_name=TOOL_NAME,
        status=status,
        evidence=evidence,
        trace=trace_snapshot,
    )


def _to_evidence(chunk: Chunk, score: float, rank: int) -> Evidence:
    return Evidence(
        content=chunk.text,
        source_type=SourceType.DOCUMENT,
        source=chunk.source,
        locator=f"chunk:{chunk.index}",
        version=None,
        observed_at=None,
        authority=DOCUMENT_AUTHORITY,
        confidence=None,
        metadata={
            "rank": rank,
            "chunk_index": chunk.index,
            # A BM25/vector/RRF score is a ranking signal, not a probability.
            "retrieval_score": score,
        },
    )
