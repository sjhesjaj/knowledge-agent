"""Orchestration layer: evidence contracts, planning, and the tools that produce evidence.

Re-exports only. This package performs no work at import time.
"""

from __future__ import annotations

from .contracts import (
    AUTHORITY_MAX,
    AUTHORITY_MIN,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    Evidence,
    SourceType,
    ToolResult,
    ToolStatus,
)
from .document_adapter import document_search
from .evidence_policy import (
    FactAssertion,
    FactResolution,
    FactScope,
    PolicyDecision,
    PolicyOutcome,
    ToolFailure,
    evaluate_evidence,
)
from .planner import Plan, RequestSignals, Route, ToolName, plan_request
from .system_provider import SYSTEM_AUTHORITY, SystemOperation, system_query
from .wiki_adapter import WIKI_AUTHORITY, load_wiki_pages, wiki_query
from .wiki_schema import WikiClaim, WikiPage, validate_collection

__all__ = [
    "AUTHORITY_MAX",
    "AUTHORITY_MIN",
    "CONFIDENCE_MAX",
    "CONFIDENCE_MIN",
    "SYSTEM_AUTHORITY",
    "WIKI_AUTHORITY",
    "Evidence",
    "FactAssertion",
    "FactResolution",
    "FactScope",
    "Plan",
    "PolicyDecision",
    "PolicyOutcome",
    "RequestSignals",
    "Route",
    "SourceType",
    "SystemOperation",
    "ToolFailure",
    "ToolName",
    "ToolResult",
    "ToolStatus",
    "WikiClaim",
    "WikiPage",
    "document_search",
    "evaluate_evidence",
    "load_wiki_pages",
    "plan_request",
    "system_query",
    "validate_collection",
    "wiki_query",
]
