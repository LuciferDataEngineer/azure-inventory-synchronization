-- Current stock excludes retained tombstones.
SELECT tenant_id, COUNT(*) AS active_products, SUM(stock) AS stock_units
FROM inventory_current WHERE deleted = 0 GROUP BY tenant_id ORDER BY tenant_id;

-- Nearest-rank p95 on applied mutations, excluding the version-one bootstrap.
-- Clocks are supplied by the local simulator.
WITH ranked AS (
    SELECT latency_ms, ROW_NUMBER() OVER (ORDER BY latency_ms) AS rn, COUNT(*) OVER () AS n
    FROM projection_audit WHERE version > 1
)
SELECT latency_ms AS p95_visibility_ms FROM ranked WHERE rn = (95 * n + 99) / 100;

-- A checkpoint is a cursor in a partition of a shared sequence space.
-- Count actual outstanding rows: subtraction of sequence numbers overstates lag.
SELECT p.partition_id, p.checkpoint, p.offered_seq, COUNT(l.seq) AS pending_deliveries
FROM feed_partition p
LEFT JOIN source_log l ON l.partition_id = p.partition_id AND l.seq > p.checkpoint
GROUP BY p.partition_id ORDER BY p.partition_id;

SELECT q.source_seq, q.reason, l.sha256 FROM quarantine q
JOIN source_log l ON l.seq = q.source_seq ORDER BY q.source_seq;
