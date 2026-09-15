"""Reproduce the two R5 defects from the third acceptance review.

Both are about the same thing from opposite ends: whether a *factual assertion
about a number* is actually checked.

1. The exemption was granted by keyword presence in the prefix, never by any
   relation between that keyword and this numeral. `没有特殊限制的…每年享有99天`
   was excused by a `没有` that negates the restrictions, not the 99.
2. `validate_answer()` returned early on `is_refusal()`, so a text that opens
   with a refusal phrase and then asserts a false number skipped the quantity
   check entirely.

Deterministic: the Ollama HTTP responses are scripted. Routing, generation
handling, delivery, the SSE protocol and SQLite are product code. This measures
code paths, not how often a real model produces these strings.

Run from an isolated source copy:  python docs/m10-selftest/repro_rework_4.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

# Runnable from anywhere: the product modules live at the repository root.
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
REFUSAL = "根据现有资料无法确定。"

# (name, expected_deliverable, answer)
CASES = (
    ("denial_of_restrictions", False,
     "没有特殊限制的正式员工每年享有99天带薪年假。[来源1]"),
    ("condition_with_jiu", False,
     "如果入职满一年就每年享有99天带薪年假。[来源1]"),
    ("attribution_of_another_input", False,
     "按你提供的入职日期计算每年享有99天带薪年假。[来源1]"),
    ("endorsed_guess", False,
     "你说的99天确实是公司规定的年假额度。[来源1]"),
    ("refusal_plus_false_fact", False,
     "根据现有资料无法确定奖金金额。正式员工每年享有99天带薪年假。[来源1]"),
    ("correct_correction", True, "不是99天而是5天带薪年假。[来源1]"),
    ("explicit_hypothesis", True, "如果按你说的99天计算，结论仍需核对原文。[来源1]"),
    ("pure_refusal", True, REFUSAL),
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
    run_id = uuid4().hex[:12]
    original_storage = api.storage
    api.storage = SQLiteStorage(Path(temp.name) / "repro4.db")

    passed = True
    head("R5-a/b  豁免作用域，以及拒答短语能否跳过事实校验")
    print(f"  证据: {SOURCE}")
    print(f"  问句: {QUESTION}\n")
    try:
        with TestClient(api.app) as client, patch.object(co, "prepare", return_value=prepared):
            for name, expected, body in CASES:
                verdict, reason = rag.validate_answer(body, EVIDENCE, QUESTION)
                flagged = rag.ungrounded_quantities(body, EVIDENCE, QUESTION)

                visible = {}
                calls = {}
                stored = {}
                terminal = None
                for mode, path in (("plain", "/api/chat"), ("sse", "/api/chat/stream")):
                    session = f"{name}-{mode}"
                    # Same scripted reply for the first call and the recheck, so
                    # neither interface gets a different second chance.
                    with patch.object(
                        rag.requests, "post", side_effect=lambda *a, **k: Response(body)
                    ) as mocked:
                        response = client.post(path, json={
                            "question": QUESTION, "mode": "orchestrated",
                            "client_id": run_id, "session_id": session,
                        })
                    calls[mode] = mocked.call_count
                    if mode == "plain":
                        visible[mode] = response.json()["answer"]
                    else:
                        visible[mode], terminal = sse_parts(response)
                    messages = api.storage.get_messages(session, run_id)
                    stored[mode] = messages[-1]["content"] if messages else None

                # Deliverable means "this exact text reaches the user"; blocked
                # means it must not, in either interface or in storage.
                if expected:
                    good = (verdict and visible["plain"] == body
                            and visible["sse"] == body and stored["plain"] == body)
                else:
                    good = (not verdict and body not in visible["plain"]
                            and body not in visible["sse"]
                            and stored["plain"] != body and stored["sse"] != body)
                passed &= good

                want = "可交付" if expected else "拦截"
                print(f"  [{'OK  ' if good else 'FAIL'}] 期望{want}：{name}")
                print(f"         {body}")
                print(f"         validate={verdict}/{reason}  ungrounded={flagged}")
                print(f"         plain={visible['plain']!r}")
                print(f"         sse  ={visible['sse']!r}  terminal={terminal}")
                print(f"         calls={calls}  stored_plain={stored['plain']!r}")
    finally:
        api.storage = original_storage
        temp.cleanup()

    head("SUMMARY")
    print(f"  R5: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
