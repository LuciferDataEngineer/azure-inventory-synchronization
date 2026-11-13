-- Read-only lab observation query (Azure SQL). Run in the Inventory database.
-- Replace this range with the recorded UTC start and end of an approved lab.
-- Rows are applied post-seed versions. Repeated deliveries have no new audit row.
DECLARE @end_utc datetime2(3) = SYSUTCDATETIME();
DECLARE @start_utc datetime2(3) = DATEADD(hour, -4, @end_utc);

;WITH delays AS (
    SELECT tenant_id,
           DATEDIFF_BIG(millisecond, CONVERT(datetime2(3), '1970-01-01T00:00:00'), sql_observed_at)
               - source_epoch_s * CONVERT(bigint, 1000) AS delay_ms
    FROM dbo.ProjectionObservation
    WHERE sql_observed_at >= @start_utc AND sql_observed_at < @end_utc
      AND version > 1
),
ranked AS (
    SELECT tenant_id, delay_ms,
           ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY delay_ms) AS sample_rank,
           COUNT_BIG(*) OVER (PARTITION BY tenant_id) AS samples
    FROM delays
    WHERE delay_ms >= 0
),
invalid AS (
    SELECT tenant_id, SUM(CONVERT(bigint, CASE WHEN delay_ms < 0 THEN 1 ELSE 0 END)) AS negative_samples
    FROM delays GROUP BY tenant_id
)
SELECT i.tenant_id, COUNT_BIG(r.sample_rank) AS eligible_applied_versions,
       MAX(CASE WHEN r.sample_rank = CEILING(r.samples * 0.95) THEN r.delay_ms END) AS p95_ms,
       SUM(CONVERT(bigint, CASE WHEN r.delay_ms > 60000 THEN 1 ELSE 0 END)) AS over_60s,
       MAX(r.delay_ms) AS worst_ms, i.negative_samples
FROM invalid AS i
LEFT JOIN ranked AS r ON r.tenant_id = i.tenant_id
GROUP BY i.tenant_id, i.negative_samples
ORDER BY i.tenant_id;

SELECT tenant_id,active_products,stock_units,retained_deletes FROM dbo.InventoryTotals ORDER BY tenant_id;
