"""Harness plumbing shared by environment-pinned eval runs.

Self-contained on purpose: the Stage 0 script `eval/run_stage0_eval.py` stays
frozen as the reproduction path for Stage 0-1 results and is not imported.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import re
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = "http://localhost:11434"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def rel(path: Path) -> str:
    resolved = Path(path).resolve()
    shown = resolved.relative_to(ROOT) if resolved.is_relative_to(ROOT) else resolved
    return str(shown).replace("\\", "/")


# --------------------------------------------------------------------------
# Git and runtime versions
# --------------------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def git_state() -> dict:
    """Commit plus every non-ignored change, tracked or not (new files count too)."""
    changes = [line for line in git("status", "--porcelain").splitlines() if line.strip()]
    return {"commit": git("rev-parse", "HEAD"), "describe": git("describe", "--tags", "--always"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "dirty": bool(changes), "changes": changes}


def ollama_version() -> str | None:
    try:
        return requests.get(f"{OLLAMA_URL}/api/version", timeout=10).json().get("version")
    except requests.RequestException:
        return None


def runtime_versions() -> dict:
    """Recorded for every run; informative only, never a gate."""
    return {"python": platform.python_version(), "platform": platform.platform(),
            "ollama": ollama_version()}


def ollama_tags() -> list[dict]:
    """Installed Ollama models. Raises requests.RequestException when Ollama is down."""
    response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=10)
    response.raise_for_status()
    return response.json().get("models", [])


def model_digest(tags: list[dict], model: str) -> str | None:
    names = {model, model if ":" in model else f"{model}:latest"}
    match = next((t for t in tags if t.get("name") in names or t.get("model") in names), None)
    return match.get("digest") if match else None


def ollama_model_info(model: str) -> dict:
    info: dict = {"name": model}
    try:
        info["digest"] = model_digest(ollama_tags(), model)
        shown = requests.post(f"{OLLAMA_URL}/api/show", json={"model": model}, timeout=30).json()
        details = shown.get("details", {})
        info.update({key: details.get(key) for key in ("quantization_level", "parameter_size", "family", "format")})
        info["parameters"] = shown.get("parameters")
        from_line = re.search(r"^FROM (.+)$", shown.get("modelfile", ""), flags=re.MULTILINE)
        if from_line:
            info["weights_blob"] = Path(from_line.group(1).strip()).name
        general = shown.get("model_info", {})
        for key in ("general.basename", "general.finetune", "general.size_label"):
            if key in general:
                info[key] = general[key]
    except requests.RequestException as exc:
        info["error"] = str(exc)
    return info


# --------------------------------------------------------------------------
# Experiment configuration (recorded, not part of the corpus environment)
# --------------------------------------------------------------------------


def _fast_path_thresholds(rag) -> dict:
    """BM25 fast-path thresholds: named constants (current rag) or, for older code, its source."""
    score, ratio = getattr(rag, "BM25_CONFIDENT_SCORE", None), getattr(rag, "BM25_CONFIDENT_RATIO", None)
    if score is None or ratio is None:
        found = re.search(r"first >= ([\d.]+) and \(second == 0 or first / second >= ([\d.]+)\)",
                          inspect.getsource(rag.retrieve_fast))
        score, ratio = (float(found.group(1)), float(found.group(2))) if found else (None, None)
    return {"min_top_score": score, "min_ratio_to_second": ratio}


def _retrieval_budget(rag, defaults) -> dict:
    """Retrieval budget knobs present in this version of rag (absent ones are omitted)."""
    budget = {name: getattr(rag, name) for name in ("MAX_SUB_QUESTIONS", "PADDING_SCORE_RATIO") if hasattr(rag, name)}
    for name in ("fit_to_budget", "cover_merged_clauses", "drop_padding_results"):
        if hasattr(rag, name) and defaults(getattr(rag, name)):
            budget[name] = defaults(getattr(rag, name))
    return budget


def _wiki_weights() -> dict:
    from orchestration import wiki_adapter

    return {name: getattr(wiki_adapter, name) for name in
            ("TITLE_WEIGHT", "ALIAS_WEIGHT", "SUMMARY_WEIGHT", "CLAIM_WEIGHT") if hasattr(wiki_adapter, name)}


def retriever_config() -> dict:
    import rag
    from orchestration import ExecutionContext

    def defaults(function) -> dict:
        return {name: p.default for name, p in inspect.signature(function).parameters.items()
                if p.default is not inspect.Parameter.empty and p.default is not None}

    context = ExecutionContext()
    return {
        "split_text": defaults(rag.split_text),
        "bm25_rank": defaults(rag.bm25_rank),
        "hybrid_retrieve": defaults(rag.hybrid_retrieve),
        "retrieve_with_rerank": defaults(rag.retrieve_with_rerank),
        "retrieve_fast": defaults(rag.retrieve_fast),
        "bm25_fast_path": _fast_path_thresholds(rag),
        "retrieval_budget": _retrieval_budget(rag, defaults),
        "wiki_weights": _wiki_weights(),
        "executor": {"document_top_k": context.document_top_k, "wiki_top_k": context.wiki_top_k},
        "refusal_markers": list(rag.REFUSAL_MARKERS),
    }


# --------------------------------------------------------------------------
# Observing what the run sent, and tracing each case
# --------------------------------------------------------------------------


class ChatRequestSpy:
    """Records /api/chat and /api/embed request bodies; never alters a call."""

    def __init__(self) -> None:
        self.original = requests.post
        self.system_prompts: dict[str, str] = {}
        self.system_prompt_calls: Counter = Counter()
        self.request_shapes: Counter = Counter()
        self.chat_calls = 0
        self.embed_calls = 0

    def __call__(self, url, *args, **kwargs):
        body = kwargs.get("json")
        if isinstance(body, dict) and str(url).endswith("/api/chat"):
            self.chat_calls += 1
            messages = body.get("messages") or []
            system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            digest = sha256_text(system)
            self.system_prompts[digest] = system
            self.system_prompt_calls[digest] += 1
            fmt = body.get("format")
            shape = {"model": body.get("model"), "stream": body.get("stream"), "think": body.get("think"),
                     "format": fmt if fmt in (None, "json") else "schema:" + sha256_text(
                         json.dumps(fmt, sort_keys=True, ensure_ascii=False))[:16],
                     "options": body.get("options"), "tools": bool(body.get("tools")),
                     "system_prompt_sha256": digest}
            self.request_shapes[json.dumps(shape, sort_keys=True, ensure_ascii=False)] += 1
        elif isinstance(body, dict) and str(url).endswith("/api/embed"):
            self.embed_calls += 1
        return self.original(url, *args, **kwargs)

    def report(self) -> dict:
        return {
            "chat_calls": self.chat_calls, "embed_calls": self.embed_calls,
            "system_prompts": [{"sha256": d, "calls": self.system_prompt_calls[d], "text": t}
                               for d, t in sorted(self.system_prompts.items())],
            "request_shapes": [{"calls": c, **json.loads(s)} for s, c in sorted(self.request_shapes.items())],
        }


def install_eval_tracing(evaluate_answerability, rag, trace_db: Path, *, env_id: str):
    """One trace run per evaluated case; returns an undo callable."""
    import agent_trace

    original_run_case = evaluate_answerability.run_case
    original_answer = rag.answer_structured

    def traced_run_case(case, chunks, run_index, context):
        run = agent_trace.start_run(
            trace_db, kind="eval", entrypoint=f"eval:{env_id}", mode="orchestrated", streaming=False,
            question=case["question"], dataset=Path(context["dataset"]).name,
            dataset_sha256=context["dataset_sha256"], case_id=case["id"], eval_run_index=run_index,
            truncate=False)
        with run:
            record = original_run_case(case, chunks, run_index, context)
        record["trace_run_id"] = run.run_id
        return record

    def traced_answer(question, results, history, **kwargs):
        with agent_trace.span("generation", "answer_structured",
                              input=agent_trace.generation_input(question, results, history)) as span:
            reply = original_answer(question, results, history, **kwargs)
            if span:
                span.output = {"answer": reply}
        return reply

    evaluate_answerability.run_case = traced_run_case
    rag.answer_structured = traced_answer

    def undo() -> None:
        evaluate_answerability.run_case = original_run_case
        rag.answer_structured = original_answer

    return undo


def trace_summary(trace_db: Path) -> dict | None:
    if not trace_db.exists():
        return None
    connection = sqlite3.connect(trace_db)
    try:
        by_status = dict(connection.execute("SELECT status, COUNT(*) FROM trace_runs GROUP BY status").fetchall())
        spans = connection.execute("SELECT COUNT(*) FROM trace_spans").fetchone()[0]
    finally:
        connection.close()
    return {"runs_by_status": by_status, "spans": spans}


def condense_cases(run_dir: Path, runs: int) -> list[dict]:
    per_case: dict[str, dict] = {}
    for run_index in range(1, runs + 1):
        data = json.loads((run_dir / f"run-{run_index}.json").read_text(encoding="utf-8"))
        for record in data["cases"]:
            entry = per_case.setdefault(record["id"], {
                "id": record["id"], "question": record["question"], "category": record["category"],
                "expected_behavior": record["expected_behavior"], "expected_route": record["expected_route"],
                "runs": []})
            entry["runs"].append({
                "run": run_index, "passed": record["passed"], "actual_behavior": record["actual_behavior"],
                "actual_route": record["actual_route"], "model_called": record.get("model_called"),
                "all_facts_hit": record["all_facts_hit"], "has_citation": record["has_citation"],
                "citation_indices_valid": record["citation_indices_valid"],
                "required_source_coverage": record["required_source_coverage"],
                "source_types": record.get("source_types"),
                "duration_seconds": round(record["duration_seconds"], 3), "answer": record["answer"],
                "trace_run_id": record.get("trace_run_id")})
    for entry in per_case.values():
        entry["pass_count"] = sum(r["passed"] for r in entry["runs"])
    return [per_case[key] for key in sorted(per_case)]
