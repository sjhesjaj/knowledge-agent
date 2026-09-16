"""R2 coverage self-test: does honouring `top_k` lose the facts a multi-clause
question needs?

Two measurements, both on questions with more clauses than the sub-query cap
(`r2_coverage_cases.json`, every clause annotated with the handbook section that
answers it):

A. **Retrieval coverage** - `document_search` at `top_k` 3 and 4. For each
   question: how many pieces of evidence came back, how many sub-queries ran,
   and what fraction of the answerable clauses have their section in the
   evidence.
B. **Answer coverage** - the production path, through
   `evaluate_answerability.run_case` imported unchanged (so
   `chat_orchestration.prepare` + `rag.answer_structured`, production
   `top_k`). What fraction of the answerable clauses have their fact in the
   delivered answer.

Run it inside an isolated candidate copy - it imports `rag` from the current
directory - and write the output outside the copy:

    python r2_coverage.py run <cases.json> <out.json> [--runs N]
    python r2_coverage.py compare <cases.json> <before.json> <after.json>

`compare` also derives the two reference points the fix is judged against:
*naive truncation* (the old ordered selection cut with `[:top_k]` - the shape
the review warned against, because it drops the last clauses first) and the
*best achievable* coverage with `top_k` distinct sections.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from time import perf_counter

TOP_KS = (3, 4)


def load_cases(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def answerable(case: dict) -> list[dict]:
    return [clause for clause in case["clauses"] if clause["section"] is not None]


def section_coverage(case: dict, indices: list[int]) -> float:
    clauses = answerable(case)
    present = set(indices)
    return sum(clause["section"] in present for clause in clauses) / len(clauses)


def best_achievable(case: dict, top_k: int) -> float:
    """The most clauses any `top_k` distinct sections could cover."""
    clauses = answerable(case)
    sections = sorted({clause["section"] for clause in clauses})
    if len(sections) <= top_k:
        return 1.0
    best = 0
    for chosen in itertools.combinations(sections, top_k):
        best = max(best, sum(clause["section"] in chosen for clause in clauses))
    return best / len(clauses)


def run(cases_path: Path, out_path: Path, runs: int) -> int:
    import evaluate_answerability as ev
    import rag
    from orchestration.document_adapter import document_search

    spec = load_cases(cases_path)
    chunks = ev.build_chunks()
    warm = ev.warm_up(chunks)
    context = {
        "git_sha": "isolated-copy",
        "chat_model": rag.CHAT_MODEL,
        "embed_model": rag.EMBED_MODEL,
        "dataset_sha256": ev.sha256_of(cases_path),
    }
    import hashlib
    rag_path = Path(rag.__file__).resolve()
    rag_sha = hashlib.sha256(rag_path.read_bytes()).hexdigest()
    context["rag_path"], context["rag_sha256"] = str(rag_path), rag_sha
    print(f"rag={rag_path} sha256={rag_sha[:16]}")
    print(f"chunks={len(chunks)} warm-up={warm:.2f}s runs={runs}")

    retrieval: list[dict] = []
    answers: list[dict] = []
    for run_index in range(1, runs + 1):
        for case in spec["cases"]:
            for top_k in TOP_KS:
                trace: dict = {}
                started = perf_counter()
                result = document_search(case["question"], chunks, top_k=top_k, trace=trace)
                indices = [item.metadata["chunk_index"] for item in result.evidence]
                retrieval.append({
                    "run": run_index,
                    "id": case["id"],
                    "top_k": top_k,
                    "evidence": indices,
                    "count": len(indices),
                    "overflow": max(0, len(indices) - top_k),
                    "path": trace.get("retrieval_path"),
                    "sub_questions": trace.get("sub_questions"),
                    "clauses": trace.get("clauses"),
                    "coverage": section_coverage(case, indices),
                    "seconds": perf_counter() - started,
                })

            clauses = answerable(case)
            record = ev.run_case(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "expected_behavior": "answer",
                    "expected_route": "document_only",
                    "required_source_types": ["document"],
                    "expected_fact_patterns": [clause["facts"] for clause in clauses],
                    "category": "document",
                    "difficulty": "hard",
                },
                chunks,
                run_index,
                context,
            )
            hits = [group["hit"] for group in record["expected_fact_groups"]]
            answers.append({
                "run": run_index,
                "id": case["id"],
                "behavior": record["actual_behavior"],
                "outcome": record["outcome"],
                "reason_codes": record["reason_codes"],
                "route": record["actual_route"],
                "source_count": record["source_count"],
                "clause_hits": dict(zip((clause["text"] for clause in clauses), hits)),
                "fact_coverage": sum(hits) / len(hits),
                "answer": record["answer"],
                "seconds": record["duration_seconds"],
            })
            last = retrieval[-1]
            print(f"run {run_index} {case['id']}  top_k=4 evidence={last['count']} "
                  f"subs={last['sub_questions']} path={last['path']}  "
                  f"answer={record['actual_behavior']} facts={sum(hits)}/{len(hits)}")

    out_path.write_text(
        json.dumps(
            {"context": context, "retrieval": retrieval, "answers": answers},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_path}")
    return 0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compare(cases_path: Path, before_path: Path, after_path: Path) -> int:
    spec = load_cases(cases_path)
    by_id = {case["id"]: case for case in spec["cases"]}
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))

    print("A. Retrieval coverage (share of answerable clauses whose section is in the evidence)")
    for top_k in TOP_KS:
        rows_b = [r for r in before["retrieval"] if r["top_k"] == top_k]
        rows_a = [r for r in after["retrieval"] if r["top_k"] == top_k]
        naive = [
            section_coverage(by_id[r["id"]], r["evidence"][:top_k]) for r in rows_b
        ]
        ideal = [best_achievable(by_id[r["id"]], top_k) for r in rows_a]
        print(f"\n  top_k={top_k}")
        print(f"    {'':26s} {'max count':>9s} {'overflow':>8s} {'max subs':>8s} {'coverage':>8s}")
        print(f"    {'before (r10)':26s} {max(r['count'] for r in rows_b):9d} "
              f"{sum(r['overflow'] > 0 for r in rows_b):8d} "
              f"{max(r['sub_questions'] or 0 for r in rows_b):8d} "
              f"{_mean([r['coverage'] for r in rows_b]):8.1%}")
        print(f"    {'naive [:top_k] of before':26s} {top_k:9d} {0:8d} {'-':>8s} {_mean(naive):8.1%}")
        print(f"    {'after (this round)':26s} {max(r['count'] for r in rows_a):9d} "
              f"{sum(r['overflow'] > 0 for r in rows_a):8d} "
              f"{max(r['sub_questions'] or 0 for r in rows_a):8d} "
              f"{_mean([r['coverage'] for r in rows_a]):8.1%}")
        print(f"    {'best achievable in budget':26s} {top_k:9d} {0:8d} {'-':>8s} {_mean(ideal):8.1%}")

        print(f"    per question (mean over runs): before / naive / after / best")
        for case_id in by_id:
            b = _mean([r["coverage"] for r in rows_b if r["id"] == case_id])
            n = _mean([
                section_coverage(by_id[case_id], r["evidence"][:top_k])
                for r in rows_b if r["id"] == case_id
            ])
            a = _mean([r["coverage"] for r in rows_a if r["id"] == case_id])
            best = best_achievable(by_id[case_id], top_k)
            flag = "  <-- below best" if a + 1e-9 < best else ""
            print(f"      {case_id}  {b:6.1%} / {n:6.1%} / {a:6.1%} / {best:6.1%}{flag}")

    print("\nB. Answer coverage (share of answerable clauses whose fact is in the delivered answer)")
    for label, data in (("before (r10)", before), ("after (this round)", after)):
        rows = data["answers"]
        delivered = sum(r["behavior"] == "answer" for r in rows)
        print(f"  {label:20s} fact coverage {_mean([r['fact_coverage'] for r in rows]):6.1%}  "
              f"delivered {delivered}/{len(rows)}  "
              f"p50 {sorted(r['seconds'] for r in rows)[len(rows) // 2]:.2f}s")
    print("  per question (mean over runs): before -> after")
    for case_id in by_id:
        b = _mean([r["fact_coverage"] for r in before["answers"] if r["id"] == case_id])
        a = _mean([r["fact_coverage"] for r in after["answers"] if r["id"] == case_id])
        mark = "  <-- lower" if a + 1e-9 < b else ("  higher" if a > b + 1e-9 else "")
        print(f"      {case_id}  {b:6.1%} -> {a:6.1%}{mark}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("cases", type=Path)
    run_parser.add_argument("out", type=Path)
    run_parser.add_argument("--runs", type=int, default=3)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("cases", type=Path)
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.cases, args.out, args.runs)
    return compare(args.cases, args.before, args.after)


if __name__ == "__main__":
    sys.exit(main())
