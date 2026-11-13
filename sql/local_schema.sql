CREATE TABLE IF NOT EXISTS feed_partition (
    partition_id TEXT PRIMARY KEY,
    offered_seq INTEGER NOT NULL,
    checkpoint INTEGER NOT NULL DEFAULT 0 CHECK(checkpoint BETWEEN 0 AND offered_seq)
);
CREATE TABLE IF NOT EXISTS source_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    partition_id TEXT NOT NULL REFERENCES feed_partition(partition_id),
    product_id TEXT,
    version INTEGER,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    admission TEXT NOT NULL CHECK(admission IN ('changed','duplicate','stale','invalid','conflict')),
    reason TEXT
);
CREATE INDEX IF NOT EXISTS feed_page ON source_log(partition_id, seq);
CREATE INDEX IF NOT EXISTS first_version ON source_log(partition_id, product_id, version, seq)
    WHERE admission IN ('changed','stale');
CREATE TABLE IF NOT EXISTS source_head (
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_seq INTEGER NOT NULL REFERENCES source_log(seq),
    PRIMARY KEY(tenant_id, product_id)
);
CREATE TABLE IF NOT EXISTS inventory_current (
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK(stock >= 0),
    version INTEGER NOT NULL CHECK(version > 0),
    deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
    changed_at_ms INTEGER NOT NULL CHECK(changed_at_ms >= 0),
    parent_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL REFERENCES source_log(seq),
    PRIMARY KEY(tenant_id,product_id)
);
CREATE INDEX IF NOT EXISTS active_stock ON inventory_current(tenant_id, stock) WHERE deleted=0;
CREATE TABLE IF NOT EXISTS projection_audit (
    source_seq INTEGER PRIMARY KEY REFERENCES source_log(seq),
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    changed_at_ms INTEGER NOT NULL,
    visible_at_ms INTEGER NOT NULL CHECK(visible_at_ms >= changed_at_ms),
    latency_ms INTEGER GENERATED ALWAYS AS (visible_at_ms - changed_at_ms) STORED,
    UNIQUE(tenant_id, product_id, version)
);
CREATE INDEX IF NOT EXISTS visibility_interval ON projection_audit(visible_at_ms);
CREATE TABLE IF NOT EXISTS quarantine (
    source_seq INTEGER PRIMARY KEY REFERENCES source_log(seq),
    reason TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
