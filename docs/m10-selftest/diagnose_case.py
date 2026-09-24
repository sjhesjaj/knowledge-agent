"""Capture the real first-round and recheck responses for one dataset case.

Same idea as `diagnose_answer_v2_002.py`, but driven by a case id from a
dataset, and through the orchestrated preparation the evaluator uses - so the
evidence is the evidence the case really gets, not a hand-built stand-in.

Calls the real local model. Run from an isolated source copy:

    python docs/m10-selftest/diagnose_case.py <dataset.json> <case_id> [out.json]
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

import chat_orchestration as co
import rag


def main() -> int:
    dataset, case_id = sys.argv[1], sys.argv[2]
    cases = json.loads(Path(dataset).read_text(encoding="utf-8"))
    case = next(c for c in cases if c.get("id") == case_id)
    question = case["question"]

    text = Path("sample_company_rules.md").read_text(encoding="utf-8")
    chunks = rag.reindex_chunks(rag.split_text(text, "sample_company_rules.md"))
    rag.build_index(chunks)

    prepared = co.prepare(question, chunks)
    results = prepared.results_for_answer
    record: dict = {
        "id": case_id,
        "question": question,
        "route": prepared.route,
        "steps": list(prepared.steps),
        "outcome": prepared.outcome,
        "fixed_answer": prepared.fixed_answer,
        "evidence": [
            {"rank": i, "source": c.source, "index": c.index, "text": c.text}
            for i, (c, _) in enumerate(results, start=1)
        ],
    }

    if prepared.fixed_answer is not None:
        record["delivered"] = prepared.fixed_answer
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    messages = rag.build_answer_messages(question, results, [])
    response = rag.requests.post(
        f"{rag.OLLAMA_URL}/api/chat",
        json={"model": rag.CHAT_MODEL, "messages": messages, "stream": False,
              "think": False, "format": rag.ANSWER_SCHEMA, "options": {"temperature": 0}},
        timeout=180,
    )
    response.raise_for_status()
    first = rag.extract_answer_text(response.json()["message"]["content"])

    # Every gate, separately, so the reason is visible rather than inferred.
    record["first_round"] = {
        "body": first,
        "validate": list(rag.validate_answer(first, results, question)),
        "restates_question": rag.restates_question(first, question),
        "invalid_citations": rag.invalid_citation_indices(first, len(results)),
        "ungrounded_quantities": rag.ungrounded_quantities(first, results, question),
        "unsupported_identifiers": rag.unsupported_identifiers(first, results, question),
        "describes_only_process": rag.describes_only_process(first, results),
        "is_refusal": rag.is_refusal(first),
        "is_pure_refusal": rag.is_pure_refusal(first),
        "needs_recheck": rag.needs_evidence_recheck(first, results, question),
    }
    if record["first_round"]["needs_recheck"]:
        retry = rag._evidence_recheck(question, results)
        record["recheck"] = {
            "body": retry,
            "valid_retry": rag.is_valid_evidence_retry(retry, question) if retry else None,
            "validate": list(rag.validate_answer(retry, results, question)) if retry else None,
        }
    record["delivered"] = rag.decide_delivery(question, results, first)

    print(json.dumps(record, ensure_ascii=False, indent=2))
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
