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

# Leave-taking. A sign-off is a social register in its own right, and people
# write it compositionally rather than with the two or three fixed farewells a
# short list would hold: `就到这儿` was there, `聊到这儿` was not, and the
# planner answered a goodbye by searching the source documents. The pieces
# below are the recurring shapes - "let us stop here", "talk later", "I am off"
# - each of which names an end to the conversation and asks for nothing.
# A sign-off also routinely pairs an acknowledgement with a "nothing further"
# and a farewell (`明白，暂时没别的需求，回见`). The acknowledgement and the
# "nothing further" halves were missing, and `回见` was only present in its
# longer form `回头见`, so the whole message fell through to document search.
# These stay safe because `_is_direct` still requires *every* clause to ask for
# nothing: `我不明白年假规定` names a policy and is therefore still a request.
SOCIAL_CLOSING_MARKERS = (
    "聊到这儿", "聊到这里", "说到这儿", "说到这里", "谈到这儿", "到此为止",
    "回头聊", "回头说", "回头联系", "改天说", "改天联系", "下次聊", "下回聊",
    "有空聊", "有空再聊", "明天再说", "明天再聊", "下次再说",
    "先忙", "忙去了", "去忙了", "不打扰了", "不打扰你了",
    "就这样吧", "就先这样", "准备下班", "下班了", "收工", "散会",
    "回见", "明白", "知道了", "了解了", "清楚了",
    "没别的", "没其他", "没问题了", "就这些", "先到这", "问完了",
)

# Thanks, written the way people actually end a conversation. The table held
# only the full forms (`谢谢`, `多谢`, `感谢`), so the commonest sign-off of all -
# a bare `谢啦` - carried no social signal whatsoever, and a message made of
# nothing but thanks and a good wish was searched against the source documents.
SOCIAL_THANKS_MARKERS = (
    "谢啦", "谢了", "谢过", "谢谢啦", "谢谢了", "多谢啦", "多谢了",
    "感谢啦", "感谢了", "太感谢", "非常感谢", "有劳", "费心了",
)

# Well-wishes. A wish asks for nothing and names no topic, so it can only ever
# be the social half of a message; `_is_direct` still requires every other
# clause to ask for nothing, so `祝好，另外年假有几天` stays a real question.
# `一切顺利` and friends are listed whole - bare `顺利` would swallow
# `报销流程顺利吗`, which is a genuine question about a process.
SOCIAL_WISH_MARKERS = (
    "祝你", "祝您", "祝大家", "祝各位", "祝好", "祝顺利",
    "一切顺利", "顺顺利利", "万事如意", "工作顺利", "一路顺风", "一路平安",
    "旅途愉快", "假期愉快", "节日快乐", "新年快乐", "生日快乐",
    "身体健康", "保重", "多保重", "早点休息", "注意休息", "别太累",
)

# Time adverbs that only situate a wish or a farewell. Stripped alongside the
# social markers so `祝你接下来一切顺利` reduces to nothing: without this the
# leftover `接下来` kept the clause from reading as pure pleasantry. Safe
# because `_is_social_clause` first requires a social marker to be present, and
# `接下来的报销流程是什么` has none.
SOCIAL_TIME_FILLER = (
    "接下来", "往后", "今后", "以后", "后面", "未来", "这段时间", "最近",
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
) + SOCIAL_CLOSING_MARKERS + SOCIAL_THANKS_MARKERS + SOCIAL_WISH_MARKERS

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
# Three shapes, all asking for the same thing: summarise (`概括`), walk the whole
# topic (`过一遍`), or explain it fully (`讲清楚`). The list is closed over the
# shapes rather than over one word per shape, because a caller picks whichever
# synonym comes to mind and a page request is a page request either way.
# `讲清楚`/`说清楚` were tried and removed: they demand clarity about whatever
# is being asked, not a compiled page, so `引用原文讲清楚盘点口径` is a request
# for the source text and `先说清楚一点` is not a request at all.
WIKI_OVERVIEW_VERBS = (
    "概述", "概览", "总览", "综述", "概括", "介绍", "梳理", "科普",
    "讲讲", "讲一讲", "讲一下", "讲下", "说说", "说一说",
    "捋一遍", "捋一捋", "捋捋", "过一遍", "理一遍", "串一遍",
    "了解一下", "介绍下",
)

# Scope words that mark a low-detail request. On their own they are enough:
# `整体立场是什么` asks for an overview without naming a verb.
#
# `整套` and `从头到尾`/`来龙去脉` are the same judgement stated over a span
# rather than a level of detail: naming the whole of a topic asks for the
# compiled page, not for one clause out of it. Only quantifiers that say
# "all of it" qualify - the demonstratives `那套`/`这套` were tried and removed,
# because they point back at something already named (`我们那套旧系统`) instead
# of asking for its full extent.
WIKI_OVERVIEW_SCOPE = (
    "整体", "大方向", "总体", "主要内容", "主要思路",
    "要点", "轮廓", "脉络", "立场", "思路", "有个数", "历史沿革", "影响分析",
    "关系", "大概讲什么", "整体说明",
    "框架", "全貌", "整套", "全套",
    "从头到尾", "来龙去脉", "前因后果",
)

# Hedges, not requests for an overview. `SKU-A100 大概还有多少` softens a live
# lookup; it does not ask for a compiled topic page. These count only when the
# clause is not already an unambiguous live-value question - see
# `_clause_signals`.
WIKI_OVERVIEW_SOFT = (
    "大概", "大致", "大体", "说下", "说一下", "简单说",
)

WIKI_OVERVIEW_STRONG = WIKI_OVERVIEW_VERBS + WIKI_OVERVIEW_SCOPE
WIKI_OVERVIEW_MARKERS = WIKI_OVERVIEW_STRONG + WIKI_OVERVIEW_SOFT

# Wording, clause, and citation intent: the caller wants the source text itself.
DOCUMENT_EXACT_MARKERS = (
    "原文", "条款", "条文", "依据", "引用", "页码", "出处", "原话",
    "具体怎么写", "明确规定", "怎么写的",
)

# Demands for precision. `要精确的` and `准确说法` name no document, but they say
# the caller will not accept a paraphrase - which is exactly the request the
# source-document path exists to serve. Kept apart from DOCUMENT_EXACT_MARKERS
# because these words qualify a neighbouring request rather than name a source,
# and they routinely arrive as a clause of their own (`……，要准确的`).
DOCUMENT_PRECISION_MARKERS = (
    "准确", "精确", "精准", "确切", "严格按照",
    "一字不差", "一字不落", "原封不动", "原样", "逐字",
)

# How close a stock noun has to sit for a precision marker to be describing it.
# Deliberately short: this is adjacency (`存量准确数`), not clause-wide scope.
PRECISION_WINDOW = 4

# Who the rule puts in charge. `超过 1 天要谁签` asks what a document says about
# approval authority; it is not a question about any particular record, so it
# belongs to the document path and not to System.
DOCUMENT_AUTHORITY_MARKERS = (
    "审批人", "签字人", "批准人", "审批权", "签批权", "经办人", "责任人",
)

# Cues that turn a nearby exact-citation marker into a refusal of one.
# `不用抠原文` asks for the opposite of what `原文` alone implies.
NEGATION_CUES = (
    "不用", "不需要", "无需", "不必", "不要", "没必要", "不想",
)

# `别` is a negation only when it stands on its own. As a bare substring it also
# sits inside `分别`, `级别`, `区别`, `特别`, `类别`, `识别` - and
# `请分别给：信息安全概述、…` was read as `别给` and lost the Wiki step, because
# a one-character cue matched the tail of another word.
#
# So it counts when nothing Chinese precedes it (clause start, punctuation, a
# space) or when the character before it is one of the adverbs and pronouns that
# genuinely lead into it: `先别给我`, `请别列`, `千万别`.
_BARE_NEGATION_BIE = r"(?:(?<![一-鿿])别|(?<=[先就可请你您我也都还万千])别)"
_BIE_NEGATION_PATTERN = re.compile(_BARE_NEGATION_BIE)

# How far back a negation may reach. Deliberately short and clipped at the
# nearest clause delimiter: this is a local window, not sentence-level scope
# analysis, so `不用讲太宽泛的东西。请直接引用原文` still requests the source.
NEGATION_WINDOW = 8

# Chinese also negates after the object: `具体条款先别给我` fronts `条款` and puts
# the refusal on the verb that would have supplied it. A backward-only window
# reads that as a request for the clause text, i.e. the exact opposite.
#
# The scope is narrow on purpose. Only a negated verb of *supply* cancels the
# marker, so `原文别省略` - which asks for more of the source, not less - is left
# alone. Anything past a clause delimiter is out of reach, as it is backwards.
NEGATED_SUPPLY_VERBS = (
    "给", "发", "列", "提供", "展开", "贴", "附", "抄", "念", "罗列", "复制",
)

_FORWARD_NEGATION_PATTERN = re.compile(
    "(?:" + "|".join(NEGATION_CUES) + "|" + _BARE_NEGATION_BIE + ")"
    + "[^" + re.escape("，。；！？,;!?、\n") + "]{0,3}?"
    + "(?:" + "|".join(NEGATED_SUPPLY_VERBS) + ")"
)

# Chinese also declines an object *after* naming it, with no verb of supply at
# all: `制度概览就免了` names the overview and then waves it away. Neither the
# backward window (the refusal is to the right) nor the supply-verb pattern
# (`免` supplies nothing, so there is no verb to negate) sees this, so the
# planner read `概览` as a request and added the Wiki step the caller had just
# declined.
#
# `免了` needs the lookbehind: `避免了` and `以免了` contain it and mean the
# opposite of a dismissal. `算了`/`省了` need a leading adverb for the same
# reason - `打算了` is not a refusal.
# Three ways to wave a channel away after naming it:
#
# - dismissing it outright - `制度概览就免了`;
# - deferring it - `总览先不用`, where `不用` carries no `了`;
# - taking it on yourself - `具体条文我等下自己翻`, which declines the clause
#   just as plainly as `别给我条文` does.
#
# The last two were missing, so both channels were still selected.
_POST_DISMISSAL_PATTERN = re.compile(
    r"(?<![避以难])免了|免谈|(?:就|都|也|倒|先)(?:算了|省了)"
    r"|不用了|不必了|不需要了|跳过|略过"
    r"|(?:先|就|都|也|暂)(?:不用|不必|不需要)"
    r"|自己(?:翻|看|查|找|读|搜|来)|我来(?:翻|看|查|找|读)"
)

# Strong: the caller is asking whether a specific case qualifies, which only
# the source text settles. `要求` was tried here and moved to the policy table:
# `整体是怎么要求的` is an overview of a topic, not a request for its conditions,
# and treating it as strong pulled a document step into every such request.
DOCUMENT_CONDITION_MARKERS = (
    "条件", "是否允许", "能否", "适用于",
)

# Numeric intent. Ambiguous on its own: it can describe a policy limit or a
# current system value, so `_extract_signals` resolves it by context.
DOCUMENT_QUANTITY_MARKERS = (
    "多少", "几天", "比例", "金额", "上限", "下限", "期限", "时限", "标准",
    "天数", "周期", "次数", "频率",
)

DOCUMENT_COMPARISON_MARKERS = (
    "区别", "比较", "差异", "例外", "对比", "差别", "差在", "相比", "比起来",
)

# Context markers only: they say the topic is a policy, not that the caller
# needs the exact wording.
DOCUMENT_POLICY_MARKERS = (
    "制度", "政策", "办法", "规则", "规定", "手册", "sop", "公告", "通知", "流程",
    "要求",
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

# Weak: an explicit lookup verb aimed at a system object. `看一眼` and `查一下`
# are the same act; which one a caller types is a matter of register. Only
# multi-character forms are listed, so the bare verb inside an unrelated word
# cannot match.
SYSTEM_QUERY_VERBS = (
    "查询", "查一下", "查下", "查看", "帮我查",
    "看一眼", "看一下", "看下", "看看", "瞅一眼",
)

# Weak: asking for the value or status an object currently holds. `审批进度规定`
# shows why these stay weak - the same words appear in policy questions.
#
# Availability is the same question asked without a number: `库存还有货吗` wants
# the live count just as `当前库存还有多少` does. These stay weak for the same
# reason the rest do - `剩余库存管理办法` is still a document question - so a
# policy noun in the clause continues to outrank them.
SYSTEM_VALUE_MARKERS = (
    "状态", "多少", "剩余", "进度", "到哪一步",
    "有货", "缺货", "到货", "没货", "现货", "存量", "余量", "库存量",
    "还有", "还剩", "剩下", "够不够", "够用", "还够", "够吗",
)

# `货` and `系统里` name the same things the other entries do - the goods a
# stock question is about, and the place a live value is read from.
#
# `存量`/`余量`/`现货` appear here as well as in SYSTEM_VALUE_MARKERS, and that
# is deliberate rather than an oversight: they name the object and its value in
# one word, so `现在的存量` is a complete lookup with no second noun to supply.
# The pairing rule is unchanged for every ambiguous term.
#
# All of these stay as weak as the rest: a policy noun in the clause still
# wins, which is what keeps `退货办法怎么规定` on the document path.
SYSTEM_OBJECT_MARKERS = (
    "订单", "库存", "余额", "物流", "审批", "工单", "账户", "积分", "额度",
    "排班", "考勤", "申请记录",
    "货", "系统里", "系统中", "系统上", "后台",
    "存量", "余量", "库存量", "现货", "备货", "在库",
    # `存货` reads as one word, so the ambiguous single `货` never fires on it:
    # `请给我此时的存货数量` named the stock and still missed System entirely.
    "存货", "存货量", "货量", "剩余数量", "剩多少",
)

# The stock nouns specifically. A demand for precision that sits next to one of
# these is asking for an accurate *reading*, not for the text of a policy - see
# `_precision_describes_stock`.
STOCK_NOUNS = (
    "库存量", "库存", "存货量", "存货", "存量", "余量", "现货", "在库", "备货",
    "货量", "货",
)

# Unambiguous state questions that name no object but clearly ask about the
# caller's current standing.
SYSTEM_STATE_PHRASES = (
    "是否到账", "是否通过", "我当前是否符合", "到哪一步了", "办到哪了",
)

FRESHNESS_MARKERS = (
    "当前", "现在", "实时", "目前", "最新", "截至",
    # `此时`/`眼下`/`这会儿` are the same "as of now" the others name.
    "此时", "此刻", "眼下", "这会儿", "当下",
)

# Exposed for introspection, including the vocabulary-hygiene test.
MARKER_TABLES: dict[str, tuple[str, ...]] = {
    "DIRECT_MARKERS": DIRECT_MARKERS,
    "SOCIAL_MARKERS": SOCIAL_MARKERS,
    "SOCIAL_CLOSING_MARKERS": SOCIAL_CLOSING_MARKERS,
    "SOCIAL_THANKS_MARKERS": SOCIAL_THANKS_MARKERS,
    "SOCIAL_WISH_MARKERS": SOCIAL_WISH_MARKERS,
    "SOCIAL_TIME_FILLER": SOCIAL_TIME_FILLER,
    "QUESTION_CUES": QUESTION_CUES,
    "REQUEST_CUES": REQUEST_CUES,
    "WIKI_OVERVIEW_VERBS": WIKI_OVERVIEW_VERBS,
    "WIKI_OVERVIEW_SCOPE": WIKI_OVERVIEW_SCOPE,
    "WIKI_OVERVIEW_SOFT": WIKI_OVERVIEW_SOFT,
    "NEGATION_CUES": NEGATION_CUES,
    "NEGATED_SUPPLY_VERBS": NEGATED_SUPPLY_VERBS,
    "WIKI_OVERVIEW_MARKERS": WIKI_OVERVIEW_MARKERS,
    "DOCUMENT_EXACT_MARKERS": DOCUMENT_EXACT_MARKERS,
    "DOCUMENT_PRECISION_MARKERS": DOCUMENT_PRECISION_MARKERS,
    "DOCUMENT_AUTHORITY_MARKERS": DOCUMENT_AUTHORITY_MARKERS,
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

# `总结` is a verb *and* a noun, and only the verb asks for an overview.
# `总结制度变化` requests one; `培训总结提交期限的原文` is asking about a document
# called a summary, and treating it as a request pulled a Wiki step into a
# pure source-text question. The verb reading opens the clause or is introduced
# by an asking word; the noun reading is preceded by its own topic.
SUMMARY_VERB_PATTERN = re.compile(
    r"(?:^|[^一-鿿]|[帮请先再来给我])总结|总结(?:一下|下|一遍)"
)

# `第三条`, `第 12 条`, `第十二条`.
CLAUSE_NUMBER_PATTERN = re.compile(r"第\s*[0-9〇零一二三四五六七八九十百千]+\s*条")

# `那条`, `这几条`, `那句`, `那一段`. A demonstrative in front of a clause/sentence
# noun points at one piece of the source text without naming its number, which
# is how people refer to a clause they have just described in their own words.
#
# Only the pronominal reading counts. `条`/`段` are also ordinary measure words,
# and `这条线` classifies a line rather than quoting one, so the phrase must end
# the clause or be followed by a particle or pronoun - never by the noun it
# would otherwise be counting.
CLAUSE_REFERENCE_PATTERN = re.compile(
    r"[那这][一几]?\s*[条句段](?![一-鿿])"
    r"|[那这][一几]?\s*[条句段](?=[的我你您他她它们])"
)

# `由谁签字`, `谁审批`, `找谁批`. An interrogative about the approving party is a
# question about the rule, so the verbs are limited to ones that grant or
# withhold approval. `谁在处理我的订单` is deliberately outside this set: that
# names a record's current handler, which is a System question.
AUTHORITY_QUESTION_PATTERN = re.compile(
    r"谁[^，。；！？,;!?、\n]{0,3}?(?:签字|签批|签|批准|审批|批|审核|审|核准|核|负责|决定|授权|同意|把关)"
)

# `几天`, `几个工作日`, `多久`, `多长时间`. A duration or measure interrogative
# asks for a threshold the document sets. The unit is required: bare `几` is an
# ordinary question cue and says nothing about where the answer lives.
#
# The unit must also be a *measure*. The generic classifier `个` was tried and
# removed: `覆盖哪几个方面` counts topics in an overview, so treating it as a
# threshold pulled a document step into a plain Wiki request.
QUANTITY_INTERROGATIVE_PATTERN = re.compile(
    r"几\s*(?:天|日|小时|分钟|周|个?月|年|个?工作日|次|元|块|件|箱)"
    r"|多久|多长时间|多少天|多少小时"
)

# `走完了没`, `批下来了吗`, `办好了没有`. A completion interrogative asks where a
# specific record currently stands, so it counts as state intent - weakly, like
# the other state cues, and still subject to the policy-noun override.
COMPLETION_STATE_PATTERN = re.compile(
    r"(?:走完|办完|批完|审完|跑完|处理完|办好|批好|完成|结束|通过|下来|到位)"
    r"\s*(?:了)?\s*(?:没有|没|吗|嘛)"
)

# A record identifier: an alphabetic prefix joined to a number, as in
# `APR-3001` or `ORD-1002`. Unlike SKU_PATTERN it cannot identify its own
# domain, so it never selects System by itself - it only supplies the *which
# record* half that the state cues leave open. `ABC-123 是什么意思` asks what a
# token means and names no state, so it stays off the System path.
RECORD_ID_PATTERN = re.compile(r"(?<![a-z0-9])[a-z]{2,10}[-_]\d{3,8}(?![a-z0-9])")

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


def _negated_at(text: str, index: int, end: int) -> bool:
    """Is the marker spanning `[index, end)` inside a refusal of it?

    Both directions are checked. Backwards is the ordinary `不用抠原文`; forwards
    is `条款先别给我`, where the object comes first and the refusal lands on the
    verb that would have supplied it.
    """
    window = text[max(0, index - NEGATION_WINDOW) : index]
    # A negation never reaches across a clause boundary.
    for delimiter in _CLAUSE_DELIMITERS:
        cut = window.rfind(delimiter)
        if cut >= 0:
            window = window[cut + 1 :]
    if any(cue in window for cue in NEGATION_CUES):
        return True
    if _BIE_NEGATION_PATTERN.search(window):
        return True

    trailing = text[end:]
    for delimiter in _CLAUSE_DELIMITERS:
        cut = trailing.find(delimiter)
        if cut >= 0:
            trailing = trailing[:cut]
    if _FORWARD_NEGATION_PATTERN.search(trailing):
        return True
    return bool(_POST_DISMISSAL_PATTERN.search(trailing))


def _contains_any_unnegated(text: str, markers: tuple[str, ...]) -> bool:
    """Like `_contains_any`, but a marker the caller declined does not count."""
    for marker in markers:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            if not _negated_at(text, index, index + len(marker)):
                return True
            start = index + 1
    return False


def _search_unnegated(text: str, pattern: re.Pattern[str]) -> bool:
    """`pattern.search`, ignoring matches the caller declined."""
    for match in pattern.finditer(text):
        if not _negated_at(text, match.start(), match.end()):
            return True
    return False


def _strip_social(clause: str) -> str:
    residue = clause
    for marker in SOCIAL_MARKERS + SOCIAL_TIME_FILLER:
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
    if SUMMARY_VERB_PATTERN.search(clause):
        return True
    if _contains_any(clause, VERSION_CHANGE_MARKERS):
        return True
    if _contains_any_unnegated(clause, DOCUMENT_EXACT_MARKERS):
        return True
    for table in (
        DOCUMENT_PRECISION_MARKERS,
        DOCUMENT_AUTHORITY_MARKERS,
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
    if AUTHORITY_QUESTION_PATTERN.search(clause) or QUANTITY_INTERROGATIVE_PATTERN.search(clause):
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


def _precision_describes_stock(clause: str) -> bool:
    """Is `准确` describing a stock reading rather than demanding source text?

    `存量准确数` and `准确库存` ask for the live number to be exact. That is a
    System request; the caller is not asking what a policy document says. But
    `报一个准确天数` next to `请引用请假原文` is exactly that demand, so the
    precision marker only loses its document pull when a stock noun is sitting
    right beside it - either side, within a few characters, and only when the
    clause names no document of its own.
    """
    if _contains_any(clause, DOCUMENT_POLICY_MARKERS) or _contains_any(
        clause, DOCUMENT_EXACT_MARKERS
    ):
        return False
    for marker in DOCUMENT_PRECISION_MARKERS:
        start = clause.find(marker)
        while start >= 0:
            window = clause[max(0, start - PRECISION_WINDOW) : start + len(marker) + PRECISION_WINDOW]
            if _contains_any(window, STOCK_NOUNS):
                return True
            start = clause.find(marker, start + 1)
    return False


def _clause_signals(clause: str) -> _ClauseSignals:
    version_change = _contains_any_unnegated(clause, VERSION_CHANGE_MARKERS)

    strong_document = (
        _contains_any_unnegated(clause, DOCUMENT_EXACT_MARKERS)
        or (
            _contains_any_unnegated(clause, DOCUMENT_PRECISION_MARKERS)
            and not _precision_describes_stock(clause)
        )
        or _contains_any_unnegated(clause, DOCUMENT_AUTHORITY_MARKERS)
        or _search_unnegated(clause, CLAUSE_REFERENCE_PATTERN)
        or _search_unnegated(clause, AUTHORITY_QUESTION_PATTERN)
        or _contains_any(clause, DOCUMENT_CONDITION_MARKERS)
        or _contains_any(clause, DOCUMENT_COMPARISON_MARKERS)
        or bool(CLAUSE_NUMBER_PATTERN.search(clause))
    )
    quantity_document = _contains_any(
        clause, DOCUMENT_QUANTITY_MARKERS
    ) or bool(QUANTITY_INTERROGATIVE_PATTERN.search(clause))
    policy_context = _contains_any_unnegated(clause, DOCUMENT_POLICY_MARKERS)

    has_sku = bool(SKU_PATTERN.search(clause))
    has_system_object = _contains_any(clause, SYSTEM_OBJECT_MARKERS)
    state_phrase = _contains_any(clause, SYSTEM_STATE_PHRASES)
    personal_state = _contains_any(clause, PERSONAL_STATE_MARKERS)
    # An identifier says *which* record. It is the counterpart of the object
    # noun, not of the state cue: `ord-1002 现在什么状态` names no object noun
    # at all, yet it is plainly a lookup.
    has_record_id = bool(RECORD_ID_PATTERN.search(clause))
    # Any of these alone only hints at a state question; none of them can
    # outweigh policy semantics on its own.
    weak_state_intent = (
        _contains_any(clause, TIME_STATE_MARKERS)
        or _contains_any(clause, SYSTEM_QUERY_VERBS)
        or _contains_any(clause, SYSTEM_VALUE_MARKERS)
        or bool(COMPLETION_STATE_PATTERN.search(clause))
        # "As of now" is a live-value cue in its own right: `此时的存货数量`
        # names the stock and pins it to this moment, with no query verb at all.
        # It stays weak - the `not policy_context` guard below is what keeps
        # `现在的订单管理制度怎么规定` on the document path.
        or _contains_any(clause, FRESHNESS_MARKERS)
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
    elif (has_system_object or has_record_id) and weak_state_intent and not policy_context:
        # `帮我查一下账户余额`, `账户余额是多少`, `当前库存还有多少`,
        # `ord-1002 现在什么状态`.
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
    wiki_strong = _contains_any_unnegated(clause, WIKI_OVERVIEW_STRONG) or (
        _search_unnegated(clause, SUMMARY_VERB_PATTERN)
    )
    wiki_soft = _contains_any_unnegated(clause, WIKI_OVERVIEW_SOFT)
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


def document_focus(question: str) -> str:
    """The part of `question` a source-document search should actually read.

    A compound request routinely carries a clause the documents cannot answer:
    `设备遗失几小时内要上报？Sku-C300 当前存量报我一下` asks a policy question and
    a live-stock question in one breath. Handed whole to retrieval, the stock
    clause is decomposed alongside the policy one and is allocated an evidence
    slot of its own, which it then fills with whatever chunk happens to score
    highest - a section about working hours, in that example. The policy
    evidence is not outranked; it is simply crowded out of the budget.

    So System-only clauses are dropped, along with pure pleasantries. Wiki-only
    clauses are kept: they name the topic, which helps rather than hurts a
    lexical search.

    Pure text surgery - no plan, no tool, no model, and the same string out for
    the same string in. When there is nothing to drop the original is returned
    **unchanged**, so the ordinary single-question path is byte-for-byte what it
    was before.
    """
    if not question or not question.strip():
        raise ValueError("question must not be blank")

    kept: list[str] = []
    dropped = False
    for part in _CLAUSE_SPLIT_PATTERN.split(question):
        stripped = part.strip()
        if not stripped:
            continue
        clause = _normalize(stripped).strip(_EDGE_PUNCTUATION)
        if not clause:
            continue
        if _is_social_clause(clause) or _is_system_only_clause(clause):
            dropped = True
            continue
        kept.append(stripped.strip(_EDGE_PUNCTUATION) or stripped)

    if not dropped or not kept:
        # Nothing to remove, or removing everything - either way the caller is
        # better served by the request it actually made.
        return question
    return "，".join(kept)


def _is_system_only_clause(clause: str) -> bool:
    """Does this clause ask the business system, and nothing else?"""
    signals = _clause_signals(clause)
    if not signals.needs_system:
        return False
    return not (
        signals.exact_document
        or signals.policy_forces_document
        or signals.version_change
        or signals.wiki_overview
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
