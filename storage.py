from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from rag import EMBED_MODEL, Chunk


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SQLiteStorage:
    def __init__(self, path: str | Path = "data/knowledge_agent.db") -> None:
        candidate = Path(path)
        self.path = candidate if candidate.is_absolute() else Path(__file__).resolve().parent / candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    position INTEGER PRIMARY KEY,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    embedding_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    sources_json TEXT,
                    trace_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "client_id" not in columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN client_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_client ON conversations(client_id, updated_at)"
            )
            connection.execute("PRAGMA user_version = 1")

    def _replace_chunks(self, connection: sqlite3.Connection, chunks: list[Chunk]) -> None:
        now = utc_now()
        rows = [
            (
                chunk.index,
                chunk.text,
                chunk.source,
                json.dumps(chunk.embedding) if chunk.embedding is not None else None,
                now,
            )
            for chunk in chunks
        ]
        connection.execute("DELETE FROM knowledge_chunks")
        connection.executemany(
            """
            INSERT INTO knowledge_chunks(position, text, source, embedding_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)",
            [("embedding_model", EMBED_MODEL), ("index_version", "1")],
        )

    def replace_chunks(self, chunks: list[Chunk]) -> None:
        with self.connect() as connection:
            self._replace_chunks(connection, chunks)

    def replace_knowledge(self, chunks: list[Chunk]) -> None:
        """Replace the index and clear incompatible conversations atomically."""
        with self.connect() as connection:
            self._replace_chunks(connection, chunks)
            connection.execute("DELETE FROM conversations")

    def load_chunks(self) -> list[Chunk]:
        with self.connect() as connection:
            metadata = dict(
                connection.execute("SELECT key, value FROM app_meta").fetchall()
            )
            rows = connection.execute(
                """
                SELECT position, text, source, embedding_json
                FROM knowledge_chunks
                ORDER BY position
                """
            ).fetchall()
        if rows and metadata.get("embedding_model") != EMBED_MODEL:
            return []
        return [
            Chunk(
                text=row["text"],
                source=row["source"],
                index=row["position"],
                embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else None,
            )
            for row in rows
        ]

    def clear_chunks(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM knowledge_chunks")
            connection.execute("DELETE FROM app_meta")

    def clear_knowledge(self) -> None:
        """Clear the index metadata and all conversations in one transaction."""
        with self.connect() as connection:
            connection.execute("DELETE FROM knowledge_chunks")
            connection.execute("DELETE FROM app_meta")
            connection.execute("DELETE FROM conversations")

    def create_conversation(
        self,
        client_id: str,
        title: str = "新对话",
        conversation_id: str | None = None,
    ) -> dict:
        conversation_id = conversation_id or str(uuid4())
        now = utc_now()
        clean_title = title.strip()[:60] or "新对话"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, client_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, client_id, clean_title, now, now),
            )
        return {
            "id": conversation_id,
            "client_id": client_id,
            "title": clean_title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }

    def ensure_conversation(
        self,
        conversation_id: str,
        client_id: str,
        title: str = "新对话",
    ) -> None:
        """Create a missing conversation and reject IDs owned by another client."""
        now = utc_now()
        clean_title = title.strip()[:60] or "新对话"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations(id, client_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, client_id, clean_title, now, now),
            )
            owner = connection.execute(
                "SELECT client_id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if owner is None or owner["client_id"] != client_id:
                raise PermissionError("conversation does not belong to this client")

    def list_conversations(self, client_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.client_id = ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC
                """,
                (client_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_messages(self, conversation_id: str, client_id: str) -> list[dict]:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND client_id = ?",
                (conversation_id, client_id),
            ).fetchone()
            if not exists:
                raise KeyError(conversation_id)
            rows = connection.execute(
                """
                SELECT id, role, content, sources_json, trace_json, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "sources": json.loads(row["sources_json"]) if row["sources_json"] else [],
                "trace": json.loads(row["trace_json"]) if row["trace_json"] else None,
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_recent_context(
        self,
        conversation_id: str,
        client_id: str,
        limit: int = 4,
    ) -> list[dict[str, str]]:
        with self.connect() as connection:
            owns_conversation = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND client_id = ?",
                (conversation_id, client_id),
            ).fetchone()
            if not owns_conversation:
                return []
            rows = connection.execute(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            {"role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]

    def commit_exchange(
        self,
        conversation_id: str,
        client_id: str,
        question: str,
        answer: str,
        sources: list[dict] | None = None,
        trace: dict | None = None,
    ) -> None:
        now = utc_now()
        title = question.strip().replace("\n", " ")[:32] or "新对话"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations(id, client_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, client_id, title, now, now),
            )
            owner = connection.execute(
                "SELECT client_id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if owner is None or owner["client_id"] != client_id:
                raise PermissionError("conversation does not belong to this client")
            message_count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            if message_count == 0:
                connection.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    (title, conversation_id),
                )
            connection.executemany(
                """
                INSERT INTO messages(conversation_id, role, content, sources_json, trace_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (conversation_id, "user", question, None, None, now),
                    (
                        conversation_id,
                        "assistant",
                        answer,
                        json.dumps(sources or [], ensure_ascii=False),
                        json.dumps(trace, ensure_ascii=False) if trace else None,
                        now,
                    ),
                ],
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def delete_conversation(self, conversation_id: str, client_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ? AND client_id = ?",
                (conversation_id, client_id),
            )
        return cursor.rowcount > 0

    def clear_conversations(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM conversations")
