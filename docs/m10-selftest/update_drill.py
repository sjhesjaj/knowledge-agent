"""Real publish-and-update drill over HTTP, with the real local model.

Uploads the sample corpus, waits for the first Wiki build to publish, asks the
annual-leave question on both the Wiki and Document paths, then re-uploads the
*same filename* with `5 天` changed to `6 天`, waits for the next build, and
asks again.

What it is for: proving that a changed fact actually reaches both paths and that
the superseded one stops being cited - through the real HTTP surface, not by
calling adapters directly.

Everything is isolated: run it from an isolated source copy, and it starts a
uvicorn server on a free port whose SQLite and Wiki roots live beside that copy.
It never touches the development worktree's database.

Usage (from an isolated source copy):
    python docs/m10-selftest/update_drill.py --output <result.json>
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

CLIENT_ID = "m10-update-drill"

# Both evidence paths, because a Document-only pair proves nothing about Wiki.
# Their routes are asserted, not assumed: `route` in the response has to be the
# one named here, or the drill fails rather than quietly measuring one path twice.
PATHS = (
    ("wiki_only", "年假制度整体介绍一下"),
    ("document_only", "正式员工入职满一年后每年有多少天带薪年假"),
)
ORIGINAL = "每年享有 5 天带薪年假"
UPDATED = "每年享有 6 天带薪年假"
# The fact itself, not a bare digit: a corpus full of 5s and 6s would satisfy
# "contains 5" no matter what happened to annual leave.
OLD_FACT = re.compile(r"5\s*天带薪年假|每年享有\s*5\s*天")
NEW_FACT = re.compile(r"6\s*天带薪年假|每年享有\s*6\s*天")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    def __init__(self, port: int):
        import api

        self.base = f"http://127.0.0.1:{port}"
        self._config = uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        # Wait on uvicorn's own flag rather than probing an endpoint: `/api/health`
        # reports on Ollama too, so a non-OK body there says nothing about whether
        # the socket is listening, and polling it hid the real state.
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._server.started:
                return self
            time.sleep(0.1)
        raise RuntimeError("the API did not start within 60s")

    def __exit__(self, *_args):
        self._server.should_exit = True
        self._thread.join(timeout=30)
        return False


def published_build(base: str) -> str | None:
    """The build id currently serving queries, per `/api/wiki/status`."""
    status = requests.get(f"{base}/api/wiki/status", timeout=10).json()
    return status.get("current_build_id")


def wait_for_build(base: str, previous: str | None, timeout: float = 600) -> tuple[str, float]:
    started = time.perf_counter()
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = published_build(base)
        if current and current != previous:
            return current, time.perf_counter() - started
        time.sleep(1.0)
    raise RuntimeError(f"no new build published (previous={previous})")


def upload(base: str, name: str, text: str) -> None:
    # The endpoint takes a *list* under `files`, and takes no client id: the
    # knowledge base is shared, only conversations are per-client.
    response = requests.post(
        f"{base}/api/knowledge/upload",
        files={"files": (name, text.encode("utf-8"), "text/markdown")},
        timeout=600,
    )
    response.raise_for_status()


def ask(base: str, question: str, session: str) -> dict:
    started = time.perf_counter()
    response = requests.post(
        f"{base}/api/chat",
        json={"question": question, "session_id": session,
              "client_id": CLIENT_ID, "mode": "orchestrated"},
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "answer": payload.get("answer"),
        "route": payload.get("route"),
        "sources": payload.get("sources", []),
        "seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()

    corpus = (REPO_ROOT / "sample_company_rules.md").read_text(encoding="utf-8")
    if ORIGINAL not in corpus:
        raise SystemExit(f"corpus does not contain {ORIGINAL!r}; refusing to guess")
    updated_corpus = corpus.replace(ORIGINAL, UPDATED, 1)

    import api  # noqa: F401  - imported for the resolved database path

    record: dict = {"database": str(getattr(api.storage, "path", "?")), "paths": {}}

    port = free_port()
    with Server(port) as server:
        before_build = published_build(server.base)

        upload(server.base, "sample_company_rules.md", corpus)
        build_one, seconds_one = wait_for_build(server.base, before_build)
        record["first_publish"] = {"build_id": build_one, "seconds": round(seconds_one, 2)}
        for name, question in PATHS:
            record["paths"][name] = {
                "question": question,
                "before": ask(server.base, question, f"drill-before-{name}"),
            }

        upload(server.base, "sample_company_rules.md", updated_corpus)
        build_two, seconds_two = wait_for_build(server.base, build_one)
        record["second_publish"] = {"build_id": build_two, "seconds": round(seconds_two, 2)}
        for name, question in PATHS:
            record["paths"][name]["after"] = ask(server.base, question, f"drill-after-{name}")

    checks: dict = {"build_changed": build_one != build_two}
    for name, entry in record["paths"].items():
        before, after = entry["before"], entry["after"]
        before_sources = json.dumps(before["sources"], ensure_ascii=False)
        after_sources = json.dumps(after["sources"], ensure_ascii=False)
        checks[name] = {
            # The route has to be the one this path is named for, both times.
            "route_before": before["route"] == name,
            "route_after": after["route"] == name,
            "before_states_five": bool(OLD_FACT.search(before["answer"] or "")),
            "after_states_six": bool(NEW_FACT.search(after["answer"] or "")),
            "after_drops_five": not OLD_FACT.search(after["answer"] or ""),
            "evidence_before_states_five": bool(OLD_FACT.search(before_sources)),
            "evidence_after_states_six": bool(NEW_FACT.search(after_sources)),
            "superseded_fact_gone_from_evidence": not OLD_FACT.search(after_sources),
        }
    record["checks"] = checks

    def flatten(value):
        if isinstance(value, dict):
            return all(flatten(item) for item in value.values())
        return bool(value)

    record["passed"] = flatten(checks)

    print(json.dumps(record, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
