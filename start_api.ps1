$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m uvicorn api:app --reload --host 127.0.0.1 --port 8000
