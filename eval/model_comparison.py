"""Stage 2.5: a controlled model comparison inside one pinned eval environment.

    python eval/model_comparison.py run    --env eval-env-v1 --runs 3 [--series stage25|pmi]
    python eval/model_comparison.py report [--series stage25|pmi]

A *series* names one experiment: its labels and output directory. `stage25` is
the original Stage 2.5 run; `pmi` is the post-main-integration re-baseline, whose
report also compares each arm with its Stage 2.5 counterpart (a code change,
not an environment or model change).

The single experimental variable is the LLM provider/model. Everything else -
dataset, corpus, labels, embeddings, retriever, agent workflow and the code
commit - is held fixed:

- both arms run in one process from one clean commit, after one environment
  verification. Before the second arm the tree may differ from that state only
  by the first arm's own output file; anything else aborts the experiment.
- the provider is switched with LLM_PROVIDER + llm_provider.reset_provider();
  DeepSeek's thinking mode is pinned off by the provider itself.
- an outer wrapper around requests.post records every chat call's raw usage
  (including DeepSeek's cache hit/miss split) and wall-clock time, which the
  report turns into list-price cost. Agent, provider, eval_env and the
  diagnostic are used as they are.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ARTIFACTS = ROOT / "eval" / "artifacts"
SERIES = {
    "stage25": {"out_dir": ROOT / "eval" / "stage25", "prefix": "stage25", "kind": "stage-2.5 model comparison",
                "title": "Stage 2.5 — Qwen vs DeepSeek on eval-env-v1", "previous": None},
    "pmi": {"out_dir": ROOT / "eval" / "post_main_integration", "prefix": "pmi",
            "kind": "post-main-integration baseline",
            "title": "Post-main-integration baseline — Qwen vs DeepSeek on eval-env-v1",
            "previous": "stage25"},
}


def arms_for(series: str) -> tuple:
    prefix = SERIES[series]["prefix"]
    return ({"arm": "qwen", "label": f"{prefix}_qwen_env_v1", "provider": "ollama"},
            {"arm": "deepseek", "label": f"{prefix}_deepseek_env_v1", "provider": "deepseek"})
# https://api-docs.deepseek.com/quick_start/pricing, checked 2026-09-24. USD per 1M tokens.
DEEPSEEK_PRICING = {
    "model": "deepseek-flash",
    "source": "https://api-docs.deepseek.com/quick_start/pricing",
    "checked": "2026-09-24",
    "per_million_usd": {"off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.6},
                        "peak": {"input_cache_hit": 0.006, "input_cache_miss": 0.3, "output": 1.2}},
    "peak_hours_utc": "01:00-04:00 and 06:00-10:00, Monday-Friday (Chinese public holidays are off-peak; not modelled)",
}


def is_peak(moment: datetime) -> bool:
    utc = moment.astimezone(timezone.utc)
    return utc.weekday() < 5 and (1 <= utc.hour < 4 or 6 <= utc.hour < 10)


def call_cost(record: dict) -> float | None:
    usage = record.get("usage") or {}
    if record.get("kind") != "deepseek" or "prompt_cache_miss_tokens" not in usage:
        return None
    prices = DEEPSEEK_PRICING["per_million_usd"]["peak" if record["peak"] else "off_peak"]
    return (usage.get("prompt_cache_hit_tokens", 0) * prices["input_cache_hit"]
            + usage.get("prompt_cache_miss_tokens", 0) * prices["input_cache_miss"]
            + usage.get("completion_tokens", 0) * prices["output"]) / 1_000_000


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@contextmanager
def usage_recorder(arm: str, sink: list):
    """Outermost wrapper on requests.post: raw usage per chat call, nothing altered."""
    import requests

    import agent_trace

    original = requests.post

    def recording_post(url, *args, **kwargs):
        text = str(url)
        kind = "deepseek" if text.endswith("/chat/completions") else "ollama" if text.endswith("/api/chat") else None
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        response = original(url, *args, **kwargs)
        if kind is not None and not kwargs.get("stream"):
            run = agent_trace.current()
            entry = {"arm": arm, "kind": kind, "trace_run_id": run.run_id if run else None,
                     "started_at": started_at.isoformat(timespec="milliseconds"), "peak": is_peak(started_at),
                     "latency_ms": (perf_counter() - started) * 1000}
            try:
                body = response.json()
                if kind == "deepseek":
                    entry["usage"] = body.get("usage")
                    entry["model"] = body.get("model")
                else:
                    entry["usage"] = {"prompt_tokens": body.get("prompt_eval_count"),
                                      "completion_tokens": body.get("eval_count")}
            except Exception as exc:  # recording must never break the call
                entry["usage_error"] = repr(exc)
            sink.append(entry)
        return response

    requests.post = recording_post
    try:
        yield
    finally:
        requests.post = original


def run_experiment(env_id: str, runs: int, series: str = "stage25") -> Path:
    import llm_provider
    import rag
    from eval_env import __main__ as env_cli
    from eval_env import common
    from eval_env.environment import ENVIRONMENTS_DIR, EnvironmentRefused, verify_environment

    out_dir = SERIES[series]["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    experiment_path = out_dir / "experiment.json"
    if experiment_path.exists():
        raise EnvironmentRefused(f"the {series} experiment already ran; results are never overwritten")
    llm_provider.load_config("deepseek")  # fail before anything runs if DeepSeek is not configured
    verified = verify_environment(ENVIRONMENTS_DIR / env_id)  # clean tree, reference run
    produced: list[str] = []
    arm_records = []

    for arm in arms_for(series):
        # The only tree change allowed since verification: earlier arms' own outputs.
        changes = sorted(line[3:].strip() for line in common.git_state()["changes"])
        unexpected = [path for path in changes if path not in produced]
        if unexpected:
            raise EnvironmentRefused(f"tree changed during the experiment beyond arm outputs: {unexpected}")

        def reuse_verification(env_dir, *, allow_dirty=False):
            # Pre-run check: the experiment's single verification. Post-run check (allow_dirty):
            # the real one, which confirms the snapshots were not touched.
            return verify_environment(env_dir, allow_dirty=True) if allow_dirty else verified

        os.environ["LLM_PROVIDER"] = arm["provider"]
        llm_provider.reset_provider()
        config = llm_provider.load_config()
        usage: list[dict] = []
        started = datetime.now(timezone.utc)
        with patch.object(env_cli, "verify_environment", reuse_verification), \
                patch.object(rag, "CHAT_MODEL", config.model), usage_recorder(arm["arm"], usage):
            output = env_cli.run(env_id, arm["label"], runs)
        finished = datetime.now(timezone.utc)
        usage_path = ARTIFACTS / arm["label"] / "llm_usage.jsonl"
        usage_path.write_text("".join(json.dumps(u, ensure_ascii=False) + "\n" for u in usage), encoding="utf-8")
        produced.append(common.rel(output))
        arm_records.append({**arm, "provider_config": config.public_dict(), "output": common.rel(output),
                            "usage_log": common.rel(usage_path), "started_at": started.isoformat(timespec="seconds"),
                            "finished_at": finished.isoformat(timespec="seconds"), "chat_calls_recorded": len(usage)})
    os.environ.pop("LLM_PROVIDER", None)
    llm_provider.reset_provider()

    experiment = {
        "series": series, "baseline_kind": SERIES[series]["kind"],
        "question": "Qwen vs DeepSeek on eval-env-v1; the LLM provider/model is the only variable",
        "env_id": env_id, "environment_manifest_sha256": verified.manifest_sha256, "runs_per_arm": runs,
        "git": {k: verified.git[k] for k in ("commit", "dirty")},
        "verification": "one clean verification for the whole experiment; before each later arm the tree differed "
                        "only by the earlier arms' output files",
        "deepseek_thinking": "disabled (pinned by OpenAICompatibleProvider)",
        "pricing": DEEPSEEK_PRICING, "arms": arm_records,
    }
    experiment_path.write_text(json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")
    return experiment_path


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def _pct(values):
    ordered = sorted(values)
    return ordered[min(int(round(0.95 * (len(ordered) - 1))), len(ordered) - 1)] if ordered else None


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def arm_metrics(arm: dict, eval_record: dict, diagnostic: dict, usage: list[dict]) -> dict:
    import agent_trace

    store = agent_trace.TraceStore(ROOT / eval_record["trace_db"])
    per_run = []
    for index, summary in enumerate(eval_record["per_run_summary"], start=1):
        per_run.append({"run": index, "passed": summary["passed"], "pass_rate": summary["pass_rate"],
                        "answer_success_rate": summary["answer_success_rate"],
                        "false_refusal_rate": summary["false_refusal_rate"]})

    # Trace facts per case run: tokens, LLM calls, tool calls, adaptations.
    traces = {}
    for case in eval_record["cases"]:
        for run in case["runs"]:
            trace = store.load(run["trace_run_id"])
            spans = trace["spans"]
            execute = next((s for s in spans if s["name"] == "execute_plan"), None)
            tools = [s for s in spans if execute and s["parent_span_id"] == execute["span_id"]]
            llm = [s for s in spans if s["stage"] == "llm_call"]
            adaptations = [a for s in llm for a in ((s.get("attributes") or {}).get("prompt_adaptations") or [])]
            differs = sum(1 for s in llm if (s.get("attributes") or {}).get("logical_prompt_sha256")
                          != (s.get("attributes") or {}).get("effective_prompt_sha256"))
            traces[(case["id"], run["run"])] = {
                "duration_s": run["duration_seconds"], "passed": run["passed"],
                "prompt_tokens": trace["run"]["prompt_tokens"] or 0,
                "completion_tokens": trace["run"]["completion_tokens"] or 0,
                "llm_calls": trace["run"]["llm_calls"] or 0, "tool_calls": len(tools),
                "llm_latency_ms": trace["run"]["llm_latency_ms"] or 0,
                "adaptations": [a.get("reason") for a in adaptations], "effective_differs": differs,
                "llm_call_names": [s["name"] for s in llm],
            }
    facts = list(traces.values())
    task_runs = len(facts)
    passed_runs = sum(f["passed"] for f in facts)

    diag_by_run = {}
    for entry in diagnostic["cases"]:
        bucket = diag_by_run.setdefault(entry["run"], {"primary": {}, "secondary": {}, "latent": {}, "unattributed": {}})
        if entry["primary_error"]:
            bucket["primary"][entry["primary_error"]] = bucket["primary"].get(entry["primary_error"], 0) + 1
        if entry["unattributed"]:
            kind = entry["unattributed"]["kind"]
            bucket["unattributed"][kind] = bucket["unattributed"].get(kind, 0) + 1
        for effect in entry["secondary_effects"]:
            bucket["secondary"][effect["category"]] = bucket["secondary"].get(effect["category"], 0) + 1
        for issue in entry["latent_issues"]:
            bucket["latent"][issue["category"]] = bucket["latent"].get(issue["category"], 0) + 1

    case_usage = [u for u in usage if u.get("trace_run_id")]
    costs = [call_cost(u) for u in case_usage]
    cost_known = [c for c in costs if c is not None]
    total_cost = sum(cost_known) if cost_known else None
    hit = sum((u.get("usage") or {}).get("prompt_cache_hit_tokens", 0) for u in case_usage if u["kind"] == "deepseek")
    miss = sum((u.get("usage") or {}).get("prompt_cache_miss_tokens", 0) for u in case_usage if u["kind"] == "deepseek")
    aggregate = eval_record["aggregate"]
    adaptation_counts: dict = {}
    for f in facts:
        for reason in f["adaptations"]:
            adaptation_counts[reason] = adaptation_counts.get(reason, 0) + 1
    return {
        "arm": arm["arm"], "label": arm["label"], "provider": arm["provider_config"],
        "per_run": per_run,
        "mean": {key: _mean([r[key] for r in per_run]) for key in ("passed", "pass_rate", "answer_success_rate",
                                                                  "false_refusal_rate")},
        "stability": {"stability_rate": aggregate["stability_rate"], "stable_pass": aggregate["stable_pass"],
                      "stable_fail": aggregate["stable_fail"], "unstable": aggregate["unstable"],
                      "unstable_ids": aggregate["unstable_ids"], "stable_fail_ids": aggregate["stable_fail_ids"]},
        "diagnostic_aggregate": {k: diagnostic["aggregate"][k] for k in (
            "official_failed", "primary_errors", "unattributed", "secondary_effects", "latent_issues")},
        "diagnostic_per_run": dict(sorted(diag_by_run.items())),
        "latency_s": {"task_mean": _mean([f["duration_s"] for f in facts]), "task_p95": _pct([f["duration_s"] for f in facts]),
                      "llm_mean_per_task": _mean([f["llm_latency_ms"] / 1000 for f in facts])},
        "tokens": {"prompt_total": sum(f["prompt_tokens"] for f in facts),
                   "completion_total": sum(f["completion_tokens"] for f in facts),
                   "prompt_per_task": _mean([f["prompt_tokens"] for f in facts]),
                   "completion_per_task": _mean([f["completion_tokens"] for f in facts]),
                   "deepseek_cache_hit_tokens": hit if arm["arm"] == "deepseek" else None,
                   "deepseek_cache_miss_tokens": miss if arm["arm"] == "deepseek" else None},
        "calls": {"llm_calls_total": sum(f["llm_calls"] for f in facts), "llm_calls_per_task": _mean([f["llm_calls"] for f in facts]),
                  "tool_calls_total": sum(f["tool_calls"] for f in facts), "tool_calls_per_task": _mean([f["tool_calls"] for f in facts]),
                  "usage_records": len(case_usage), "warmup_or_untraced_calls": len(usage) - len(case_usage)},
        "cost_usd": None if total_cost is None else {
            "total": total_cost, "per_task": total_cost / task_runs,
            "per_successful_task": total_cost / passed_runs if passed_runs else None,
            "task_runs": task_runs, "successful_task_runs": passed_runs,
            "calls_priced": len(cost_known), "calls_at_peak": sum(1 for u in case_usage if u.get("peak")),
            "basis": "list price x recorded usage (cache hit/miss split, peak by call time); not reconciled with the invoice"},
        "prompt_adaptations": {"calls_with_effective_prompt_differing": sum(f["effective_differs"] for f in facts),
                               "by_reason": adaptation_counts},
        "_traces": traces,
    }


def transitions(qwen: dict, deepseek: dict) -> dict:
    q = {c["id"]: c for c in qwen["cases"]}
    d = {c["id"]: c for c in deepseek["cases"]}
    runs_q, runs_d = qwen["runs"], deepseek["runs"]
    rows, counts = [], {}
    for case_id in sorted(q):
        a, b = q[case_id]["pass_count"], d[case_id]["pass_count"]
        if a == runs_q and b == runs_d:
            kind = "stable_pass"
        elif a == 0 and b == runs_d:
            kind = "fixed"
        elif a == runs_q and b == 0:
            kind = "newly_failed"
        elif a == 0 and b == 0:
            kind = "unchanged_failure"
        else:
            kind = "unstable"  # at least one side is neither all-pass nor all-fail: not forced into the four
        counts[kind] = counts.get(kind, 0) + 1
        if kind != "stable_pass":
            rows.append({"id": case_id, "category": q[case_id]["category"], "transition": kind,
                         "qwen_pass": f"{a}/{runs_q}", "deepseek_pass": f"{b}/{runs_d}",
                         "qwen_behaviours": sorted({r["actual_behavior"] for r in q[case_id]["runs"]}),
                         "deepseek_behaviours": sorted({r["actual_behavior"] for r in d[case_id]["runs"]})})
    for kind in ("stable_pass", "fixed", "newly_failed", "unchanged_failure", "unstable"):
        counts.setdefault(kind, 0)
    return {"definitions": {"stable_pass": "3/3 in both", "fixed": "0/3 qwen -> 3/3 deepseek",
                            "newly_failed": "3/3 qwen -> 0/3 deepseek", "unchanged_failure": "0/3 in both",
                            "unstable": "any other combination (partial pass on either side)"},
            "counts": counts, "cases": rows}


def h008_comparison(records: dict) -> dict:
    import agent_trace

    out = {}
    for arm, record in records.items():
        case = next(c for c in record["cases"] if c["id"] == "answer_document_h008")
        run = case["runs"][0]
        trace = agent_trace.load_trace(ROOT / record["trace_db"], run["trace_run_id"])
        spans = {s["name"]: s for s in trace["spans"]}
        out[arm] = {"trace_run_id": run["trace_run_id"], "passes": f"{case['pass_count']}/{record['runs']}",
                    "answer": run["answer"], "signals": spans["plan_request"]["output"]["signals"],
                    "route": spans["plan_request"]["output"]["route"],
                    "evidence_outcome": spans["evaluate_evidence"]["output"]["outcome"],
                    "evidence_reasons": spans["evaluate_evidence"]["output"]["reason_codes"],
                    "retrieved": [e["locator"] for e in spans["document_search"]["output"]["evidence"]],
                    "llm_calls": trace["run"]["llm_calls"], "provider": trace["run"]["provider"],
                    "model": trace["run"]["model"], "tree": agent_trace.format_trace(trace)}
    return out


def code_change_diff(old: dict, new: dict, old_diag: dict, new_diag: dict, attribution: str) -> dict:
    """Same environment and model, different code: case-level changes between two runs."""
    def primaries(diag):
        out: dict = {}
        for entry in diag["cases"]:
            if not entry["official_passed"]:
                label = entry["primary_error"] or f"unattributed:{entry['unattributed']['kind']}"
                out.setdefault(entry["case_id"], []).append(label)
        return out

    old_p, new_p = primaries(old_diag), primaries(new_diag)
    old_cases, new_cases = ({c["id"]: c for c in r["cases"]} for r in (old, new))
    rows, counts = [], {}
    for cid in sorted(old_cases):
        a, b = old_cases[cid], new_cases[cid]
        beh_a, beh_b = sorted({r["actual_behavior"] for r in a["runs"]}), sorted({r["actual_behavior"] for r in b["runs"]})
        ans_a, ans_b = sorted({r["answer"] for r in a["runs"]}), sorted({r["answer"] for r in b["runs"]})
        if a["pass_count"] != b["pass_count"]:
            change = "pass_count_changed"
        elif beh_a != beh_b:
            change = "behaviour_changed"
        elif ans_a != ans_b:
            change = "answer_changed"
        else:
            change = "unchanged"
        counts[change] = counts.get(change, 0) + 1
        if change != "unchanged":
            rows.append({"id": cid, "category": b["category"], "change": change,
                         "pass": f"{a['pass_count']}/{old['runs']} -> {b['pass_count']}/{new['runs']}",
                         "behaviours": [beh_a, beh_b], "distinct_answers": [len(ans_a), len(ans_b)],
                         "primary": [old_p.get(cid), new_p.get(cid)], "attribution": attribution})
    return {"attribution": attribution, "counts": counts, "cases": rows,
            "passed_per_run": [[r["passed"] for r in old["per_run_summary"]], [r["passed"] for r in new["per_run_summary"]]]}


def build_report(series: str = "stage25") -> dict:
    from diagnostic_eval.report import diagnose_eval, write_report

    out_dir = SERIES[series]["out_dir"]
    written = [out_dir / "qwen_vs_deepseek.json", out_dir / "qwen_vs_deepseek.md"]
    written += [ROOT / "eval" / "diagnostics" / f"{arm['label']}.diagnostic{ext}"
                for arm in arms_for(series) for ext in (".json", ".md")]
    existing = [str(path.relative_to(ROOT)) for path in written if path.exists()]
    if existing:
        # Reports are results too: a later code version must never rewrite an earlier series.
        from eval_env.environment import EnvironmentRefused

        raise EnvironmentRefused(f"report outputs already exist and are never overwritten: {existing}")
    experiment = json.loads((out_dir / "experiment.json").read_text(encoding="utf-8"))
    labels = ROOT / "eval" / "environments" / experiment["env_id"] / "labels" / "validation_v1.labels.json"
    records, metrics, diagnostics = {}, {}, {}
    for arm in experiment["arms"]:
        record = json.loads((ROOT / arm["output"]).read_text(encoding="utf-8"))
        diagnostic = diagnose_eval(ROOT / arm["output"], labels)
        diagnostics[arm["arm"]] = diagnostic
        write_report(diagnostic, ROOT / "eval" / "diagnostics" / f"{arm['label']}.diagnostic.json")
        usage = [json.loads(line) for line in (ROOT / arm["usage_log"]).read_text(encoding="utf-8").splitlines() if line]
        records[arm["arm"]] = record
        metrics[arm["arm"]] = arm_metrics(arm, record, diagnostic, usage)
    previous = SERIES[series]["previous"]
    versus_previous = {}
    if previous:
        from diagnostic_eval.report import diagnose_eval as _diagnose

        for arm in experiment["arms"]:
            old_label = f"{SERIES[previous]['prefix']}_{arm['arm']}_env_v1"
            old_path = ROOT / "eval" / f"{old_label}.json"
            old = json.loads(old_path.read_text(encoding="utf-8"))
            old_commit, new_commit = old["environment"]["git"]["commit"], records[arm["arm"]]["environment"]["git"]["commit"]
            versus_previous[arm["arm"]] = {
                "old": old_label, "new": arm["label"], "old_commit": old_commit, "new_commit": new_commit,
                **code_change_diff(old, records[arm["arm"]], _diagnose(old_path, labels), diagnostics[arm["arm"]],
                                   f"code_change: {old_commit[:7]} -> {new_commit[:7]} (main integration); "
                                   "same environment, same model"),
            }
    report = {
        "experiment": {k: v for k, v in experiment.items() if k != "arms"},
        "versus_previous_series": versus_previous,
        "arms": {name: {k: v for k, v in m.items() if k != "_traces"} for name, m in metrics.items()},
        "transitions": transitions(records["qwen"], records["deepseek"]),
        "h008": h008_comparison(records),
        "notes": {
            "tokens": "Token counts come from each provider's own tokenizer and are descriptive only; they are not "
                      "comparable as context size and say nothing about context optimisation.",
            "prompts": "DeepSeek's effective prompts are not byte-identical to Qwen's: schema requests are sent as "
                       "json_object with an appended JSON Schema instruction (see prompt_adaptations). Logical prompts "
                       "are identical by construction.",
            "cost": "Qwen runs locally; its API cost is 0 and hardware is not costed.",
        },
    }
    (out_dir / "qwen_vs_deepseek.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "qwen_vs_deepseek.md").write_text(markdown(report, SERIES[series]["title"]), encoding="utf-8")
    return report


def markdown(report: dict, title: str = "Stage 2.5 — Qwen vs DeepSeek on eval-env-v1") -> str:
    q, d = report["arms"]["qwen"], report["arms"]["deepseek"]
    fmt = lambda v, p=1: "-" if v is None else f"{v * 100:.{p}f}%"
    num = lambda v, p=2: "-" if v is None else f"{v:.{p}f}"
    lines = [
        f"# {title}", "",
        f"- series `{report['experiment'].get('series', 'stage25')}` — {report['experiment'].get('baseline_kind', 'stage-2.5 model comparison')}",
        f"- environment `{report['experiment']['env_id']}` (manifest `{report['experiment']['environment_manifest_sha256'][:16]}…`), "
        f"commit `{report['experiment']['git']['commit'][:12]}`, {report['experiment']['runs_per_arm']} runs per arm",
        f"- only variable: provider/model — qwen `{q['provider']['model']}` (ollama) vs deepseek `{d['provider']['model']}`; "
        f"DeepSeek thinking {report['experiment']['deepseek_thinking']}", "",
        "## Official results", "", "| metric | qwen | deepseek |", "|---|---|---|",
        f"| passed per run | {' / '.join(str(r['passed']) for r in q['per_run'])} | {' / '.join(str(r['passed']) for r in d['per_run'])} |",
        f"| pass rate (mean) | {fmt(q['mean']['pass_rate'])} | {fmt(d['mean']['pass_rate'])} |",
        f"| answer success (mean) | {fmt(q['mean']['answer_success_rate'])} | {fmt(d['mean']['answer_success_rate'])} |",
        f"| false refusal (mean) | {fmt(q['mean']['false_refusal_rate'])} | {fmt(d['mean']['false_refusal_rate'])} |",
        f"| cross-run stability | {fmt(q['stability']['stability_rate'])} (unstable {q['stability']['unstable']}) | "
        f"{fmt(d['stability']['stability_rate'])} (unstable {d['stability']['unstable']}) |",
        "", "## Diagnostic (all case runs; primary counted once per failed run)", "",
        "| | qwen | deepseek |", "|---|---|---|"]
    for category in q["diagnostic_aggregate"]["primary_errors"]:
        lines.append(f"| primary {category} | {q['diagnostic_aggregate']['primary_errors'][category]} | "
                     f"{d['diagnostic_aggregate']['primary_errors'][category]} |")
    for group in ("secondary_effects", "latent_issues", "unattributed"):
        lines.append(f"| {group} | {sum(q['diagnostic_aggregate'][group].values())} "
                     f"{ {k: v for k, v in q['diagnostic_aggregate'][group].items() if v} or ''} | "
                     f"{sum(d['diagnostic_aggregate'][group].values())} {({k: v for k, v in d['diagnostic_aggregate'][group].items() if v}) or ''} |")
    lines += ["", "Per run:", ""]
    for arm_name, arm in (("qwen", q), ("deepseek", d)):
        for run, bucket in arm["diagnostic_per_run"].items():
            lines.append(f"- {arm_name} run {run}: primary {bucket['primary'] or '{}'}, secondary {bucket['secondary'] or '{}'}, "
                         f"latent {bucket['latent'] or '{}'}, unattributed {bucket['unattributed'] or '{}'}")
    t = report["transitions"]
    lines += ["", "## Case transitions (qwen -> deepseek, over 3 runs each)", "",
              " · ".join(f"{k} {v}" for k, v in t["counts"].items()), "",
              "| case | transition | qwen | deepseek | behaviours (qwen -> deepseek) |", "|---|---|---|---|---|"]
    lines += [f"| `{r['id']}` | {r['transition']} | {r['qwen_pass']} | {r['deepseek_pass']} | "
              f"{r['qwen_behaviours']} -> {r['deepseek_behaviours']} |" for r in t["cases"]] or ["| - | all stable_pass | | | |"]
    lines += ["", "## Latency, tokens, calls, cost", "", "| | qwen | deepseek |", "|---|---|---|",
              f"| task latency mean / p95 (s) | {num(q['latency_s']['task_mean'])} / {num(q['latency_s']['task_p95'])} | "
              f"{num(d['latency_s']['task_mean'])} / {num(d['latency_s']['task_p95'])} |",
              f"| LLM latency per task (s) | {num(q['latency_s']['llm_mean_per_task'])} | {num(d['latency_s']['llm_mean_per_task'])} |",
              f"| prompt / completion tokens (total) | {q['tokens']['prompt_total']} / {q['tokens']['completion_total']} | "
              f"{d['tokens']['prompt_total']} / {d['tokens']['completion_total']} |",
              f"| prompt / completion tokens per task | {num(q['tokens']['prompt_per_task'], 1)} / {num(q['tokens']['completion_per_task'], 1)} | "
              f"{num(d['tokens']['prompt_per_task'], 1)} / {num(d['tokens']['completion_per_task'], 1)} |",
              f"| DeepSeek cache hit / miss tokens | - | {d['tokens']['deepseek_cache_hit_tokens']} / {d['tokens']['deepseek_cache_miss_tokens']} |",
              f"| LLM calls total (per task) | {q['calls']['llm_calls_total']} ({num(q['calls']['llm_calls_per_task'])}) | "
              f"{d['calls']['llm_calls_total']} ({num(d['calls']['llm_calls_per_task'])}) |",
              f"| tool calls total (per task) | {q['calls']['tool_calls_total']} ({num(q['calls']['tool_calls_per_task'])}) | "
              f"{d['calls']['tool_calls_total']} ({num(d['calls']['tool_calls_per_task'])}) |"]
    cost = d["cost_usd"]
    if cost:
        lines += [f"| API cost total | $0 (local) | ${cost['total']:.6f} |",
                  f"| cost / task | $0 | ${cost['per_task']:.8f} ({cost['task_runs']} task runs) |",
                  f"| cost / successful task | $0 | ${cost['per_successful_task']:.8f} ({cost['successful_task_runs']} passed) |"]
    lines += ["", f"_{report['notes']['tokens']}_", "", f"_{report['notes']['cost']}_ Cost basis: {cost['basis'] if cost else '-'}; "
              f"{cost['calls_at_peak'] if cost else 0} of {cost['calls_priced'] if cost else 0} DeepSeek calls fell in peak hours.", "",
              "## Prompt adaptation (DeepSeek)", "",
              f"{report['notes']['prompts']}", "",
              f"- qwen: calls whose effective prompt differs from the logical one: {q['prompt_adaptations']['calls_with_effective_prompt_differing']}; "
              f"adaptations {q['prompt_adaptations']['by_reason'] or '{}'}",
              f"- deepseek: calls whose effective prompt differs from the logical one: {d['prompt_adaptations']['calls_with_effective_prompt_differing']}; "
              f"adaptations {d['prompt_adaptations']['by_reason']}", "",
              "## answer_document_h008 trace comparison (run 1)", ""]
    for arm_name, h in report["h008"].items():
        lines += [f"### {arm_name} — {h['passes']} passed", "", "```", h["tree"], "```",
                  f"- route `{h['route']}`, requires_freshness `{h['signals']['requires_freshness']}`, "
                  f"evidence `{h['evidence_outcome']}` {h['evidence_reasons']}, llm_calls {h['llm_calls']}",
                  f"- retrieved {h['retrieved']}", f"- answer: {h['answer']}", ""]
    for arm_name, diff in report.get("versus_previous_series", {}).items():
        lines += ["", f"## {arm_name}: {diff['old']} -> {diff['new']} (code change, same env and model)", "",
                  f"> {diff['attribution']}", "",
                  f"- passed per run: {diff['passed_per_run'][0]} -> {diff['passed_per_run'][1]}",
                  f"- cases: {diff['counts']}", "",
                  "| case | change | pass | behaviours | distinct answers (old / new) | primary (old -> new) |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| `{r['id']}` | {r['change']} | {r['pass']} | {r['behaviours'][0]} -> {r['behaviours'][1]} | "
                  f"{r['distinct_answers'][0]} / {r['distinct_answers'][1]} | {r['primary'][0]} -> {r['primary'][1]} |"
                  for r in diff["cases"]] or ["| - | unchanged | | | | |"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--env", default="eval-env-v1")
    run_parser.add_argument("--runs", type=int, default=3)
    run_parser.add_argument("--series", choices=sorted(SERIES), default="stage25")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--series", choices=sorted(SERIES), default="stage25")
    args = parser.parse_args(argv)
    if args.command == "run":
        print(f"Wrote {run_experiment(args.env, args.runs, args.series)}")
    else:
        report = build_report(args.series)
        print(json.dumps({arm: {"passed": [r["passed"] for r in m["per_run"]], "cost": m["cost_usd"]}
                          for arm, m in report["arms"].items()}, ensure_ascii=False))
        print(f"transitions: {report['transitions']['counts']}")
        for arm, diff in report.get("versus_previous_series", {}).items():
            print(f"{arm} vs previous series: {diff['counts']}")
        print(f"Wrote {SERIES[args.series]['out_dir'] / 'qwen_vs_deepseek.md'}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    sys.exit(main())
