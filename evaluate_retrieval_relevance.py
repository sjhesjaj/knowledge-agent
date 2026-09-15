"""M10 retrieval-relevance evaluation for the three evidence paths.

Additive: nothing here is imported by the product, and no existing evaluator,
dataset or run record is touched. It exists because the frozen evaluators score
*answers*, and an answer can be right while the evidence behind it was found by
luck - or wrong while the evidence was there all along. This measures the
retrieval step on its own.

What is measured, per case, on each of three paths:

- **static Wiki** - `wiki_query` over the committed `wiki_pages/` collection;
- **dynamic Wiki** - `wiki_query` over a Wiki compiled from the corpus by the
  real model, so an answer cannot come from the hand-authored file;
- **document** - `document_search` over `rag`-indexed chunks of the corpus.

`Relevant Hit@3` follows the M10 definition: at least one of the first three
pieces of evidence must contain the case's annotated required fact *and* be
traceable to the annotated source section. Sharing a title or a topic is not a
hit, which is why the locator is checked as well as the text.

Usage:
    python evaluate_retrieval_relevance.py --output-dir <dir>
    python evaluate_retrieval_relevance.py --output-dir <dir> --skip-dynamic
    python evaluate_retrieval_relevance.py --output-dir <dir> \
        --wiki-build-cache <file>

`--wiki-build-cache` writes the compiled pages on the first run and reuses them
afterwards, so repeated runs do not re-invoke the model. Delete the file to
recompile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import rag
from orchestration.document_adapter import document_search
from orchestration.wiki_adapter import load_wiki_pages, page_from_json, wiki_query
from orchestration.wiki_schema import WikiPage

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - already UTF-8 or piped
        pass

REPO_ROOT = Path(__file__).resolve().parent
CORPUS_PATH = REPO_ROOT / "sample_company_rules.md"
DATASET_PATH = REPO_ROOT / "eval_retrieval_relevance_m10.json"

# The gate the caller is measured against. `wiki_query` is asked for its
# documented default; `document_search` returns four and the first three are
# scored, which is what the M10 plan specifies.
WIKI_TOP_K = 3
DOCUMENT_TOP_K = 4
SCORED_DEPTH = 3

PATH_STATIC_WIKI = "static_wiki"
PATH_DYNAMIC_WIKI = "dynamic_wiki"
PATH_DOCUMENT = "document"
ALL_PATHS = (PATH_STATIC_WIKI, PATH_DYNAMIC_WIKI, PATH_DOCUMENT)

HIT_AT_3_THRESHOLD = 0.90
COMBINED_TOP1_THRESHOLD = 0.90
PER_PATH_TOP1_FLOOR = 0.80


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------


def load_dataset(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = raw["cases"]
    seen: set[str] = set()
    for case in cases:
        for field in ("id", "question", "source_section", "required_fact_patterns"):
            if not case.get(field):
                raise ValueError(f"case {case.get('id')!r} is missing {field!r}")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']!r}")
        seen.add(case["id"])
        for pattern in case["required_fact_patterns"]:
            re.compile(pattern)
    return cases


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Relevance judgement
# --------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Collapse the whitespace a compiler or chunker may have moved."""
    return re.sub(r"\s+", " ", text)


def states_required_fact(content: str, patterns: list[str]) -> bool:
    """Does this evidence actually assert the fact the case asks for?

    Any one pattern is enough: a case lists alternatives when the same fact is
    phrased differently in the Wiki and in the source document.
    """
    text = _normalize(content)
    relaxed = text.replace(" ", "")
    return any(
        re.search(pattern, text) or re.search(pattern.replace(r"\s*", ""), relaxed)
        for pattern in patterns
    )


def traces_to_section(evidence, section: str, chunk_sections: dict[int, str]) -> bool:
    """Is this evidence traceable to the annotated section of the corpus?

    Wiki claims carry `section:<heading>` locators. Document chunks carry
    `chunk:<n>`, so the heading is recovered from the chunk itself - the same
    trail `chat_orchestration.serialize_evidence` exposes to the client.
    """
    locator = evidence.locator or ""
    if locator.startswith("section:"):
        return locator.split(":", 1)[1].strip() == section
    if locator.startswith("chunk:"):
        try:
            index = int(locator.split(":", 1)[1])
        except ValueError:
            return False
        return chunk_sections.get(index) == section
    return False


def judge(evidence_list, case: dict, chunk_sections: dict[int, str]) -> dict:
    """Score one path for one case."""
    patterns = case["required_fact_patterns"]
    section = case["source_section"]
    per_rank = [
        {
            "rank": rank,
            "source": item.source,
            "locator": item.locator,
            "states_fact": states_required_fact(item.content, patterns),
            "traces_to_section": traces_to_section(item, section, chunk_sections),
            "content": _normalize(item.content)[:160],
        }
        for rank, item in enumerate(evidence_list, start=1)
    ]
    scored = per_rank[:SCORED_DEPTH]
    relevant_ranks = [
        entry["rank"]
        for entry in scored
        if entry["states_fact"] and entry["traces_to_section"]
    ]
    return {
        "returned": len(per_rank),
        "hit_at_3": bool(relevant_ranks),
        "top1_relevant": bool(relevant_ranks) and relevant_ranks[0] == 1,
        "first_relevant_rank": relevant_ranks[0] if relevant_ranks else None,
        "evidence": per_rank,
    }


# --------------------------------------------------------------------------
# Corpus and Wiki preparation
# --------------------------------------------------------------------------


def chunk_section_map(chunks: list[rag.Chunk]) -> dict[int, str]:
    """Map each chunk index to the `## ` heading it was cut from."""
    sections: dict[int, str] = {}
    for chunk in chunks:
        heading = None
        for line in chunk.text.splitlines():
            if line.startswith("## "):
                heading = line[3:].strip()
                break
        if heading is not None:
            sections[chunk.index] = heading
    return sections


def build_document_chunks() -> list[rag.Chunk]:
    text = CORPUS_PATH.read_text(encoding="utf-8")
    return rag.reindex_chunks(rag.split_text(text, CORPUS_PATH.name))


def compile_dynamic_wiki(cache_path: Path | None) -> tuple[WikiPage, ...]:
    """Compile the corpus into a Wiki with the real model, or reuse a cache."""
    if cache_path is not None and cache_path.exists():
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return tuple(
            page_from_json(page, f"cached_pages[{index}]")
            for index, page in enumerate(raw["pages"])
        )

    from wiki_maintenance.compiler import compile_wiki_fast
    from wiki_maintenance.ollama_compiler import OllamaWikiModel
    from wiki_maintenance.source_spans import build_source_spans_from_text

    spans = build_source_spans_from_text(
        "doc-relevance", CORPUS_PATH.read_text(encoding="utf-8"), CORPUS_PATH.name
    )
    pages = compile_wiki_fast(OllamaWikiModel(), spans=spans)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "pages": [_page_to_json(page) for page in pages],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return pages


def _page_to_json(page: WikiPage) -> dict:
    return {
        "page_id": page.page_id,
        "title": page.title,
        "summary": page.summary,
        "version": page.version,
        "aliases": list(page.aliases),
        "claims": [
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "source": claim.source,
                "locator": claim.locator,
                "source_span_ids": list(claim.source_span_ids),
            }
            for claim in page.claims
        ],
    }


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def _rate(passed: int, total: int) -> float:
    return passed / total if total else 0.0


def evaluate(cases: list[dict], *, skip_dynamic: bool, cache_path: Path | None) -> dict:
    chunks = build_document_chunks()
    sections = chunk_section_map(chunks)
    static_pages = load_wiki_pages()
    dynamic_pages = None if skip_dynamic else compile_dynamic_wiki(cache_path)

    records: list[dict] = []
    for case in cases:
        question = case["question"]
        result: dict[str, object] = {
            "id": case["id"],
            "question": question,
            "topic": case["topic"],
            "source_section": case["source_section"],
            "paths": {},
        }
        static_result = wiki_query(question, static_pages, top_k=WIKI_TOP_K)
        result["paths"][PATH_STATIC_WIKI] = judge(
            static_result.evidence, case, sections
        )
        if dynamic_pages is not None:
            dynamic_result = wiki_query(question, dynamic_pages, top_k=WIKI_TOP_K)
            result["paths"][PATH_DYNAMIC_WIKI] = judge(
                dynamic_result.evidence, case, sections
            )
        document_result = document_search(question, chunks, top_k=DOCUMENT_TOP_K)
        result["paths"][PATH_DOCUMENT] = judge(document_result.evidence, case, sections)
        records.append(result)

    measured = [path for path in ALL_PATHS if path in records[0]["paths"]]
    summary = {
        path: {
            "total": len(records),
            "hit_at_3": sum(r["paths"][path]["hit_at_3"] for r in records),
            "top1_relevant": sum(r["paths"][path]["top1_relevant"] for r in records),
        }
        for path in measured
    }
    for path, counts in summary.items():
        counts["hit_at_3_rate"] = _rate(counts["hit_at_3"], counts["total"])
        counts["top1_rate"] = _rate(counts["top1_relevant"], counts["total"])

    combined_total = sum(c["total"] for c in summary.values())
    combined_top1 = sum(c["top1_relevant"] for c in summary.values())

    gates = []
    for path in measured:
        gates.append(
            {
                "name": f"Relevant Hit@3 [{path}]",
                "value": summary[path]["hit_at_3_rate"],
                "threshold": HIT_AT_3_THRESHOLD,
            }
        )
        gates.append(
            {
                "name": f"Top-1 Relevance floor [{path}]",
                "value": summary[path]["top1_rate"],
                "threshold": PER_PATH_TOP1_FLOOR,
            }
        )
    gates.append(
        {
            "name": "Combined Top-1 Relevance",
            "value": _rate(combined_top1, combined_total),
            "threshold": COMBINED_TOP1_THRESHOLD,
        }
    )
    for gate in gates:
        gate["passed"] = gate["value"] >= gate["threshold"]

    return {
        "dataset": DATASET_PATH.name,
        "dataset_sha256": sha256_of(DATASET_PATH),
        "corpus_sha256": sha256_of(CORPUS_PATH),
        "python_version": sys.version.split()[0],
        "chat_model": rag.CHAT_MODEL,
        "wiki_top_k": WIKI_TOP_K,
        "document_top_k": DOCUMENT_TOP_K,
        "scored_depth": SCORED_DEPTH,
        "measured_paths": measured,
        "summary": summary,
        "combined_top1_relevance": _rate(combined_top1, combined_total),
        "gates": gates,
        "cases": records,
    }


def report(result: dict) -> None:
    print(f"Dataset      : {result['dataset']} ({len(result['cases'])} cases)")
    print(f"Dataset SHA  : {result['dataset_sha256']}")
    print(f"Corpus SHA   : {result['corpus_sha256']}")
    print(f"Paths        : {', '.join(result['measured_paths'])}")
    print()
    for path in result["measured_paths"]:
        counts = result["summary"][path]
        print(
            f"  {path:<14} Hit@3 {counts['hit_at_3']:>2}/{counts['total']:<3}"
            f" {counts['hit_at_3_rate']:>6.1%}   "
            f"Top-1 {counts['top1_relevant']:>2}/{counts['total']:<3}"
            f" {counts['top1_rate']:>6.1%}"
        )
    print(f"\n  combined Top-1 relevance: {result['combined_top1_relevance']:.1%}")

    print("\nMisses")
    any_miss = False
    for record in result["cases"]:
        for path, outcome in record["paths"].items():
            if not outcome["hit_at_3"]:
                any_miss = True
                print(f"  [{path}] {record['id']}: {record['question']}")
                for entry in outcome["evidence"][:SCORED_DEPTH]:
                    print(
                        f"        {entry['rank']}. {entry['locator']}"
                        f" fact={'Y' if entry['states_fact'] else 'n'}"
                        f" section={'Y' if entry['traces_to_section'] else 'n'}"
                        f" | {entry['content'][:70]}"
                    )
    if not any_miss:
        print("  none")

    print("\nGates")
    for gate in result["gates"]:
        status = "PASS" if gate["passed"] else "FAIL"
        print(
            f"  [{status}] {gate['name']:<34} {gate['value']:.1%}"
            f" (>= {gate['threshold']:.0%})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--skip-dynamic",
        action="store_true",
        help="measure the static Wiki and Document paths only (no model call)",
    )
    parser.add_argument(
        "--wiki-build-cache",
        default=None,
        help="reuse a previously compiled dynamic Wiki instead of recompiling",
    )
    args = parser.parse_args()

    cases = load_dataset(DATASET_PATH)
    cache_path = Path(args.wiki_build_cache) if args.wiki_build_cache else None
    result = evaluate(cases, skip_dynamic=args.skip_dynamic, cache_path=cache_path)
    report(result)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "relevance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {output_dir / 'relevance.json'}")
    return 0 if all(gate["passed"] for gate in result["gates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
