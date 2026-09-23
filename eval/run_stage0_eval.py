"""Stage 0 baseline / regression runner for the answerability evaluation.

Wraps `evaluate_answerability.main` without changing it, and adds what a
baseline needs in order to be reproducible and comparable later:

- environment: git commit and tag, tracked-file dirtiness, Ollama version, the
  chat and embedding models' digests and quantization, the published Wiki build;
- the retrieval configuration, read from the code's own defaults;
- every /api/chat request body, observed by a passive spy on `requests.post`
  (the call still goes through unchanged). Distinct system prompts and request
  shapes are recorded with SHA-256, so a regression run can prove it sent the
  same prompts and parameters as the baseline;
- every case's result in every run, for flip analysis in `compare_stage0.py`.

Outputs: the evaluator's raw run files go to `eval/artifacts/<label>/`
(git-ignored); the condensed, committed record is `eval/<label>.json`.

Usage:
    .venv\\Scripts\\python.exe eval\\run_stage0_eval.py --label baseline_qwen --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

DATASET = "eval_answerability_validation_v1.json"
ARTIFACTS_DIR = ROOT / "eval" / "artifacts"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


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
            shape = {
                "model": body.get("model"),
                "stream": body.get("stream"),
                "think": body.get("think"),
                "format": fmt if fmt in (None, "json") else "schema:" + sha256_text(
                    json.dumps(fmt, sort_keys=True, ensure_ascii=False))[:16],
                "options": body.get("options"),
                "tools": bool(body.get("tools")),
                "system_prompt_sha256": digest,
            }
            self.request_shapes[json.dumps(shape, sort_keys=True, ensure_ascii=False)] += 1
        elif isinstance(body, dict) and str(url).endswith("/api/embed"):
            self.embed_calls += 1
        return self.original(url, *args, **kwargs)

    def report(self) -> dict:
        return {
            "chat_calls": self.chat_calls,
            "embed_calls": self.embed_calls,
            "system_prompts": [
                {"sha256": digest, "calls": self.system_prompt_calls[digest], "text": text}
                for digest, text in sorted(self.system_prompts.items())
            ],
            "request_shapes": [
                {"calls": calls, **json.loads(shape)}
                for shape, calls in sorted(self.request_shapes.items())
            ],
        }


def ollama_model_info(base_url: str, model: str) -> dict:
    info: dict = {"name": model}
    try:
        tags = requests.get(f"{base_url}/api/tags", timeout=10).json().get("models", [])
        match = next((t for t in tags if t.get("name") == model or t.get("model") == model), None)
        if match:
            info["digest"] = match.get("digest")
        shown = requests.post(f"{base_url}/api/show", json={"model": model}, timeout=30).json()
        details = shown.get("details", {})
        info.update({
            "quantization_level": details.get("quantization_level"),
            "parameter_size": details.get("parameter_size"),
            "family": details.get("family"),
            "format": details.get("format"),
            "parameters": shown.get("parameters"),
        })
        from_line = re.search(r"^FROM (.+)$", shown.get("modelfile", ""), flags=re.MULTILINE)
        if from_line:
            info["weights_blob"] = Path(from_line.group(1).strip()).name
        general = shown.get("model_info", {})
        for key in ("general.basename", "general.finetune", "general.size_label", "general.name"):
            if key in general:
                info[key] = general[key]
    except requests.RequestException as exc:
        info["error"] = str(exc)
    return info


def retrieval_config(rag) -> dict:
    def defaults(function) -> dict:
        return {
            name: parameter.default
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is not inspect.Parameter.empty and parameter.default is not None
        }

    fast_source = inspect.getsource(rag.retrieve_fast)
    thresholds = re.search(r"first >= ([\d.]+) and \(second == 0 or first / second >= ([\d.]+)\)", fast_source)
    return {
        "split_text": defaults(rag.split_text),
        "bm25_rank": defaults(rag.bm25_rank),
        "hybrid_retrieve": defaults(rag.hybrid_retrieve),
        "retrieve_with_rerank": defaults(rag.retrieve_with_rerank),
        "retrieve_fast": defaults(rag.retrieve_fast),
        "bm25_fast_path": {
            "min_top_score": float(thresholds.group(1)) if thresholds else None,
            "min_ratio_to_second": float(thresholds.group(2)) if thresholds else None,
        },
        "embed_model": rag.EMBED_MODEL,
        "refusal_markers": list(rag.REFUSAL_MARKERS),
    }


def wiki_environment() -> dict:
    current = ROOT / "data" / "wiki" / "current.json"
    result: dict = {"current_json_present": current.exists()}
    if current.exists():
        result["current_json_sha256"] = hashlib.sha256(current.read_bytes()).hexdigest()
    try:
        import wiki_runtime

        result["published_build_id"] = wiki_runtime.RUNTIME.current_build_id()
    except Exception as exc:  # environment record only; never fail the run on it
        result["published_build_id_error"] = repr(exc)
    return result


def condense_cases(run_dir: Path, runs: int) -> list[dict]:
    per_case: dict[str, dict] = {}
    for run_index in range(1, runs + 1):
        data = json.loads((run_dir / f"run-{run_index}.json").read_text(encoding="utf-8"))
        for record in data["cases"]:
            entry = per_case.setdefault(record["id"], {
                "id": record["id"],
                "question": record["question"],
                "category": record["category"],
                "expected_behavior": record["expected_behavior"],
                "expected_route": record["expected_route"],
                "runs": [],
            })
            entry["runs"].append({
                "run": run_index,
                "passed": record["passed"],
                "actual_behavior": record["actual_behavior"],
                "actual_route": record["actual_route"],
                "model_called": record.get("model_called"),
                "all_facts_hit": record["all_facts_hit"],
                "has_citation": record["has_citation"],
                "citation_indices_valid": record["citation_indices_valid"],
                "required_source_coverage": record["required_source_coverage"],
                "duration_seconds": round(record["duration_seconds"], 3),
                "answer": record["answer"],
            })
    for entry in per_case.values():
        entry["pass_count"] = sum(r["passed"] for r in entry["runs"])
    return [per_case[key] for key in sorted(per_case)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="e.g. baseline_qwen / regression_qwen")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dataset", default=DATASET)
    args = parser.parse_args()

    import rag
    import evaluate_answerability

    ollama_url = getattr(rag, "OLLAMA_URL", "http://localhost:11434")
    try:
        ollama_version = requests.get(f"{ollama_url}/api/version", timeout=10).json().get("version")
    except requests.RequestException as exc:
        ollama_version = f"unavailable: {exc}"

    # Raw evaluator output is large and git-ignored (see eval/README.md); the
    # condensed eval/<label>.json below is what gets committed. Stage 0's own
    # raw runs predate this rule and stay where they were committed, eval/runs/.
    run_dir = ARTIFACTS_DIR / args.label
    spy = ChatRequestSpy()
    requests.post = spy
    try:
        exit_code = evaluate_answerability.main([
            "--dataset", str(ROOT / args.dataset),
            "--runs", str(args.runs),
            "--output-dir", str(run_dir),
        ])
    finally:
        requests.post = spy.original

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    tracked_dirty = [line for line in git("status", "--porcelain", "--untracked-files=no").splitlines() if line]
    llm_config = None
    try:
        import llm_provider  # present only after the Stage 0 change

        llm_config = llm_provider.load_config().public_dict()
    except ImportError:
        pass

    result = {
        "label": args.label,
        "dataset": args.dataset,
        "dataset_sha256": summary["context"]["dataset_sha256"],
        "runs": args.runs,
        "evaluator_exit_code": exit_code,
        "environment": {
            "git_commit": git("rev-parse", "HEAD"),
            "git_describe": git("describe", "--tags", "--always"),
            "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "tracked_files_dirty": tracked_dirty,
            # Lets a later commit be checked against the exact files that were evaluated.
            "code_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ("rag.py", "agent.py", "llm_provider.py", "chat_orchestration.py",
                             "evaluate_answerability.py")
                if (ROOT / name).exists()
            },
            "python": summary["context"]["python_version"],
            "platform": summary["context"]["platform"],
            "ollama_version": ollama_version,
            "ollama_url": ollama_url,
            "llm_provider_config": llm_config,
            "chat_model": ollama_model_info(ollama_url, rag.CHAT_MODEL),
            "embed_model": ollama_model_info(ollama_url, rag.EMBED_MODEL),
            "wiki": wiki_environment(),
        },
        "retrieval_config": retrieval_config(rag),
        "observed_llm_requests": spy.report(),
        "per_run_summary": summary["runs"],
        "aggregate": summary["aggregate"],
        "gates": summary["gates"],
        "cases": condense_cases(run_dir, args.runs),
    }
    output = ROOT / "eval" / f"{args.label}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
