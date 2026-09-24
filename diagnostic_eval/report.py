"""Case-level diagnostic report and aggregate root-cause distribution.

Counting rules:
- `primary_errors` counts failed case runs whose earliest failing stage is
  backed by a deterministic rule. Each failed case run counts at most once.
- `unattributed` counts failed case runs the rules could not attribute, by gap.
- `secondary_effects` and `latent_issues` are tallied separately and never
  feed the primary distribution.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import agent_trace

from .rules import (
    ERROR_CATEGORIES,
    STAGES,
    UNATTRIBUTED_KINDS,
    SKIPPED,
    DiagnosisContext,
    DisabledJudge,
    Diagnosis,
    SemanticJudge,
    diagnose,
)
from .labels import load_labels

ROOT = Path(__file__).resolve().parent.parent


def diagnose_eval(eval_json: str | Path, labels_path: str | Path | None, *,
                  trace_db: str | Path | None = None, judge: SemanticJudge | None = None) -> dict:
    """Diagnose every case run recorded in an eval/<label>.json file."""
    eval_json = Path(eval_json)
    record = json.loads(eval_json.read_text(encoding="utf-8"))
    dataset_path = ROOT / record["dataset"]
    labels = load_labels(dataset_path, labels_path)
    trace_path = Path(trace_db) if trace_db else (ROOT / record["trace_db"] if record.get("trace_db") else None)
    store = agent_trace.TraceStore(trace_path) if trace_path and trace_path.exists() else None
    judge = judge or DisabledJudge()
    context = eval_context(record)

    diagnoses: list[Diagnosis] = []
    for case in record["cases"]:
        case_labels = labels.get(case["id"])
        if case_labels is None:
            raise ValueError(f"case {case['id']} is not in {dataset_path.name}")
        for run in case["runs"]:
            run_id = run.get("trace_run_id")
            trace = store.load(run_id) if store is not None and run_id else None
            diagnoses.append(diagnose(case_labels, run, trace, judge, context))

    return {
        "source_eval": _display(eval_json),
        "dataset": record["dataset"],
        "dataset_sha256": record.get("dataset_sha256"),
        "labels": _display(labels_path),
        "trace_db": _display(trace_path),
        "judge": {"name": type(judge).__name__, "calls": getattr(judge, "calls", None)},
        "environment": {"wiki_corpus_evaluated": context.wiki_corpus,
                        "wiki_corpus_labelled": next(iter(labels.values())).wiki_corpus if labels else None},
        "label_coverage": label_coverage(labels),
        "aggregate": aggregate(diagnoses),
        "cases": [d.to_dict() for d in diagnoses],
    }


def eval_context(record: dict) -> DiagnosisContext:
    """What the evaluated run actually read, from the eval file's environment record."""
    wiki = (record.get("environment") or {}).get("wiki") or {}
    if "published_build_id" not in wiki:
        return DiagnosisContext()  # not recorded: labels are taken at face value
    build = wiki.get("published_build_id")
    return DiagnosisContext(wiki_corpus=f"published_build:{build}" if build else "committed_sample")


def _display(path) -> str | None:
    """Repo-relative, forward-slash path for the report (absolute if outside the repo)."""
    if path is None:
        return None
    resolved = Path(path).resolve()
    shown = resolved.relative_to(ROOT) if resolved.is_relative_to(ROOT) else resolved
    return str(shown).replace("\\", "/")


def label_coverage(labels: dict) -> dict:
    fields = ("acceptable_routes", "forbidden_tools", "plan_constraints", "expected_arguments",
              "expected_tool_status", "expected_evidence")
    return {"cases": len(labels),
            "overlay_fields": {name: sum(1 for l in labels.values() if l.provenance.get(name) == "overlay")
                               for name in fields}}


def aggregate(diagnoses: list[Diagnosis]) -> dict:
    failed = [d for d in diagnoses if not d.official_passed]
    primary = Counter(d.primary_error for d in failed if d.primary_error)
    primary_by_method = Counter(d.primary_method for d in failed if d.primary_error)
    unattributed = Counter(d.unattributed["kind"] for d in failed if d.unattributed)
    secondary = Counter(effect["category"] for d in failed for effect in d.secondary_effects)
    latent = Counter(issue["category"] for d in diagnoses for issue in d.latent_issues)
    stage_status = {stage: dict(Counter(d.stages[stage].status for d in diagnoses if stage in d.stages))
                    for stage in STAGES}
    skipped = Counter(check.name for d in diagnoses for result in d.stages.values()
                      for check in result.checks if check.status == SKIPPED)

    per_case: dict[str, list[Diagnosis]] = defaultdict(list)
    for d in failed:
        per_case[d.case_id].append(d)
    failing_cases = []
    for case_id, runs in sorted(per_case.items()):
        labels = [r.primary_error or f"unattributed:{r.unattributed['kind']}" for r in runs]
        failing_cases.append({"case_id": case_id, "failed_runs": [r.run for r in runs],
                              "attribution_per_run": labels, "consistent": len(set(labels)) == 1})

    return {
        "case_runs": len(diagnoses),
        "official_passed": len(diagnoses) - len(failed),
        "official_failed": len(failed),
        "primary_errors": {category: primary.get(category, 0) for category in ERROR_CATEGORIES},
        "primary_attributed": sum(primary.values()),
        "primary_by_method": dict(primary_by_method),
        "unattributed": {kind: unattributed.get(kind, 0) for kind in UNATTRIBUTED_KINDS},
        "secondary_effects": {category: secondary.get(category, 0) for category in ERROR_CATEGORIES},
        "latent_issues": {category: latent.get(category, 0) for category in ERROR_CATEGORIES},
        "stage_status": stage_status,
        "skipped_checks": dict(skipped),
        "failing_cases": failing_cases,
        "latent_cases": sorted({(d.case_id, issue["category"], issue["check"]) for d in diagnoses
                                for issue in d.latent_issues}),
    }


def markdown(report: dict) -> str:
    a = report["aggregate"]
    lines = [
        f"# Diagnostic report: {report['source_eval']}",
        "",
        f"- dataset: `{report['dataset']}` · labels: `{report['labels']}` · traces: `{report['trace_db']}`",
        f"- semantic judge: {report['judge']['name']} (calls: {report['judge']['calls']})",
        f"- case runs: {a['case_runs']} · official passed {a['official_passed']} · failed {a['official_failed']}",
        f"- failed runs with a deterministic primary root cause: {a['primary_attributed']} · "
        f"unattributed: {sum(a['unattributed'].values())}",
        "",
        "## Primary root cause (failed case runs, each counted once)",
        "",
        "| category | primary | secondary effects | latent issues (passed runs) |",
        "|---|---:|---:|---:|",
    ]
    for category in ERROR_CATEGORIES:
        lines.append(f"| {category} | {a['primary_errors'][category]} | {a['secondary_effects'][category]} | "
                     f"{a['latent_issues'][category]} |")
    lines += ["", "## Unattributed (diagnostic gaps, not error categories)", "",
              "| kind | failed runs |", "|---|---:|"]
    lines += [f"| {kind} | {count} |" for kind, count in a["unattributed"].items()]
    lines += ["", "## Stage status over all case runs", "", "| stage | " + " | ".join(
        ["pass", "fail", "inconclusive", "blocked", "not_applicable"]) + " |", "|---|" + "---:|" * 5]
    for stage in STAGES:
        counts = a["stage_status"].get(stage, {})
        lines.append(f"| {stage} | " + " | ".join(str(counts.get(s, 0)) for s in
                                                  ("pass", "fail", "inconclusive", "blocked", "not_applicable")) + " |")
    lines += ["", "## Failed case runs", ""]
    failed = [c for c in report["cases"] if not c["official_passed"]]
    if not failed:
        lines.append("None.")
    for case in failed:
        attribution = (f"**{case['primary_error']}** ({case['primary_stage']}) - {case['primary_reason']}"
                       if case["primary_error"] else
                       f"unattributed **{case['unattributed']['kind']}** - {case['unattributed'].get('detail', '')}")
        lines.append(f"- `{case['case_id']}` run {case['run']} · trace `{case['trace_run_id']}` · {attribution}")
        for effect in case["secondary_effects"]:
            lines.append(f"    - secondary: {effect['category']} ({effect['check']}) {effect['detail']}")
    lines += ["", "## Latent issues (official pass, a stage rule failed)", ""]
    lines += [f"- `{case_id}` {category} ({check})" for case_id, category, check in a["latent_cases"]] or ["None."]
    if a["skipped_checks"]:
        env = report["environment"]
        lines += ["", "## Skipped checks (label does not apply to this run's environment)", "",
                  f"Wiki labels were written for `{env['wiki_corpus_labelled']}`; this run read "
                  f"`{env['wiki_corpus_evaluated']}`.", ""]
        lines += [f"- {name}: {count} case runs" for name, count in a["skipped_checks"].items()]
    labels = report["label_coverage"]
    lines += ["", "## Label coverage", "", f"{labels['cases']} cases; overlay fields: " +
              ", ".join(f"{name} {count}" for name, count in labels["overlay_fields"].items())]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = output.with_suffix(".md")
    md.write_text(markdown(report), encoding="utf-8")
    return output, md
