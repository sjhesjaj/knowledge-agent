"""Reproduce the five residual defects from the third acceptance review.

Deterministic: every model response is scripted, so no inference runs. What is
exercised is the real extraction, validation, delivery, FastAPI consumption and
SQLite persistence code - only the HTTP responses are fixed.

Run from an isolated source copy:  python docs/m10-selftest/repro_rework_3.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Runnable from anywhere: the product modules live at the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import rag
from orchestration.planner import Route, plan_request
from rag import Chunk

LEAVE = [(Chunk(text="## 请假制度\n正式员工入职满一年后，每年享有 5 天带薪年假。",
                source="rules.md", index=1), 3.5)]
STOCK_234 = [(Chunk(text="## 库存\n仓库当前库存为234件。", source="rules.md", index=1), 3.5)]
STOCK_34 = [(Chunk(text="## 库存\n仓库当前库存为34件。", source="rules.md", index=1), 3.5)]
STOCK_2000 = [(Chunk(text="## 库存\n仓库当前库存为2000件。", source="rules.md", index=1), 3.5)]

GUESS_QUESTION = "正式员工入职满一年后，每年有 99 天年假吗？请核对原文"
STOCK_QUESTION = "仓库现在有多少件库存"
LEAVE_QUESTION = "年假有多少天"


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
    """A streamed `{"answer": ...}` envelope, chopped into 4-character chunks."""

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


def deliverable(question, results, answer_text, *, recheck=None):
    """Final body from both interfaces plus the model call count.

    `recheck` is the scripted second (recheck) generation, when one happens.
    Both interfaces get the identical script, so any divergence is the code's.
    """
    replies = [Reply(answer_text)] + ([Reply(recheck)] if recheck else [])
    with patch.object(rag.requests, "post", side_effect=list(replies)) as post:
        plain = rag.answer_structured(question, results, [])
        plain_calls = post.call_count

    stream_replies = [Stream(answer_text)] + ([Reply(recheck)] if recheck else [])
    with patch.object(rag.requests, "post", side_effect=list(stream_replies)) as post:
        emitted = list(rag.answer_stream(question, results, []))
        stream_calls = post.call_count
    return plain, "".join(emitted), plain_calls, stream_calls


# --------------------------------------------------------------------------
# R5: does a modifier actually modify *this* number?
# --------------------------------------------------------------------------
R5_CASES = (
    ("不是99天而是5天。[来源1]", True, "否定猜测并给出有依据的值"),
    ("不是5天而是99天。[来源1]", False, "纠正方向相反：99 才是被肯定的断言"),
    ("正式员工每年享有99天带薪年假且可以先休5天。[来源1]", False, "同句另一个有依据数字不能替 99 免责"),
    ("正式员工每年享有99天带薪年假且没有特殊限制。[来源1]", False, "无关否定词不能替 99 免责"),
    ("并不是所有人都有99天年假但正式员工每年确实有99天。[来源1]", False, "先否定后肯定，后一次仍是断言"),
    ("如果入职满一年则每年可享受99天带薪年假。[来源1]", False, "资格条件不是把 99 设为假设"),
    ("如果按你说的99天计算，资料并未这样规定；原文写的是5天。[来源1]", True, "明确复述用户猜测，保留原有豁免"),
)


def r5():
    head("R5  数值的肯定/否定/引用位置")
    ok = True
    for text, should_deliver, why in R5_CASES:
        passed, reason = rag.validate_answer(text, LEAVE, GUESS_QUESTION)
        good = passed == should_deliver
        ok &= good
        want = "可交付" if should_deliver else "拦截"
        print(f"  [{'OK  ' if good else 'FAIL'}] 期望{want}：{text}")
        print(f"         validate={passed} reason={reason}  ({why})")
    return ok


# --------------------------------------------------------------------------
# R4: a complete numeral must never be truncated into a grounded-looking one
# --------------------------------------------------------------------------
R4_CASES = (
    (STOCK_234, "仓库当前库存为二百三十四件。[来源1]", True, "支持的写法，正确答案"),
    (STOCK_34, "仓库当前库存为二百三十四件。[来源1]", False, "234 不在证据里"),
    (STOCK_2000, "仓库当前库存为两千件。[来源1]", True, "支持的写法，正确答案"),
    (STOCK_2000, "仓库当前库存为一万两千件。[来源1]", False, "不得截取成 2000"),
)


def r4():
    head("R4  完整数词边界")
    ok = True
    for results, text, should_deliver, why in R4_CASES:
        passed, reason = rag.validate_answer(text, results, STOCK_QUESTION)
        good = passed == should_deliver
        ok &= good
        want = "可交付" if should_deliver else "拦截"
        print(f"  [{'OK  ' if good else 'FAIL'}] 期望{want}：{text}")
        print(f"         extracted={sorted(rag.quantities(text))} validate={passed} reason={reason}  ({why})")

    passed, reason = rag.validate_answer("每年享有二点五天带薪年假。[来源1]", LEAVE, LEAVE_QUESTION)
    good = passed is False
    ok &= good
    print(f"  [{'OK  ' if good else 'FAIL'}] 期望拦截：二点五天（不得截取成 5）")
    print(f"         extracted={sorted(rag.quantities('每年享有二点五天带薪年假。'))} validate={passed} reason={reason}")
    return ok


# --------------------------------------------------------------------------
# R1: think-tag shell wrapped around a legal JSON envelope
# --------------------------------------------------------------------------
def r1():
    head("R1  思考标签外壳 + 合法 JSON 信封")
    body = "正式员工入职满一年后，每年享有5天带薪年假。[来源1]"
    shelled = f'<think>检查资料</think>{{"answer":"{body}"}}'
    with patch.object(rag.requests, "post",
                      side_effect=[Reply("not json at all", raw=True), Reply(shelled, raw=True)]):
        delivered = rag.answer_structured(LEAVE_QUESTION, LEAVE, [])
    print(f"  delivered = {delivered!r}")
    leaked = '"answer"' in delivered or delivered.startswith("{")
    ok = not leaked and delivered == body
    print(f"  RESULT: {'OK - 提取正文' if ok else 'FAIL - 信封泄漏或正文不符'}")
    return ok


# --------------------------------------------------------------------------
# R2 tail: leading/trailing whitespace must not make the interfaces differ
# --------------------------------------------------------------------------
def r2_padding():
    head("R2  首尾空白：普通返回值与原始 delta 拼接是否逐字一致")
    padded = " \n正式员工每年享有5天带薪年假。[来源1]\n "
    plain, streamed, plain_calls, stream_calls = deliverable(LEAVE_QUESTION, LEAVE, padded)
    print(f"  plain  = {plain!r}   calls={plain_calls}")
    print(f"  stream = {streamed!r}   calls={stream_calls}")
    ok = plain == streamed and plain_calls == stream_calls
    # Deliberately NOT stripping before the comparison: stripping here is what
    # hid the difference in the first place.
    print(f"  RESULT: {'OK - 逐字一致' if ok else 'FAIL - 原始 delta 与普通返回值不同'}")
    return ok


# --------------------------------------------------------------------------
# R3: thanks/wishes/farewell combinations, and a negated overview request
# --------------------------------------------------------------------------
ROUTE_CASES = (
    ("谢啦，祝你接下来一切顺利。", Route.DIRECT),
    ("先聊到这里，谢啦，祝你顺顺利利。", Route.DIRECT),
    ("谢啦，请告诉我年假的具体天数。", Route.DOCUMENT_ONLY),
    ("给我薪资疑问反馈时限的原始条款，制度概览就免了。", Route.DOCUMENT_ONLY),
)


def r3():
    head("R3  致谢/祝愿/告别组合与概览的后置否定")
    ok = True
    for question, expected in ROUTE_CASES:
        actual = plan_request(question).route
        good = actual == expected
        ok &= good
        print(f"  [{'OK  ' if good else 'FAIL'}] {question}")
        print(f"         expected={expected.value}  actual={actual.value}")
    return ok


if __name__ == "__main__":
    results = {"R5": r5(), "R4": r4(), "R1": r1(), "R2": r2_padding(), "R3": r3()}
    head("SUMMARY")
    for key, passed in results.items():
        print(f"  {key}: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
