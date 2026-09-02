"""The Ollama-backed `WikiModel`. The only place this package talks to a model.

Kept out of `wiki_maintenance/__init__` on purpose: the compiler is
model-agnostic, so importing the package must never be what decides which model
compiles the Wiki. A caller names this backend explicitly or supplies its own.

Two settings are deliberate rather than copied:

`format="json"` without a strict schema. A rigid response schema combined with
    suppressed thinking has previously left this model emitting well-formed but
    unreasoned output. Topic planning is the stage that most needs the model to
    actually think, so the format constraint stays loose and `think` is left at
    the model's default rather than forced off.
`temperature=0`. Two runs over the same spans should produce the same Wiki; a
    sampled one would make every rebuild look like an edit.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .compiler import ModelRequest

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class OllamaWikiModel:
    """Non-streaming JSON completion against a local Ollama server."""

    model: str = DEFAULT_MODEL
    url: str = DEFAULT_OLLAMA_URL
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    def generate(self, request: ModelRequest) -> str:
        response = requests.post(
            f"{self.url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
