"""Environment-pinned eval runs.

    python -m eval_env make   --env-id eval-env-v1 --dataset ... --labels ... --document ...
                              --system-fixture ... (--wiki-pages ... | --wiki-build ...)
    python -m eval_env verify --env eval-env-v1
    python -m eval_env run    --env eval-env-v1 --label env_v1_validation_qwen [--runs 3] [--allow-dirty]
    python -m eval_env diff   eval/<old>.json eval/<new>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import common
from .environment import ENVIRONMENTS_DIR, EnvironmentRefused, activate, make_environment, verify_environment

ARTIFACTS = common.ROOT / "eval" / "artifacts"
CODE_FILES = ("rag.py", "agent.py", "llm_provider.py", "chat_orchestration.py", "evaluate_answerability.py",
              "agent_trace.py", "eval_env/environment.py", "eval_env/common.py")


def run(env_id: str, label: str, runs: int, *, allow_dirty: bool = False) -> Path:
    if allow_dirty and "baseline" in label.lower():
        raise EnvironmentRefused("an --allow-dirty run is exploratory and may not be labelled as a baseline")
    output = common.ROOT / "eval" / f"{label}.json"
    run_dir = ARTIFACTS / label
    if output.exists() or run_dir.exists():
        raise EnvironmentRefused(f"{label} already has results; eval outputs are never overwritten")
    env = verify_environment(ENVIRONMENTS_DIR / env_id, allow_dirty=allow_dirty)

    import evaluate_answerability
    import llm_provider
    import rag
    import requests

    trace_db = run_dir / "traces.sqlite"
    spy = common.ChatRequestSpy()
    requests.post = spy
    undo_tracing = common.install_eval_tracing(evaluate_answerability, rag, trace_db, env_id=env.env_id)
    try:
        with activate(env, run_dir / "env-scratch") as index_facts:
            exit_code = evaluate_answerability.main([
                "--dataset", str(env.dataset_path), "--runs", str(runs), "--output-dir", str(run_dir)])
    finally:
        undo_tracing()
        requests.post = spy.original
    post_run = verify_environment(env.root, allow_dirty=True)  # the snapshots must be untouched by the run

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    retriever = common.retriever_config()
    result = {
        "label": label,
        "run_kind": "exploratory" if env.exploratory else "reference",
        "baseline_eligible": not env.exploratory,
        "dataset": env.manifest["dataset"]["path"],
        "dataset_sha256": env.manifest["dataset"]["sha256"],
        "diagnostic_labels": env.metadata()["diagnostic_labels"],
        "runs": runs,
        "evaluator_exit_code": exit_code,
        "environment": {
            "eval_environment": {**env.metadata(), "index": index_facts,
                                 "post_run_manifest_sha256": post_run.manifest_sha256},
            "git": {k: env.git[k] for k in ("commit", "describe", "branch", "dirty", "changes")},
            "runtime": common.runtime_versions(),
            "llm_provider_config": llm_provider.load_config().public_dict(),
            "chat_model": common.ollama_model_info(rag.CHAT_MODEL),
            "embed_model": common.ollama_model_info(rag.EMBED_MODEL),
            "retriever": {"config": retriever,
                          "config_sha256": common.sha256_text(json.dumps(retriever, sort_keys=True, ensure_ascii=False))},
            "code_sha256": {name: common.sha256_file(common.ROOT / name) for name in CODE_FILES
                            if (common.ROOT / name).exists()},
        },
        "observed_llm_requests": spy.report(),
        "trace_db": common.rel(trace_db),
        "trace_summary": common.trace_summary(trace_db),
        "per_run_summary": summary["runs"],
        "aggregate": summary["aggregate"],
        "gates": summary["gates"],
        "cases": common.condense_cases(run_dir, runs),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def environment_diff(old_path: Path, new_path: Path) -> dict:
    """Case-by-case differences between two runs made in different environments.

    Attribution is fixed: every difference is an environment change. This is
    deliberately not a regression judgement.
    """
    old, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (old_path, new_path))

    def wiki_of(record: dict) -> str:
        env = record.get("environment", {})
        pinned = (env.get("eval_environment") or {}).get("corpus", {}).get("wiki", {}).get("corpus_id")
        if pinned:
            return pinned
        build = (env.get("wiki") or {}).get("published_build_id")
        return f"unpinned (live data/wiki published_build:{build})" if build else "unpinned (committed sample fallback)"

    old_cases, new_cases = ({c["id"]: c for c in r["cases"]} for r in (old, new))
    rows = []
    for case_id in sorted(set(old_cases) | set(new_cases)):
        a, b = old_cases.get(case_id), new_cases.get(case_id)
        if a is None or b is None:
            rows.append({"id": case_id, "change": "only_in_" + ("new" if a is None else "old")})
            continue
        fields = {
            "pass_count": (a["pass_count"], b["pass_count"]),
            "behaviours": (sorted({r["actual_behavior"] for r in a["runs"]}), sorted({r["actual_behavior"] for r in b["runs"]})),
            "routes": (sorted({r["actual_route"] for r in a["runs"]}), sorted({r["actual_route"] for r in b["runs"]})),
            "source_types": (sorted({t for r in a["runs"] for t in (r.get("source_types") or [])}),
                             sorted({t for r in b["runs"] for t in (r.get("source_types") or [])})),
            "answers": (sorted({r["answer"] for r in a["runs"]}), sorted({r["answer"] for r in b["runs"]})),
        }
        changed = [name for name, (x, y) in fields.items() if x != y and not (name == "source_types" and not x)]
        change = ("pass_count_changed" if "pass_count" in changed else "behaviour_changed" if "behaviours" in changed
                  else "answer_changed" if "answers" in changed else "unchanged")
        rows.append({"id": case_id, "category": b.get("category"), "change": change, "changed_fields": changed,
                     **{name: {"old": x, "new": y} for name, (x, y) in fields.items() if name in changed or name == "pass_count"},
                     # A fact, not a judgement: how much the text already varies inside each side's own runs.
                     "distinct_answers_within_runs": {"old": len(fields["answers"][0]), "new": len(fields["answers"][1])},
                     "wiki_evidence_used": "wiki" in set(fields["source_types"][0]) | set(fields["source_types"][1]),
                     "attribution": "environment_change" if change != "unchanged" else None})
    counts: dict = {}
    for row in rows:
        counts[row["change"]] = counts.get(row["change"], 0) + 1
    return {
        "old": common.rel(Path(old_path)), "new": common.rel(Path(new_path)),
        "attribution_rule": "Every difference is attributed to the environment change; this is not a regression or "
                            "improvement judgement of the agent.",
        "environments": {"old": {"wiki": wiki_of(old), "git_commit": (old.get("environment", {}).get("git_sha")
                                                                      or old.get("environment", {}).get("git_commit"))},
                         "new": {"wiki": wiki_of(new), "env_id": new["environment"]["eval_environment"]["env_id"],
                                 "git_commit": new["environment"]["git"]["commit"]}},
        "passed_per_run": {"old": [r["passed"] for r in old["per_run_summary"]],
                           "new": [r["passed"] for r in new["per_run_summary"]]},
        "change_counts": counts,
        "cases": rows,
    }


def diff_markdown(diff: dict) -> str:
    env = diff["environments"]
    lines = [f"# Environment diff: {diff['old']} -> {diff['new']}", "", f"> {diff['attribution_rule']}", "",
             f"- old wiki: `{env['old']['wiki']}` (commit {str(env['old']['git_commit'])[:12]})",
             f"- new wiki: `{env['new']['wiki']}` in `{env['new']['env_id']}` (commit {env['new']['git_commit'][:12]})",
             f"- passed per run: old {diff['passed_per_run']['old']} -> new {diff['passed_per_run']['new']}",
             f"- cases: {diff['change_counts']}", "",
             "`distinct answers` counts different answer texts within each side's own runs; `wiki` says whether "
             "either side used wiki evidence. Both are facts shown for context; the attribution stays environment_change.",
             "", "| case | change | pass (old -> new) | distinct answers (old / new) | wiki | what changed |",
             "|---|---|---|---|---|---|"]
    for row in diff["cases"]:
        if row["change"] == "unchanged":
            continue
        passes = row.get("pass_count", {})
        details = []
        for name in ("behaviours", "routes", "source_types"):
            if name in row:
                details.append(f"{name}: {row[name]['old']} -> {row[name]['new']}")
        if "answers" in row:
            details.append("answer text differs")
        distinct = row.get("distinct_answers_within_runs", {})
        lines.append(f"| `{row['id']}` | {row['change']} | {passes.get('old')} -> {passes.get('new')} | "
                     f"{distinct.get('old')} / {distinct.get('new')} | {'yes' if row.get('wiki_evidence_used') else 'no'} | "
                     f"{'; '.join(details)} |")
    unchanged = [row["id"] for row in diff["cases"] if row["change"] == "unchanged"]
    lines += ["", f"Unchanged ({len(unchanged)}): " + ", ".join(f"`{c}`" for c in unchanged)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_env")
    sub = parser.add_subparsers(dest="command", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--env-id", required=True)
    mk.add_argument("--dataset", required=True)
    mk.add_argument("--labels", required=True)
    mk.add_argument("--document", required=True, action="append")
    mk.add_argument("--system-fixture", required=True)
    wiki = mk.add_mutually_exclusive_group(required=True)
    wiki.add_argument("--wiki-pages")
    wiki.add_argument("--wiki-build")
    mk.add_argument("--description", default="")
    vf = sub.add_parser("verify")
    vf.add_argument("--env", required=True)
    vf.add_argument("--allow-dirty", action="store_true")
    rn = sub.add_parser("run")
    rn.add_argument("--env", required=True)
    rn.add_argument("--label", required=True)
    rn.add_argument("--runs", type=int, default=3)
    rn.add_argument("--allow-dirty", action="store_true",
                    help="exploratory run on uncommitted code; marked in metadata, never a baseline")
    df = sub.add_parser("diff")
    df.add_argument("old")
    df.add_argument("new")
    args = parser.parse_args(argv)

    try:
        if args.command == "make":
            root = make_environment(
                args.env_id, dataset=common.ROOT / args.dataset, labels=common.ROOT / args.labels,
                documents=[common.ROOT / d for d in args.document], system_fixture=common.ROOT / args.system_fixture,
                wiki_pages=common.ROOT / args.wiki_pages if args.wiki_pages else None,
                wiki_build=common.ROOT / args.wiki_build if args.wiki_build else None, description=args.description)
            print(f"Created {common.rel(root)}")
        elif args.command == "verify":
            env = verify_environment(ENVIRONMENTS_DIR / args.env, allow_dirty=args.allow_dirty)
            print(f"{env.env_id} verified: {len(env.checks)} checks, manifest sha256 {env.manifest_sha256}, "
                  f"run kind {'exploratory' if env.exploratory else 'reference'}")
        elif args.command == "run":
            output = run(args.env, args.label, args.runs, allow_dirty=args.allow_dirty)
            print(f"Wrote {common.rel(output)}")
        else:
            diff = environment_diff(Path(args.old), Path(args.new))
            stem = f"{Path(args.new).stem}_vs_{Path(args.old).stem}.environment_diff"
            target = common.ROOT / "eval" / "diagnostics" / f"{stem}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
            target.with_suffix(".md").write_text(diff_markdown(diff), encoding="utf-8")
            print(f"{diff['change_counts']} -> wrote {common.rel(target)} and .md")
    except EnvironmentRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    sys.exit(main())
