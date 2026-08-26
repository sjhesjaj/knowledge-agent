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
from .planner import Plan, RequestSignals, Route, ToolName, plan_request
from .wiki_adapter import WIKI_AUTHORITY, load_wiki_pages, wiki_query
from .wiki_schema import WikiClaim, WikiPage, validate_collection

__all__ = [
    "AUTHORITY_MAX",
    "AUTHORITY_MIN",
    "CONFIDENCE_MAX",
    "CONFIDENCE_MIN",
    "WIKI_AUTHORITY",
    "Evidence",
    "Plan",
    "RequestSignals",
    "Route",
    "SourceType",
    "ToolName",
    "ToolResult",
    "ToolStatus",
    "WikiClaim",
    "WikiPage",
    "document_search",
    "load_wiki_pages",
    "plan_request",
    "validate_collection",
    "wiki_query",
]
