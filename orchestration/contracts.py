"""Domain-neutral evidence contracts shared by every knowledge-agent tool.

These types are the boundary between a tool's native result shape and the
orchestration layer. They carry no retrieval, wiki, or database logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceType(str, Enum):
    WIKI = "wiki"
    DOCUMENT = "document"
    SYSTEM = "system"


class ToolStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


AUTHORITY_MIN = 0
AUTHORITY_MAX = 100
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0


@dataclass(kw_only=True)
class Evidence:
    """One traceable claim produced by a tool.

    Keyword-only so that `authority` can stay required: a tool that forgets to
    declare its standing is an integration error and must fail loudly rather
    than silently produce valid lowest-authority evidence.

    `observed_at` is deliberately not auto-filled with the query time: that
    would describe when we looked, not how fresh the source is. Only a tool
    that actually knows the source's observation time may set it.

    `confidence` is a calibrated probability. Retrieval scores are ranking
    signals on an arbitrary scale and belong in `metadata` instead.
    """

    content: str
    source_type: SourceType
    source: str
    locator: str | None = None
    version: str | None = None
    observed_at: str | None = None
    authority: int
    confidence: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise ValueError("Evidence.content must not be empty")
        if not self.source or not self.source.strip():
            raise ValueError("Evidence.source must not be empty")
        if isinstance(self.authority, bool) or not isinstance(self.authority, int):
            raise ValueError("Evidence.authority must be an integer")
        if not AUTHORITY_MIN <= self.authority <= AUTHORITY_MAX:
            raise ValueError(
                f"Evidence.authority must be between {AUTHORITY_MIN} and {AUTHORITY_MAX}"
            )
        if self.confidence is not None and not (
            CONFIDENCE_MIN <= self.confidence <= CONFIDENCE_MAX
        ):
            raise ValueError(
                f"Evidence.confidence must be between {CONFIDENCE_MIN} and {CONFIDENCE_MAX}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "source_type": self.source_type.value,
            "source": self.source,
            "locator": self.locator,
            "version": self.version,
            "observed_at": self.observed_at,
            "authority": self.authority,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


@dataclass
class ToolResult:
    """The outcome of one tool call, including why it produced no evidence.

    The status invariants keep "found nothing" and "failed to run" distinct, so
    an infrastructure failure can never be presented as an absence of evidence.
    """

    tool_name: str
    status: ToolStatus
    evidence: tuple[Evidence, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    trace: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_name or not self.tool_name.strip():
            raise ValueError("ToolResult.tool_name must not be empty")
        self.evidence = tuple(self.evidence)
        has_error = bool(self.error_code) or bool(self.error_message)

        if self.status is ToolStatus.OK:
            if not self.evidence:
                raise ValueError("ToolResult 'ok' requires at least one evidence item")
            if has_error:
                raise ValueError("ToolResult 'ok' must not carry error fields")
        elif self.status is ToolStatus.EMPTY:
            if self.evidence:
                raise ValueError("ToolResult 'empty' must not carry evidence")
            if has_error:
                raise ValueError("ToolResult 'empty' must not carry error fields")
        else:
            if self.evidence:
                raise ValueError("ToolResult 'error' must not carry evidence")
            if not self.error_code or not self.error_code.strip():
                raise ValueError("ToolResult 'error' requires a non-empty error_code")
            if not self.error_message or not self.error_message.strip():
                raise ValueError("ToolResult 'error' requires a non-empty error_message")

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "trace": dict(self.trace),
        }
