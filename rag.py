from __future__ import annotations

import io
import json
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import requests
import jieba
from pypdf import PdfReader


OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "qwen3:4b"
EMBED_MODEL = "nomic-embed-text"
CACHE_FILE = Path(".cache/embeddings.json")


@dataclass
class Chunk:
    text: str
    source: str
    index: int
    embedding: list[float] | None = None


def check_ollama() -> tuple[bool, str]:
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        response.raise_for_status()
        names = [item["name"] for item in response.json().get("models", [])]
        return True, "、".join(names) if names else "服务已启动，但尚未下载模型"
    except requests.RequestException as exc:
        return False, f"无法连接 Ollama：{exc}"


def read_file(name: str, raw: bytes) -> str:
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix in {"txt", "md"}:
        for encoding in ("utf-8", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass
    raise ValueError(f"暂不支持文件：{name}")


def split_text(text: str, source: str, size: int = 220, overlap: int = 40) -> list[Chunk]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Markdown 优先按二级标题分节，避免把不同主题混在同一文本块中。
    sections: list[str] = []
    current: list[str] = []
    document_title = ""
    for line in lines:
        if line.startswith("# ") and not document_title:
            document_title = line
            continue
        if line.startswith("## ") and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    cleaned_sections = sections or ["\n".join(lines)]
    chunks: list[Chunk] = []
    for section in cleaned_sections:
        prefix = f"{document_title}\n" if document_title else ""
        start = 0
        while start < len(section):
            end = min(start + size, len(section))
            piece = (prefix + section[start:end]).strip()
            if piece:
                chunks.append(Chunk(text=piece, source=source, index=len(chunks) + 1))
            if end == len(section):
                break
            start = end - overlap
    return chunks


def reindex_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Assign stable, globally unique positions after merging multiple files."""
    for index, chunk in enumerate(chunks, start=1):
        chunk.index = index
    return chunks


def embed(text: str) -> list[float]:
    return embed_many([text])[0]


def embed_many(texts: list[str]) -> list[list[float]]:
    response = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def build_index(chunks: list[Chunk], stats: dict | None = None) -> list[Chunk]:
    started = perf_counter()
    cache: dict[str, list[float]] = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    changed = False
    cache_hits = 0
    for chunk in chunks:
        cache_key = hashlib.sha256(
            f"{EMBED_MODEL}\n{chunk.source}\n{chunk.text}".encode("utf-8")
        ).hexdigest()
        if cache_key in cache:
            chunk.embedding = cache[cache_key]
            cache_hits += 1
        else:
            chunk.embedding = embed(chunk.text)
            cache[cache_key] = chunk.embedding
            changed = True
    if changed:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    if stats is not None:
        stats.update({
            "chunks": len(chunks),
            "cache_hits": cache_hits,
            "cache_misses": len(chunks) - cache_hits,
            "index_seconds": perf_counter() - started,
        })
    return chunks


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def retrieve(
    question: str,
    chunks: list[Chunk],
    top_k: int = 4,
    question_vector: list[float] | None = None,
) -> list[tuple[Chunk, float]]:
    question_vector = question_vector or embed(question)
    scored = [(chunk, cosine(question_vector, chunk.embedding or [])) for chunk in chunks]
    return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]


def lexical_tokens(text: str) -> list[str]:
    """使用 Jieba 为中文分词，同时保留英文、数字和下划线词项。"""
    lowered = text.lower()
    return [
        token.strip()
        for token in jieba.lcut(lowered)
        if token.strip() and re.search(r"[a-z0-9_\u4e00-\u9fff]", token)
    ]


def bm25_rank(question: str, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> list[tuple[Chunk, float]]:
    query_tokens = lexical_tokens(question)
    documents = [lexical_tokens(chunk.text) for chunk in chunks]
    average_length = sum(map(len, documents)) / len(documents) if documents else 1
    document_frequency = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))

    ranked: list[tuple[Chunk, float]] = []
    total = len(documents)
    for chunk, tokens in zip(chunks, documents):
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(tokens) / average_length)
            score += idf * frequency * (k1 + 1) / denominator
        ranked.append((chunk, score))
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def hybrid_retrieve(
    question: str,
    chunks: list[Chunk],
    top_k: int = 4,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    keyword_weight: float = 1.2,
    question_vector: list[float] | None = None,
) -> list[tuple[Chunk, float]]:
    """使用 RRF 融合向量排名与 BM25 排名。"""
    vector_results = retrieve(question, chunks, top_k=len(chunks), question_vector=question_vector)
    keyword_results = bm25_rank(question, chunks)
    scores = {chunk.index: 0.0 for chunk in chunks}
    by_index = {chunk.index: chunk for chunk in chunks}
    for rank, (chunk, _) in enumerate(vector_results, start=1):
        scores[chunk.index] += vector_weight / (rrf_k + rank)
    for rank, (chunk, _) in enumerate(keyword_results, start=1):
        scores[chunk.index] += keyword_weight / (rrf_k + rank)
    fused = [(by_index[index], score) for index, score in scores.items()]
    return sorted(fused, key=lambda item: item[1], reverse=True)[:top_k]


def rerank(question: str, candidates: list[tuple[Chunk, float]], top_k: int = 4) -> list[tuple[Chunk, float]]:
    """让本地聊天模型对候选片段做一次语义重排序。"""
    candidate_text = "\n\n".join(
        f"候选ID={chunk.index}\n{chunk.text}" for chunk, _ in candidates
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是检索重排序器。根据用户问题判断候选片段的相关性。"
                "只输出JSON，格式为{\"ranking\":[片段ID,...]}。"
                "最能直接回答问题的片段排在最前，不要解释。 /no_think"
            ),
        },
        {"role": "user", "content": f"问题：{question}\n\n{candidate_text}"},
    ]
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": CHAT_MODEL, "messages": messages, "stream": False, "think": False, "format": "json"},
            timeout=180,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if "</think>" in content:
            content = content.rsplit("</think>", 1)[-1]
        ranking = [int(value) for value in json.loads(content)["ranking"]]
        by_index = {chunk.index: chunk for chunk, _ in candidates}
        ordered = [by_index[index] for index in ranking if index in by_index]
        ordered.extend(chunk for chunk, _ in candidates if chunk.index not in {item.index for item in ordered})
        return [(chunk, 1.0 / rank) for rank, chunk in enumerate(ordered[:top_k], start=1)]
    except (requests.RequestException, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return candidates[:top_k]


def select_for_subquestions(
    sub_questions: list[str], candidates: list[tuple[Chunk, float]]
) -> list[tuple[Chunk, float]]:
    """为每个子问题选择一个最相关片段，保证复合问题的意图覆盖。"""
    candidate_text = "\n\n".join(
        f"候选ID={chunk.index}\n{chunk.text}" for chunk, _ in candidates
    )
    questions = "\n".join(f"问题{i}：{q}" for i, q in enumerate(sub_questions, start=1))
    messages = [
        {
            "role": "system",
            "content": (
                "你是检索片段选择器。必须为每个子问题选择一个最能直接回答它的候选ID。"
                "选择标准不是主题相似，而是片段中必须包含能够直接提取的具体答案；"
                "例如询问最晚到岗时间，应选择明确写有到岗时间的片段。"
                "只输出JSON，格式为{\"selections\":[片段ID,...]}，数组顺序与问题顺序一致。"
                "不要解释。 /no_think"
            ),
        },
        {"role": "user", "content": f"{questions}\n\n{candidate_text}"},
    ]
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": CHAT_MODEL, "messages": messages, "stream": False, "think": False, "format": "json"},
            timeout=180,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if "</think>" in content:
            content = content.rsplit("</think>", 1)[-1]
        selections = [int(value) for value in json.loads(content)["selections"]]
        by_index = {chunk.index: chunk for chunk, _ in candidates}
        ordered: list[Chunk] = []
        for index in selections:
            if index in by_index and index not in {chunk.index for chunk in ordered}:
                ordered.append(by_index[index])
        if not ordered:
            raise ValueError("模型没有返回有效候选ID")
        # 模型漏选部分子问题时，用原候选顺序补足，避免向答案阶段传入空资料。
        for chunk, _ in candidates:
            if len(ordered) >= len(sub_questions):
                break
            if chunk.index not in {item.index for item in ordered}:
                ordered.append(chunk)
        return [(chunk, 1.0 / rank) for rank, chunk in enumerate(ordered, start=1)]
    except (requests.RequestException, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return rerank("；".join(sub_questions), candidates, top_k=len(sub_questions))


def retrieve_with_rerank(
    question: str,
    chunks: list[Chunk],
    top_k: int = 4,
    candidate_k: int = 8,
    trace: dict | None = None,
) -> list[tuple[Chunk, float]]:
    """复合问题先分解召回候选，再由 Qwen3 做最终重排序。"""
    total_started = perf_counter()
    split_started = perf_counter()
    sub_questions = decompose_question(question)
    if not sub_questions:
        sub_questions = [question]
    split_seconds = perf_counter() - split_started

    recall_started = perf_counter()
    candidate_map: dict[int, tuple[Chunk, float]] = {}
    per_query_k = max(top_k, min(candidate_k, len(chunks)))
    question_vectors = embed_many(sub_questions)
    for sub_question, question_vector in zip(sub_questions, question_vectors):
        for chunk, score in hybrid_retrieve(
            sub_question, chunks, top_k=per_query_k, question_vector=question_vector
        ):
            previous = candidate_map.get(chunk.index)
            if previous is None or score > previous[1]:
                candidate_map[chunk.index] = (chunk, score)
    candidates = sorted(candidate_map.values(), key=lambda item: item[1], reverse=True)
    recall_seconds = perf_counter() - recall_started
    rerank_started = perf_counter()
    if len(sub_questions) > 1:
        selected = select_for_subquestions(sub_questions, candidates)
        results = selected[:max(top_k, len(sub_questions))]
    else:
        results = rerank(question, candidates, top_k=top_k)
    if not results:
        results = candidates[:top_k]
    if trace is not None:
        trace.update({
            "sub_questions": len(sub_questions),
            "candidates": len(candidates),
            "split_seconds": split_seconds,
            "recall_seconds": recall_seconds,
            "rerank_seconds": perf_counter() - rerank_started,
            "retrieval_total_seconds": perf_counter() - total_started,
        })
    return results


def decompose_question(question: str) -> list[str]:
    """拆分标点分隔的复合问题和常见比较型问题。"""
    parts = [
        part.strip(" ，,。")
        for part in re.split(r"[？?；;]+", question)
        if part.strip(" ，,。")
    ]
    if len(parts) > 1:
        # “需要谁审批”一类省略主语的追问必须继承前一问上下文，
        # 否则会只按“审批”召回转正、报销等无关制度。
        contextualized = [parts[0]]
        for part in parts[1:]:
            if re.match(r"^(需要|由谁|谁|如何|怎么|是否|能否)", part):
                contextualized.append(f"{parts[0]}；{part}")
            else:
                contextualized.append(part)
        parts = contextualized
    if len(parts) == 1:
        text = re.sub(r"^(请|帮我)?(比较|对比)", "", parts[0]).strip()
        match = re.match(r"(.+?)(?:和|与|及)(.+?)(?:有什么区别|的区别|区别|制度)?$", text)
        if match:
            left, right = match.group(1).strip(), match.group(2).strip()
            return [f"{left}的相关规定", f"{right}的相关规定"]
    return parts


def retrieve_fast(question: str, chunks: list[Chunk], top_k: int = 4, trace: dict | None = None) -> list[tuple[Chunk, float]]:
    """BM25明显领先时跳过Embedding与Reranker，否则使用完整检索链。"""
    parts = decompose_question(question)
    if len(parts) > 1:
        started = perf_counter()
        selected: list[Chunk] = []
        confident = True
        for part in parts:
            ranked = bm25_rank(part, chunks)
            first = ranked[0][1] if ranked else 0
            second = ranked[1][1] if len(ranked) > 1 else 0
            if first < 3.0 or (second > 0 and first / second < 1.5):
                confident = False
                break
            if ranked[0][0].index not in {chunk.index for chunk in selected}:
                selected.append(ranked[0][0])
        if confident and selected:
            results = [(chunk, 1.0 / rank) for rank, chunk in enumerate(selected, start=1)]
            if trace is not None:
                elapsed = perf_counter() - started
                trace.update({
                    "retrieval_path": "bm25_multi_fast", "sub_questions": len(parts),
                    "candidates": len(selected), "split_seconds": 0.0,
                    "recall_seconds": elapsed, "rerank_seconds": 0.0,
                    "retrieval_total_seconds": elapsed,
                })
            return results
    else:
        started = perf_counter()
        bm25_results = bm25_rank(question, chunks)
        first = bm25_results[0][1] if bm25_results else 0
        second = bm25_results[1][1] if len(bm25_results) > 1 else 0
        if first >= 3.0 and (second == 0 or first / second >= 1.5):
            results = [(chunk, 1.0 / rank) for rank, (chunk, _) in enumerate(bm25_results[:top_k], start=1)]
            if trace is not None:
                elapsed = perf_counter() - started
                trace.update({
                    "retrieval_path": "bm25_fast", "sub_questions": 1,
                    "candidates": len(bm25_results), "split_seconds": 0.0,
                    "recall_seconds": elapsed, "rerank_seconds": 0.0,
                    "retrieval_total_seconds": elapsed,
                })
            return results
    if trace is not None:
        trace["retrieval_path"] = "hybrid_rerank"
    return retrieve_with_rerank(question, chunks, top_k=top_k, trace=trace)


def answer(question: str, results: list[tuple[Chunk, float]], history: list[dict]) -> str:
    messages = build_answer_messages(question, results, history)
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": CHAT_MODEL, "messages": messages, "stream": False, "think": False},
        timeout=180,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    # 不同 Qwen3/Ollama 版本可能返回完整或残缺的 think 标签。
    # 如果存在结束标签，最终答案通常位于它后面；否则清除完整标签块。
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1]
    else:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"<think>.*", "", content, flags=re.DOTALL)
    return content.strip()


def answer_structured(question: str, results: list[tuple[Chunk, float]], history: list[dict]) -> str:
    """使用结构化输出减少思考文本和生成长度。"""
    messages = build_answer_messages(question, results, history)
    schema = {
        "type": "object", "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": CHAT_MODEL, "messages": messages, "stream": False,
            "think": False, "format": schema, "options": {"temperature": 0},
        },
        timeout=180,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    try:
        result = json.loads(content)["answer"].strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        return answer(question, results, history)
    refusal_markers = ("无法确定", "没有相关", "未提及", "资料不足", "无法回答")
    if results and any(marker in result for marker in refusal_markers):
        top_chunk = results[0][0]
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "你是证据提取器。检查片段是否明确包含问题答案。"
                    "若包含，直接提取准确答案并标注[来源1]；确实没有才回答根据现有资料无法确定。"
                ),
            },
            {"role": "user", "content": f"问题：{question}\n\n[来源1]\n{top_chunk.text}"},
        ]
        retry = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": CHAT_MODEL, "messages": retry_messages, "stream": False,
                "think": False, "format": schema, "options": {"temperature": 0},
            },
            timeout=180,
        )
        retry.raise_for_status()
        try:
            result = json.loads(retry.json()["message"]["content"])["answer"].strip()
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return result


def build_answer_messages(question: str, results: list[tuple[Chunk, float]], history: list[dict]) -> list[dict]:
    context = "\n\n".join(
        f"[来源 {i}：{chunk.source}，片段 {chunk.index}]\n{chunk.text}"
        for i, (chunk, _) in enumerate(results, start=1)
    )
    recent = history[-4:]
    messages = [
        {
            "role": "system",
            "content": (
                "你是严谨的企业知识库助手。请直接阅读用户消息中“资料”部分并回答“问题”。"
                "只能使用资料里的事实，不得使用外部知识。若资料明确包含答案，必须作答；"
                "只有资料确实没有相关信息时才说“根据现有资料无法确定”。"
                "最终答案控制在3句话以内，不要展示分析过程、推理步骤或自我检查。"
                "在每个关键结论后使用[来源1]这样的编号标注依据。 /no_think"
            ),
        },
        *recent,
        {"role": "user", "content": f"以下是检索到的资料：\n\n{context}\n\n请回答问题：{question}\n/no_think"},
    ]
    return messages


def answer_stream(question: str, results: list[tuple[Chunk, float]], history: list[dict]):
    """流式解析结构化 JSON，只向调用方输出最终答案字段。"""
    messages = build_answer_messages(question, results, history)
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    with requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": CHAT_MODEL,
            "messages": messages,
            "stream": True,
            "think": False,
            "format": schema,
            "options": {"temperature": 0},
        },
        timeout=(10, 180),
        stream=True,
    ) as response:
        response.raise_for_status()
        full_json = ""
        prefix_buffer = ""
        answer_started = False
        answer_finished = False
        escaping = False
        unicode_digits: str | None = None
        pending_high_surrogate: int | None = None
        streamed_answer: list[str] = []
        stream_done = False

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("error"):
                raise RuntimeError(f"Ollama 流式生成失败：{payload['error']}")
            if payload.get("done"):
                stream_done = True
            content = payload.get("message", {}).get("content", "")
            if not content:
                continue
            full_json += content

            if not answer_started:
                prefix_buffer += content
                match = re.search(r'"answer"\s*:\s*"', prefix_buffer)
                if not match:
                    continue
                answer_started = True
                content = prefix_buffer[match.end():]
                prefix_buffer = ""

            visible: list[str] = []
            for char in content:
                if answer_finished:
                    break
                if unicode_digits is not None:
                    unicode_digits += char
                    if len(unicode_digits) == 4:
                        codepoint = int(unicode_digits, 16)
                        unicode_digits = None
                        escaping = False
                        if 0xD800 <= codepoint <= 0xDBFF:
                            pending_high_surrogate = codepoint
                        elif 0xDC00 <= codepoint <= 0xDFFF and pending_high_surrogate is not None:
                            combined = 0x10000 + ((pending_high_surrogate - 0xD800) << 10) + (codepoint - 0xDC00)
                            visible.append(chr(combined))
                            pending_high_surrogate = None
                        else:
                            if pending_high_surrogate is not None:
                                raise RuntimeError("结构化答案包含无效 Unicode 转义")
                            visible.append(chr(codepoint))
                    continue
                if escaping:
                    if char == "u":
                        unicode_digits = ""
                        continue
                    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
                    if char not in escapes:
                        raise RuntimeError("结构化答案包含无效转义")
                    visible.append(escapes[char])
                    escaping = False
                    continue
                if char == "\\":
                    escaping = True
                elif char == '"':
                    answer_finished = True
                else:
                    visible.append(char)

            if visible:
                delta = "".join(visible)
                streamed_answer.append(delta)
                yield delta

        if not stream_done:
            raise RuntimeError("Ollama 流式响应意外中断")
        if not answer_started or not answer_finished:
            raise RuntimeError("Ollama 未返回完整的结构化答案")
        try:
            parsed_answer = json.loads(full_json)["answer"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Ollama 返回了无效的结构化答案") from exc
        if "".join(streamed_answer) != parsed_answer:
            raise RuntimeError("流式答案校验失败")
