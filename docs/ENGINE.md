# Incremental inventory projection

The local reference implementation models a source, an at-least-once latest-version feed and a reporting database using SQLite. It runs with Python’s standard library and SQLite 3.31 or later. Each producer supplies complete product documents. Each tenant owns a separate feed partition, and each target row is identified by the pair `(tenant_id, product_id)`. Names from different tenants can overlap.

## Producer contract

| Field | Type | Meaning |
| --- | --- | --- |
| tenant_id | Non-empty string | Tenant and local logical partition |
| product_id | Non-empty string | Stable product key within the tenant |
| sku | Non-empty string | Business identifier carried into reporting |
| stock | Integer ≥ 0 | Current quantity in units |
| version | Integer ≥ 1 | Increasing version of this product, assigned by the producer |
| deleted | JSON boolean | A retained tombstone when true |
| changed_at_ms | Integer ≥ 0 | Source change time in milliseconds on the producer’s clock |
| parent_id | Non-empty string | Identifier for the source sample or synthetic parent |

Text must be trimmed Unicode with at most 128 UTF-16 code units, matching the Azure SQL storage width. Integer fields fit a signed 64-bit integer. The adapter supplies JSON-compatible objects. A valid document has exactly these eight fields. A tombstone retains the document and version, so delayed updates cannot resurrect a product. An intentional reinstatement requires a higher version.

## Storage and progress

| Table | Grain | Purpose |
| --- | --- | --- |
| source_log | One submitted JSON value per global sequence | Stores canonical JSON, SHA-256, admission decision and any error reason |
| source_head | One version per tenant and product | References the latest admitted source state, including deletes |
| feed_partition | One cursor per tenant or invalid-tenant bucket | Stores the highest offered sequence and last committed checkpoint |
| inventory_current | One row per tenant and product | Holds the latest projected values and their source sequence |
| projection_audit | One applied version per tenant and product | Records the first SQL visibility clock and its latency relative to the source clock |
| quarantine | One record per rejected source sequence | Preserves the reason and links back to the original payload representation |

`append_changes` atomically records a batch and updates the source head. A repeated version with the same canonical document is a duplicate. A different payload claiming a previously observed valid version is a conflict, even after newer revisions arrive. Invalid JSON-compatible values and conflicts are recorded for quarantine. A lower unseen version cannot change current stock. A Python value with no JSON representation causes the entire submission batch to roll back.

`drain` captures high watermarks at entry. For each partition, it reads bounded pages after that partition’s durable checkpoint. Within a page, only the newest admitted update for each product needs to reach the sink. Page boundaries can cause several versions of an item to be observed across pages. The target’s version predicate protects against old, duplicate or concurrent delivery. The projection, audit, quarantine and checkpoint commit together per page. SQLite’s immediate write transaction serializes competing local workers. A new worker can reopen the database and drain the remaining pages after interruption.

The local source log intentionally keeps every submission for inspection and replay experiments. Cosmos DB latest-version delivery can collapse intermediate versions. Retaining the most recent document and expressing deletions as soft deletes allow reconciliation to the final source state. Local cursors and single-database transactions model the recovery contract. Distributed checkpointing, SQL transactions and permissions on Azure require separate validation.

## Metric and recovery

`latency_ms = visible_at_ms - changed_at_ms` for each newly applied version. The caller supplies a common clock in milliseconds. With a simulated clock, the result measures the chosen polling and outage schedule. Retries, coalesced revisions and quarantine records do not earn new latency samples. A negative latency aborts the page. The reporting SQL excludes the version-one bootstrap and returns the nearest-rank p95, defined as the sample at rank ⌈0.95 × n⌉. An empty observation set has no p95. The persisted ordinal checkpoint uses a global sequence space; the backlog query counts pending rows within each partition.

After a sink outage, reopen the same database and call `drain` at the recovery clock. Compare `source_state(db)` with `target_state(db)`, including the delete flag, version and all other document fields. Inspect quarantine payloads using their recorded source sequence and SHA-256. Correct a conflicting or invalid product by submitting a valid, higher-version document. Leave the original submission in place for provenance.

Run the nine engine tests from the repository root.

```sh
python3 -m unittest discover -s tests -p 'test_sync.py' -v
```

The suite exercises concurrent workers, replay after checkpoint rewind, restart after a partially completed drain, an injected SQL failure after earlier writes in the same page, malformed documents and monotonic soft deletes. To inspect current stock, latency, outstanding work and quarantine, run `sqlite3 -header -column PATH_TO_DRILL_DATABASE < sql/local_reporting.sql`.
