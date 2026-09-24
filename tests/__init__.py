# Unit tests must not depend on a developer's .env or shell LLM settings:
# pin the local Ollama provider and disable .env loading before any test
# module imports rag. The live DeepSeek test reads .env by explicit path.
import os

os.environ["LLM_DOTENV"] = ""
os.environ["LLM_PROVIDER"] = "ollama"
for _name in ("OLLAMA_BASE_URL", "OLLAMA_CHAT_MODEL", "LLM_TIMEOUT"):
    os.environ.pop(_name, None)
