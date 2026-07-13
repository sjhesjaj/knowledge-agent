from time import perf_counter

import streamlit as st

from agent import decide_action, list_sources, summarize_knowledge_base
from rag import answer_structured, build_index, check_ollama, read_file, retrieve_fast, split_text


st.set_page_config(page_title="企业知识库 Agent", page_icon="📚", layout="wide")
st.title("📚 企业知识库 Agent")
st.caption("上传企业资料，由本地 Agent 自主选择工具并进行带引用的知识问答")

if "chunks" not in st.session_state:
    st.session_state.chunks = []
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("1. 连接检查")
    ok, detail = check_ollama()
    if ok:
        st.success("Ollama 已连接")
        st.caption(detail)
    else:
        st.error("Ollama 未连接")

    st.header("2. 导入知识")
    files = st.file_uploader("上传 PDF、TXT 或 Markdown", type=["pdf", "txt", "md"], accept_multiple_files=True)
    if st.button("建立知识库", type="primary", disabled=not files or not ok):
        all_chunks, index_stats = [], {}
        try:
            with st.status("正在解析和向量化文档……") as status:
                for file in files:
                    all_chunks.extend(split_text(read_file(file.name, file.getvalue()), file.name))
                st.write(f"已切分为 {len(all_chunks)} 个文本块")
                st.session_state.chunks = build_index(all_chunks, index_stats)
                status.update(label="知识库建立完成", state="complete")
            st.success(
                f"建立耗时 {index_stats['index_seconds']:.3f} 秒｜"
                f"缓存命中 {index_stats['cache_hits']}｜新生成 {index_stats['cache_misses']}"
            )
        except Exception as exc:
            st.exception(exc)

    if st.session_state.chunks:
        st.info(f"当前知识库：{len(st.session_state.chunks)} 个文本块")
        if st.button("清空知识库"):
            st.session_state.chunks = []
            st.session_state.messages = []
            st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

question = st.chat_input("可以问候、查询制度、列出来源或总结知识库……", disabled=not st.session_state.chunks)
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    try:
        with st.chat_message("assistant"):
            total_started = perf_counter()
            decision = decide_action(question, st.session_state.messages[:-1])
            tool_name = decision.get("tool", "direct")
            results, trace = [], {"agent_seconds": decision["seconds"]}

            if decision["type"] == "direct":
                reply = decision["content"]
                st.markdown(reply)
            elif tool_name == "list_knowledge_sources":
                reply = list_sources(st.session_state.chunks)
                st.markdown(reply)
            elif tool_name == "summarize_knowledge_base":
                with st.spinner("Agent 正在总结知识库……"):
                    reply = summarize_knowledge_base(st.session_state.chunks)
                st.markdown(reply)
            else:
                tool_query = decision.get("arguments", {}).get("query") or question
                with st.spinner("Agent 正在调用知识库搜索工具……"):
                    results = retrieve_fast(tool_query, st.session_state.chunks, trace=trace)
                    answer_started = perf_counter()
                    reply = answer_structured(question, results, st.session_state.messages[:-1])
                    st.markdown(reply)
                    trace["answer_seconds"] = perf_counter() - answer_started

            trace["total_seconds"] = perf_counter() - total_started
            with st.expander("查看 Agent 运行轨迹"):
                st.write(f"Agent 决策：{tool_name}")
                st.write(f"决策耗时：{trace['agent_seconds']:.3f} 秒")
                if results:
                    st.write(f"子问题数量：{trace['sub_questions']}")
                    st.write(f"候选片段数量：{trace['candidates']}")
                    st.write(f"检索路径：{trace.get('retrieval_path', 'hybrid_rerank')}")
                    st.write(f"混合召回：{trace['recall_seconds']:.3f} 秒")
                    st.write(f"重排序：{trace['rerank_seconds']:.3f} 秒")
                    st.write(f"答案生成：{trace['answer_seconds']:.3f} 秒")
                st.write(f"总耗时：{trace['total_seconds']:.3f} 秒")

            if results:
                with st.expander("查看检索依据"):
                    for rank, (chunk, score) in enumerate(results, start=1):
                        heading = next(
                            (line.removeprefix("## ") for line in chunk.text.splitlines() if line.startswith("## ")),
                            "未命名章节",
                        )
                        with st.container(border=True):
                            st.markdown(f"### 排名 {rank}｜片段 {chunk.index}｜{heading}｜重排分 {score:.3f}")
                            st.caption(chunk.source)
                            st.write(chunk.text)
        st.session_state.messages.append({"role": "assistant", "content": reply})
    except Exception as exc:
        st.error(f"Agent 运行失败：{exc}")
