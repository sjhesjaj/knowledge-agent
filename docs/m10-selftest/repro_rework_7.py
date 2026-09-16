"""Reproduce the two R5 defects from the sixth acceptance review.

Both are the same mistake in two places: a *partial* match treated as proof of
a complete structure.

A (a regression I introduced in rework 6): `_REFUTATION_PATTERN` matched `不对`
inside `不对第三方公开` and `有误` inside `有误工补贴`. Neither denies the
number - one says "not disclosed to third parties", the other says a subsidy
exists - but the prefix matched and the exemption was granted.

B (a residual): `is_pure_refusal()` asked each assertion segment to *contain* a
refusal phrase and only rejected a quantity standing before it. A fact joined by
bare `而`, or a fact carrying no number at all, still slipped through.

Deterministic: the Ollama HTTP responses are scripted. Routing, generation
handling, the bounded recheck, delivery, the SSE protocol and SQLite are
product code. This measures code paths, not how often a real model writes these.

Run from an isolated source copy:  python docs/m10-selftest/repro_rework_7.py
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
    ("prefix_not_a_refutation_bu_dui", False,
     "公司规定的99天不对第三方公开。[来源1]",
     "`不对第三方公开` 是不向第三方披露，`不对` 只是它的前缀"),
    ("prefix_not_a_refutation_you_wu", False,
     "正式员工每年休99天有误工补贴。[来源1]",
     "`有误工补贴` 是存在某种补贴，`有误` 只是它的前缀"),
    ("mixed_refusal_er_fact_after", False,
     "奖金金额无法确定而正式员工入职满一年后每年享有5天带薪年假。",
     "事实排在拒答之后、由 `而` 连接，仍是无引用的事实断言"),
    ("mixed_refusal_no_number", False,
     "正式员工享有带薪年假这点已确定只是奖金金额无法确定。",
     "被肯定的事实不含数字，同样需要引用"),
    ("mixed_refusal_fact_first", False,
     "正式员工入职满一年后每年享有5天带薪年假而奖金金额无法确定。",
     "同样的事实排在拒答之前"),
    ("refutation_bu_dui", True,
     "你说的99天不对，原文写的是5天。[来源1]",
     "完整的驳斥：`不对` 之后就是句读"),
    ("refutation_you_wu", True,
     "你说的99天有误，原文写的是5天。[来源1]",
     "完整的驳斥：`有误` 之后就是句读"),
    ("cited_mixed_er", True,
     "奖金金额无法确定而正式员工入职满一年后每年享有5天带薪年假。[来源1]",
     "同一混合回答补上引用后可交付"),
    ("comma_only_refusals", True,
     "根据现有资料无法确定奖金金额，也无法确定补贴标准。",
     "逗号连接的纯拒答，没有事实需要引用"),
    ("endorsement_names_the_latter", True,
     "不是99天。5天才对。[来源1]",
     "明确纠正，背书认可的是有依据的 5"),
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
    api.storage = SQLiteStorage(Path(temp.name) / "repro7.db")
    run = uuid4().hex[:8]

    passed = True
    head("R5-A 驳斥必须是完整表达；R5-B 纯拒答必须正向覆盖整段")
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
                print(f"         {body}")
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
