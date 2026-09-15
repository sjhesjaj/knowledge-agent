"""Reproduce the five defects from the first independent acceptance review.

Run before and after the fix. Offline for R3/R4/R5; R1/R2 script the model
response so no real inference is involved.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Runnable from anywhere: the product modules live at the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import rag
from orchestration.planner import Route, plan_request
from rag import Chunk

LEAVE = Chunk(
    text="## 请假制度\n正式员工入职满一年后，每年享有 5 天带薪年假。",
    source="rules.md",
    index=1,
)
STOCK = Chunk(text="## 库存\n库存为42件。", source="rules.md", index=1)
ONE = [(LEAVE, 3.5)]
STOCK_ONE = [(STOCK, 3.5)]


class Scripted:
    """One non-streaming Ollama reply, optionally malformed JSON."""

    def __init__(self, body: str, *, raw: bool = False):
        self.body = body
        self.raw = raw

    def raise_for_status(self):
        return None

    def json(self):
        content = self.body if self.raw else json.dumps({"answer": self.body}, ensure_ascii=False)
        return {"message": {"content": content}}


def head(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def r1():
    head("R1  JSON 解析失败的回退答案是否绕过交付校验")
    # 1st reply: not JSON at all -> triggers the plain-text fallback.
    # 2nd reply: what answer() returns - a fabricated 99 days citing source 99.
    bogus = "正式员工入职满一年后，每年享有 99 天带薪年假。[来源99]"
    with patch.object(rag.requests, "post", side_effect=[Scripted("not json at all", raw=True),
                                                         Scripted(bogus)]):
        delivered = rag.answer_structured("年假有多少天", ONE, [])
    verdict = rag.validate_answer(bogus, ONE, "年假有多少天")
    print(f"  validate_answer(bogus) = {verdict}")
    print(f"  answer_structured delivered = {delivered!r}")
    leaked = "99" in delivered
    print(f"  RESULT: {'FAIL - bogus answer delivered' if leaked else 'OK - refused'}")
    return not leaked


def r2():
    head("R2  相同脚本化输出下，普通接口与 SSE 的最终正文/终态是否一致")
    bogus = "每年享有 99 天带薪年假。[来源1]"

    with patch.object(rag.requests, "post", side_effect=[Scripted(bogus), Scripted(bogus)]):
        plain = rag.answer_structured("年假有多少天", ONE, [])

    class ScriptedStream:
        def __init__(self, text):
            payload = json.dumps({"answer": text}, ensure_ascii=False)
            self.lines = [json.dumps({"message": {"content": payload[i:i + 4]}})
                          for i in range(0, len(payload), 4)]
            self.lines.append(json.dumps({"message": {"content": ""}, "done": True}))

        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=False): return iter(self.lines)

    emitted, error = [], None
    # The stream now runs the same bounded recheck the plain path runs, so the
    # second (non-streaming) response has to be scripted too - identical text,
    # so neither interface gets a different second chance.
    with patch.object(
        rag.requests, "post", side_effect=[ScriptedStream(bogus), Scripted(bogus)]
    ):
        try:
            for part in rag.answer_stream("年假有多少天", ONE, []):
                emitted.append(part)
        except Exception as exc:
            error = type(exc).__name__
    shown = "".join(emitted)
    print(f"  plain  final body : {plain!r}")
    print(f"  stream shown text : {shown!r}")
    print(f"  stream terminated : {error}")
    consistent = (shown.strip() == plain.strip()) and error is None
    print(f"  RESULT: {'OK - identical final body' if consistent else 'FAIL - divergent'}")
    return consistent


ROUTE_CASES = (
    ("我不需要概览，只核对培训总结提交期限的原文。", Route.DOCUMENT_ONLY),
    ("不用介绍全貌，直接引用报销单笔金额的审批分界。", Route.DOCUMENT_ONLY),
    ("我只要 apr-3002 当前审批状态，不用解释审批制度。", Route.SYSTEM_ONLY),
    ("明白，暂时没别的需求，回见。", Route.DIRECT),
)


def r3():
    head("R3  否定范围与寒暄收尾")
    ok = True
    for question, expected in ROUTE_CASES:
        actual = plan_request(question).route
        good = actual == expected
        ok &= good
        print(f"  [{'OK  ' if good else 'FAIL'}] {question}")
        print(f"         expected={expected.value}  actual={actual.value}")
    return ok


def r4():
    head("R4  中文数值归一化")
    print(f"  _cn_number_to_arabic('四十二') = {rag._cn_number_to_arabic('四十二')!r}")
    ungrounded = rag.ungrounded_quantities("库存为四十二件。[来源1]", STOCK_ONE, "库存多少件")
    print(f"  ungrounded_quantities = {ungrounded}")
    ok = not ungrounded
    print(f"  RESULT: {'OK - accepted' if ok else 'FAIL - correct answer rejected'}")
    return ok


def r5():
    head("R5  问句里的猜测数字是否被当成支持依据")
    question = "正式员工入职满一年后，每年有 99 天年假吗？请核对原文"
    asserted = "正式员工入职满一年后，每年享有 99 天带薪年假。[来源1]"
    verdict = rag.validate_answer(asserted, ONE, question)
    print(f"  断言猜测值        : validate={verdict}")
    caught = verdict[0] is False
    print(f"  RESULT: {'OK - rejected' if caught else 'FAIL - guess accepted as fact'}")

    corrected = "不是 99 天。正式员工每年享有 5 天带薪年假。[来源1]"
    v2 = rag.validate_answer(corrected, ONE, question)
    print(f"  来源否定猜测(应放行): validate={v2}  -> {'OK' if v2[0] else 'FAIL'}")

    conditional = "如果按你说的 99 天计算，资料并未这样规定；原文写的是 5 天。[来源1]"
    v3 = rag.validate_answer(conditional, ONE, question)
    print(f"  条件复述(应放行)   : validate={v3}  -> {'OK' if v3[0] else 'FAIL'}")
    return caught and v2[0] and v3[0]


if __name__ == "__main__":
    results = {"R1": r1(), "R2": r2(), "R3": r3(), "R4": r4(), "R5": r5()}
    head("SUMMARY")
    for key, passed in results.items():
        print(f"  {key}: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
