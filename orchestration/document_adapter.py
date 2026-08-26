"""Adapt the existing document retrieval path to the Evidence contract.

This is a thin boundary: retrieval logic stays in `rag.py`. Nothing in the
production request path uses this adapter yet.
"""

from __future__ import annotations

from rag import Chunk, retrieve_fast

from .contracts import Evidence, SourceType, ToolResult, ToolStatus

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

    Retrieval failures propagate unchanged: an infrastructure error must not be
    reported as "no evidence found".
    """
    if not question or not question.strip():
        raise ValueError("question must not be blank")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    results = retrieve_fast(question, chunks, top_k=top_k, trace=trace)

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
