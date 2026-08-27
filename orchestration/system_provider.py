"""Read-only queries against a demonstration business system.

This provider answers *what is true right now* for one business record. Its
authority is scoped: `authority_scope` is always `current_operational_state`,
which means System evidence outranks other sources on live state and carries no
weight at all when interpreting what a policy means.

Safety properties, all structural rather than advisory:

- the caller chooses an operation from a closed enum, never a table, column,
  predicate, or SQL string;
- one whitelisted single-statement query per operation, with every value bound
  through a placeholder, so an injection string is only ever a value;
- the module contains no write statement of any kind and opens no connection -
  it borrows one the caller already owns;
- rows are read positionally on the provider's own cursor, so a caller's
  `row_factory` changes nothing about decoding and is never overwritten;
- `observed_at` is the record's own `updated_at`, never the query time, because
  query time describes when we looked rather than when the state was true;
- `subject_id` is used to scope the lookup but never travels into Evidence.

Offline and deterministic: no clock, no randomness, no network.
"""

from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path

from .contracts import Evidence, SourceType, ToolResult, ToolStatus

SYSTEM_TOOL_NAME = "system_query"
# Above document (80) and wiki (60), but only for live operational state.
SYSTEM_AUTHORITY = 100
SYSTEM_SOURCE = "demo-business-system"
AUTHORITY_SCOPE = "current_operational_state"

# Location only. The provider never reads or runs this file; tests load it.
SAMPLE_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "system_fixtures"
    / "sample_business_system.sql"
)


class SystemOperation(str, Enum):
    GET_ORDER_STATUS = "get_order_status"
    GET_INVENTORY_LEVEL = "get_inventory_level"
    GET_APPROVAL_STATUS = "get_approval_status"


# Ordered tuples: binding order comes from this declaration, never from dict
# iteration order.
OPERATION_PARAMETERS: dict[SystemOperation, tuple[str, ...]] = {
    SystemOperation.GET_ORDER_STATUS: ("subject_id", "order_id"),
    SystemOperation.GET_INVENTORY_LEVEL: ("sku",),
    SystemOperation.GET_APPROVAL_STATUS: ("subject_id", "approval_id"),
}

# One single-statement query per operation. Values bind through placeholders.
# subject_id is a predicate only: it is deliberately not selected, so it cannot
# reach Evidence.
OPERATION_SQL: dict[SystemOperation, str] = {
    SystemOperation.GET_ORDER_STATUS: (
        "SELECT order_id, status, updated_at FROM orders"
        " WHERE subject_id = ? AND order_id = ?"
    ),
    SystemOperation.GET_INVENTORY_LEVEL: (
        "SELECT sku, quantity, unit, updated_at FROM inventory WHERE sku = ?"
    ),
    SystemOperation.GET_APPROVAL_STATUS: (
        "SELECT approval_id, status, current_step, updated_at FROM approvals"
        " WHERE subject_id = ? AND approval_id = ?"
    ),
}

# Result columns are read by position against these declarations.
OPERATION_COLUMNS: dict[SystemOperation, tuple[str, ...]] = {
    SystemOperation.GET_ORDER_STATUS: ("order_id", "status", "updated_at"),
    SystemOperation.GET_INVENTORY_LEVEL: ("sku", "quantity", "unit", "updated_at"),
    SystemOperation.GET_APPROVAL_STATUS: (
        "approval_id",
        "status",
        "current_step",
        "updated_at",
    ),
}

OPERATION_TABLES: dict[SystemOperation, str] = {
    SystemOperation.GET_ORDER_STATUS: "orders",
    SystemOperation.GET_INVENTORY_LEVEL: "inventory",
    SystemOperation.GET_APPROVAL_STATUS: "approvals",
}

OPERATION_RECORD_ID: dict[SystemOperation, str] = {
    SystemOperation.GET_ORDER_STATUS: "order_id",
    SystemOperation.GET_INVENTORY_LEVEL: "sku",
    SystemOperation.GET_APPROVAL_STATUS: "approval_id",
}

# Structured business fields carried in metadata, per operation.
OPERATION_BUSINESS_FIELDS: dict[SystemOperation, tuple[str, ...]] = {
    SystemOperation.GET_ORDER_STATUS: ("status",),
    SystemOperation.GET_INVENTORY_LEVEL: ("quantity", "unit"),
    SystemOperation.GET_APPROVAL_STATUS: ("status", "current_step"),
}

# Module constants so the wording cannot drift. These render Evidence content
# only; no SQL is ever built by formatting.
OPERATION_CONTENT: dict[SystemOperation, str] = {
    SystemOperation.GET_ORDER_STATUS: "订单 {order_id} 的当前状态为 {status}。",
    SystemOperation.GET_INVENTORY_LEVEL: "SKU {sku} 的当前库存为 {quantity} {unit}。",
    SystemOperation.GET_APPROVAL_STATUS: (
        "审批 {approval_id} 的当前状态为 {status}，当前环节为 {current_step}。"
    ),
}


def _validate(operation: object, parameters: object) -> None:
    """Reject every contract violation before the connection is touched.

    Messages name the operation and the parameter, never the parameter's value.
    """
    if not isinstance(operation, SystemOperation):
        raise ValueError(
            "operation must be a SystemOperation member, got "
            + type(operation).__name__
        )
    if not isinstance(parameters, dict):
        raise ValueError(
            operation.value
            + ".parameters must be a dict, got "
            + type(parameters).__name__
        )

    # Keys are checked before any set/sort/join touches them: a non-string key
    # would otherwise escape as a TypeError from sorted() or join().
    for key in parameters:
        if not isinstance(key, str):
            raise ValueError(
                operation.value
                + ".parameters keys must all be strings, got "
                + type(key).__name__
            )

    required = OPERATION_PARAMETERS[operation]
    supplied = set(parameters)
    missing = [name for name in required if name not in supplied]
    if missing:
        raise ValueError(
            operation.value + ".parameters is missing: " + ", ".join(missing)
        )
    unexpected = sorted(supplied - set(required))
    if unexpected:
        raise ValueError(
            operation.value
            + ".parameters does not accept: "
            + ", ".join(unexpected)
        )

    for name in required:
        value = parameters[name]
        path = operation.value + ".parameters." + name
        if not isinstance(value, str):
            raise ValueError(
                path + " must be a string, got " + type(value).__name__
            )
        if not value.strip():
            raise ValueError(path + " must not be empty")


def system_query(
    connection: sqlite3.Connection,
    operation: SystemOperation,
    parameters: dict[str, str],
    *,
    trace: dict | None = None,
) -> ToolResult:
    """Look up one business record's current state.

    Raises `ValueError` for any input-contract violation, before the connection
    is touched. Database failures propagate unchanged: an infrastructure error
    is never reported as an absence of records.
    """
    _validate(operation, parameters)

    bindings = tuple(parameters[name] for name in OPERATION_PARAMETERS[operation])

    # The provider's own cursor with the factory cleared: the caller's
    # connection.row_factory is left untouched and cannot change decoding.
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        cursor.execute(OPERATION_SQL[operation], bindings)
        rows = cursor.fetchall()
    finally:
        cursor.close()

    if len(rows) > 1:
        # The lookup key is a primary key, so this cannot happen against a valid
        # fixture. Fail loudly rather than silently picking a row.
        raise ValueError(
            operation.value
            + " matched "
            + str(len(rows))
            + " rows; a record lookup must match at most one"
        )

    evidence: tuple[Evidence, ...] = ()
    if rows:
        evidence = (_to_evidence(operation, rows[0]),)

    trace_copy = dict(trace) if trace is not None else {}
    result_trace: dict[str, object] = {
        **trace_copy,
        "system_operation": operation.value,
        # Names only. Values, including subject_id, must never be traced.
        "system_parameter_names": sorted(parameters),
        "system_rows_matched": len(rows),
        "system_returned_evidence": len(evidence),
    }

    status = ToolStatus.OK if evidence else ToolStatus.EMPTY
    return ToolResult(
        tool_name=SYSTEM_TOOL_NAME,
        status=status,
        evidence=evidence,
        trace=result_trace,
    )


def _to_evidence(operation: SystemOperation, row: tuple) -> Evidence:
    values = dict(zip(OPERATION_COLUMNS[operation], row))
    record_id = values[OPERATION_RECORD_ID[operation]]

    metadata: dict[str, object] = {
        "operation": operation.value,
        "record_id": record_id,
        # System authority holds for live state only, never for policy meaning.
        "authority_scope": AUTHORITY_SCOPE,
    }
    for name in OPERATION_BUSINESS_FIELDS[operation]:
        metadata[name] = values[name]

    return Evidence(
        content=OPERATION_CONTENT[operation].format_map(values),
        source_type=SourceType.SYSTEM,
        source=SYSTEM_SOURCE,
        locator=OPERATION_TABLES[operation] + ":" + str(record_id),
        version=None,
        # From the record, never from the clock.
        observed_at=values["updated_at"],
        authority=SYSTEM_AUTHORITY,
        confidence=None,
        metadata=metadata,
    )
