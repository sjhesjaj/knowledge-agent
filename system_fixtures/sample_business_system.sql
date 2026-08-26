-- Demonstration business system for the read-only System provider.
--
-- Entirely fictional data. No real customer, order, or personal information.
-- Loaded by tests into an in-memory database; the provider never runs this file.
--
-- updated_at values are fixed literals, never generated at load time, so that
-- Evidence.observed_at is reproducible across runs.

CREATE TABLE orders (
    order_id   TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO orders (order_id, subject_id, status, updated_at) VALUES
    ('ord-1001', 'subject-001', '已发货', '2026-08-20T09:15:00+08:00'),
    ('ord-1002', 'subject-001', '待付款', '2026-08-21T10:30:00+08:00'),
    ('ord-2001', 'subject-002', '已完成', '2026-08-19T16:45:00+08:00');

CREATE TABLE inventory (
    sku        TEXT PRIMARY KEY,
    quantity   INTEGER NOT NULL CHECK (quantity >= 0),
    unit       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO inventory (sku, quantity, unit, updated_at) VALUES
    ('sku-a100', 42, '件', '2026-08-22T08:00:00+08:00'),
    ('sku-b200', 0, '件', '2026-08-22T08:05:00+08:00'),
    ('sku-c300', 7, '箱', '2026-08-22T08:10:00+08:00');

CREATE TABLE approvals (
    approval_id  TEXT PRIMARY KEY,
    subject_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_step TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

INSERT INTO approvals (approval_id, subject_id, status, current_step, updated_at) VALUES
    ('apr-3001', 'subject-001', '审批中', '部门负责人', '2026-08-23T11:00:00+08:00'),
    ('apr-3002', 'subject-002', '已通过', '已归档', '2026-08-18T14:20:00+08:00');
