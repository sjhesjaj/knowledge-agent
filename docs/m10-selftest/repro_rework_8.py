"""Reproduce the two R5 residuals from the seventh acceptance review.

A: the refutation's end was checked against a *truncated* window. `following`
stops before the next quantity, so `text[end:following]` was just `不对` and the
regex `$` matched - while the real sentence went on `5天内入职的员工公开`.
Whitespace had the same problem from the other side: `\\s` was listed as a legal
terminator, so `不对 第三方公开` and `有误\\t工补贴` were approved too.

B: `topic`/`about` accepted any short span that contained no quantity and no
listed breaker word. Character count and "no known bad word found" are not
evidence that a span is a noun topic, so short facts on either side of the
refusal were swallowed - and a negated refusal predicate (`不是无法确定…`) still
qualified as a pure refusal.

Deterministic: the Ollama HTTP responses are scripted. Routing, generation
handling, the bounded recheck, delivery, the SSE protocol and SQLite are
product code. This measures code paths, not how often a real model writes these.

Run from an isolated source copy:  python docs/m10-selftest/repro_rework_8.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover
    pass

import api
import chat_orchestration as co
import rag
from fastapi.testclient import TestClient
from rag import Chunk
from storage import SQLiteStorage

SOURCE = "正式员工入职满一年后，每年享有5天带薪年假。"
QUESTION = "正式员工入职满一年后每年有99天年假吗？请核对原文。"
CHUNK = Chunk(text=SOURCE, source="sample_company_rules.md", index=1)
EVIDENCE = [(CHUNK, 1.0)]
CORRECTED = "正式员工入职满一年后每年享有5天带薪年假。[来源1]"

# (name, expected_deliverable, answer, why)
CASES = (
    # --- A: the boundary must be judged on the full text ------------------
    ("window_end_faked_by_next_quantity", False,
     "公司规定的99天不对5天内入职的员工公开。[来源1]",
     "下一个数量 `5天` 把检查窗口切断，`不对` 后面其实还有 `5天内入职的员工公开`"),
    ("space_is_not_a_predicate_end", False,
     "公司规定的99天不对 第三方公开。[来源1]",
     "空格之后仍是同一个表达的宾语"),
    ("tab_is_not_a_predicate_end", False,
     "正式员工每年休99天有误\t工补贴。[来源1]",
     "制表符之后仍是同一个词的词尾"),
    # --- B: a topic must be positively recognised -------------------------
    ("fact_before_via_zhiyu", False,
     "年假带薪至于奖金无法确定。",
     "`年假带薪` 是断言，只对奖金拒答"),
    ("fact_after_via_ling", False,
     "奖金无法确定另年假为带薪。",
     "拒答之后又断言 `年假为带薪`"),
    ("negated_refusal_predicate", False,
     "不是无法确定年假是带薪的。",
     "拒答谓语本身被否定，这不是拒答"),
    ("short_fact_before", False,
     "员工有年假至于奖金无法确定。",
     "短事实同样是事实"),
    ("short_fact_after", False,
     "奖金无法确定员工有年假。",
     "同上，顺序相反"),
    ("juxtaposed_fact", False,
     "年假带薪奖金无法确定。",
     "没有连接词，仍是事实加拒答"),
    # --- positives that must survive --------------------------------------
    ("refutation_bu_dui", True,
     "你说的99天不对，原文写的是5天。[来源1]",
     "真正的驳斥：`不对` 之后就是句读"),
    ("refutation_bu_dui_space_before_punctuation", True,
     "你说的99天不对 ，原文写的是5天。[来源1]",
     "空白可以出现在真实句读之前"),
    ("refutation_you_wu", True,
     "你说的99天有误，原文写的是5天。[来源1]",
     "同上"),
    ("refutation_bing_bu_zhengque", True,
     "你说的99天并不正确，原文写的是5天。[来源1]",
     "既有驳斥正例"),
    ("correction_then_endorsed_latter", True,
     "不是99天。5天才对。[来源1]",
     "逐次数值判定与背书归属未受影响"),
    ("pure_refusal_topic_after", True,
     "无法确定奖金金额。",
     "拒答主题在谓语之后"),
    ("pure_refusal_topic_before", True,
     "奖金金额无法确定。",
     "拒答主题在谓语之前"),
    ("pure_refusal_not_mentioned", True,
     "原文中未提及奖金金额。",
     "另一种拒答谓语"),
    ("pure_refusal_insufficient", True,
     "资料不足，无法确定。",
     "两条拒答子句"),
    ("pure_refusal_no_record", True,
     "资料中没有相关记录，无法确定。",
     "同上"),
    ("pure_refusal_two_topics", True,
     "根据现有资料无法确定奖金金额，也无法确定补贴标准。",
     "逗号连接的两条拒答"),
    ("cited_mixed_zhiyu", True,
     "年假带薪至于奖金无法确定。[来源1]",
     "同一混合回答补上引用后可交付"),
    ("cited_mixed_short_fact", True,
     "奖金无法确定员工有年假。[来源1]",
     "同上"),
)


class Response:
    """One reply, usable as both the plain and the streaming response."""

    def __init__(self, body: str):
        self.raw = json.dumps({"answer": body}, ensure_ascii=False)

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.raw}}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=True):
        for i in range(0, len(self.raw), 4):
            yield json.dumps({"message": {"content": self.raw[i : i + 4]}})
        yield json.dumps({"done": True})


def head(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def sse_parts(response):
    deltas, terminal = [], None
    for block in response.text.split("\n\n"):
        event = next((s[7:] for s in block.splitlines() if s.startswith("event: ")), None)
        data = next((s[6:] for s in block.splitlines() if s.startswith("data: ")), None)
        if not event or not data:
            continue
        if event == "delta":
            deltas.append(json.loads(data).get("content", ""))
        elif event in {"done", "error"}:
            terminal = event
    return "".join(deltas), terminal


def drive(client, session, first, recheck):
    """Both interfaces, identical two-response script."""
    out = {}
    for mode, path in (("plain", "/api/chat"), ("sse", "/api/chat/stream")):
        replies = iter([Response(first), Response(recheck)])
        with patch.object(
            rag.requests, "post", side_effect=lambda *a, **k: next(replies)
        ) as mocked:
            response = client.post(path, json={
                "question": QUESTION, "mode": "orchestrated",
                "client_id": session, "session_id": f"{session}-{mode}",
            })
        if mode == "plain":
            visible, terminal = response.json()["answer"], "done"
        else:
            visible, terminal = sse_parts(response)
        messages = api.storage.get_messages(f"{session}-{mode}", session)
        out[mode] = {
            "visible": visible,
            "terminal": terminal,
            "calls": mocked.call_count,
            "stored": messages[-1]["content"] if messages else None,
        }
    return out


def main():
    prepared = co.Prepared(
        route="document_only",
        steps=["document_search"],
        outcome="answer",
        reason_codes=[],
        sources=[{"rank": 1, "type": "document", "source": CHUNK.source,
                  "locator": "section:请假制度", "content": SOURCE}],
        results_for_answer=EVIDENCE,
        executor_seconds=0.0,
    )

    temp = tempfile.TemporaryDirectory()
    original_storage = api.storage
    api.storage = SQLiteStorage(Path(temp.name) / "repro8.db")
    run = uuid4().hex[:8]

    passed = True
    head("R5-A 原文边界 vs 检查窗口末尾；R5-B 拒答主题的正向识别")
    print(f"  证据: {SOURCE}")
    print(f"  问句: {QUESTION}\n")
    try:
        with TestClient(api.app) as client, patch.object(co, "prepare", return_value=prepared):
            for index, (name, expected, body, why) in enumerate(CASES):
                verdict, reason = rag.validate_answer(body, EVIDENCE, QUESTION)

                # Both attempts return the same text: nothing may rescue it.
                same = drive(client, f"{run}s{index}", body, body)
                if expected:
                    good = (verdict
                            and all(same[m]["visible"] == body for m in same)
                            and all(same[m]["stored"] == body for m in same))
                else:
                    good = (not verdict
                            and all(body not in same[m]["visible"] for m in same)
                            and all(same[m]["stored"] != body for m in same))
                good &= same["plain"]["calls"] == same["sse"]["calls"] <= 2

                # For a blocked case, a correct recheck must still rescue it.
                rescued = None
                if not expected:
                    rescued = drive(client, f"{run}r{index}", body, CORRECTED)
                    good &= all(rescued[m]["visible"] == CORRECTED for m in rescued)
                    good &= all(rescued[m]["stored"] == CORRECTED for m in rescued)
                    good &= rescued["plain"]["calls"] == rescued["sse"]["calls"] <= 2
                passed &= good

                want = "可交付" if expected else "拦截"
                print(f"  [{'OK  ' if good else 'FAIL'}] 期望{want}：{name}")
                print(f"         {body!r}")
                print(f"         {why}")
                print(f"         validate={verdict}/{reason}")
                for mode in ("plain", "sse"):
                    item = same[mode]
                    print(f"         {mode:5s} visible={item['visible']!r}"
                          f" terminal={item['terminal']} calls={item['calls']}"
                          f" stored={item['stored']!r}")
                if rescued is not None:
                    print(f"         复查纠正后: plain={rescued['plain']['visible']!r}"
                          f" sse={rescued['sse']['visible']!r}"
                          f" calls={rescued['plain']['calls']}/{rescued['sse']['calls']}")
    finally:
        api.storage = original_storage
        temp.cleanup()

    head("SUMMARY")
    print(f"  R5: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
