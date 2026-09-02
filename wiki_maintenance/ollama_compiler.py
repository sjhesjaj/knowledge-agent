"""The Ollama-backed `WikiModel`. The only place this package talks to a model.

Kept out of `wiki_maintenance/__init__` on purpose: the compiler is
model-agnostic, so importing the package must never be what decides which model
compiles the Wiki. A caller names this backend explicitly or supplies its own.

Three settings, all of them measured rather than assumed:

`think=false`. qwen3:4b reasons at enormous length before answering. With
    thinking left on, `document_decision` over a 20-span document took 484s and
    `topic_plan` did not finish inside 1800s; with it off the same calls took
    9.5s and 144s and produced the same shape of answer. The earlier worry -
    that suppressing thinking degrades output - came from pairing it with a
    strict response schema. The format here stays the loose `"json"`, and the
    compiler's own checks (span resolution, number sourcing, batch indices) are
    what actually guard quality.
`num_predict` per stage. A cap the answer comfortably fits stops the model
    padding past the JSON it already finished. Sized from observed output:
    the decision emitted 46 tokens, a 20-page plan 985, a 4-page batch 1425.
`temperature=0`. Two runs over the same spans should produce the same Wiki; a
    sampled one would make every rebuild look like an edit.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .compiler import ModelRequest

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_SECONDS = 600

#: Output caps per stage, with roughly 2x headroom over what each stage was
#: measured to emit. Too tight truncates the JSON; too loose costs minutes.
DECISION_NUM_PREDICT = 256
TOPIC_PLAN_NUM_PREDICT = 2048
PAGE_BATCH_NUM_PREDICT = 2048
DEFAULT_NUM_PREDICT = 2048

REPAIR_SUFFIX = "_repair"
PAGE_STAGE_PREFIX = "page_compilation:"


def num_predict_for(stage: str) -> int:
    """The output cap for one stage.

    A repair re-emits the same JSON, so it gets the same cap as the request it
    is repairing - a smaller one would truncate the very answer it was asked to
    fix, and the compiler only allows one repair.
    """
    base = stage[: -len(REPAIR_SUFFIX)] if stage.endswith(REPAIR_SUFFIX) else stage
    if base == "document_decision":
        return DECISION_NUM_PREDICT
    if base == "topic_plan":
        return TOPIC_PLAN_NUM_PREDICT
    if base.startswith(PAGE_STAGE_PREFIX):
        return PAGE_BATCH_NUM_PREDICT
    return DEFAULT_NUM_PREDICT


def request_payload(model: str, request: ModelRequest) -> dict:
    """The exact body sent to Ollama. Separated so a test can read it."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": num_predict_for(request.stage),
        },
    }


@dataclass(frozen=True)
class OllamaWikiModel:
    """Non-streaming JSON completion against a local Ollama server."""

    model: str = DEFAULT_MODEL
    url: str = DEFAULT_OLLAMA_URL
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    def generate(self, request: ModelRequest) -> str:
        response = requests.post(
            f"{self.url}/api/chat",
            json=request_payload(self.model, request),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
