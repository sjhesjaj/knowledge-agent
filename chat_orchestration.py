"""Glue between the HTTP layer and the orchestration sidecar.

`api.py` gains only wiring; the decisions live here. The orchestration package
stays a pure sidecar and is imported, never modified.

Trust boundary: `orchestration/` and `rag.py` are trusted internal code and are
used through their published contracts. Strict validation here is for what
crosses in from outside - the user's question and the SKU it may contain - and
for what goes back out to the client.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

from rag import Chunk

from orchestration import (
    ExecutionContext,
    PolicyOutcome,
    SourceType,
    SystemOperation,
    SystemRequest,
    ToolName,
    execute_plan,
    load_wiki_pages,
    plan_request,
)
from orchestration.evidence_policy import (
    REASON_EMPTY_TOOL_RESULT,
    REASON_EXACT_CITATION_MISSING_DOCUMENT,
    REASON_EXACT_CITATION_MISSING_LOCATOR,
    REASON_MISSING_TOOL_RESULT,
    REASON_TOOL_ERROR,
)
from orchestration.system_provider import SAMPLE_FIXTURE_PATH

MODE_LEGACY = "legacy"
MODE_ORCHESTRATED = "orchestrated"

# Fixed answers. None of these is produced by a model: when evidence is missing
# the product says so rather than guessing around the gap.
MESSAGE_DIRECT = "你好！有什么企业知识库问题可以帮你？"
MESSAGE_NO_SKU = "请提供需要查询的 SKU。"
# Saying "give me a SKU" for an order question would imply orders are queryable.
MESSAGE_SYSTEM_LIMITED = "当前版本仅支持库存查询。"
MESSAGE_NO_DOCUMENTS = "当前没有可用的文档知识库。"
MESSAGE_NO_WIKI = "当前没有可用的 Wiki 页面。"
MESSAGE_TOOL_UNAVAILABLE = "部分信息源暂时不可用，无法给出完整回答。"
MESSAGE_NO_CITATION = "缺少可引用的原文依据，无法回答。"
MESSAGE_NO_ANSWER = "根据现有资料无法确定。"

STREAM_ERROR_EVENT = {
    "code": "ORCHESTRATED_STREAM_ERROR",
    "message": "回答生成失败，请稍后重试。",
}

# `\b` is useless here: in "查询SKU-A100" both neighbours are word characters
# under Unicode, so no boundary exists. Look for a non-ASCII-alphanumeric edge
# instead, which also rejects "xsku-a100" and "sku-a1000".
SKU_PATTERN = re.compile(r"(?<![a-z0-9])sku[-_ ]?([a-z]\d{3})(?![a-z0-9])", re.IGNORECASE)


def _load_wiki_pages_safely() -> tuple:
    """A missing or invalid Wiki file must not stop the service from starting."""
    try:
        return load_wiki_pages()
    except (OSError, ValueError):
        return ()


# Loaded once at import, mirroring how api.py loads chunks.
WIKI_PAGES = _load_wiki_pages_safely()


def extract_sku(question: str) -> str | None:
    """Deterministic, offline. Never guesses and never falls back to a default."""
    found = {"sku-" + match.group(1).lower() for match in SKU_PATTERN.finditer(question)}
    if len(found) != 1:
        return None
    return found.pop()


@contextmanager
def demo_system_connection():
    """A fresh in-memory demo database per request.

    Thread-safe under FastAPI's threadpool, and structurally incapable of
    writing anything persistent. The real product database is never touched.
    """
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SAMPLE_FIXTURE_PATH.read_text(encoding="utf-8"))
        yield connection
    finally:
        connection.close()


def _heading_of(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


def serialize_evidence(evidence, rank: int) -> dict:
    """Legacy source shape plus provenance; inapplicable fields are null."""
    metadata = evidence.metadata or {}
    is_document = evidence.source_type is SourceType.DOCUMENT
    return {
        "rank": rank,
        "type": evidence.source_type.value,
        "source": evidence.source,
        "heading": _heading_of(evidence.content) if is_document else None,
        "chunk_index": metadata.get("chunk_index") if is_document else None,
        "locator": evidence.locator,
        "score": metadata.get("retrieval_score") if is_document else None,
        "content": evidence.content,
    }


def evidence_as_results(evidence) -> list[tuple[Chunk, float]]:
    """Convert Evidence into the shape the existing answer chain expects.

    These synthetic Chunks build one prompt and are never indexed, persisted, or
    written to storage. `index` is the citation number, so a `[来源 N]` the model
    emits maps to the same source the API returns.
    """
    return [
        (Chunk(text=item.content, source=item.source, index=rank), 1.0)
        for rank, item in enumerate(evidence, start=1)
    ]


def _refusal_message(reason_codes: tuple[str, ...]) -> str:
    codes = set(reason_codes)
    if codes & {REASON_MISSING_TOOL_RESULT, REASON_TOOL_ERROR}:
        return MESSAGE_TOOL_UNAVAILABLE
    if REASON_EMPTY_TOOL_RESULT in codes:
        return MESSAGE_NO_ANSWER
    if codes & {
        REASON_EXACT_CITATION_MISSING_DOCUMENT,
        REASON_EXACT_CITATION_MISSING_LOCATOR,
    }:
        return MESSAGE_NO_CITATION
    return MESSAGE_NO_ANSWER


@dataclass(frozen=True)
class Prepared:
    """Everything the endpoints need, with generation still to do."""

    route: str
    steps: list[str]
    outcome: str
    reason_codes: list[str]
    sources: list[dict]
    results_for_answer: list[tuple[Chunk, float]]
    executor_seconds: float
    fixed_answer: str | None = None

    @property
    def needs_generation(self) -> bool:
        return self.fixed_answer is None


def _is_inventory_question(question: str) -> bool:
    """Cheap and deliberate: no general intent classifier in V1."""
    lowered = question.lower()
    return "库存" in question or "sku" in lowered


def _unavailable(question, plan, chunks, sku) -> str | None:
    """A planned channel with no dependency is a fixed answer, never a 500."""
    if ToolName.DOCUMENT_SEARCH in plan.steps and not chunks:
        return MESSAGE_NO_DOCUMENTS
    if ToolName.WIKI_QUERY in plan.steps and not WIKI_PAGES:
        return MESSAGE_NO_WIKI
    if ToolName.SYSTEM_QUERY in plan.steps and sku is None:
        # Only inventory is open. Asking an order question for a SKU would
        # promise a capability this version does not have.
        if _is_inventory_question(question):
            return MESSAGE_NO_SKU
        return MESSAGE_SYSTEM_LIMITED
    return None


def _prepared_without_execution(plan, message: str) -> Prepared:
    return Prepared(
        route=plan.route.value,
        steps=[step.value for step in plan.steps],
        outcome=PolicyOutcome.REFUSE.value,
        reason_codes=[],
        sources=[],
        results_for_answer=[],
        executor_seconds=0.0,
        fixed_answer=message,
    )


def prepare(question: str, chunks: list[Chunk]) -> Prepared:
    """Plan, check availability, execute, and judge - without answering."""
    plan = plan_request(question)
    sku = extract_sku(question)

    if not plan.steps:
        # A greeting needs no evidence, so it must not reach the answer model.
        return Prepared(
            route=plan.route.value,
            steps=[],
            outcome=PolicyOutcome.DIRECT.value,
            reason_codes=[],
            sources=[],
            results_for_answer=[],
            executor_seconds=0.0,
            fixed_answer=MESSAGE_DIRECT,
        )

    message = _unavailable(question, plan, chunks, sku)
    if message is not None:
        return _prepared_without_execution(plan, message)

    started = perf_counter()
    if ToolName.SYSTEM_QUERY in plan.steps:
        with demo_system_connection() as connection:
            bundle = _execute(question, plan, chunks, connection, sku)
    else:
        bundle = _execute(question, plan, chunks, None, None)
    executor_seconds = perf_counter() - started

    decision = bundle.decision
    sources = [
        serialize_evidence(item, rank)
        for rank, item in enumerate(decision.usable_evidence, start=1)
    ]
    fixed_answer = (
        _refusal_message(decision.reason_codes)
        if decision.outcome is PolicyOutcome.REFUSE
        else None
    )
    return Prepared(
        route=plan.route.value,
        steps=[step.value for step in plan.steps],
        outcome=decision.outcome.value,
        reason_codes=list(decision.reason_codes),
        sources=sources,
        results_for_answer=evidence_as_results(decision.usable_evidence),
        executor_seconds=executor_seconds,
        fixed_answer=fixed_answer,
    )


def _execute(question, plan, chunks, connection, sku):
    request = (
        SystemRequest(
            operation=SystemOperation.GET_INVENTORY_LEVEL, parameters={"sku": sku}
        )
        if sku is not None
        else None
    )
    context = ExecutionContext(
        chunks=tuple(chunks),
        wiki_pages=WIKI_PAGES,
        system_connection=connection,
        # No trusted identity resolver exists, so subject-scoped operations are
        # not offered at all. Inventory is not subject-scoped.
        subject_id=None,
        system_request=request,
    )
    return execute_plan(question, plan, context)


def build_trace(prepared: Prepared) -> dict:
    """A trimmed trace: no per-tool traces, no per-step timings."""
    return {
        "mode": MODE_ORCHESTRATED,
        "route": prepared.route,
        "steps": list(prepared.steps),
        "outcome": prepared.outcome,
        "reason_codes": list(prepared.reason_codes),
        "executor_total_seconds": prepared.executor_seconds,
    }
