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

# Social openers, closers, and acknowledgements. Wider than DIRECT_MARKERS on
# purpose: this table only says "a social register is present", never that the
# whole message is social. That judgement is made per clause, so a greeting in
# front of a real question cannot short-circuit the plan.
SOCIAL_MARKERS = (
    "你好", "您好", "哈喽", "哈啰", "嗨", "早上好", "上午好", "中午好",
    "下午好", "晚上好", "晚安", "在吗", "在不在", "方便吗", "打扰", "打个招呼",
    "谢谢", "多谢", "感谢", "辛苦", "麻烦了", "收到", "明白了", "懂了", "好的",
    "再见", "拜拜", "明天见", "回头见", "下周聊", "改天聊", "周末愉快",
    "先撤", "先走", "就到这儿", "就到这里",
    "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye",
    "good morning", "good afternoon", "good evening",
)

# Particles and fillers that carry no request. Stripped only when deciding
# whether a clause is purely social.
SOCIAL_FILLER = "呀啊哇哈呢吧嘛咯喽哦噢嗯唉了的呗啦么行好那这就是我你您他她们也都还再先"

# Interrogative cues. Presence of one in a *non-social* clause means the caller
# is asking for something, even when no topic marker matched.
QUESTION_CUES = (
    "吗", "呢", "?", "？", "什么", "怎么", "如何", "哪", "几", "多少",
    "是否", "能否", "能不能", "可不可以", "为什么", "请问", "有没有",
)

# Ways of asking for something that name no topic this planner knows.
# `你好，帮我订一间会议室` is out of scope, but it is still a request: answering
# it with a fixed greeting would drop the user's actual message on the floor.
# Out-of-scope requests belong on the normal path, where retrieval and the
# refusal chain can say "no" honestly.
REQUEST_CUES = (
    "帮我", "帮忙", "我想", "想问", "请问", "请", "告诉我", "给我",
    "看看", "看下", "查一下", "查询", "确认", "联系", "预订", "预约",
    "投诉", "处理", "申请", "提交",
)

# Verbs that by themselves request a compiled explanation rather than a clause.
WIKI_OVERVIEW_VERBS = (
    "概述", "概览", "综述", "介绍", "梳理", "科普", "总结",
    "讲讲", "讲一讲", "讲一下", "讲下", "说说", "说一说",
    "捋一遍", "捋一捋", "捋捋", "了解一下", "介绍下",
)

# Scope words that mark a low-detail request. On their own they are enough:
# `整体立场是什么` asks for an overview without naming a verb.
WIKI_OVERVIEW_SCOPE = (
    "整体", "大方向", "总体", "主要内容", "主要思路",
    "要点", "轮廓", "脉络", "立场", "思路", "有个数", "历史沿革", "影响分析",
    "关系", "大概讲什么", "整体说明",
)

# Hedges, not requests for an overview. `SKU-A100 大概还有多少` softens a live
# lookup; it does not ask for a compiled topic page. These count only when the
# clause is not already an unambiguous live-value question - see
# `_clause_signals`.
WIKI_OVERVIEW_SOFT = (
    "大概", "大致", "说下", "说一下", "简单说",
)

WIKI_OVERVIEW_STRONG = WIKI_OVERVIEW_VERBS + WIKI_OVERVIEW_SCOPE
WIKI_OVERVIEW_MARKERS = WIKI_OVERVIEW_STRONG + WIKI_OVERVIEW_SOFT

# Wording, clause, and citation intent: the caller wants the source text itself.
DOCUMENT_EXACT_MARKERS = (
    "原文", "条款", "依据", "引用", "页码", "出处", "原话",
    "具体怎么写", "明确规定", "怎么写的",
)

# Cues that turn a nearby exact-citation marker into a refusal of one.
# `不用抠原文` asks for the opposite of what `原文` alone implies.
NEGATION_CUES = (
    "不用", "不需要", "无需", "不必", "不要", "没必要", "不想", "别",
)

# How far back a negation may reach. Deliberately short and clipped at the
# nearest clause delimiter: this is a local window, not sentence-level scope
# analysis, so `不用讲太宽泛的东西。请直接引用原文` still requests the source.
NEGATION_WINDOW = 8

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
    "SOCIAL_MARKERS": SOCIAL_MARKERS,
    "QUESTION_CUES": QUESTION_CUES,
    "REQUEST_CUES": REQUEST_CUES,
    "WIKI_OVERVIEW_VERBS": WIKI_OVERVIEW_VERBS,
    "WIKI_OVERVIEW_SCOPE": WIKI_OVERVIEW_SCOPE,
    "WIKI_OVERVIEW_SOFT": WIKI_OVERVIEW_SOFT,
    "NEGATION_CUES": NEGATION_CUES,
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

# A SKU names a specific record in the business system, so it is an
# unambiguous System object in a way a common noun never is. Matched on the
# normalized (lowercased) text. The `sku` prefix is required, so an arbitrary
# alphanumeric token is never mistaken for one; the neighbour guards reject
# `xsku-a100` and `sku-a1000`.
SKU_PATTERN = re.compile(r"(?<![a-z0-9])sku[-_ ]?[a-z]\d{3}(?![a-z0-9])")

# Splitting on punctuation alone merges `介绍一下远程办公 顺便查 SKU-A100`, so a few
# additive connectives are delimiters too.
#
# Deliberately short. Only words that are reliably clause-initial qualify:
# `还有` and `最后` were tried and removed because they occur *inside* a single
# request (`当前库存还有多少`, `SKU-A100 最后还剩多少`). Splitting there separates a
# quantity word from its business object, which flips a live lookup into a
# document search. In real compound sentences a connective is nearly always
# preceded by punctuation, which already splits, so this table stays small.
CLAUSE_CONNECTIVES = (
    "另外", "顺便", "顺手", "对了", "同时", "其次",
)
_CLAUSE_DELIMITERS = "，。；！？,;!?、\n"
_CLAUSE_SPLIT_PATTERN = re.compile(
    "[" + re.escape(_CLAUSE_DELIMITERS) + "]|" + "|".join(CLAUSE_CONNECTIVES)
)

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


def _split_clauses(normalized: str) -> list[str]:
    """Break a message into the separate requests it actually makes.

    Whole-string matching let one clause veto another: in
    `库存管理办法怎么写？SKU-A100 还有多少`, the policy noun in the first clause
    suppressed the live lookup in the second. Signals are therefore collected
    per clause and unioned.
    """
    parts = [part.strip(_EDGE_PUNCTUATION) for part in _CLAUSE_SPLIT_PATTERN.split(normalized)]
    clauses = [part for part in parts if part]
    # A message that is nothing but delimiters still has to be analysed.
    return clauses or [normalized.strip(_EDGE_PUNCTUATION) or normalized]


def _negated_at(text: str, index: int) -> bool:
    """Is the marker starting at `index` inside a refusal of it?"""
    window = text[max(0, index - NEGATION_WINDOW) : index]
    # A negation never reaches across a clause boundary.
    for delimiter in _CLAUSE_DELIMITERS:
        cut = window.rfind(delimiter)
        if cut >= 0:
            window = window[cut + 1 :]
    return any(cue in window for cue in NEGATION_CUES)


def _contains_any_unnegated(text: str, markers: tuple[str, ...]) -> bool:
    """Like `_contains_any`, but a marker the caller declined does not count."""
    for marker in markers:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            if not _negated_at(text, index):
                return True
            start = index + 1
    return False


def _strip_social(clause: str) -> str:
    residue = clause
    for marker in SOCIAL_MARKERS:
        residue = residue.replace(marker, "")
    return "".join(ch for ch in residue if ch not in SOCIAL_FILLER).strip(
        _EDGE_PUNCTUATION
    )


def _is_social_clause(clause: str) -> bool:
    """A clause that is only pleasantry once its social wording is removed.

    `在吗` and `方便吗` are interrogative in form but ask for nothing, so they
    must be recognised here before the question-cue check sees them.
    """
    if not _contains_any(clause, SOCIAL_MARKERS):
        return False
    return len(_strip_social(clause)) < 2


def _has_request_intent(clause: str) -> bool:
    """Does this clause ask for knowledge, source text, or system state?"""
    if _contains_any(clause, WIKI_OVERVIEW_MARKERS):
        return True
    if _contains_any(clause, VERSION_CHANGE_MARKERS):
        return True
    if _contains_any_unnegated(clause, DOCUMENT_EXACT_MARKERS):
        return True
    for table in (
        DOCUMENT_CONDITION_MARKERS,
        DOCUMENT_COMPARISON_MARKERS,
        DOCUMENT_QUANTITY_MARKERS,
        DOCUMENT_POLICY_MARKERS,
        SYSTEM_OBJECT_MARKERS,
        SYSTEM_STATE_PHRASES,
        SYSTEM_QUERY_VERBS,
    ):
        if _contains_any(clause, table):
            return True
    if CLAUSE_NUMBER_PATTERN.search(clause) or SKU_PATTERN.search(clause):
        return True
    # No topic marker matched, but the caller is still plainly asking something.
    # `公司班车几点发车？` is interrogative; `帮我订一间会议室` is an imperative
    # request with no question mark at all. Both are requests, and neither may
    # be answered with a greeting.
    return _contains_any(clause, QUESTION_CUES) or _contains_any(clause, REQUEST_CUES)


def _is_direct(clauses: list[str]) -> bool:
    """Direct only when every clause is social and none asks for anything.

    Widened from whole-string equality, which rejected `早上好呀，今天开始干活了`
    and every other greeting a person actually types. The guard is that a single
    requesting clause anywhere disqualifies the whole message.
    """
    if not any(_contains_any(clause, SOCIAL_MARKERS) for clause in clauses):
        return False
    for clause in clauses:
        if _is_social_clause(clause):
            continue
        if _has_request_intent(clause):
            return False
    return True


@dataclass(frozen=True, kw_only=True)
class _ClauseSignals:
    """What one clause on its own asks for."""

    wiki_overview: bool = False
    version_change: bool = False
    exact_document: bool = False
    policy_forces_document: bool = False
    needs_system: bool = False
    requires_freshness: bool = False


def _clause_signals(clause: str) -> _ClauseSignals:
    version_change = _contains_any(clause, VERSION_CHANGE_MARKERS)

    strong_document = (
        _contains_any_unnegated(clause, DOCUMENT_EXACT_MARKERS)
        or _contains_any(clause, DOCUMENT_CONDITION_MARKERS)
        or _contains_any(clause, DOCUMENT_COMPARISON_MARKERS)
        or bool(CLAUSE_NUMBER_PATTERN.search(clause))
    )
    quantity_document = _contains_any(clause, DOCUMENT_QUANTITY_MARKERS)
    policy_context = _contains_any(clause, DOCUMENT_POLICY_MARKERS)

    has_sku = bool(SKU_PATTERN.search(clause))
    has_system_object = _contains_any(clause, SYSTEM_OBJECT_MARKERS)
    state_phrase = _contains_any(clause, SYSTEM_STATE_PHRASES)
    personal_state = _contains_any(clause, PERSONAL_STATE_MARKERS)
    # Any of these alone only hints at a state question; none of them can
    # outweigh policy semantics on its own.
    weak_state_intent = (
        _contains_any(clause, TIME_STATE_MARKERS)
        or _contains_any(clause, SYSTEM_QUERY_VERBS)
        or _contains_any(clause, SYSTEM_VALUE_MARKERS)
    )

    if has_sku:
        # A SKU names one row in the business system. Unlike `库存`, it cannot
        # also be a policy noun, so it needs no corroborating state word and is
        # not suppressed by policy semantics in the same clause.
        needs_system = True
    elif state_phrase or (has_system_object and personal_state):
        # An explicit personal/state signal keeps System even when the same
        # clause also asks what the policy says.
        needs_system = True
    elif has_system_object and weak_state_intent and not policy_context:
        # `帮我查一下账户余额`, `账户余额是多少`, `当前库存还有多少`.
        needs_system = True
    else:
        # `查询订单管理制度` and `现在的订单管理制度怎么规定` land here: the
        # object is real, but the clause is asking what the rule says.
        needs_system = False

    # `当前库存还有多少`: the numeric language describes a live system value, not
    # a policy limit, so it must not drag in the document path.
    if needs_system and quantity_document and not strong_document and not policy_context:
        quantity_document = False

    exact_document = strong_document or quantity_document

    # Overview is decided after System, because a hedge means different things
    # in the two cases. `大概` in `SKU-A100 大概还有多少` softens a live lookup;
    # in `大概讲什么` it asks for a compiled page. A soft marker therefore
    # counts only when the clause is not already an unambiguous live-value
    # question about something that is not a policy topic.
    wiki_strong = _contains_any(clause, WIKI_OVERVIEW_STRONG)
    wiki_soft = _contains_any(clause, WIKI_OVERVIEW_SOFT)
    if wiki_soft and needs_system and not policy_context:
        wiki_soft = False
    wiki_overview = wiki_strong or wiki_soft

    # A pure overview request keeps policy nouns as context only; an explicit
    # exact or version-change signal still adds the document step.
    policy_forces_document = policy_context and not (wiki_overview and not exact_document)

    return _ClauseSignals(
        wiki_overview=wiki_overview,
        version_change=version_change,
        exact_document=exact_document,
        policy_forces_document=policy_forces_document,
        needs_system=needs_system,
        requires_freshness=_contains_any(clause, FRESHNESS_MARKERS),
    )


def _extract_signals(normalized: str) -> tuple[RequestSignals, list[str]]:
    clauses = _split_clauses(normalized)

    if _is_direct(clauses):
        return (
            RequestSignals(is_direct=True),
            [REASON_DIRECT_UTTERANCE],
        )

    per_clause = [_clause_signals(clause) for clause in clauses]
    any_of = lambda field: any(getattr(item, field) for item in per_clause)

    wiki_overview = any_of("wiki_overview")
    version_change = any_of("version_change")
    exact_document = any_of("exact_document")
    policy_forces_document = any_of("policy_forces_document")

    needs_wiki = wiki_overview or version_change
    needs_document = exact_document or version_change or policy_forces_document
    needs_system = any_of("needs_system")
    requires_freshness = any_of("requires_freshness")
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
