import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import storage as storage_module
from rag import EMBED_MODEL, Chunk, reindex_chunks, split_text
from storage import SQLiteStorage


CLIENT_A = "client-a"
CLIENT_B = "client-b"


class SQLiteStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "storage.db"
        self.storage = SQLiteStorage(self.database_path)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_chunks_and_model_metadata_survive_storage_reopen(self):
        expected = [
            Chunk(text="Policy A", source="rules-a.md", index=1, embedding=[0.1, 0.2]),
            Chunk(text="Policy B", source="rules-b.md", index=2, embedding=[0.3, 0.4]),
        ]
        self.storage.replace_chunks(expected)

        reopened = SQLiteStorage(self.database_path)
        chunks = reopened.load_chunks()
        self.assertEqual(
            [(item.index, item.text, item.source, item.embedding) for item in chunks],
            [(item.index, item.text, item.source, item.embedding) for item in expected],
        )

        with closing(sqlite3.connect(self.database_path)) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM app_meta"))
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(metadata["embedding_model"], EMBED_MODEL)
        self.assertEqual(metadata["index_version"], "1")
        self.assertEqual(schema_version, 1)

    def test_chunks_are_rejected_when_embedding_model_changes(self):
        self.storage.replace_chunks([
            Chunk(text="Policy", source="rules.md", index=1, embedding=[0.1]),
        ])

        with patch.object(storage_module, "EMBED_MODEL", "another-embedding-model"):
            reopened = SQLiteStorage(self.database_path)
            self.assertEqual(reopened.load_chunks(), [])

    def test_conversation_messages_and_metadata_are_restored(self):
        conversation = self.storage.create_conversation(CLIENT_A)
        self.storage.commit_exchange(
            conversation["id"],
            CLIENT_A,
            "How do I request annual leave?",
            "Submit the request in the system. [Source 1]",
            [{"rank": 1, "source": "rules.md"}],
            {"tool": "search_knowledge_base", "total_seconds": 1.2},
        )

        reopened = SQLiteStorage(self.database_path)
        listed = reopened.list_conversations(CLIENT_A)
        messages = reopened.get_messages(conversation["id"], CLIENT_A)
        context = reopened.get_recent_context(conversation["id"], CLIENT_A)

        self.assertEqual(listed[0]["title"], "How do I request annual leave?")
        self.assertEqual(listed[0]["message_count"], 2)
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["sources"], [])
        self.assertIsNone(messages[0]["trace"])
        self.assertEqual(messages[1]["sources"][0]["source"], "rules.md")
        self.assertEqual(messages[1]["trace"]["tool"], "search_knowledge_base")
        self.assertEqual(
            context,
            [
                {"role": "user", "content": "How do I request annual leave?"},
                {"role": "assistant", "content": "Submit the request in the system. [Source 1]"},
            ],
        )

    def test_clients_cannot_list_read_delete_or_append_to_each_others_conversations(self):
        conversation = self.storage.create_conversation(
            CLIENT_A,
            conversation_id="shared-looking-id",
        )
        self.storage.commit_exchange(
            conversation["id"],
            CLIENT_A,
            "Question A",
            "Answer A",
        )

        self.assertEqual(self.storage.list_conversations(CLIENT_B), [])
        self.assertEqual(self.storage.get_recent_context(conversation["id"], CLIENT_B), [])
        with self.assertRaises(KeyError):
            self.storage.get_messages(conversation["id"], CLIENT_B)
        self.assertFalse(self.storage.delete_conversation(conversation["id"], CLIENT_B))
        with self.assertRaises(PermissionError):
            self.storage.ensure_conversation(conversation["id"], CLIENT_B)
        with self.assertRaises(PermissionError):
            self.storage.commit_exchange(
                conversation["id"],
                CLIENT_B,
                "Question B",
                "Answer B",
            )

        messages = self.storage.get_messages(conversation["id"], CLIENT_A)
        self.assertEqual([item["content"] for item in messages], ["Question A", "Answer A"])

    def test_deleting_conversation_cascades_messages(self):
        conversation = self.storage.create_conversation(CLIENT_A, title="Test conversation")
        self.storage.commit_exchange(
            conversation["id"],
            CLIENT_A,
            "Question",
            "Answer",
        )

        self.assertTrue(self.storage.delete_conversation(conversation["id"], CLIENT_A))
        self.assertEqual(self.storage.list_conversations(CLIENT_A), [])
        with closing(sqlite3.connect(self.database_path)) as connection:
            message_count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conversation["id"],),
            ).fetchone()[0]
        self.assertEqual(message_count, 0)

    def test_reindex_chunks_assigns_unique_global_positions_after_file_merge(self):
        first_file = split_text("A" * 300, "first.md", size=120, overlap=20)
        second_file = split_text("B" * 300, "second.md", size=120, overlap=20)
        combined = first_file + second_file
        self.assertLess(len({item.index for item in combined}), len(combined))

        result = reindex_chunks(combined)

        self.assertIs(result, combined)
        self.assertEqual([item.index for item in result], list(range(1, len(result) + 1)))
        self.assertEqual(len({item.index for item in result}), len(result))
        self.assertEqual({item.source for item in result}, {"first.md", "second.md"})


if __name__ == "__main__":
    unittest.main()
