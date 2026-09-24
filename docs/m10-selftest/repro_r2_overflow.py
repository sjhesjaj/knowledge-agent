"""R2 reproduction: sub-query count and evidence count per retrieval path.

Prints, for each probe question and each `top_k`, how many sub-queries the
decomposition produced and how many pieces of evidence came back - plus which
retrieval path answered, because the two overflow paths are reached under
different conditions: `bm25_multi_fast` only when every clause is confident
(first BM25 score >= 3.0 and >= 1.5x the runner-up) on its own section, the full
chain otherwise. The full chain calls the embedding and chat models, so run this
serially with any other model job.

Run from the root of an isolated candidate copy:

    PYTHONPATH=. python docs/m10-selftest/repro_r2_overflow.py
"""
from __future__ import annotations

import io
import sys

import rag
from orchestration.document_adapter import document_search

CORPUS = "sample_company_rules.md"

QUESTIONS = [
    # the acceptance side's reported overflow input
    "年假多少天？谁审批？多久失效？怎么申请？还能顺延吗？",
    "报销要多久？谁签字？发票怎么交？超额怎么办？能预支吗？",
    # bm25_multi_fast: every clause is confident and lands on a distinct section
    "带薪年假多少天？远程办公每周最多几天？工资每月几号发放？费用报销多少天内提交？访客进入办公区域陪同？",
    "带薪年假多少天？远程办公每周最多几天？工资每月几号发放？",
    "年假多少天？谁审批？",
    "报销申请最晚要在费用发生后多少天内提交？需要附哪些材料",
]


def main() -> int:
    chunks = rag.split_text(io.open(CORPUS, encoding="utf-8").read(), source=CORPUS)
    print(f"corpus chunks: {len(chunks)}")
    worst = 0
    for question in QUESTIONS:
        subs = rag.decompose_question(question)
        print(f"\nQ: {question}")
        print(f"   sub_questions={len(subs)} -> {subs}")
        for top_k in (2, 3, 4):
            trace: dict = {}
            result = document_search(question, chunks, top_k=top_k, trace=trace)
            count = len(result.evidence)
            over = count > top_k
            worst = max(worst, count - top_k)
            print(f"   top_k={top_k}: evidence={count} "
                  f"path={trace.get('retrieval_path')} "
                  f"subs={trace.get('sub_questions')}"
                  f"{'   <-- OVERFLOW' if over else ''}")
    print(f"\nworst overflow: +{worst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
