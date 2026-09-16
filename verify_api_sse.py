"""M10 real-interface check: the same questions through `/api/chat` and SSE.

Additive self-test. The frozen answerability evaluator calls
`chat_orchestration.prepare` and `rag.answer_structured` directly, so it never
touches an HTTP handler, an SSE frame, the conversation lock, or persistence.
This runs a real uvicorn server on a free port and drives both endpoints with
the real local model, so what is measured is what a client would get.

Isolation is the caller's job and is checked, not assumed: the server is
started from the *current working directory*, and `api` resolves its database
next to `storage.py`. Run it from an isolated source copy, never from the
working tree you develop in. The startup banner prints the database path it
resolved so the run record shows where the data went.

Measured per question and interface: route, steps, the answer, the sources,
citation indices, model-call count, total latency, and - for SSE only - the
delay before the first visible character of the answer body.

Usage:
    python verify_api_sse.py --output-dir <dir>
    python verify_api_sse.py --output-dir <dir> --port 8123
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - already UTF-8 or piped
        pass

REPO_ROOT = Path(__file__).resolve().parent
CORPUS_PATH = REPO_ROOT / "sample_company_rules.md"
CLIENT_ID = "m10-verify"
MODE = "orchestrated"

# Eight answerable questions covering all seven non-direct routes, with a
# second three-channel compound case, plus four boundary/refusal questions.
# Fresh sentences: none is copied from a frozen dataset.
CASES = [
    {
        "id": "api_wiki_only",
        "question": "远程办公这块整体介绍一下",
        "expected_route": "wiki_only",
        "expect": "answer",
        "facts": [r"每周最多.*2\s*天|2\s*天远程办公"],
    },
    {
        "id": "api_document_only",
        "question": "培训预算每年的上限是多少，要准确的",
        "expected_route": "document_only",
        "expect": "answer",
        "facts": [r"2000\s*元|2,000\s*元"],
    },
    {
        "id": "api_system_only",
        "question": "SKU-A100 现在还剩多少件",
        "expected_route": "system_only",
        "expect": "answer",
        "facts": [r"42"],
    },
    {
        "id": "api_wiki_document",
        "question": "账号权限整体讲讲，高权限复核周期那条要准确的",
        "expected_route": "wiki_document",
        "expect": "answer",
        "facts": [r"90\s*天"],
    },
    {
        "id": "api_wiki_system",
        "question": "信息安全整体介绍一下，顺便看下 SKU-C300 那边的存量",
        "expected_route": "wiki_system",
        "expect": "answer",
        # `\b` is useless against a CJK neighbour - in `为 7 箱` both sides of
        # the digit are word characters under Unicode, so no boundary exists.
        # Bind the number to its unit instead, which is what makes it a fact.
        "facts": [r"公共网盘|加密", r"7\s*(?:箱|件|个)"],
    },
    {
        "id": "api_document_system",
        "question": "报销单笔多少钱以上要部门负责人签字，要准确的；SKU_B200 目前的存量报一下",
        "expected_route": "document_system",
        "expect": "answer",
        "facts": [r"2000\s*元", r"0\s*(?:件|箱|个)"],
    },
    {
        "id": "api_wiki_document_system",
        "question": "请假制度先给个总览，超过 1 天谁审批那句要准确的，再看下 SKU-A100 的存量",
        "expected_route": "wiki_document_system",
        "expect": "answer",
        "facts": [r"部门负责人", r"42"],
    },
    {
        "id": "api_compound_three_channel",
        "question": "信息安全先来个概览，公共网盘那条的准确表述发我，另外 Sku-C300 还有多少",
        "expected_route": "wiki_document_system",
        "expect": "answer",
        "facts": [r"公共网盘", r"7\s*(?:箱|件|个)"],
    },
    {
        "id": "api_boundary_no_sku",
        "question": "库存现在还有货吗",
        "expected_route": "system_only",
        "expect": "boundary",
        "expected_message_key": "MESSAGE_NO_SKU",
    },
    {
        "id": "api_boundary_multi_sku",
        "question": "SKU-A100 和 SKU-C300 的存量一起报给我",
        "expected_route": "system_only",
        "expect": "boundary",
        "expected_message_key": "MESSAGE_MULTIPLE_SKU",
    },
    {
        "id": "api_boundary_system_limited",
        "question": "ord-1001 现在什么状态",
        "expected_route": "system_only",
        "expect": "boundary",
        "expected_message_key": "MESSAGE_SYSTEM_LIMITED",
    },
    {
        "id": "api_refusal_unsupported",
        "question": "公司年终奖发几个月工资，要准确的",
        "expected_route": "document_only",
        "expect": "refuse",
    },
]

ANSWERABLE = [c for c in CASES if c["expect"] == "answer"]
BOUNDARY_OR_REFUSAL = [c for c in CASES if c["expect"] != "answer"]

CITATION_PATTERN = re.compile(r"\[\s*来源\s*([^\]]*)\]")


def citation_indices(text: str) -> list[int]:
    found: list[int] = []
    for raw in CITATION_PATTERN.findall(text):
        found.extend(int(d) for d in re.findall(r"\d+", raw))
    return found


# --------------------------------------------------------------------------
# Server lifecycle
# --------------------------------------------------------------------------


class ServerHandle:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread, port: int):
        self.server = server
        self.thread = thread
        self.port = port

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)


def start_server(port: int) -> ServerHandle:
    import api

    print(f"api database : {api.storage.path}")
    print(f"wiki root    : {__import__('wiki_runtime').RUNTIME.root}")
    config = uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started:
            return ServerHandle(server, thread, port)
        time.sleep(0.1)
    raise RuntimeError("the API did not start within 30s")


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def upload_corpus(base: str) -> dict:
    with CORPUS_PATH.open("rb") as handle:
        response = requests.post(
            f"{base}/api/knowledge/upload",
            files={"files": (CORPUS_PATH.name, handle, "text/markdown")},
            timeout=600,
        )
    response.raise_for_status()
    return response.json()


def wait_for_wiki_build(base: str, *, timeout: float = 900.0) -> dict:
    """Block until the background Wiki compilation settles.

    Two reasons, both about honesty of the numbers. Compilation is started by
    the upload and runs on the same local model, so measuring while it works
    charges the first questions for someone else's inference. And until it
    publishes, `wiki_query` is still answering from the committed sample - so
    an unwaited run would report the static Wiki while claiming to exercise the
    live one.
    """
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base}/api/wiki/status", timeout=30)
            response.raise_for_status()
            last = response.json()
        except requests.RequestException:
            time.sleep(2)
            continue
        if last.get("status") not in {"queued", "running"}:
            return last
        time.sleep(3)
    return {**last, "timed_out": True}


# --------------------------------------------------------------------------
# Drivers
# --------------------------------------------------------------------------


def ask_plain(base: str, case: dict, session: str) -> dict:
    started = time.perf_counter()
    response = requests.post(
        f"{base}/api/chat",
        json={
            "question": case["question"],
            "session_id": session,
            "client_id": CLIENT_ID,
            "mode": MODE,
        },
        timeout=600,
    )
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    body = response.json()
    return {
        "interface": "plain",
        "route": body.get("route"),
        "steps": body.get("steps"),
        "answer": body.get("answer", ""),
        "sources": body.get("sources", []),
        "citation_indices": citation_indices(body.get("answer", "")),
        "total_seconds": elapsed,
        "first_visible_seconds": None,
        "trace": body.get("trace", {}),
        "error": None,
    }


def ask_stream(base: str, case: dict, session: str) -> dict:
    started = time.perf_counter()
    first_visible = None
    parts: list[str] = []
    sources: list[dict] = []
    trace: dict = {}
    error = None
    saw_done = False

    with requests.post(
        f"{base}/api/chat/stream",
        json={
            "question": case["question"],
            "session_id": session,
            "client_id": CLIENT_ID,
            "mode": MODE,
        },
        stream=True,
        timeout=600,
    ) as response:
        response.raise_for_status()
        event = None
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if event == "delta":
                if first_visible is None and payload.get("content"):
                    first_visible = time.perf_counter() - started
                parts.append(payload.get("content", ""))
            elif event == "sources":
                sources = payload.get("sources", [])
            elif event == "done":
                trace = payload.get("trace", {})
                saw_done = True
            elif event == "error":
                error = payload

    answer = "".join(parts).strip()
    return {
        "interface": "stream",
        "route": trace.get("route"),
        "steps": trace.get("steps"),
        "answer": answer,
        "sources": sources,
        "citation_indices": citation_indices(answer),
        "total_seconds": time.perf_counter() - started,
        "first_visible_seconds": first_visible,
        "trace": trace,
        "error": error,
        "done": saw_done,
    }


# --------------------------------------------------------------------------
# Judgement
# --------------------------------------------------------------------------


def judge(case: dict, observed: dict, messages: dict) -> dict:
    answer = observed["answer"]
    route_ok = observed["route"] == case["expected_route"]
    citations = observed["citation_indices"]
    citations_valid = all(1 <= n <= len(observed["sources"]) for n in citations)

    if case["expect"] == "answer":
        facts = [
            {"pattern": pattern, "hit": bool(re.search(pattern, answer))}
            for pattern in case.get("facts", [])
        ]
        facts_ok = all(item["hit"] for item in facts)
        refused = any(
            marker in answer for marker in ("无法确定", "没有相关", "未提及", "资料不足")
        )
        passed = route_ok and facts_ok and citations_valid and not refused
        detail = {"facts": facts, "refused": refused}
    elif case["expect"] == "boundary":
        expected = messages[case["expected_message_key"]]
        passed = route_ok and answer.strip() == expected
        detail = {"expected_message": expected}
    else:
        refused = any(
            marker in answer for marker in ("无法确定", "没有相关", "未提及", "资料不足")
        )
        passed = refused
        detail = {"refused": refused}

    return {
        "route_ok": route_ok,
        "citations_valid": citations_valid,
        "passed": bool(passed) and observed["error"] is None,
        **detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    import chat_orchestration

    messages = {
        "MESSAGE_NO_SKU": chat_orchestration.MESSAGE_NO_SKU,
        "MESSAGE_MULTIPLE_SKU": chat_orchestration.MESSAGE_MULTIPLE_SKU,
        "MESSAGE_SYSTEM_LIMITED": chat_orchestration.MESSAGE_SYSTEM_LIMITED,
    }

    handle = start_server(args.port or free_port())
    records: list[dict] = []
    try:
        upload = upload_corpus(handle.base)
        print(f"uploaded     : {upload.get('chunks')} chunks from {CORPUS_PATH.name}")

        # Compilation runs on the same local model. Waiting keeps its inference
        # out of the measured latencies, and means the Wiki channel answers
        # from the freshly compiled build rather than the committed sample.
        wiki = wait_for_wiki_build(handle.base)
        print(
            f"wiki build   : status={wiki.get('status')} "
            f"build={wiki.get('current_build_id')} "
            f"published={wiki.get('published_build_id')}"
            + ("  [TIMED OUT]" if wiki.get("timed_out") else "")
        )

        # One warm-up so the first measured question is not paying for the
        # model load. Reported separately, never mixed into the results.
        warm_started = time.perf_counter()
        ask_plain(handle.base, CASES[0], "warmup")
        warm_seconds = time.perf_counter() - warm_started
        print(f"warm-up      : {warm_seconds:.2f}s (excluded)\n")

        for index, case in enumerate(CASES, start=1):
            for driver, name in ((ask_plain, "plain"), (ask_stream, "stream")):
                observed = driver(handle.base, case, f"m10-{name}-{index}")
                verdict = judge(case, observed, messages)
                records.append({"id": case["id"], **case, **observed, **verdict})
                status = "PASS" if verdict["passed"] else "FAIL"
                first = observed["first_visible_seconds"]
                print(
                    f"{status} [{name:<6}] {case['id']:<28}"
                    f" route {observed['route']}"
                    f" total {observed['total_seconds']:.2f}s"
                    + (f" first {first:.2f}s" if first is not None else "")
                )
                if not verdict["passed"]:
                    print(f"       A: {observed['answer'][:150]}")
    finally:
        handle.stop()

    summary = {}
    for name in ("plain", "stream"):
        rows = [r for r in records if r["interface"] == name]
        answerable = [r for r in rows if r["expect"] == "answer"]
        boundary = [r for r in rows if r["expect"] != "answer"]
        latencies = sorted(r["total_seconds"] for r in rows)
        firsts = sorted(
            r["first_visible_seconds"] for r in rows if r["first_visible_seconds"]
        )
        summary[name] = {
            "answerable_passed": sum(r["passed"] for r in answerable),
            "answerable_total": len(answerable),
            "boundary_passed": sum(r["passed"] for r in boundary),
            "boundary_total": len(boundary),
            "citation_validity": (
                sum(r["citations_valid"] for r in rows) / len(rows) if rows else 0.0
            ),
            "total_seconds_p50": latencies[len(latencies) // 2] if latencies else None,
            "total_seconds_max": latencies[-1] if latencies else None,
            "first_visible_p50": firsts[len(firsts) // 2] if firsts else None,
        }

    agreement = []
    for case in CASES:
        rows = {r["interface"]: r for r in records if r["id"] == case["id"]}
        agreement.append(
            {
                "id": case["id"],
                "route_agrees": rows["plain"]["route"] == rows["stream"]["route"],
                "source_count_agrees": len(rows["plain"]["sources"])
                == len(rows["stream"]["sources"]),
                "verdict_agrees": rows["plain"]["passed"] == rows["stream"]["passed"],
            }
        )

    result = {
        "cases": records,
        "summary": summary,
        "interface_agreement": agreement,
        "warm_up_seconds": warm_seconds,
        "wiki_build": wiki,
        "uploaded_chunks": upload.get("chunks"),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "api-sse.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\nSummary")
    for name, counts in summary.items():
        print(
            f"  {name:<7} answerable {counts['answerable_passed']}/{counts['answerable_total']}"
            f"   boundary/refusal {counts['boundary_passed']}/{counts['boundary_total']}"
            f"   citation validity {counts['citation_validity']:.0%}"
            f"   p50 {counts['total_seconds_p50']:.2f}s"
            + (
                f"   first-visible p50 {counts['first_visible_p50']:.2f}s"
                if counts["first_visible_p50"]
                else ""
            )
        )
    disagreements = [a for a in agreement if not all(v for k, v in a.items() if k != "id")]
    print(f"  interface disagreements: {len(disagreements)}")
    for item in disagreements:
        print(f"    {item}")
    print(f"\nWrote {output_dir / 'api-sse.json'}")

    ok = all(
        counts["answerable_passed"] >= 7 and counts["boundary_passed"] == counts["boundary_total"]
        for counts in summary.values()
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
