"""M10 rework 9: a channel the caller waved away must not be selected.

Three route regressions, three different causes:

1. `具体条文我等下自己翻` declines the source text by taking it on yourself.
   Only outright dismissals (`免了`, `不用了`) were recognised, so the document
   step was still added.
2. `总览先不用` defers instead of dismissing - `不用` with no `了` - and the
   overview step survived.
3. `请分别给：信息安全概述、…` lost the Wiki step entirely, because `别` is a
   negation cue matched as a bare substring and `分别` contains it. A
   one-character cue was reading the tail of an unrelated word as `别给`.

Written against behaviour: the tables below vary clause order, punctuation, an
inserted aside, the topic entity and where the refusal sits, so a rule that only
fires on the sentence it was written against shows up as a failure here.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from orchestration.planner import Route, plan_request

REPO_ROOT = Path(__file__).resolve().parent.parent


def route_of(question: str) -> Route:
    return plan_request(question).route


# Declining the source text by saying you will handle it yourself.
SELF_SERVICE_DECLINES = (
    "数据备份这块先给个整体印象，具体条文我等下自己翻",
    "数据备份先讲讲整体，原文我自己查",
    "先说说信息安全的大致情况，条款我回头自己看",
    "把考勤这块整体介绍一下，细则我来翻",
)

# Deferring a channel: `不用` with no `了`.
DEFERRED_DECLINES = (
    "我在写制度摘要，总览先不用——境外出差需要谁审批，按原文说",
    "总览先不用，报销单笔上限的原文发我",
    "整体介绍就不用，直接给年假天数的准确条款",
    "概述暂不需要，只要值班津贴那条原样条文",
)

# A list-style request naming all three channels.
THREE_CHANNEL_LISTS = (
    "请分别给：信息安全概述、公共网盘限制的原文、SKU-A100 当前存货。",
    "请分别给：考勤概述、迟到扣款的原文、SKU-A100 当前库存。",
    "麻烦分别给出：报销概述、单笔上限原文、SKU-B200 的存量。",
)


class SelfServiceDeclineTests(unittest.TestCase):
    """Taking a channel on yourself is declining it."""

    def test_the_declined_document_step_is_not_selected(self):
        for question in SELF_SERVICE_DECLINES:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_ONLY)

    def test_the_same_request_without_the_decline_keeps_the_document(self):
        """Control: drop the trailing clause and the document step returns."""
        self.assertEqual(
            route_of("数据备份这块先给个整体印象，具体条文也发我"), Route.WIKI_DOCUMENT
        )

    def test_wanting_to_read_it_yourself_is_not_declined_when_it_is_the_request(self):
        """`我自己看原文` asks *for* the source, and `自己看` must not cancel it."""
        self.assertEqual(route_of("把年假条款的原文发我，我自己看"), Route.DOCUMENT_ONLY)


class DeferredDeclineTests(unittest.TestCase):
    """`先不用` declines just as `不用了` does."""

    def test_a_deferred_overview_is_not_selected(self):
        for question in DEFERRED_DECLINES:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.DOCUMENT_ONLY)

    def test_the_same_sentence_asking_for_both_keeps_the_overview(self):
        self.assertEqual(
            route_of("总览也给我，境外出差需要谁审批，按原文说"), Route.WIKI_DOCUMENT
        )

    def test_the_dash_separates_the_decline_from_the_request(self):
        """The refusal binds to `总览`, not to the question after the dash."""
        self.assertEqual(route_of("总览先不用——考勤这块整体讲讲"), Route.WIKI_ONLY)


class ListedThreeChannelTests(unittest.TestCase):
    """A `分别给` list must not lose a channel to a word-internal `别`."""

    def test_all_three_channels_are_selected(self):
        for question in THREE_CHANNEL_LISTS:
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_DOCUMENT_SYSTEM)

    def test_the_order_of_the_listed_items_does_not_matter(self):
        for question in (
            "请分别给：SKU-A100 当前存货、信息安全概述、公共网盘限制的原文。",
            "请分别给：公共网盘限制的原文、SKU-A100 当前存货、信息安全概述。",
        ):
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_DOCUMENT_SYSTEM)

    def test_the_punctuation_between_items_does_not_matter(self):
        for question in (
            "请分别给：信息安全概述、公共网盘限制的原文、SKU-A100 当前存货。",
            "请分别给信息安全概述，公共网盘限制的原文，SKU-A100 当前存货。",
            "请分别给信息安全概述；公共网盘限制的原文；SKU-A100 当前存货。",
        ):
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_DOCUMENT_SYSTEM)

    def test_an_inserted_aside_does_not_drop_a_channel(self):
        self.assertEqual(
            route_of("我在准备周会材料，请分别给：信息安全概述、公共网盘限制的原文、SKU-A100 当前存货。"),
            Route.WIKI_DOCUMENT_SYSTEM,
        )


class BareNegationBieTests(unittest.TestCase):
    """`别` negates only when it stands alone."""

    def test_a_listed_overview_request_keeps_its_wiki_step(self):
        """`分别介绍` asks for an overview of each topic.

        Kept separate because it is the one control here that *detects* the
        word-internal match on its own: the others below assert the right route,
        but a policy noun in the same clause selects the document path anyway,
        so they stay green even when `别` is matching inside `分别`.
        """
        self.assertEqual(route_of("请分别介绍考勤和年假这两块"), Route.WIKI_ONLY)

    def test_words_that_merely_contain_bie_are_not_negations(self):
        for question, expected in (
            ("请分别给年假原文", Route.DOCUMENT_ONLY),
            ("请按类别给出报销原文", Route.DOCUMENT_ONLY),
            ("这两者有什么区别？按原文说", Route.DOCUMENT_ONLY),
            ("特别是境外出差那条，原文发我", Route.DOCUMENT_ONLY),
            ("不同级别的审批权限，原文怎么写", Route.DOCUMENT_ONLY),
        ):
            with self.subTest(question=question):
                self.assertEqual(route_of(question), expected)

    def test_a_standing_bie_still_negates(self):
        """The other side of the pair: the real refusal must keep working."""
        for question in (
            "别给我原文，讲讲整体",
            "具体条文先别给我，只要概览",
            "请别列条款，说说大概",
            "原文你千万别贴，整体介绍一下",
        ):
            with self.subTest(question=question):
                self.assertEqual(route_of(question), Route.WIKI_ONLY)


class DeclinedChannelIndependenceTests(unittest.TestCase):
    """This module must not restate the implementation it checks."""

    def test_only_the_public_planner_surface_is_imported(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "orchestration"
            ):
                imported.update(alias.name for alias in node.names)
        self.assertEqual(imported, {"Route", "plan_request"})


if __name__ == "__main__":
    unittest.main()
