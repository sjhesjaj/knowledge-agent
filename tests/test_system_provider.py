import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration.contracts import SourceType, ToolStatus
from orchestration.document_adapter import DOCUMENT_AUTHORITY
from orchestration.system_provider import (
    AUTHORITY_SCOPE,
    OPERATION_PARAMETERS,
    OPERATION_SQL,
    SAMPLE_FIXTURE_PATH,
    SYSTEM_AUTHORITY,
    SYSTEM_SOURCE,
    SYSTEM_TOOL_NAME,
    SystemOperation,
    system_query,
)
from orchestration.wiki_adapter import WIKI_AUTHORITY

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDER_SOURCE = (
    REPO_ROOT / "orchestration" / "system_provider.py"
).read_text(encoding="utf-8")

SUBJECT_A = "subject-001"
SUBJECT_B = "subject-002"
ORDER_A = "ord-1001"
ORDER_B = "ord-2001"
APPROVAL_A = "apr-3001"
APPROVAL_B = "apr-3002"
SKU_STOCKED = "sku-a100"
SKU_ZERO = "sku-b200"

TABLES = ("orders", "inventory", "approvals")
NON_STRING_VALUES = (1, True, None, [], {})

VALID_CALLS = (
    (SystemOperation.GET_ORDER_STATUS, {"subject_id": SUBJECT_A, "order_id": ORDER_A}),
    (SystemOperation.GET_INVENTORY_LEVEL, {"sku": SKU_STOCKED}),
    (
        SystemOperation.GET_APPROVAL_STATUS,
        {"subject_id": SUBJECT_A, "approval_id": APPROVAL_A},
    ),
)


def memory_connection() -> sqlite3.Connection:
    """A fresh in-memory database. No test in this module creates a file."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(SAMPLE_FIXTURE_PATH.read_text(encoding="utf-8"))
    return connection


def snapshot(connection: sqlite3.Connection) -> dict[str, list]:
    return {
        table: connection.execute(
            "SELECT * FROM " + table + " ORDER BY 1"
        ).fetchall()
        for table in TABLES
    }


class RecordingCursor:
    """Delegates to a real cursor while recording what the provider did."""

    def __init__(self, cursor, recorder):
        self._cursor = cursor
        self._recorder = recorder
        self.closed = False

    @property
    def row_factory(self):
        return self._cursor.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._cursor.row_factory = value

    def execute(self, sql, bindings=()):
        self._recorder.executed.append((sql, bindings))
        return self._cursor.execute(sql, bindings)

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self.closed = True
        self._recorder.closed_cursors += 1
        return self._cursor.close()


class RecordingConnection:
    """Only `cursor()` is used by the provider; everything else delegates."""

    def __init__(self, connection):
        self._connection = connection
        self.cursor_calls = 0
        self.closed_cursors = 0
        self.executed: list[tuple] = []
        self.cursors: list[RecordingCursor] = []

    def cursor(self):
        self.cursor_calls += 1
        recording = RecordingCursor(self._connection.cursor(), self)
        self.cursors.append(recording)
        return recording

    def __getattr__(self, name):
        return getattr(self._connection, name)


class SystemProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.connection = memory_connection()
        self.addCleanup(self.connection.close)


class HappyPathTests(SystemProviderTestCase):
    def test_order_status(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )
        self.assertEqual(result.status, ToolStatus.OK)
        self.assertEqual(result.tool_name, SYSTEM_TOOL_NAME)
        evidence = result.evidence[0]
        self.assertEqual(evidence.content, "订单 ord-1001 的当前状态为 已发货。")
        self.assertEqual(evidence.locator, "orders:ord-1001")
        self.assertEqual(evidence.source, SYSTEM_SOURCE)
        self.assertEqual(evidence.metadata["operation"], "get_order_status")
        self.assertEqual(evidence.metadata["record_id"], ORDER_A)
        self.assertEqual(evidence.metadata["status"], "已发货")

    def test_inventory_level(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_INVENTORY_LEVEL,
            {"sku": SKU_STOCKED},
        )
        evidence = result.evidence[0]
        self.assertEqual(evidence.content, "SKU sku-a100 的当前库存为 42 件。")
        self.assertEqual(evidence.locator, "inventory:sku-a100")
        self.assertEqual(evidence.metadata["quantity"], 42)
        self.assertEqual(evidence.metadata["unit"], "件")

    def test_approval_status(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_APPROVAL_STATUS,
            {"subject_id": SUBJECT_A, "approval_id": APPROVAL_A},
        )
        evidence = result.evidence[0]
        self.assertEqual(
            evidence.content, "审批 apr-3001 的当前状态为 审批中，当前环节为 部门负责人。"
        )
        self.assertEqual(evidence.locator, "approvals:apr-3001")
        self.assertEqual(evidence.metadata["status"], "审批中")
        self.assertEqual(evidence.metadata["current_step"], "部门负责人")

    def test_zero_quantity_is_present_not_absent(self):
        result = system_query(
            self.connection, SystemOperation.GET_INVENTORY_LEVEL, {"sku": SKU_ZERO}
        )
        self.assertEqual(result.status, ToolStatus.OK)
        self.assertEqual(result.evidence[0].metadata["quantity"], 0)


class SubjectIsolationTests(SystemProviderTestCase):
    def test_order_of_another_subject_is_not_readable(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_B},
        )
        self.assertEqual(result.status, ToolStatus.EMPTY)
        self.assertEqual(result.evidence, ())

    def test_approval_of_another_subject_is_not_readable(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_APPROVAL_STATUS,
            {"subject_id": SUBJECT_A, "approval_id": APPROVAL_B},
        )
        self.assertEqual(result.status, ToolStatus.EMPTY)

    def test_owner_can_read_their_own_record(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_B, "order_id": ORDER_B},
        )
        self.assertEqual(result.status, ToolStatus.OK)


class UnknownRecordTests(SystemProviderTestCase):
    def test_unknown_records_return_empty(self):
        cases = (
            (
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": SUBJECT_A, "order_id": "ord-9999"},
            ),
            (SystemOperation.GET_INVENTORY_LEVEL, {"sku": "sku-zzz"}),
            (
                SystemOperation.GET_APPROVAL_STATUS,
                {"subject_id": SUBJECT_A, "approval_id": "apr-9999"},
            ),
        )
        for operation, parameters in cases:
            with self.subTest(operation=operation.value):
                result = system_query(self.connection, operation, parameters)
                self.assertEqual(result.status, ToolStatus.EMPTY)
                self.assertEqual(result.evidence, ())
                self.assertIsNone(result.error_code)
                self.assertIsNone(result.error_message)
                self.assertEqual(result.tool_name, SYSTEM_TOOL_NAME)


class InputContractTests(SystemProviderTestCase):
    def test_unknown_operation_is_rejected(self):
        for operation in ("get_order_status", "drop_everything", None, 1):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    system_query(
                        self.connection,
                        operation,
                        {"subject_id": SUBJECT_A, "order_id": ORDER_A},
                    )

    def test_parameters_must_be_a_dict(self):
        for parameters in ([], "subject_id", None, 1):
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    system_query(
                        self.connection,
                        SystemOperation.GET_INVENTORY_LEVEL,
                        parameters,
                    )

    def test_missing_parameters_are_rejected(self):
        for operation, valid in VALID_CALLS:
            for name in OPERATION_PARAMETERS[operation]:
                with self.subTest(operation=operation.value, missing=name):
                    parameters = dict(valid)
                    del parameters[name]
                    with self.assertRaises(ValueError):
                        system_query(self.connection, operation, parameters)

    def test_blank_and_non_string_values_are_rejected(self):
        for operation, valid in VALID_CALLS:
            for name in OPERATION_PARAMETERS[operation]:
                for bad in ("", "   ") + NON_STRING_VALUES:
                    with self.subTest(operation=operation.value, name=name, bad=bad):
                        parameters = dict(valid)
                        parameters[name] = bad
                        with self.assertRaises(ValueError):
                            system_query(self.connection, operation, parameters)

    def test_non_string_parameter_keys_are_rejected(self):
        bad_keys = (1, None, (1, 2), 1.5, True, frozenset({"a"}))
        for key in bad_keys:
            with self.subTest(key=key):
                parameters = {"sku": SKU_STOCKED, key: "x"}
                with self.assertRaises(ValueError) as caught:
                    system_query(
                        self.connection,
                        SystemOperation.GET_INVENTORY_LEVEL,
                        parameters,
                    )
                message = str(caught.exception)
                self.assertIn("get_inventory_level", message)
                self.assertIn(".parameters", message)
                self.assertNotIn("x", message)

    def test_mixed_string_and_non_string_extra_keys_are_rejected(self):
        parameters = {"sku": SKU_STOCKED, "zzz": "x", 1: "y", None: "z"}
        with self.assertRaises(ValueError):
            system_query(
                self.connection, SystemOperation.GET_INVENTORY_LEVEL, parameters
            )

    def test_non_string_key_never_reaches_the_connection(self):
        recording = RecordingConnection(self.connection)
        for parameters in (
            {"sku": SKU_STOCKED, 1: "x"},
            {"sku": SKU_STOCKED, None: "x"},
            {"sku": SKU_STOCKED, (1, 2): "x", "extra": "y"},
            {1: "x"},
        ):
            with self.subTest(parameters=sorted(map(repr, parameters))):
                with self.assertRaises(ValueError):
                    system_query(
                        recording,
                        SystemOperation.GET_INVENTORY_LEVEL,
                        parameters,
                    )
        self.assertEqual(recording.cursor_calls, 0)
        self.assertEqual(recording.executed, [])

    def test_extra_parameters_are_rejected(self):
        for operation, valid in VALID_CALLS:
            with self.subTest(operation=operation.value):
                parameters = dict(valid)
                parameters["extra"] = "x"
                with self.assertRaises(ValueError):
                    system_query(self.connection, operation, parameters)

    def test_inventory_rejects_subject_id(self):
        with self.assertRaises(ValueError):
            system_query(
                self.connection,
                SystemOperation.GET_INVENTORY_LEVEL,
                {"sku": SKU_STOCKED, "subject_id": SUBJECT_A},
            )

    def test_messages_name_the_operation_and_parameter_but_never_the_value(self):
        secret = "subject-super-secret"
        with self.assertRaises(ValueError) as caught:
            system_query(
                self.connection,
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": secret, "order_id": ""},
            )
        message = str(caught.exception)
        self.assertIn("get_order_status.parameters.order_id", message)
        self.assertNotIn(secret, message)

        with self.assertRaises(ValueError) as caught:
            system_query(
                self.connection,
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": secret, "order_id": 5},
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_invalid_input_never_reaches_the_connection(self):
        recording = RecordingConnection(self.connection)
        bad_calls = (
            ("get_order_status", {"subject_id": SUBJECT_A, "order_id": ORDER_A}),
            (SystemOperation.GET_ORDER_STATUS, {"subject_id": SUBJECT_A}),
            (SystemOperation.GET_ORDER_STATUS, {"subject_id": SUBJECT_A, "order_id": ""}),
            (SystemOperation.GET_INVENTORY_LEVEL, {"sku": SKU_STOCKED, "extra": "x"}),
            (SystemOperation.GET_INVENTORY_LEVEL, ["sku"]),
        )
        for operation, parameters in bad_calls:
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    system_query(recording, operation, parameters)
        self.assertEqual(recording.cursor_calls, 0)
        self.assertEqual(recording.executed, [])


class CursorContractTests(SystemProviderTestCase):
    def test_caller_row_factory_is_isolated_and_preserved(self):
        baseline = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )

        def dict_factory(cursor, row):
            return {
                column[0]: value for column, value in zip(cursor.description, row)
            }

        self.connection.row_factory = dict_factory
        for operation, parameters in VALID_CALLS:
            with self.subTest(operation=operation.value):
                result = system_query(self.connection, operation, parameters)
                self.assertEqual(result.status, ToolStatus.OK)
        with_factory = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )

        self.assertEqual(
            with_factory.evidence[0].to_dict(), baseline.evidence[0].to_dict()
        )
        self.assertIs(self.connection.row_factory, dict_factory)

    def test_cursor_is_closed_on_success_and_on_database_error(self):
        recording = RecordingConnection(self.connection)
        system_query(
            recording,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )
        self.assertEqual(recording.closed_cursors, 1)

        self.connection.execute("DROP TABLE orders")
        with self.assertRaises(sqlite3.Error):
            system_query(
                recording,
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": SUBJECT_A, "order_id": ORDER_A},
            )
        self.assertEqual(recording.closed_cursors, 2)
        self.assertTrue(all(cursor.closed for cursor in recording.cursors))


class SqlSafetyTests(SystemProviderTestCase):
    def test_queries_use_placeholders_and_pass_bindings_separately(self):
        recording = RecordingConnection(self.connection)
        system_query(
            recording,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )
        sql, bindings = recording.executed[0]
        self.assertIn("?", sql)
        self.assertNotIn(SUBJECT_A, sql)
        self.assertNotIn(ORDER_A, sql)
        self.assertEqual(bindings, (SUBJECT_A, ORDER_A))

    def test_sql_templates_are_fixed_strings_with_placeholders_only(self):
        for operation, sql in OPERATION_SQL.items():
            with self.subTest(operation=operation.value):
                self.assertIsInstance(sql, str)
                # No formatting hook of any kind can reach the SQL.
                for artifact in ("{", "}", "%s", "%d", "+"):
                    self.assertNotIn(artifact, sql)
                self.assertEqual(
                    sql.count("?"), len(OPERATION_PARAMETERS[operation])
                )

    def test_module_uses_no_f_strings(self):
        for forbidden in ('f"', "f'"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, PROVIDER_SOURCE)

    def test_module_contains_no_write_statements(self):
        for forbidden in (
            "INSERT",
            "UPDATE",
            "DELETE",
            "REPLACE",
            "CREATE",
            "DROP",
            "ALTER",
            "PRAGMA",
            "ATTACH",
            "executescript",
            "executemany",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, PROVIDER_SOURCE)

    def test_injection_strings_are_treated_as_values(self):
        before = snapshot(self.connection)
        injections = (
            (
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": SUBJECT_A, "order_id": "' OR '1'='1"},
            ),
            (
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": "x'; DROP TABLE orders;--", "order_id": ORDER_A},
            ),
            (SystemOperation.GET_INVENTORY_LEVEL, {"sku": "%"}),
            (SystemOperation.GET_INVENTORY_LEVEL, {"sku": "sku-a100' OR 1=1 --"}),
        )
        for operation, parameters in injections:
            with self.subTest(parameters=sorted(parameters)):
                result = system_query(self.connection, operation, parameters)
                self.assertEqual(result.status, ToolStatus.EMPTY)
                self.assertEqual(result.evidence, ())
        self.assertEqual(snapshot(self.connection), before)

    def test_queries_never_write(self):
        before = snapshot(self.connection)
        changes_before = self.connection.total_changes
        for operation, parameters in VALID_CALLS:
            system_query(self.connection, operation, parameters)
        system_query(
            self.connection, SystemOperation.GET_INVENTORY_LEVEL, {"sku": "sku-zzz"}
        )
        self.assertEqual(snapshot(self.connection), before)
        self.assertEqual(self.connection.total_changes, changes_before)


class MultiRowDefenceTests(unittest.TestCase):
    """A duplicate lookup key must fail loudly, not silently pick a row."""

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.executescript(
            """
            CREATE TABLE orders (
                order_id TEXT, subject_id TEXT, status TEXT, updated_at TEXT
            );
            CREATE TABLE inventory (
                sku TEXT, quantity INTEGER, unit TEXT, updated_at TEXT
            );
            INSERT INTO orders VALUES ('ord-1', 'subject-001', 'A', 't1');
            INSERT INTO orders VALUES ('ord-1', 'subject-001', 'B', 't2');
            INSERT INTO inventory VALUES ('sku-1', 1, '件', 't1');
            INSERT INTO inventory VALUES ('sku-1', 2, '件', 't2');
            """
        )

    def test_duplicate_rows_raise_naming_the_operation(self):
        cases = (
            (
                SystemOperation.GET_ORDER_STATUS,
                {"subject_id": "subject-001", "order_id": "ord-1"},
            ),
            (SystemOperation.GET_INVENTORY_LEVEL, {"sku": "sku-1"}),
        )
        for operation, parameters in cases:
            with self.subTest(operation=operation.value):
                recording = RecordingConnection(self.connection)
                with self.assertRaises(ValueError) as caught:
                    system_query(recording, operation, parameters)
                self.assertIn(operation.value, str(caught.exception))
                self.assertEqual(recording.closed_cursors, 1)
                self.assertTrue(all(c.closed for c in recording.cursors))


class DatabaseErrorTests(SystemProviderTestCase):
    def test_missing_table_raises_rather_than_returning_empty(self):
        self.connection.execute("DROP TABLE inventory")
        with self.assertRaises(sqlite3.Error):
            system_query(
                self.connection,
                SystemOperation.GET_INVENTORY_LEVEL,
                {"sku": SKU_STOCKED},
            )

    def test_closed_connection_raises(self):
        connection = memory_connection()
        connection.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            system_query(
                connection, SystemOperation.GET_INVENTORY_LEVEL, {"sku": SKU_STOCKED}
            )


class EvidenceMappingTests(SystemProviderTestCase):
    def test_observed_at_comes_from_the_record(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )
        stored = self.connection.execute(
            "SELECT updated_at FROM orders WHERE order_id = ?", (ORDER_A,)
        ).fetchone()[0]
        self.assertEqual(result.evidence[0].observed_at, stored)
        self.assertEqual(result.evidence[0].observed_at, "2026-08-20T09:15:00+08:00")

    def test_module_imports_no_clock(self):
        for forbidden in ("import datetime", "from datetime", "import time"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, PROVIDER_SOURCE)

    def test_authority_scope_and_ordering(self):
        self.assertLess(WIKI_AUTHORITY, DOCUMENT_AUTHORITY)
        self.assertLess(DOCUMENT_AUTHORITY, SYSTEM_AUTHORITY)
        self.assertEqual(SYSTEM_AUTHORITY, 100)
        for operation, parameters in VALID_CALLS:
            with self.subTest(operation=operation.value):
                evidence = system_query(
                    self.connection, operation, parameters
                ).evidence[0]
                self.assertEqual(evidence.authority, SYSTEM_AUTHORITY)
                self.assertEqual(evidence.metadata["authority_scope"], AUTHORITY_SCOPE)
                self.assertEqual(evidence.source_type, SourceType.SYSTEM)

    def test_confidence_and_version_are_none(self):
        for operation, parameters in VALID_CALLS:
            with self.subTest(operation=operation.value):
                evidence = system_query(
                    self.connection, operation, parameters
                ).evidence[0]
                self.assertIsNone(evidence.confidence)
                self.assertIsNone(evidence.version)

    def test_subject_id_never_reaches_evidence(self):
        for operation, parameters in VALID_CALLS:
            if "subject_id" not in parameters:
                continue
            with self.subTest(operation=operation.value):
                evidence = system_query(
                    self.connection, operation, parameters
                ).evidence[0]
                self.assertNotIn(SUBJECT_A, evidence.content)
                self.assertNotIn(SUBJECT_A, evidence.locator)
                self.assertNotIn(SUBJECT_A, repr(evidence.metadata))
                self.assertNotIn(SUBJECT_A, repr(evidence.to_dict()))


class TraceContractTests(SystemProviderTestCase):
    def test_trace_has_caller_keys_plus_provider_fields(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
            trace={"caller": "test"},
        )
        self.assertEqual(
            set(result.trace),
            {
                "caller",
                "system_operation",
                "system_parameter_names",
                "system_rows_matched",
                "system_returned_evidence",
            },
        )
        self.assertEqual(result.trace["system_operation"], "get_order_status")
        self.assertEqual(
            result.trace["system_parameter_names"], ["order_id", "subject_id"]
        )
        self.assertEqual(result.trace["system_rows_matched"], 1)
        self.assertEqual(result.trace["system_returned_evidence"], 1)

    def test_empty_result_reports_zero_counts(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_INVENTORY_LEVEL,
            {"sku": "sku-zzz"},
        )
        self.assertEqual(result.trace["system_rows_matched"], 0)
        self.assertEqual(result.trace["system_returned_evidence"], 0)

    def test_provider_value_overrides_a_colliding_caller_key(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_INVENTORY_LEVEL,
            {"sku": SKU_STOCKED},
            trace={"system_operation": "spoofed"},
        )
        self.assertEqual(result.trace["system_operation"], "get_inventory_level")

    def test_caller_trace_is_not_mutated_and_top_level_isolated(self):
        trace = {"caller": "test"}
        snapshot_before = dict(trace)
        result = system_query(
            self.connection,
            SystemOperation.GET_INVENTORY_LEVEL,
            {"sku": SKU_STOCKED},
            trace=trace,
        )
        self.assertEqual(trace, snapshot_before)

        trace["added_later"] = True
        trace["caller"] = "changed"
        self.assertNotIn("added_later", result.trace)
        self.assertEqual(result.trace["caller"], "test")

    def test_trace_leaks_no_values_or_sql(self):
        result = system_query(
            self.connection,
            SystemOperation.GET_ORDER_STATUS,
            {"subject_id": SUBJECT_A, "order_id": ORDER_A},
        )
        rendered = repr(result.trace)
        self.assertNotIn(SUBJECT_A, rendered)
        self.assertNotIn(ORDER_A, rendered)
        for sql_token in ("SELECT", "FROM", "WHERE", "?"):
            with self.subTest(sql_token=sql_token):
                self.assertNotIn(sql_token, rendered)


class FixtureTests(SystemProviderTestCase):
    def test_fixture_defines_the_required_tables_and_columns(self):
        names = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(names, set(TABLES))

        expected = {
            "orders": {"order_id", "subject_id", "status", "updated_at"},
            "inventory": {"sku", "quantity", "unit", "updated_at"},
            "approvals": {
                "approval_id",
                "subject_id",
                "status",
                "current_step",
                "updated_at",
            },
        }
        for table, columns in expected.items():
            with self.subTest(table=table):
                info = self.connection.execute(
                    "PRAGMA table_info(" + table + ")"
                ).fetchall()
                self.assertEqual({row[1] for row in info}, columns)

    def test_lookup_keys_are_primary_keys_and_columns_are_not_null(self):
        primary_keys = {
            "orders": "order_id",
            "inventory": "sku",
            "approvals": "approval_id",
        }
        for table, key in primary_keys.items():
            with self.subTest(table=table):
                info = self.connection.execute(
                    "PRAGMA table_info(" + table + ")"
                ).fetchall()
                # row = (cid, name, type, notnull, default, pk)
                keys = [row[1] for row in info if row[5]]
                self.assertEqual(keys, [key])
                for row in info:
                    if row[1] != key:
                        self.assertEqual(row[3], 1, row[1] + " must be NOT NULL")

    def test_duplicate_primary_key_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                (ORDER_A, SUBJECT_A, "重复", "2026-08-24T00:00:00+08:00"),
            )

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO inventory VALUES (?, ?, ?, ?)",
                ("sku-neg", -1, "件", "2026-08-24T00:00:00+08:00"),
            )

    def test_fixture_has_at_least_two_subjects(self):
        subjects = {
            row[0]
            for row in self.connection.execute("SELECT subject_id FROM orders")
        }
        self.assertGreaterEqual(len(subjects), 2)


class IsolationTests(SystemProviderTestCase):
    def test_provider_never_opens_a_connection(self):
        with patch(
            "sqlite3.connect",
            side_effect=AssertionError("provider must not open a connection"),
        ):
            for operation, parameters in VALID_CALLS:
                with self.subTest(operation=operation.value):
                    system_query(self.connection, operation, parameters)

    def test_provider_source_touches_no_database_file_or_product_table(self):
        for forbidden in (
            "import storage",
            "sqlite3.connect",
            ".db",
            "knowledge_chunks",
            "conversations",
            "messages",
            "app_meta",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, PROVIDER_SOURCE)

    def test_no_database_file_is_created_in_the_permitted_areas(self):
        for directory in (
            REPO_ROOT,
            REPO_ROOT / "orchestration",
            REPO_ROOT / "tests",
            REPO_ROOT / "system_fixtures",
        ):
            with self.subTest(directory=directory.name):
                self.assertEqual(list(directory.glob("*.db")), [])


if __name__ == "__main__":
    unittest.main()
