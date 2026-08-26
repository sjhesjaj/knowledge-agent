"""Orchestration layer: evidence contracts and the tools that produce them.

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

__all__ = [
    "AUTHORITY_MAX",
    "AUTHORITY_MIN",
    "CONFIDENCE_MAX",
    "CONFIDENCE_MIN",
    "Evidence",
    "SourceType",
    "ToolResult",
    "ToolStatus",
    "document_search",
]
