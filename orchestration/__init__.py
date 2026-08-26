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

__all__ = [
    "AUTHORITY_MAX",
    "AUTHORITY_MIN",
    "CONFIDENCE_MAX",
    "CONFIDENCE_MIN",
    "Evidence",
    "Plan",
    "RequestSignals",
    "Route",
    "SourceType",
    "ToolName",
    "ToolResult",
    "ToolStatus",
    "document_search",
    "plan_request",
]
