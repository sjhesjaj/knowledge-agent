"""Capture the real first-round and recheck responses for `answer_v2_002`.

The public V2 set's stable failure. The candidate refuses; the freshly measured
baseline answers. Guessing at the cause from the final refusal is not enough -
this records what the model actually returned at each stage and what the
validator said about it, so the fix is aimed at the real reason.

Calls the real local model. Run from an isolated source copy:

    python docs/m10-selftest/diagnose_answer_v2_002.py [output.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover
    pass

import rag

QUESTION = "单笔 2500 元的报销，除了直属主管还需要谁审批？门槛金额是多少"


def main() -> int:
    text = Path("sample_company_rules.md").read_text(encoding="utf-8")
    chunks = rag.reindex_chunks(rag.split_text(text, "sample_company_rules.md"))
    rag.build_index(chunks)
    results = rag.retrieve_fast(QUESTION, chunks, top_k=4)

    record: dict = {
        "question": QUESTION,
        "evidence": [
            {"rank": i, "source": c.source, "index": c.index, "text": c.text}
            for i, (c, _) in enumerate(results, start=1)
        ],
    }

    # Stage 1: what the normal generation returns, before any verdict.
    messages = rag.build_answer_messages(QUESTION, results, [])
    response = rag.requests.post(
        f"{rag.OLLAMA_URL}/api/chat",
        json={"model": rag.CHAT_MODEL, "messages": messages, "stream": False,
              "think": False, "format": rag.ANSWER_SCHEMA, "options": {"temperature": 0}},
        timeout=180,
    )
    response.raise_for_status()
    raw_first = response.json()["message"]["content"]
    first = rag.extract_answer_text(raw_first)
    record["first_round"] = {
        "raw": raw_first,
        "body": first,
        "validate": list(rag.validate_answer(first, results, QUESTION)),
        "ungrounded_quantities": rag.ungrounded_quantities(first, results, QUESTION),
        "quantities_in_answer": sorted(rag.quantities(first)),
        "quantities_in_question": sorted(rag.quantities(QUESTION)),
        "quantities_in_evidence": sorted(
            rag.quantities(" ".join(c.text for c, _ in results))
        ),
        "needs_recheck": rag.needs_evidence_recheck(first, results, QUESTION),
    }

    # Stage 2: the bounded recheck, if the first round would trigger it.
    if record["first_round"]["needs_recheck"]:
        retry = rag._evidence_recheck(QUESTION, results)
        record["recheck"] = {
            "body": retry,
            "valid_retry": rag.is_valid_evidence_retry(retry, QUESTION) if retry else None,
            "validate": list(rag.validate_answer(retry, results, QUESTION)) if retry else None,
        }

    record["delivered"] = rag.decide_delivery(QUESTION, results, first)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
