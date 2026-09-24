from __future__ import annotations

import hashlib
import json
import re
from time import perf_counter
from pathlib import Path

import llm_provider
from rag import CHAT_MODEL, Chunk


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "查询企业内部制度、流程、规定和员工手册中的具体事实。",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string", "description": "需要检索的完整问题"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_knowledge_sources",
            "description": "列出知识库当前包含的文件和章节。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_knowledge_base",
            "description": "概括或介绍整个知识库、员工手册的主要内容。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def clean_content(content: str) -> str:
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1]
    return re.sub(r"<think>.*", "", content, flags=re.DOTALL).strip()


def decide_action(question: str, history: list[dict]) -> dict:
    started = perf_counter()
    normalized = question.strip().lower()
    if re.fullmatch(r"(你好|您好|hi|hello|嗨|谢谢|感谢)[！!。. ]*", normalized):
        return {"type": "direct", "content": "你好！有什么企业知识库问题可以帮你？", "seconds": perf_counter() - started}
    if any(word in normalized for word in ("知识库有什么", "有哪些内容", "有哪些资料", "列出来源", "列出章节", "资料来源")):
        return {"type": "tool", "tool": "list_knowledge_sources", "arguments": {}, "seconds": perf_counter() - started}
    if any(word in normalized for word in ("总结员工手册", "总结知识库", "概括员工手册", "概括知识库", "总结一下员工手册", "介绍整份资料", "概括整份资料")):
        return {"type": "tool", "tool": "summarize_knowledge_base", "arguments": {}, "seconds": perf_counter() - started}
    policy_words = (
        "公司", "员工", "制度", "流程", "工资", "请假", "年假", "事假", "调休", "绩效", "培训", "离职", "报销",
        "办公", "账号", "资料", "设备", "出差", "转正", "加班", "考勤", "远程", "到岗",
    )
    if any(word in normalized for word in policy_words):
        return {
            "type": "tool", "tool": "search_knowledge_base",
            "arguments": {"query": question}, "seconds": perf_counter() - started,
        }
    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库Agent。普通问候、感谢和闲聊可直接简短回答。"
                "凡是询问企业制度、事实、流程或员工手册内容，必须调用search_knowledge_base，不能凭记忆回答。"
                "用户询问知识库有什么内容时调用list_knowledge_sources；要求概括整份资料时调用summarize_knowledge_base。"
                "不要虚构工具结果。 /no_think"
            ),
        },
        *history[-4:],
        {"role": "user", "content": question},
    ]
    response = llm_provider.get_provider().chat(messages, tools=TOOLS)
    if response.tool_calls:
        call = response.tool_calls[0]
        return {
            "type": "tool",
            "tool": call.name if call.name is not None else "search_knowledge_base",
            "arguments": call.arguments or {},
            "seconds": perf_counter() - started,
        }
    return {
        "type": "direct",
        "content": clean_content(response.content) or "你好！有什么可以帮你？",
        "seconds": perf_counter() - started,
    }


def list_sources(chunks: list[Chunk]) -> str:
    files = sorted({chunk.source for chunk in chunks})
    headings = []
    for chunk in chunks:
        heading = next((line[3:] for line in chunk.text.splitlines() if line.startswith("## ")), None)
        if heading and heading not in headings:
            headings.append(heading)
    return "当前知识库文件：" + "、".join(files) + "。\n\n包含章节：" + "、".join(headings) + "。"


def summarize_knowledge_base(chunks: list[Chunk]) -> str:
    compact_sections = []
    for chunk in chunks:
        lines = [line.strip() for line in chunk.text.splitlines() if line.strip()]
        heading = next((line[3:] for line in lines if line.startswith("## ")), f"片段{chunk.index}")
        body = "".join(line for line in lines if not line.startswith("#"))
        compact_sections.append(f"{heading}：{body[:140]}")
    context = "\n".join(compact_sections)
    cache_key = hashlib.sha256((CHAT_MODEL + "\n" + context).encode("utf-8")).hexdigest()
    cache_file = Path(".cache/summaries.json")
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    if cache_key in cache:
        return cache[cache_key]

    schema = {
        "type": "object",
        "required": ["summary"],
        "properties": {
            "summary": {"type": "array", "minItems": 5, "maxItems": 8, "items": {"type": "string"}}
        },
    }
    content = llm_provider.get_provider().chat(
        [
            {"role": "system", "content": "请只依据章节摘要，合并相近主题，用5至8条简短要点概括员工手册。"},
            {"role": "user", "content": context},
        ],
        response_format=schema,
        temperature=0,
    ).content
    try:
        items = json.loads(content)["summary"]
        result = "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))
    except (json.JSONDecodeError, KeyError, TypeError):
        result = clean_content(content)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache[cache_key] = result
    cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return result
