"""Reproduce the two R5 defects from the fifth acceptance review.

A (a regression I introduced in rework 5): the trailing-denial branch checked
only the gap between the numeral and the denial cue, never what the cue was
denying. `99天没有问题` denies a *problem* and affirms the 99 days, but an
empty gap was accepted as proof of attachment.

B (a residual): `is_pure_refusal()` split on full stops and asked each piece to
contain a refusal substring, so swapping the full stop for a comma, a semicolon
or a contrastive connective put an uncited factual claim back through.

Deterministic: the Ollama HTTP responses are scripted. Routing, generation
handling, the bounded recheck, delivery, the SSE protocol and SQLite are
product code. This measures code paths, not how often a real model writes these.

Run from an isolated source copy:  python docs/m10-selftest/repro_rework_6.py
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
    ("post_denial_of_problem", False,
     "正式员工每年休99天没有问题。[来源1]",
     "`没有` 否定的是「问题」，整句在肯定可以休 99 天"),
    ("post_denial_of_restriction", False,
     "正式员工每年休99天没有额外限制。[来源1]",
     "`没有` 否定的是「额外限制」，不是这个数量"),
    ("mixed_refusal_comma", False,
     "根据现有资料无法确定奖金金额，但正式员工入职满一年后每年享有5天带薪年假。",
     "逗号加转折把拒答与无引用的事实断言连在一起"),
    ("mixed_refusal_semicolon", False,
     "根据现有资料无法确定奖金金额；正式员工入职满一年后每年享有5天带薪年假。",
     "把句号换成分号并不会让那句事实断言消失"),
    ("mixed_refusal_reversed", False,
     "正式员工入职满一年后每年享有5天带薪年假，但根据现有资料无法确定奖金金额。",
     "无引用的事实断言排在拒答前面"),
    ("supported_permission", True,
     "正式员工入职满一年后每年休5天没有问题。[来源1]",
     "同一句式，数量有依据且有引用"),
    ("actual_post_denial", True,
     "你说的99天并不正确，原文写的是5天。[来源1]",
     "后置否定确实在否定这个数值说法的正确性"),
    ("comma_only_refusals", True,
     "根据现有资料无法确定奖金金额，也无法确定补贴标准。",
     "逗号连接的纯拒答，没有事实需要引用"),
    ("cited_mixed_comma", True,
     "根据现有资料无法确定奖金金额，但正式员工入职满一年后每年享有5天带薪年假。[来源1]",
     "同一混合回答补上引用后可交付"),
    ("supported_hypothesis_form", True,
     "假设按99天计算，也需要核对原文。[来源1]",
     "明确的待核实假设，第五轮已恢复"),
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
    api.storage = SQLiteStorage(Path(temp.name) / "repro6.db")
    run = uuid4().hex[:8]

    passed = True
    head("R5-A 后置否定的对象；R5-B 纯拒答需覆盖整段含义")
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
