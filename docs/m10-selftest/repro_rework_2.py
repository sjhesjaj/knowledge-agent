"""Reproduce the four residual defects from the second acceptance review.

Offline: every model response is scripted, so no inference runs.
Run from an isolated source copy:  python repro_rework_2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Runnable from anywhere: the product modules live at the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import rag
from rag import Chunk

LEAVE = [(Chunk(text="## 请假制度\n正式员工入职满一年后，每年享有 5 天带薪年假。",
                source="rules.md", index=1), 3.5)]
STOCK_234 = [(Chunk(text="## 库存\n库存为234件。", source="rules.md", index=1), 3.5)]
STOCK_34 = [(Chunk(text="## 库存\n库存为34件。", source="rules.md", index=1), 3.5)]
QUESTION = "年假有多少天"


class Reply:
    """One non-streaming reply. `raw=True` sends the text as-is."""

    def __init__(self, text: str, *, raw: bool = False):
        self.text, self.raw = text, raw

    def raise_for_status(self):
        return None

    def json(self):
        body = self.text if self.raw else json.dumps({"answer": self.text}, ensure_ascii=False)
        return {"message": {"content": body}}


class Stream:
    def __init__(self, text: str):
        payload = json.dumps({"answer": text}, ensure_ascii=False)
        self.lines = [json.dumps({"message": {"content": payload[i:i + 4]}})
                      for i in range(0, len(payload), 4)]
        self.lines.append(json.dumps({"message": {"content": ""}, "done": True}))

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def raise_for_status(self): return None
    def iter_lines(self, decode_unicode=False): return iter(self.lines)


def head(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def r2():
    head("R2  两接口是否共用生成后的复查与最终决定")
    wrong = "每年享有 99 天带薪年假。[来源1]"
    right = "每年享有 5 天带薪年假。[来源1]"
    ok = True
    for label, first in (("首轮错误", wrong), ("首轮误拒", "根据现有资料无法确定。")):
        with patch.object(rag.requests, "post", side_effect=[Reply(first), Reply(right)]):
            plain = rag.answer_structured(QUESTION, LEAVE, [])
        with patch.object(rag.requests, "post", side_effect=[Stream(first), Reply(right)]):
            stream = "".join(rag.answer_stream(QUESTION, LEAVE, []))
        good = plain == stream == right
        ok &= good
        print(f"  [{'OK  ' if good else 'FAIL'}] {label}：plain={plain!r}")
        print(f"           stream={stream!r}")
    return ok


def r5():
    head("R5  修饰关系是否对应到具体数量所在的分句")
    question = "每年有 99 天年假吗？请核对原文"
    must_flag = (
        "每年享有 99 天带薪年假，其中可以先休 5 天。[来源1]",
        "每年享有 99 天带薪年假。如果需要申请，请提交表单。[来源1]",
        "每年享有 99 天带薪年假，没有特殊限制。[来源1]",
        "不是 99 天，是 5 天；不过公司确实按 99 天执行。[来源1]",
    )
    must_allow = (
        "不是 99 天。正式员工每年享有 5 天带薪年假。[来源1]",
        "如果按你说的 99 天计算，资料并未这样规定；原文写的是 5 天。[来源1]",
    )
    ok = True
    for text in must_flag:
        flagged = bool(rag.ungrounded_quantities(text, LEAVE, question))
        ok &= flagged
        print(f"  [{'OK  ' if flagged else 'FAIL'}] 应拦下：{text[:46]}")
    for text in must_allow:
        allowed = not rag.ungrounded_quantities(text, LEAVE, question)
        ok &= allowed
        print(f"  [{'OK  ' if allowed else 'FAIL'}] 应放行：{text[:46]}")
    return ok


def r4():
    head("R4  百位是否接入真正的提取链")
    extracted = sorted(rag.quantities("库存为二百三十四件。"))
    print(f"  quantities('库存为二百三十四件。') = {extracted}")
    correct_accepted = not rag.ungrounded_quantities("库存为二百三十四件。[来源1]", STOCK_234, "库存多少")
    wrong_rejected = bool(rag.ungrounded_quantities("库存为二百三十四件。[来源1]", STOCK_34, "库存多少"))
    ok = extracted == [("234", "件")] and correct_accepted and wrong_rejected
    print(f"  来源 234 时正确答案放行 : {correct_accepted}")
    print(f"  来源 34 时错误 234 拦下 : {wrong_rejected}")
    print(f"  RESULT: {'OK' if ok else 'FAIL'}")
    return ok


def r1():
    head("R1  回退返回合法 JSON 信封时是否提取正文")
    body = "每年享有 5 天带薪年假。[来源1]"
    envelope = json.dumps({"answer": body}, ensure_ascii=False)
    with patch.object(rag.requests, "post",
                      side_effect=[Reply("not json at all", raw=True), Reply(envelope, raw=True)]):
        delivered = rag.answer_structured(QUESTION, LEAVE, [])
    ok = delivered == body
    print(f"  delivered = {delivered!r}")
    print(f"  RESULT: {'OK - 信封已提取' if ok else 'FAIL - 信封被当成正文'}")
    return ok


if __name__ == "__main__":
    results = {"R2": r2(), "R5": r5(), "R4": r4(), "R1": r1()}
    head("SUMMARY")
    for key, passed in results.items():
        print(f"  {key}: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
