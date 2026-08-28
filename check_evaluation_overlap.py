"""Measure how much the blind holdout overlaps the sets it must be independent of.

A holdout that merely paraphrases the development set does not measure
generalization; it measures memorization of a template. This script compares
every blind-holdout question against every question in the Dev and Validation V1
sets and refuses to certify the holdout if they are too close.

Three views, because each misses something the others catch:

- **SequenceMatcher** on the normalized character stream - overall edit
  similarity, sensitive to word order.
- **Character-bigram Jaccard** - bag-of-bigrams overlap, which still fires when
  the clauses of a sentence have been reordered.
- **Entity-masked signature** - SKUs, numbers, and corpus entity names replaced
  by placeholders. Two questions that differ only in *which* SKU or *which*
  policy they name collapse onto the same signature. This is the check that
  catches template farming, and neither similarity score reliably does: swapping
  a long policy name can drag raw similarity below any threshold while leaving
  the sentence structurally identical.

Read-only. Touches no model, no network, and no production module.

Usage:
    python check_evaluation_overlap.py [--report <json>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent

# (holdout, [sets it must stay independent of])
COMPARISONS = (
    (
        "route_holdout",
        ROOT / "eval_orchestrated_routes_holdout.json",
        (
            ("route_dev", ROOT / "eval_orchestrated_routes_dev.json"),
            ("route_validation_v1", ROOT / "eval_orchestrated_routes_validation_v1.json"),
        ),
    ),
    (
        "answerability_holdout",
        ROOT / "eval_answerability_holdout.json",
        (
            ("answerability_dev", ROOT / "eval_answerability_dev.json"),
            (
                "answerability_validation_v1",
                ROOT / "eval_answerability_validation_v1.json",
            ),
        ),
    ),
)

CORPUS_PATH = ROOT / "sample_company_rules.md"
WIKI_PATH = ROOT / "wiki_pages" / "sample_company_wiki.json"

# Freeze gates.
THRESHOLD_BLOCKING = 0.85          # no pair may reach this
THRESHOLD_ELEVATED = 0.80          # at most this fraction of a holdout may reach it
MAX_ELEVATED_FRACTION = 0.05
# Masked similarity at or above this, on a pair that is not already flagged by
# raw similarity, is treated as an entity swap.
THRESHOLD_MASKED_TEMPLATE = 0.90

TOP_PAIRS_REPORTED = 20

_PUNCTUATION = re.compile(
    r"[\s，。！？；：、,.!?;:~～…\-—\"'“”‘’()（）【】《》\[\]{}<>/\\|@#$%^&*+=_`]"
)
_SKU = re.compile(r"sku[-_ ]?[a-z]\d{3}", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9]+|[〇零一二三四五六七八九十百千万两]+")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and whitespace. Matching only."""
    return _PUNCTUATION.sub("", text).lower()


def _corpus_entities() -> list[str]:
    """Entity names to mask, taken from the corpus rather than from the planner.

    Deriving them from the data keeps this checker independent of routing
    internals - it must judge the *questions*, not the implementation.
    """
    entities: set[str] = set()
    if CORPUS_PATH.exists():
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                entities.add(line[3:].strip())
    if WIKI_PATH.exists():
        try:
            wiki = json.loads(WIKI_PATH.read_text(encoding="utf-8"))
        except ValueError:
            wiki = {}
        for page in wiki.get("pages", []):
            if isinstance(page, dict):
                if isinstance(page.get("title"), str):
                    entities.add(page["title"].strip())
                for alias in page.get("aliases", []) or []:
                    if isinstance(alias, str):
                        entities.add(alias.strip())
    # Business objects the System channel deals in. Written out because they are
    # the nouns most likely to be swapped to fake a "new" question.
    entities.update(
        {
            "订单", "库存", "余额", "物流", "审批", "工单", "账户", "积分",
            "额度", "排班", "考勤", "申请记录",
        }
    )
    # Longest first so "账号与权限" masks before "账号".
    return sorted((e for e in entities if e), key=len, reverse=True)


ENTITIES = _corpus_entities()


def mask(text: str) -> str:
    """Replace SKUs, entity names, and numbers with placeholders."""
    masked = _SKU.sub("§S", text)
    for entity in ENTITIES:
        masked = masked.replace(entity, "§E")
    masked = _NUMBER.sub("§N", masked)
    return normalize(masked)


def bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)} or {text}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def load_questions(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing")
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected a JSON array")
    records = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or "question" not in case:
            raise ValueError(f"{path}: cases[{index}] has no question")
        text = case["question"]
        norm = normalize(text)
        records.append(
            {
                "id": case.get("id", f"cases[{index}]"),
                "question": text,
                "normalized": norm,
                "bigrams": bigrams(norm),
                "masked": mask(text),
            }
        )
    return records


def compare(holdout: list[dict], references: list[tuple[str, list[dict]]]) -> list[dict]:
    """For each holdout question, its single closest question anywhere else."""
    results = []
    for item in holdout:
        best = None
        for ref_name, ref_items in references:
            for other in ref_items:
                seq = ratio(item["normalized"], other["normalized"])
                jac = jaccard(item["bigrams"], other["bigrams"])
                masked = ratio(item["masked"], other["masked"])
                score = max(seq, jac)
                if best is None or score > best["similarity"]:
                    best = {
                        "id": item["id"],
                        "question": item["question"],
                        "match_dataset": ref_name,
                        "match_id": other["id"],
                        "match_question": other["question"],
                        "sequence_ratio": seq,
                        "bigram_jaccard": jac,
                        "masked_ratio": masked,
                        "similarity": score,
                    }
        # A pair can be structurally identical while reading as different text,
        # which is exactly what swapping an entity produces.
        best["entity_swap_suspect"] = (
            best["masked_ratio"] >= THRESHOLD_MASKED_TEMPLATE
            and best["similarity"] < THRESHOLD_BLOCKING
        )
        results.append(best)
    return results


def internal_templates(holdout: list[dict]) -> list[dict]:
    """Masked signatures used more than twice inside one holdout."""
    buckets: dict[str, list[str]] = {}
    for item in holdout:
        buckets.setdefault(item["masked"], []).append(item["id"])
    return [
        {"signature": signature, "ids": ids, "count": len(ids)}
        for signature, ids in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
        if len(ids) > 2
    ]


def evaluate(name: str, holdout_path: Path, refs: tuple) -> dict:
    holdout = load_questions(holdout_path)
    references = [(ref_name, load_questions(path)) for ref_name, path in refs]
    results = compare(holdout, references)
    results.sort(key=lambda r: -r["similarity"])

    blocking = [r for r in results if r["similarity"] >= THRESHOLD_BLOCKING]
    elevated = [r for r in results if r["similarity"] >= THRESHOLD_ELEVATED]
    suspects = [r for r in results if r["entity_swap_suspect"]]
    templates = internal_templates(holdout)
    allowed_elevated = int(len(holdout) * MAX_ELEVATED_FRACTION)

    gates = [
        {
            "name": f"{name}: pairs >= {THRESHOLD_BLOCKING:.2f}",
            "value": len(blocking),
            "limit": 0,
            "passed": len(blocking) == 0,
        },
        {
            "name": f"{name}: pairs >= {THRESHOLD_ELEVATED:.2f}",
            "value": len(elevated),
            "limit": allowed_elevated,
            "passed": len(elevated) <= allowed_elevated,
        },
        {
            "name": f"{name}: entity-swap suspects",
            "value": len(suspects),
            "limit": 0,
            "passed": len(suspects) == 0,
        },
        {
            "name": f"{name}: internal templates used > 2x",
            "value": len(templates),
            "limit": 0,
            "passed": len(templates) == 0,
        },
    ]

    print()
    print("=" * 78)
    print(f"{name}  ({len(holdout)} questions vs "
          f"{sum(len(items) for _, items in references)} reference questions)")
    print("=" * 78)
    print(f"Top {min(TOP_PAIRS_REPORTED, len(results))} most similar pairs:")
    for entry in results[:TOP_PAIRS_REPORTED]:
        print(
            f"  {entry['similarity']:.3f}  seq={entry['sequence_ratio']:.3f} "
            f"jac={entry['bigram_jaccard']:.3f} masked={entry['masked_ratio']:.3f}"
            f"  {entry['id']}  vs  {entry['match_dataset']}/{entry['match_id']}"
        )
        print(f"          H: {entry['question']}")
        print(f"          R: {entry['match_question']}")

    print()
    print(f"  pairs >= {THRESHOLD_BLOCKING:.2f} : {len(blocking)} (limit 0)")
    print(
        f"  pairs >= {THRESHOLD_ELEVATED:.2f} : {len(elevated)} "
        f"(limit {allowed_elevated} = {MAX_ELEVATED_FRACTION:.0%} of {len(holdout)})"
    )
    print(f"  entity-swap suspects : {len(suspects)} (limit 0)")
    print(f"  internal templates >2x: {len(templates)} (limit 0)")
    if suspects:
        print("  suspected entity swaps:")
        for entry in suspects:
            print(
                f"    {entry['id']} masked={entry['masked_ratio']:.3f} vs "
                f"{entry['match_dataset']}/{entry['match_id']}"
            )
            print(f"      H: {entry['question']}")
            print(f"      R: {entry['match_question']}")
    if templates:
        print("  internal template reuse:")
        for entry in templates:
            print(f"    x{entry['count']}: {', '.join(entry['ids'])}")

    return {
        "name": name,
        "holdout": str(holdout_path),
        "holdout_size": len(holdout),
        "reference_sets": [ref_name for ref_name, _ in refs],
        "max_similarity": results[0]["similarity"] if results else 0.0,
        "count_blocking": len(blocking),
        "count_elevated": len(elevated),
        "allowed_elevated": allowed_elevated,
        "entity_swap_suspects": suspects,
        "internal_templates": templates,
        "top_pairs": results[:TOP_PAIRS_REPORTED],
        "all_pairs": results,
        "gates": gates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        default="evaluation_runs/overlap-report.json",
        help="Where to write the full pair-level report",
    )
    args = parser.parse_args(argv)

    reports = []
    gates = []
    for name, holdout_path, refs in COMPARISONS:
        report = evaluate(name, holdout_path, refs)
        reports.append(report)
        gates.extend(report["gates"])

    print()
    print("=" * 78)
    print("Gates")
    print("=" * 78)
    for gate in gates:
        print(
            f"  [{'PASS' if gate['passed'] else 'FAIL'}] {gate['name']:<46}"
            f"{gate['value']} (limit {gate['limit']})"
        )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "thresholds": {
                    "blocking": THRESHOLD_BLOCKING,
                    "elevated": THRESHOLD_ELEVATED,
                    "max_elevated_fraction": MAX_ELEVATED_FRACTION,
                    "masked_template": THRESHOLD_MASKED_TEMPLATE,
                },
                "comparisons": reports,
                "gates": gates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print()
    print(f"Wrote {report_path}")

    return 0 if all(gate["passed"] for gate in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
