"""One real query through the unchanged business chain, answered by DeepSeek.

Same path the evaluator uses: `chat_orchestration.prepare` (retrieval, with
embeddings on local Ollama) then `rag.answer_structured`. Every chat call made
on the way is recorded: the logical messages the business code built, the
effective messages sent to DeepSeek, the provider's prompt adaptations, and
the unified response (content, reasoning, tokens, latency). Request headers are
never recorded, so the API key cannot end up in the output.

Usage:
    .venv\\Scripts\\python.exe eval\\deepseek_smoke.py ["question"]
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_PROVIDER"] = "deepseek"

import requests  # noqa: E402

import llm_provider  # noqa: E402

DEFAULT_QUESTION = "员工年假有多少天？"


class RecordingProvider:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.name, self.model = inner.name, inner.model
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        response = self.inner.chat(messages, **kwargs)
        self.calls.append({"logical_messages": messages, "options": kwargs, "response": asdict(response)})
        return response

    def chat_stream(self, messages, **kwargs):
        raise NotImplementedError("the smoke test uses the non-streaming path")


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    config = llm_provider.load_config()
    recorder = RecordingProvider(llm_provider.create_provider(config))

    sent_bodies: list[dict] = []
    original_post = requests.post

    def spy(url, *args, **kwargs):
        if str(url).endswith("/chat/completions"):
            sent_bodies.append(kwargs.get("json"))  # body only - headers carry the key
        return original_post(url, *args, **kwargs)

    import chat_orchestration
    import evaluate_answerability
    import rag

    llm_provider.get_provider = lambda: recorder
    requests.post = spy
    try:
        chunks = evaluate_answerability.build_chunks()
        started = perf_counter()
        prepared = chat_orchestration.prepare(question, chunks)
        reply = (rag.answer_structured(question, prepared.results_for_answer, [])
                 if prepared.needs_generation else prepared.fixed_answer)
        total_seconds = perf_counter() - started
    finally:
        requests.post = original_post

    for call, body in zip(recorder.calls, sent_bodies):
        call["effective_messages"] = body["messages"]
        call["effective_request"] = {k: v for k, v in body.items() if k != "messages"}
        call["effective_differs_from_logical"] = body["messages"] != call["logical_messages"]

    result = {
        "question": question,
        "provider_config": config.public_dict(),
        "rag_chat_model": rag.CHAT_MODEL,
        "route": prepared.route,
        "outcome": prepared.outcome,
        "needs_generation": prepared.needs_generation,
        "sources": prepared.sources,
        "answer": reply,
        "total_seconds": total_seconds,
        "llm_calls": recorder.calls,
        "totals": {
            "llm_calls": len(recorder.calls),
            "prompt_tokens": sum(c["response"]["prompt_tokens"] or 0 for c in recorder.calls),
            "completion_tokens": sum(c["response"]["completion_tokens"] or 0 for c in recorder.calls),
            "llm_latency_seconds": sum(c["response"]["latency_seconds"] for c in recorder.calls),
        },
    }
    output = ROOT / "eval" / "deepseek_smoke.json"
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    assert config.api_key not in text, "API key leaked into the smoke record"
    output.write_text(text, encoding="utf-8")

    print(f"Question : {question}")
    print(f"Provider : {config.provider} / {config.model} @ {config.base_url}")
    print(f"Route    : {prepared.route} ({prepared.outcome})")
    for index, call in enumerate(recorder.calls, start=1):
        response = call["response"]
        print(f"LLM call {index}: format={call['options'].get('response_format') is not None} "
              f"prompt_tokens={response['prompt_tokens']} completion_tokens={response['completion_tokens']} "
              f"latency={response['latency_seconds']:.2f}s finish={response['finish_reason']} "
              f"adaptations={[a['reason'] for a in response['prompt_adaptations']]}")
        print(f"  content: {response['content']}")
    print(f"Answer   : {reply}")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
