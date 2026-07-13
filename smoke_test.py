from pathlib import Path

from rag import answer, build_index, read_file, retrieve, split_text


path = Path("sample_company_rules.md")
chunks = build_index(split_text(read_file(path.name, path.read_bytes()), path.name))
question = "实习生可以休假吗？需要谁审批？"
results = retrieve(question, chunks)
print(answer(question, results, []))
