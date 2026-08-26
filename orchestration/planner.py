"""Decide which read-only evidence paths a request needs.

This layer is pure: it inspects the question, returns a validated `Plan`, and
never executes a tool, calls a model, or touches storage. Only the standard
library is imported on purpose - the planner must stay independent of `rag`,
`agent`, `api`, and `storage`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ToolName(str, Enum):
    WIKI_QUERY = "wiki_query"
    DOCUMENT_SEARCH = "document_search"
    SYSTEM_QUERY = "system_query"


class Route(str, Enum):
    DIRECT = "direct"
    WIKI_ONLY = "wiki_only"
    DOCUMENT_ONLY = "document_only"
    SYSTEM_ONLY = "system_only"
    WIKI_DOCUMENT = "wiki_document"
    WIKI_SYSTEM = "wiki_system"
    DOCUMENT_SYSTEM = "document_system"
    WIKI_DOCUMENT_SYSTEM = "wiki_document_system"


# Wiki, then Document, then System. Every plan is normalized to this order so a
# route can be derived from its steps rather than declared independently.
CANONICAL_STEP_ORDER = (
    ToolName.WIKI_QUERY,
    ToolName.DOCUMENT_SEARCH,
    ToolName.SYSTEM_QUERY,
)

ROUTE_BY_STEPS: dict[tuple[ToolName, ...], Route] = {
    (): Route.DIRECT,
    (ToolName.WIKI_QUERY,): Route.WIKI_ONLY,
    (ToolName.DOCUMENT_SEARCH,): Route.DOCUMENT_ONLY,
    (ToolName.SYSTEM_QUERY,): Route.SYSTEM_ONLY,
    (ToolName.WIKI_QUERY, ToolName.DOCUMENT_SEARCH): Route.WIKI_DOCUMENT,
    (ToolName.WIKI_QUERY, ToolName.SYSTEM_QUERY): Route.WIKI_SYSTEM,
    (ToolName.DOCUMENT_SEARCH, ToolName.SYSTEM_QUERY): Route.DOCUMENT_SYSTEM,
    (
        ToolName.WIKI_QUERY,
        ToolName.DOCUMENT_SEARCH,
        ToolName.SYSTEM_QUERY,
    ): Route.WIKI_DOCUMENT_SYSTEM,
}

MAX_STEPS = 3


# --------------------------------------------------------------------------
# Reason codes
# --------------------------------------------------------------------------

REASON_DIRECT_UTTERANCE = "direct_utterance"
REASON_WIKI_OVERVIEW = "wiki_overview"
REASON_DOCUMENT_EXACT = "document_exact"
REASON_DOCUMENT_POLICY_CONTEXT = "document_policy_context"
REASON_DOCUMENT_VERSION_CHANGE = "document_version_change"
REASON_SYSTEM_CURRENT_STATE = "system_current_state"
REASON_FRESHNESS_REQUESTED = "freshness_requested"
REASON_EXACT_CITATION_REQUESTED = "exact_citation_requested"
REASON_FALLBACK_SELECTED = "fallback_selected"
REASON_FALLBACK_EMPTY = "fallback_empty"
REASON_FALLBACK_INVALID = "fallback_invalid"
REASON_FALLBACK_EXCEPTION = "fallback_exception"
REASON_DEFAULT_DOCUMENT = "default_document"

# Emission order is fixed so that two equivalent plans always compare equal.
REASON_CODE_ORDER = (
    REASON_DIRECT_UTTERANCE,
    REASON_WIKI_OVERVIEW,
    REASON_DOCUMENT_VERSION_CHANGE,
    REASON_DOCUMENT_EXACT,
    REASON_DOCUMENT_POLICY_CONTEXT,
    REASON_SYSTEM_CURRENT_STATE,
    REASON_FRESHNESS_REQUESTED,
    REASON_EXACT_CITATION_REQUESTED,
    REASON_FALLBACK_SELECTED,
    REASON_FALLBACK_EMPTY,
    REASON_FALLBACK_INVALID,
    REASON_FALLBACK_EXCEPTION,
    REASON_DEFAULT_DOCUMENT,
)


# --------------------------------------------------------------------------
# Marker tables
# --------------------------------------------------------------------------
# Deliberately domain-neutral: no student-, school-, or single-company terms.

DIRECT_MARKERS = (
    "你好", "您好", "哈喽", "早上好", "中午好", "下午好", "晚上好",
    "谢谢", "多谢", "感谢", "辛苦了", "麻烦了",
    "再见", "拜拜", "晚安",
    "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye", "good morning",
)

WIKI_OVERVIEW_MARKERS = (
    "概述", "概览", "总结", "综述", "介绍", "大概讲什么", "整体说明", "主要内容",
    "关系", "脉络", "历史沿革", "影响分析", "梳理",
)

# Wording, clause, and citation intent: the caller wants the source text itself.
DOCUMENT_EXACT_MARKERS = (
    "原文", "条款", "依据", "引用", "页码", "具体怎么写", "明确规定", "怎么写的",
)

DOCUMENT_CONDITION_MARKERS = (
    "条件", "要求", "是否允许", "能否", "适用于",
)

# Numeric intent. Ambiguous on its own: it can describe a policy limit or a
# current system value, so `_extract_signals` resolves it by context.
DOCUMENT_QUANTITY_MARKERS = (
    "多少", "几天", "比例", "金额", "上限", "下限", "期限", "时限", "标准",
)

DOCUMENT_COMPARISON_MARKERS = (
    "区别", "比较", "差异", "例外", "对比",
)

# Context markers only: they say the topic is a policy, not that the caller
# needs the exact wording.
DOCUMENT_POLICY_MARKERS = (
    "制度", "政策", "办法", "规则", "规定", "手册", "sop", "公告", "通知", "流程",
)

# Version/change intent needs both paths: Wiki synthesizes, documents verify.
VERSION_CHANGE_MARKERS = (
    "变化", "变更", "新旧", "版本对比", "修订", "改动",
)

# Three distinct intents, deliberately kept apart. Collapsing them into one
# table makes `现在 + 订单` look like a state question when the sentence is
# actually asking what the policy says.

# Strong: the caller explicitly claims ownership of the record. Only first-person
# markers qualify - words like `进度` name a value, not whose value it is.
PERSONAL_STATE_MARKERS = (
    "我的", "本人", "我当前",
)

# Weak: temporal framing. `现在的...制度` is a policy question, not a state one.
TIME_STATE_MARKERS = (
    "当前", "现在", "实时", "目前",
)

# Weak: an explicit lookup verb aimed at a system object.
SYSTEM_QUERY_VERBS = (
    "查询", "查一下", "查下", "查看", "帮我查",
)

# Weak: asking for the value or status an object currently holds. `审批进度规定`
# shows why these stay weak - the same words appear in policy questions.
SYSTEM_VALUE_MARKERS = (
    "状态", "多少", "剩余", "进度", "到哪一步",
)

SYSTEM_OBJECT_MARKERS = (
    "订单", "库存", "余额", "物流", "审批", "工单", "账户", "积分", "额度",
    "排班", "考勤", "申请记录",
)

# Unambiguous state questions that name no object but clearly ask about the
# caller's current standing.
SYSTEM_STATE_PHRASES = (
    "是否到账", "是否通过", "我当前是否符合", "到哪一步了", "办到哪了",
)

FRESHNESS_MARKERS = (
    "当前", "现在", "实时", "目前", "最新", "截至",
)

# Exposed for introspection, including the vocabulary-hygiene test.
MARKER_TABLES: dict[str, tuple[str, ...]] = {
    "DIRECT_MARKERS": DIRECT_MARKERS,
    "WIKI_OVERVIEW_MARKERS": WIKI_OVERVIEW_MARKERS,
    "DOCUMENT_EXACT_MARKERS": DOCUMENT_EXACT_MARKERS,
    "DOCUMENT_CONDITION_MARKERS": DOCUMENT_CONDITION_MARKERS,
    "DOCUMENT_QUANTITY_MARKERS": DOCUMENT_QUANTITY_MARKERS,
    "DOCUMENT_COMPARISON_MARKERS": DOCUMENT_COMPARISON_MARKERS,
    "DOCUMENT_POLICY_MARKERS": DOCUMENT_POLICY_MARKERS,
    "VERSION_CHANGE_MARKERS": VERSION_CHANGE_MARKERS,
    "PERSONAL_STATE_MARKERS": PERSONAL_STATE_MARKERS,
    "TIME_STATE_MARKERS": TIME_STATE_MARKERS,
    "SYSTEM_QUERY_VERBS": SYSTEM_QUERY_VERBS,
    "SYSTEM_VALUE_MARKERS": SYSTEM_VALUE_MARKERS,
    "SYSTEM_OBJECT_MARKERS": SYSTEM_OBJECT_MARKERS,
    "SYSTEM_STATE_PHRASES": SYSTEM_STATE_PHRASES,
    "FRESHNESS_MARKERS": FRESHNESS_MARKERS,
}

# `第三条`, `第 12 条`, `第十二条`.
CLAUSE_NUMBER_PATTERN = re.compile(r"第\s*[0-9〇零一二三四五六七八九十百千]+\s*条")

_EDGE_PUNCTUATION = " \t\r\n，。！？、；：~,.!?;:…-—～\"'“”‘’()（）【】《》"


# --------------------------------------------------------------------------
# Data contracts
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RequestSignals:
    is_direct: bool = False
    needs_wiki: bool = False
    needs_document: bool = False
    needs_system: bool = False
    requires_freshness: bool = False
    requires_exact_citation: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "is_direct": self.is_direct,
            "needs_wiki": self.needs_wiki,
            "needs_document": self.needs_document,
            "needs_system": self.needs_system,
            "requires_freshness": self.requires_freshness,
            "requires_exact_citation": self.requires_exact_citation,
        }


@dataclass(frozen=True, kw_only=True)
class Plan:
    """A validated, immutable decision about which evidence paths to run.

    `fallback_used` records whether the injected fallback was *consulted*, not
    whether it succeeded. Whether it supplied the steps or failed and fell back
    to the document default is recorded in `reason_codes`.
    """

    route: Route
    steps: tuple[ToolName, ...] = ()
    signals: RequestSignals = field(default_factory=RequestSignals)
    reason_codes: tuple[str, ...] = ()
    fallback_used: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple):
            raise ValueError("Plan.steps must be a tuple")
        if not all(isinstance(step, ToolName) for step in self.steps):
            raise ValueError("Plan.steps must contain only ToolName values")
        if len(self.steps) > MAX_STEPS:
            raise ValueError(f"Plan.steps must contain at most {MAX_STEPS} steps")
        if len(set(self.steps)) != len(self.steps):
            raise ValueError("Plan.steps must not contain duplicates")
        if list(self.steps) != [
            tool for tool in CANONICAL_STEP_ORDER if tool in self.steps
        ]:
            raise ValueError("Plan.steps must follow the canonical Wiki/Document/System order")

        expected_route = ROUTE_BY_STEPS.get(self.steps)
        if expected_route is None or expected_route is not self.route:
            raise ValueError("Plan.route does not match Plan.steps")
        if self.route is Route.DIRECT and self.steps:
            raise ValueError("Plan 'direct' must not contain steps")
        if self.route is not Route.DIRECT and not self.steps:
            raise ValueError("A non-direct Plan requires at least one step")

        if not isinstance(self.reason_codes, tuple):
            raise ValueError("Plan.reason_codes must be a tuple")
        for code in self.reason_codes:
            if not isinstance(code, str) or not code.strip():
                raise ValueError("Plan.reason_codes must contain non-empty strings")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("Plan.reason_codes must not contain duplicates")

        if not isinstance(self.fallback_used, bool):
            raise ValueError("Plan.fallback_used must be a boolean")

        if not isinstance(self.signals, RequestSignals):
            raise ValueError("Plan.signals must be a RequestSignals instance")

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route.value,
            "steps": [step.value for step in self.steps],
            "signals": self.signals.to_dict(),
            "reason_codes": list(self.reason_codes),
            "fallback_used": self.fallback_used,
        }


# --------------------------------------------------------------------------
# Signal extraction
# --------------------------------------------------------------------------


def _normalize(question: str) -> str:
    """Lowercase and collapse whitespace for matching only."""
    return re.sub(r"\s+", " ", question).strip().lower()


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_direct(normalized: str) -> bool:
    """Direct applies only to a whole standalone social utterance."""
    stripped = normalized.strip(_EDGE_PUNCTUATION)
    return stripped in DIRECT_MARKERS


def _extract_signals(normalized: str) -> tuple[RequestSignals, list[str]]:
    if _is_direct(normalized):
        return (
            RequestSignals(is_direct=True),
            [REASON_DIRECT_UTTERANCE],
        )

    wiki_overview = _contains_any(normalized, WIKI_OVERVIEW_MARKERS)
    version_change = _contains_any(normalized, VERSION_CHANGE_MARKERS)

    strong_document = (
        _contains_any(normalized, DOCUMENT_EXACT_MARKERS)
        or _contains_any(normalized, DOCUMENT_CONDITION_MARKERS)
        or _contains_any(normalized, DOCUMENT_COMPARISON_MARKERS)
        or bool(CLAUSE_NUMBER_PATTERN.search(normalized))
    )
    quantity_document = _contains_any(normalized, DOCUMENT_QUANTITY_MARKERS)
    policy_context = _contains_any(normalized, DOCUMENT_POLICY_MARKERS)

    has_system_object = _contains_any(normalized, SYSTEM_OBJECT_MARKERS)
    state_phrase = _contains_any(normalized, SYSTEM_STATE_PHRASES)
    personal_state = _contains_any(normalized, PERSONAL_STATE_MARKERS)
    # Any of these alone only hints at a state question; none of them can
    # outweigh policy semantics on its own.
    weak_state_intent = (
        _contains_any(normalized, TIME_STATE_MARKERS)
        or _contains_any(normalized, SYSTEM_QUERY_VERBS)
        or _contains_any(normalized, SYSTEM_VALUE_MARKERS)
    )

    if state_phrase or (has_system_object and personal_state):
        # An explicit personal/state signal keeps System even when the same
        # sentence also asks what the policy says.
        needs_system = True
    elif has_system_object and weak_state_intent and not policy_context:
        # `帮我查一下账户余额`, `账户余额是多少`, `当前库存还有多少`.
        needs_system = True
    else:
        # `查询订单管理制度` and `现在的订单管理制度怎么规定` land here: the
        # object is real, but the sentence is asking what the rule says.
        needs_system = False

    # `当前库存还有多少`: the numeric language describes a live system value, not
    # a policy limit, so it must not drag in the document path.
    quantity_is_system_value = (
        needs_system and quantity_document and not strong_document and not policy_context
    )
    if quantity_is_system_value:
        quantity_document = False

    exact_document = strong_document or quantity_document

    # A pure overview request keeps policy nouns as context only; an explicit
    # exact or version-change signal still adds the document step.
    policy_forces_document = policy_context and not (wiki_overview and not exact_document)

    needs_wiki = wiki_overview or version_change
    needs_document = exact_document or version_change or policy_forces_document
    requires_freshness = _contains_any(normalized, FRESHNESS_MARKERS)
    requires_exact_citation = exact_document

    signals = RequestSignals(
        needs_wiki=needs_wiki,
        needs_document=needs_document,
        needs_system=needs_system,
        requires_freshness=requires_freshness,
        requires_exact_citation=requires_exact_citation,
    )

    reasons: list[str] = []
    if wiki_overview:
        reasons.append(REASON_WIKI_OVERVIEW)
    if version_change:
        reasons.append(REASON_DOCUMENT_VERSION_CHANGE)
    if exact_document:
        reasons.append(REASON_DOCUMENT_EXACT)
    if policy_forces_document:
        reasons.append(REASON_DOCUMENT_POLICY_CONTEXT)
    if needs_system:
        reasons.append(REASON_SYSTEM_CURRENT_STATE)
    if requires_freshness:
        reasons.append(REASON_FRESHNESS_REQUESTED)
    if requires_exact_citation:
        reasons.append(REASON_EXACT_CITATION_REQUESTED)
    return signals, reasons


def _steps_from_signals(signals: RequestSignals) -> tuple[ToolName, ...]:
    selected = {
        ToolName.WIKI_QUERY: signals.needs_wiki,
        ToolName.DOCUMENT_SEARCH: signals.needs_document,
        ToolName.SYSTEM_QUERY: signals.needs_system,
    }
    return tuple(tool for tool in CANONICAL_STEP_ORDER if selected[tool])


def _canonicalize(tools: tuple[ToolName, ...]) -> tuple[ToolName, ...]:
    return tuple(tool for tool in CANONICAL_STEP_ORDER if tool in tools)


def _order_reasons(reasons: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for code in REASON_CODE_ORDER:
        if code in reasons and code not in seen:
            seen.append(code)
    # Any code outside the known order is appended rather than silently dropped.
    for code in reasons:
        if code not in seen:
            seen.append(code)
    return tuple(seen)


def _validate_fallback_steps(raw: object) -> tuple[ToolName, ...] | None:
    """Treat fallback output as untrusted input."""
    if not isinstance(raw, tuple):
        return None
    if not raw or len(raw) > MAX_STEPS:
        return None
    if not all(isinstance(item, ToolName) for item in raw):
        return None
    if len(set(raw)) != len(raw):
        return None
    return _canonicalize(raw)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def plan_request(
    question: str,
    *,
    fallback: Callable[[str], tuple[ToolName, ...] | None] | None = None,
) -> Plan:
    """Return the validated evidence plan for `question`.

    The fallback is consulted only when no deterministic signal selected a
    tool, is called at most once with the original question, and can never
    choose `direct`: an ambiguous knowledge request defaults to the
    authoritative source-document path.
    """
    if not question or not question.strip():
        raise ValueError("question must not be blank")

    normalized = _normalize(question)
    signals, reasons = _extract_signals(normalized)

    if signals.is_direct:
        return Plan(
            route=Route.DIRECT,
            steps=(),
            signals=signals,
            reason_codes=_order_reasons(reasons),
            fallback_used=False,
        )

    steps = _steps_from_signals(signals)
    if steps:
        return Plan(
            route=ROUTE_BY_STEPS[steps],
            steps=steps,
            signals=signals,
            reason_codes=_order_reasons(reasons),
            fallback_used=False,
        )

    if fallback is None:
        return _document_default(signals, reasons, fallback_used=False)

    try:
        raw = fallback(question)
    except Exception:
        # Deliberately no exception text: it is untrusted and would leak into
        # a machine-readable field.
        return _document_default(
            signals, reasons + [REASON_FALLBACK_EXCEPTION], fallback_used=True
        )

    if raw is None or (isinstance(raw, tuple) and not raw):
        return _document_default(
            signals, reasons + [REASON_FALLBACK_EMPTY], fallback_used=True
        )

    validated = _validate_fallback_steps(raw)
    if validated is None:
        return _document_default(
            signals, reasons + [REASON_FALLBACK_INVALID], fallback_used=True
        )

    resolved = RequestSignals(
        needs_wiki=ToolName.WIKI_QUERY in validated,
        needs_document=ToolName.DOCUMENT_SEARCH in validated,
        needs_system=ToolName.SYSTEM_QUERY in validated,
        requires_freshness=signals.requires_freshness,
        requires_exact_citation=signals.requires_exact_citation,
    )
    return Plan(
        route=ROUTE_BY_STEPS[validated],
        steps=validated,
        signals=resolved,
        reason_codes=_order_reasons(reasons + [REASON_FALLBACK_SELECTED]),
        fallback_used=True,
    )


def _document_default(
    signals: RequestSignals, reasons: list[str], *, fallback_used: bool
) -> Plan:
    resolved = RequestSignals(
        needs_wiki=signals.needs_wiki,
        needs_document=True,
        needs_system=signals.needs_system,
        requires_freshness=signals.requires_freshness,
        requires_exact_citation=signals.requires_exact_citation,
    )
    steps = _steps_from_signals(resolved)
    return Plan(
        route=ROUTE_BY_STEPS[steps],
        steps=steps,
        signals=resolved,
        reason_codes=_order_reasons(reasons + [REASON_DEFAULT_DOCUMENT]),
        fallback_used=fallback_used,
    )
