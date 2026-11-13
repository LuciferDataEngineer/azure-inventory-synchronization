# Local consumer outage measured with a monotonic clock

The producer uses the pinned Cosmos Conf source and creates 1,000 sample product identities. Startup loads version one into both stores and closes the first SQLite consumer connection. A separate open writer submits a deterministic mutation once per second during the 600-second interval. Each submission has its own transaction. The consumer performs no reads or writes during that interval. The independent writer confirms that the original target images and checkpoints still match their pre-outage values before restart.

A fresh consumer connection reuses the original database and drains from its persisted cursors. A third connection then reads every updated key separately. It validates the complete document, including source clock, version and soft-delete state, against the expected image folded from producer submissions. A full scan checks all 1,000 keys for missing, extra or mismatched documents. The run checks SQLite integrity and all partition high watermarks before writing evidence.

Run from the repository root, using fresh output paths. The 600-second drill is opt-in. Three CI tests use the same SQLite implementation and an injected clock.

```sh
mkdir -p data
python3 wall_outage.py \
  --database data/outage-reproduction.sqlite \
  --observations data/outage-reproduction-reads.jsonl \
  > data/outage-reproduction.json
python3 -m unittest discover -s tests -p 'test_wall_outage.py' -v
```

[Observed run](../examples/wall-outage.json)

- **Consumer closed seconds** runs from just after closing the first consumer until immediately before opening the replacement. It includes the final pre-restart checks.
- **Producer commits during outage** counts individually committed updates. Seed inserts are excluded. Observed producer pacing comes from each stored mutation’s pre-transaction timestamp, relative to the start of the outage. Repeated versions of the same key can collapse during recovery.
- **Consumer reopen and drain ms** includes opening the replacement consumer and committing the last recovery page.
- **First-read p95 upper bound** uses one observation per *distinct updated key* at its final version. The JSONL includes timestamps just before and just after its last producer transaction and when that key’s independent SQL read returned. All are monotonic nanoseconds relative to the outage start. The reported upper bound subtracts pre-commit time from observed-read time. A product may already have been visible earlier in the catch-up pass. It does not measure first possible visibility or normal-operation latency.
- **Full-state SHA-256** hashes the sorted final images, including measured timestamps. The JSONL hash identifies the exact local observation file. Wall-clock values and these digests vary across runs.

This exercise models a closed consumer connection on one machine. Azure worker eviction, lease handoff, cloud authentication, cross-service retries, and regional recovery require separate deployment tests.
